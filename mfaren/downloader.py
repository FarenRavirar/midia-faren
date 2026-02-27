import os
import tempfile
import urllib.request

from .ffmpeg import (
    build_audio_cmd,
    build_video_cmd,
    build_image_cmd,
    find_ffmpeg,
    infer_runtime_accel,
    normalize_ffmpeg_progress,
    run_ffmpeg,
)
from .audio_mix import build_audio_mix
from .transcriber import transcribe_file
from .util import ensure_dir, make_output_name, sanitize_filename
from .ytdlp import download_with_fallback, find_ytdlp, get_metadata, is_youtube_url


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stage_payload(payload, stage_index, stage_total, stage_name):
    data = dict(payload or {})
    stage_percent = _to_float(data.get("percent"))
    if stage_percent is None:
        stage_percent = 0.0
    stage_percent = max(0.0, min(100.0, stage_percent))
    overall = ((stage_index - 1) + (stage_percent / 100.0)) / max(1, stage_total) * 100.0
    data["percent"] = overall
    data["message"] = f"Etapa {stage_index}/{stage_total}: {stage_name} ({stage_percent:.1f}%)"
    return data


def _download_direct(url, temp_path, progress_cb=None, cancel_event=None):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(temp_path, "wb") as f:
        total = resp.headers.get("Content-Length")
        total = int(total) if total else None
        downloaded = 0
        chunk = 1024 * 512
        while True:
            if cancel_event and cancel_event.is_set():
                return 1
            data = resp.read(chunk)
            if not data:
                break
            f.write(data)
            downloaded += len(data)
            if progress_cb and total:
                percent = (downloaded / total) * 100.0
                progress_cb(
                    {
                        "percent": percent,
                        "downloaded_bytes": downloaded,
                        "total_bytes": total,
                        "message": "Baixando",
                    }
                )
    return 0


def process_job(job, options, progress_cb, cancel_event, logger, pid_cb=None):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg nao encontrado")

    output_dir = options.get("output_dir")
    ensure_dir(output_dir)

    if job["source_type"] == "local":
        input_path = job["input_path"]
        title = job.get("title") or os.path.splitext(os.path.basename(input_path))[0] or "nao informado"
        output_base = sanitize_filename(title) or "nao informado"
        if options.get("mode") in ("mixagem", "craig_notebook"):
            options = {**options, "job_id": job.get("id")}
            output_path, meta = build_audio_mix(
                input_path,
                options,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                logger=logger,
                pid_cb=pid_cb,
            )
            return output_path, meta
        if options.get("mode") == "transcribe":
            options = {**options, "job_id": job.get("id")}
            txt_path, srt_path = transcribe_file(
                input_path,
                options,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                logger=logger,
                pid_cb=pid_cb,
            )
            return srt_path or txt_path, {"title": title, "channel": "nao informado"}
        def _convert_progress(payload):
            if progress_cb:
                progress_cb(_stage_payload(payload, 1, 1, "Conversao"))

        output_path = _convert(job, input_path, output_base, output_dir, options, _convert_progress, cancel_event, pid_cb)
        return output_path, {"title": title, "channel": "nao informado"}

    url = job["url"]
    if is_youtube_url(url) and not find_ytdlp():
        raise RuntimeError("yt-dlp nao encontrado. Coloque tools/yt-dlp.exe ou configure no PATH.")
    use_ytdlp = is_youtube_url(url)

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = os.path.join(tmpdir, "download.mkv")
        meta = {"title": "nao informado", "channel": "nao informado", "duration": None}
        if use_ytdlp:
            ytdlp = find_ytdlp()
            meta = get_metadata(ytdlp, url)
            output_base = make_output_name(meta["title"], meta["channel"])
            def _download_progress(payload):
                if progress_cb:
                    progress_cb(_stage_payload(payload, 1, 2, "Download"))

            rc, pid, last_lines = download_with_fallback(
                url,
                temp_path,
                progress_cb=_download_progress,
                cancel_event=cancel_event,
                pid_cb=pid_cb,
                logger=logger,
                options=options,
            )
            if rc != 0:
                detail = " | ".join(last_lines[-5:]) if last_lines else "sem detalhes"
                raise RuntimeError(f"Falha no download via yt-dlp: {detail}")
        else:
            output_base = make_output_name("nao informado", "nao informado")
            def _direct_progress(payload):
                if progress_cb:
                    progress_cb(_stage_payload(payload, 1, 2, "Download"))

            rc = _download_direct(url, temp_path, progress_cb=_direct_progress, cancel_event=cancel_event)
            if rc != 0:
                raise RuntimeError("Falha no download direto")
        def _convert_progress(payload):
            if progress_cb:
                progress_cb(_stage_payload(payload, 2, 2, "Conversao"))

        output_path = _convert(job, temp_path, output_base, output_dir, options, _convert_progress, cancel_event, pid_cb, meta)
        return output_path, meta


def _convert(job, input_path, output_base, output_dir, options, progress_cb, cancel_event, pid_cb=None, meta=None):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg nao encontrado")

    mode = options.get("mode")
    if mode == "audio":
        ext = options.get("format")
    elif mode == "image":
        ext = options.get("image_format")
    else:
        ext = options.get("container")
    output_path = os.path.join(output_dir, f"{output_base}.{ext}")

    duration = None
    if meta and meta.get("duration"):
        duration = meta.get("duration")

    if mode == "audio":
        cmd = build_audio_cmd(ffmpeg, input_path, output_path, options)
    elif mode == "image":
        cmd = build_image_cmd(ffmpeg, input_path, output_path, options)
    else:
        cmd = build_video_cmd(ffmpeg, input_path, output_path, options)
    runtime_accel = infer_runtime_accel(options, ffmpeg_bin=ffmpeg)
    if progress_cb:
        progress_cb(
            {
                "percent": 0.0,
                "speed": None,
                "eta_seconds": None,
                "downloaded_bytes": None,
                "total_bytes": None,
                "runtime_accel": runtime_accel,
            }
        )

    def _cb(progress):
        parsed = normalize_ffmpeg_progress(progress, duration=duration)
        parsed["runtime_accel"] = runtime_accel
        if progress_cb:
            progress_cb(parsed)

    rc, pid = run_ffmpeg(cmd, progress_cb=_cb, cancel_event=cancel_event, pid_cb=pid_cb)
    if pid_cb:
        pid_cb(pid)
    if rc != 0:
        raise RuntimeError("Falha na conversao")
    return output_path
