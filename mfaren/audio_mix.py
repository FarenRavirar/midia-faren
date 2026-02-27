import json
import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime

from . import transcribe_exec as texec
from . import transcribe_io as tio
from .util import sanitize_filename


AUDIO_EXTENSIONS = {
    ".wav",
    ".flac",
    ".mp3",
    ".ogg",
    ".m4a",
    ".aac",
    ".opus",
    ".webm",
    ".mp4",
    ".mkv",
    ".mov",
}

FORMAT_CODECS = {
    "m4a": "aac",
    "mp3": "libmp3lame",
    "wav": "pcm_s16le",
}


def _is_archive(path):
    lower = str(path or "").lower()
    return lower.endswith(".zip") or lower.endswith(".aup.zip")


def _to_int(value, default, min_value=None, max_value=None):
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = int(default)
    if min_value is not None and parsed < min_value:
        parsed = int(min_value)
    if max_value is not None and parsed > max_value:
        parsed = int(max_value)
    return parsed


def _emit_stage(
    progress_cb,
    stage_index,
    stage_total,
    stage_name,
    stage_percent,
    speed=None,
    eta_seconds=None,
    size=None,
    started_at=None,
    total_audio_seconds=None,
    runtime_state=None,
):
    if not progress_cb:
        return
    pct = max(0.0, min(100.0, float(stage_percent or 0.0)))
    overall = ((stage_index - 1) + (pct / 100.0)) / max(1, stage_total) * 100.0
    now = time.time()
    elapsed = max(0.0, float(now - float(started_at or now)))
    eta_auto = None
    remaining_auto = None
    total_estimated = None
    stalled_seconds = 0.0

    if isinstance(runtime_state, dict):
        prev_overall = runtime_state.get("last_overall_percent")
        prev_total = runtime_state.get("total_estimated_seconds")
        last_progress_ts = runtime_state.get("last_progress_ts")
        progress_advanced = (
            prev_overall is None
            or overall > (float(prev_overall) + 1e-4)
            or overall < (float(prev_overall) - 5.0)
        )

        if progress_advanced and overall > 0.0:
            raw_total = elapsed / (overall / 100.0)
            if prev_total is not None:
                try:
                    prev_total_f = float(prev_total)
                except Exception:
                    prev_total_f = raw_total
                blended = (prev_total_f * 0.75) + (raw_total * 0.25)
                low = prev_total_f * 0.85
                high = prev_total_f * 1.15
                total_estimated = max(low, min(high, blended))
            else:
                total_estimated = raw_total
            remaining_auto = max(0.0, total_estimated - elapsed)
            eta_auto = remaining_auto
            runtime_state["last_progress_ts"] = now
        else:
            if prev_total is not None:
                try:
                    total_estimated = float(prev_total)
                except Exception:
                    total_estimated = None
            prev_eta = runtime_state.get("eta_seconds")
            if prev_eta is not None:
                try:
                    remaining_auto = max(0.0, float(prev_eta))
                except Exception:
                    remaining_auto = None
            eta_auto = remaining_auto
            if last_progress_ts is not None:
                try:
                    stalled_seconds = max(0.0, now - float(last_progress_ts))
                except Exception:
                    stalled_seconds = 0.0

        runtime_state["last_overall_percent"] = float(overall)
        if total_estimated is not None:
            runtime_state["total_estimated_seconds"] = float(total_estimated)
        if eta_auto is not None:
            runtime_state["eta_seconds"] = float(eta_auto)
    else:
        if overall > 0.0:
            total_estimated = elapsed / (overall / 100.0)
            remaining_auto = max(0.0, total_estimated - elapsed)
            eta_auto = remaining_auto

    eta_final = eta_auto if eta_auto is not None else eta_seconds
    payload = {
        "percent": overall,
        "speed": speed,
        "eta_seconds": eta_final,
        "size": size,
        "message": f"Etapa {stage_index}/{stage_total}: {stage_name} ({pct:.1f}%)",
    }
    payload["mix_metrics"] = {
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining_auto if remaining_auto is not None else None,
        "total_estimated_seconds": total_estimated if total_estimated is not None else None,
        "eta_seconds": eta_final,
        "overall_percent": overall,
        "stage_index": int(stage_index),
        "stage_total": int(stage_total),
        "stage_percent": pct,
        "stalled_seconds": stalled_seconds,
    }

    total_audio = float(total_audio_seconds or 0.0)
    if total_audio > 0.0:
        current_audio = max(0.0, min(total_audio, (overall / 100.0) * total_audio))
        remaining_audio = max(0.0, total_audio - current_audio)
        rate = (current_audio / elapsed) if elapsed > 0.0 else 0.0
        eta_audio = (remaining_audio / rate) if rate > 0.0 else None
        payload["transcribe_metrics"] = {
            "elapsed_seconds": elapsed,
            "current_audio_seconds": current_audio,
            "remaining_audio_seconds": remaining_audio,
            "total_audio_seconds": total_audio,
            "eta_seconds": eta_audio if eta_audio is not None else eta_final,
        }
    progress_cb(payload)


def _assert_not_canceled(cancel_event):
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("Cancelado")


def _extract_selected_audio(
    archive_path,
    extract_dir,
    options,
    cancel_event=None,
    progress_cb=None,
    started_at=None,
    total_audio_seconds=None,
    runtime_state=None,
):
    selected_rel = tio.resolve_archive_selected_relpaths(options, archive_path)
    extracted = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        infos = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = str(info.filename or "").replace("\\", "/")
            if selected_rel and rel not in selected_rel:
                continue
            ext = os.path.splitext(rel)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue
            infos.append(info)
        infos.sort(key=lambda item: str(item.filename or "").lower())
        if not infos:
            if selected_rel:
                raise RuntimeError("Nenhum audio interno selecionado no ZIP")
            raise RuntimeError("ZIP sem audios suportados para unificacao")

        total = len(infos)
        total_bytes = float(sum(max(1, int(getattr(i, "file_size", 0) or 0)) for i in infos))
        done_bytes = 0.0
        for idx, info in enumerate(infos, start=1):
            _assert_not_canceled(cancel_event)
            rel = str(info.filename or "").replace("\\", "/")
            out_path = os.path.join(extract_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with zf.open(info, "r") as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            extracted.append(out_path)
            done_bytes += float(max(1, int(getattr(info, "file_size", 0) or 0)))
            _emit_stage(
                progress_cb,
                1,
                4,
                "Extração",
                (done_bytes / total_bytes) * 100.0 if total_bytes > 0.0 else (idx / total) * 100.0,
                started_at=started_at,
                total_audio_seconds=total_audio_seconds,
                runtime_state=runtime_state,
            )
    return extracted


def _build_prepare_cmd(ffmpeg, src_path, dst_path):
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        src_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        dst_path,
    ]
    return cmd


def _build_prepare_cmd_with_normalize(ffmpeg, src_path, dst_path, normalize_on):
    cmd = _build_prepare_cmd(ffmpeg, src_path, dst_path)
    if str(normalize_on or "").strip().lower() in ("on", "1", "true", "yes", "sim", "s"):
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            src_path,
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            dst_path,
        ]
    return cmd


def _build_mix_cmd(ffmpeg, prepared_files, mixed_wav):
    cmd = [ffmpeg, "-y"]
    for path in prepared_files:
        cmd.extend(["-i", path])
    cmd.extend(
        [
            "-filter_complex",
            f"amix=inputs={len(prepared_files)}:duration=longest:dropout_transition=2",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            mixed_wav,
        ]
    )
    return cmd


def _build_export_cmd(ffmpeg, src_wav, dst_file, out_format, bitrate_kbps):
    codec = FORMAT_CODECS.get(out_format, "aac")
    cmd = [ffmpeg, "-y", "-i", src_wav, "-ar", "16000", "-ac", "1", "-c:a", codec]
    if out_format != "wav":
        cmd.extend(["-b:a", f"{int(bitrate_kbps)}k"])
    cmd.append(dst_file)
    return cmd


def _estimate_bitrate_for_limit(limit_bytes, duration_seconds, headroom=0.95):
    duration = max(1.0, float(duration_seconds or 1.0))
    raw = (float(limit_bytes) * 8.0 * float(headroom)) / duration / 1000.0
    return int(max(24, min(512, raw)))


def _encode_once(
    ffmpeg,
    mixed_wav,
    output_path,
    out_format,
    bitrate_kbps,
    duration,
    progress_cb,
    cancel_event,
    pid_cb,
    logger,
    started_at=None,
    total_audio_seconds=None,
    runtime_state=None,
):
    cmd = _build_export_cmd(ffmpeg, mixed_wav, output_path, out_format, bitrate_kbps)

    def _stage4_progress(payload):
        local = float(payload.get("percent") or 0.0)
        _emit_stage(
            progress_cb,
            4,
            4,
            "Exportação",
            local,
            speed=payload.get("speed"),
            eta_seconds=payload.get("eta_seconds"),
            size=payload.get("downloaded_bytes"),
            started_at=started_at,
            total_audio_seconds=total_audio_seconds,
            runtime_state=runtime_state,
        )

    texec.run_ffmpeg_stage(
        cmd,
        duration,
        "Exportacao",
        progress_cb=_stage4_progress,
        cancel_event=cancel_event,
        pid_cb=pid_cb,
        logger=logger,
    )


def build_audio_mix(input_path, options, progress_cb, cancel_event, logger, pid_cb):
    ffmpeg = tio.find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg nao encontrado")

    if not _is_archive(input_path):
        raise RuntimeError("Modo Mixagem aceita apenas ZIP (.zip/.aup.zip)")

    output_dir = str(options.get("output_dir") or "").strip()
    if not output_dir:
        raise RuntimeError("Pasta de saida nao definida")
    os.makedirs(output_dir, exist_ok=True)

    output_format = str(options.get("mix_output_format") or options.get("notebook_output_format") or "m4a").strip().lower()
    if output_format not in ("m4a", "mp3", "wav"):
        output_format = "m4a"
    target_kbps = _to_int(
        options.get("mix_target_bitrate_kbps") or options.get("notebook_target_bitrate_kbps"),
        default=96,
        min_value=24,
        max_value=512,
    )
    max_size_mb = _to_int(
        options.get("mix_max_size_mb") or options.get("notebook_max_size_mb"),
        default=190,
        min_value=1,
        max_value=2048,
    )
    normalize_on = str(options.get("normalize") or "off").strip().lower()
    max_bytes = int(max_size_mb) * 1024 * 1024

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = sanitize_filename(os.path.splitext(os.path.basename(input_path))[0] or "sessao")
    final_base = f"{base_name}_mixagem_{timestamp}"
    final_output = os.path.join(output_dir, f"{final_base}.{output_format}")
    meta_path = os.path.join(output_dir, f"{final_base}.json")
    started_at = time.time()
    runtime_state = {"eta_seconds": None, "last_progress_ts": started_at}

    if logger:
        logger.info(
            "audio_mix_start input=%s output_dir=%s format=%s bitrate=%sk max_mb=%s normalize=%s",
            input_path,
            output_dir,
            output_format,
            target_kbps,
            max_size_mb,
            normalize_on,
        )

    with tempfile.TemporaryDirectory(prefix="mfaren_audio_mix_") as tmpdir:
        _assert_not_canceled(cancel_event)
        extract_dir = os.path.join(tmpdir, "extract")
        prep_dir = os.path.join(tmpdir, "prepared")
        os.makedirs(extract_dir, exist_ok=True)
        os.makedirs(prep_dir, exist_ok=True)

        sources = _extract_selected_audio(
            input_path,
            extract_dir,
            options,
            cancel_event=cancel_event,
            progress_cb=progress_cb,
            started_at=started_at,
            total_audio_seconds=None,
            runtime_state=runtime_state,
        )
        _assert_not_canceled(cancel_event)

        source_durations = [float(tio.ffprobe_duration(src) or 1.0) for src in sources]
        total_audio_seconds = max(source_durations) if source_durations else 1.0
        prepare_total_seconds = max(1.0, float(sum(source_durations)))
        prepared_seconds_done = 0.0

        prepared = []
        total_sources = len(sources)
        for idx, src in enumerate(sources, start=1):
            _assert_not_canceled(cancel_event)
            dst = os.path.join(prep_dir, f"track_{idx:04d}.wav")
            source_duration = source_durations[idx - 1] if idx - 1 < len(source_durations) else 1.0

            def _prep_progress(payload):
                local = float(payload.get("percent") or 0.0)
                stage_percent = ((prepared_seconds_done + (source_duration * (local / 100.0))) / prepare_total_seconds) * 100.0
                _emit_stage(
                    progress_cb,
                    2,
                    4,
                    "Preparação",
                    stage_percent,
                    speed=payload.get("speed"),
                    eta_seconds=payload.get("eta_seconds"),
                    size=payload.get("downloaded_bytes"),
                    started_at=started_at,
                    total_audio_seconds=total_audio_seconds,
                    runtime_state=runtime_state,
                )

            texec.run_ffmpeg_stage(
                _build_prepare_cmd_with_normalize(ffmpeg, src, dst, normalize_on),
                source_duration,
                "Preparacao",
                progress_cb=_prep_progress,
                cancel_event=cancel_event,
                pid_cb=pid_cb,
                logger=logger,
            )
            prepared.append(dst)
            prepared_seconds_done += source_duration

        _assert_not_canceled(cancel_event)
        mixed_wav = os.path.join(tmpdir, "mixed.wav")
        mix_duration_ref = max(source_durations) if source_durations else 1.0

        def _mix_progress(payload):
            local = float(payload.get("percent") or 0.0)
            _emit_stage(
                progress_cb,
                3,
                4,
                "Mixagem",
                local,
                speed=payload.get("speed"),
                eta_seconds=payload.get("eta_seconds"),
                size=payload.get("downloaded_bytes"),
                started_at=started_at,
                total_audio_seconds=total_audio_seconds,
                runtime_state=runtime_state,
            )

        texec.run_ffmpeg_stage(
            _build_mix_cmd(ffmpeg, prepared, mixed_wav),
            mix_duration_ref,
            "Mixagem",
            progress_cb=_mix_progress,
            cancel_event=cancel_event,
            pid_cb=pid_cb,
            logger=logger,
        )

        _assert_not_canceled(cancel_event)
        mixed_duration = tio.ffprobe_duration(mixed_wav) or mix_duration_ref or 1.0

        used_kbps = int(target_kbps)
        _encode_once(
            ffmpeg,
            mixed_wav,
            final_output,
            output_format,
            used_kbps,
            mixed_duration,
            progress_cb,
            cancel_event,
            pid_cb,
            logger,
            started_at=started_at,
            total_audio_seconds=total_audio_seconds,
            runtime_state=runtime_state,
        )

        size_bytes = int(os.path.getsize(final_output)) if os.path.isfile(final_output) else 0

        if output_format == "wav":
            if size_bytes > max_bytes:
                raise RuntimeError(
                    f"Arquivo WAV final ficou acima do limite ({size_bytes / (1024 * 1024):.1f}MB > {max_size_mb}MB). "
                    "Use m4a ou mp3 para auto-ajuste de tamanho."
                )
        elif size_bytes > max_bytes:
            estimated = _estimate_bitrate_for_limit(max_bytes, mixed_duration, headroom=0.95)
            attempts = [estimated, int(estimated * 0.92), int(estimated * 0.84), 24]
            normalized_attempts = []
            for value in attempts:
                v = _to_int(value, default=24, min_value=24, max_value=min(used_kbps, 512))
                if v not in normalized_attempts:
                    normalized_attempts.append(v)

            resized = False
            for retry_kbps in normalized_attempts:
                if retry_kbps >= used_kbps:
                    continue
                if logger:
                    logger.warning(
                        "audio_mix_autofit retry_bitrate=%sk current_size=%.1fMB max=%.1fMB",
                        retry_kbps,
                        size_bytes / (1024 * 1024),
                        float(max_size_mb),
                    )
                _encode_once(
                    ffmpeg,
                    mixed_wav,
                    final_output,
                    output_format,
                    retry_kbps,
                    mixed_duration,
                    progress_cb,
                    cancel_event,
                    pid_cb,
                    logger,
                    started_at=started_at,
                    total_audio_seconds=total_audio_seconds,
                    runtime_state=runtime_state,
                )
                size_bytes = int(os.path.getsize(final_output)) if os.path.isfile(final_output) else 0
                used_kbps = retry_kbps
                if size_bytes <= max_bytes:
                    resized = True
                    break

            if not resized and size_bytes > max_bytes:
                raise RuntimeError(
                    f"Nao foi possivel caber no limite de {max_size_mb}MB. Tamanho final: {size_bytes / (1024 * 1024):.1f}MB."
                )

        metadata = {
            "source_archive": os.path.abspath(input_path),
            "source_tracks": [os.path.basename(p) for p in sources],
            "tracks_count": len(sources),
            "output_path": os.path.abspath(final_output),
            "output_format": output_format,
            "duration_seconds": float(mixed_duration),
            "bitrate_kbps": int(used_kbps) if output_format != "wav" else None,
            "size_bytes": int(size_bytes),
            "size_limit_bytes": int(max_bytes),
            "size_limit_mb": int(max_size_mb),
            "timestamp": timestamp,
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    _emit_stage(
        progress_cb,
        4,
        4,
        "Exportação",
        100.0,
        size=size_bytes,
        eta_seconds=0,
        started_at=started_at,
        total_audio_seconds=total_audio_seconds,
        runtime_state=runtime_state,
    )

    if logger:
        logger.info(
            "audio_mix_done output=%s format=%s bitrate=%s size_mb=%.1f",
            final_output,
            output_format,
            used_kbps if output_format != "wav" else "wav",
            size_bytes / (1024 * 1024),
        )

    title = os.path.splitext(os.path.basename(input_path))[0] or "sessao"
    return final_output, {"title": title, "channel": "nao informado"}
