import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import json
import threading
import queue as py_queue
from datetime import datetime
from typing import Final

from . import transcribe_backends as tback
from . import transcribe_cache as tcache
from . import transcribe_chunk_checkpoint as tcheck
from . import transcribe_chunking as tchunk
from . import transcribe_exec as texec
from . import transcribe_glossary as tgloss
from . import transcribe_io as tio
from . import transcribe_postprocess as tpost
from . import transcribe_recovery as trecov
from .ffmpeg import find_ffmpeg
from .settings import get_settings
from .util import sanitize_filename

TRANSCRICOES_DIR: Final[str] = str(tio.TRANSCRICOES_DIR)
CONVERTIDOS_DIR: Final[str] = str(tio.CONVERTIDOS_DIR)


def list_models():
    return tio.list_models()


def pick_default_model(models):
    return tio.pick_default_model(models)


def get_live_path(job_id, output_dir=None):
    return tio.get_live_path(job_id, output_dir)


def _is_duration_compatible(candidate_duration, reference_duration, min_ratio, max_ratio):
    try:
        cand = float(candidate_duration or 0.0)
        ref = float(reference_duration or 0.0)
    except Exception:
        return False
    if cand <= 0.0 or ref <= 0.0:
        return False
    ratio = cand / ref
    return min_ratio <= ratio <= max_ratio


def _can_reuse_cached_wav(path, reference_duration, min_ratio, max_ratio, logger=None, stage=""):
    if not path or not os.path.isfile(path):
        return False
    cand_duration = tio.ffprobe_duration(path)
    ok = _is_duration_compatible(cand_duration, reference_duration, min_ratio, max_ratio)
    if not ok and logger:
        logger.warning(
            "cache_skip stage=%s reason=duration_mismatch file=%s cand=%.3fs ref=%.3fs",
            stage,
            path,
            float(cand_duration or 0.0),
            float(reference_duration or 0.0),
        )
    return ok


def _runtime_glossary_text(options, logger=None):
    fallback = options.get("transcribe_glossary")
    try:
        data = get_settings("transcribe")
        if isinstance(data, dict) and "transcribe_glossary" in data:
            return data.get("transcribe_glossary") or ""
    except Exception:
        if logger:
            logger.warning("glossary_runtime_reload_failed", exc_info=True)
    return fallback or ""


def _build_runtime_glossary_loader(options, logger=None):
    snapshot = dict(options or {})

    def _loader():
        return _runtime_glossary_text(snapshot, logger=logger)

    return _loader


def _glossary_runtime_context(glossary_raw):
    rules = tgloss.parse_glossary(glossary_raw)
    known_terms = tgloss.parse_known_terms(glossary_raw, glossary_rules=rules)
    guidance_prompt = tgloss.build_guidance_prompt(known_terms)
    return rules, guidance_prompt


def _compose_prompt(manual_prompt, guidance_prompt, max_chars=None):
    parts = []
    manual = str(manual_prompt or "").strip()
    guidance = str(guidance_prompt or "").strip()
    if manual:
        parts.append(manual)
    if guidance:
        parts.append(guidance)
    text = "\n".join(parts).strip()
    if not text:
        return ""
    if max_chars is None:
        return text
    limit = int(max(80, max_chars))
    if len(text) <= limit:
        return text
    sliced = text[:limit].rstrip()
    if "," in sliced:
        prefix, _ = sliced.rsplit(",", 1)
        if len(prefix.strip()) >= 48:
            sliced = prefix.strip()
    return sliced.strip()


def _merge_archive_transcripts(results, input_path, output_dir, timestamp, options=None, logger=None):
    options = options or {}
    merge_parts = []
    collected = []
    for item in results:
        srt_for_key = item["srt_path"]
        if os.path.isfile(srt_for_key):
            merge_parts.append(
                f"{os.path.basename(srt_for_key)}:{os.path.getsize(srt_for_key)}:{int(os.path.getmtime(srt_for_key))}"
            )
        speaker = tpost.speaker_from_media_path(item["media_path"])
        for start, end, text in tpost.parse_srt_segments(item["srt_path"], logger=logger):
            collected.append(
                {
                    "start": float(start),
                    "end": float(end),
                    "speaker": speaker,
                    "text": text,
                }
            )

    collected.sort(key=lambda x: (x["start"], x["end"]))
    if not collected:
        raise RuntimeError("Nao foi possivel montar merge: sem segmentos validos")

    merge_key = tcache.sha256_text("merge|" + "|".join(sorted(merge_parts)))

    merged = []
    for seg in collected:
        if not merged:
            merged.append(seg)
            continue
        prev = merged[-1]
        gap = seg["start"] - prev["end"]
        can_join = (
            seg["speaker"] == prev["speaker"]
            and gap <= 0.4
            and len(prev["text"]) + 1 + len(seg["text"]) <= 300
            and (seg["end"] - prev["start"]) <= 30.0
        )
        if can_join:
            prev["text"] = f"{prev['text']} {seg['text']}".strip()
            prev["end"] = max(prev["end"], seg["end"])
        else:
            merged.append(seg)

    bundle_base = sanitize_filename(os.path.splitext(os.path.basename(input_path))[0] or "pacote")
    stage_merge_dir = tio.project_stage_dir(options, "merge")
    if stage_merge_dir:
        txt_path = os.path.join(stage_merge_dir, f"{bundle_base}_{timestamp}_merged.txt")
        srt_path = os.path.join(stage_merge_dir, f"{bundle_base}_{timestamp}_merged.srt")
    else:
        txt_path = os.path.join(output_dir, f"{bundle_base}_{timestamp}_merged.txt")
        srt_path = os.path.join(output_dir, f"{bundle_base}_{timestamp}_merged.srt")

    tpost.write_merged_outputs(merged, txt_path, srt_path, timestamp)
    tcache.cache_put("merge", merge_key, {"txt": txt_path, "srt": srt_path}, meta={"input": os.path.basename(input_path)})

    if os.path.normcase(os.path.abspath(os.path.dirname(txt_path))) != os.path.normcase(os.path.abspath(output_dir)):
        final_txt = os.path.join(output_dir, os.path.basename(txt_path))
        final_srt = os.path.join(output_dir, os.path.basename(srt_path))
        tpost.rotate_old_if_exists(final_txt)
        tpost.rotate_old_if_exists(final_srt)
        shutil.copy2(txt_path, final_txt)
        shutil.copy2(srt_path, final_srt)
        return final_txt, final_srt

    return txt_path, srt_path


class _OffsetLiveWriter:
    def __init__(self, base_writer, offset_seconds):
        self.base_writer = base_writer
        self.offset_seconds = float(offset_seconds)

    def set_model(self, model_tag):
        if self.base_writer:
            self.base_writer.set_model(model_tag)

    def handle_segment(self, start, end, text):
        if self.base_writer:
            self.base_writer.handle_segment(start + self.offset_seconds, end + self.offset_seconds, text)

    def finalize(self):
        return


def _model_tag_for_output(model, backend):
    text = str(model or "").strip()
    if backend == "whisper_cpp":
        text = os.path.basename(text).replace("ggml-", "").replace(".bin", "")
    text = sanitize_filename(text.replace("/", "_").replace("\\", "_"))
    return text or backend


def _write_segments_to_srt(segments, srt_path):
    tpost.rotate_old_if_exists(srt_path)
    with open(srt_path, "w", encoding="utf-8") as srt:
        for idx, (start, end, text) in enumerate(segments, start=1):
            srt.write(f"{idx}\n")
            srt.write(f"{tpost.format_srt_timestamp(start)} --> {tpost.format_srt_timestamp(end)}\n")
            srt.write(f"{text}\n\n")


def _write_segments_to_json(segments, json_path):
    tpost.rotate_old_if_exists(json_path)
    payload = {
        "segments": [
            {"start": float(start), "end": float(end), "text": str(text or "")}
            for start, end, text in list(segments or [])
        ]
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _ensure_json_from_srt(srt_path, json_path, logger=None):
    try:
        segments = tpost.parse_srt_segments(srt_path, logger=logger)
        if not segments:
            return False
        _write_segments_to_json(segments, json_path)
        return True
    except Exception:
        if logger:
            logger.warning("json_sidecar_failed srt=%s json=%s", srt_path, json_path, exc_info=True)
        return False


def _run_whisperx_cli_transcribe(
    wav_path,
    output_dir,
    model,
    options,
    duration,
    progress_cb=None,
    cancel_event=None,
    pid_cb=None,
    report_cb=None,
    logger=None,
):
    language = str(options.get("language") or "").strip().lower()
    device = str(options.get("transcribe_device") or "cuda").strip() or "cuda"
    compute_type = str(options.get("transcribe_compute_type") or "float16").strip() or "float16"
    batch_size = str(tback.effective_whisperx_batch_size(options, logger=logger, resolved_device=device))
    guidance_prompt = str(options.get("transcribe_guidance_prompt") or "").strip()
    do_align = _parse_bool(options.get("whisperx_align"), default=True)
    diarize_on = _parse_bool(options.get("diarize"), default=False)

    cmd = [
        sys.executable,
        "-m",
        "whisperx",
        wav_path,
        "--model",
        str(model),
        "--device",
        device,
        "--compute_type",
        compute_type,
        "--batch_size",
        batch_size,
        "--output_dir",
        output_dir,
        "--output_format",
        "all",
        "--print_progress",
        "True",
    ]
    if language and language != "auto":
        cmd.extend(["--language", language])
    if guidance_prompt:
        cmd.extend(["--initial_prompt", guidance_prompt])
    if not do_align:
        cmd.append("--no_align")
    if diarize_on:
        cmd.append("--diarize")

    env = os.environ.copy()
    ffmpeg_for_cli = find_ffmpeg()
    if ffmpeg_for_cli:
        ffmpeg_dir = os.path.dirname(str(ffmpeg_for_cli))
        current_path = str(env.get("PATH") or "")
        lower_parts = [p.strip().lower() for p in current_path.split(os.pathsep) if p.strip()]
        if ffmpeg_dir and ffmpeg_dir.lower() not in lower_parts:
            env["PATH"] = f"{ffmpeg_dir}{os.pathsep}{current_path}" if current_path else ffmpeg_dir
        env["FFMPEG_BINARY"] = str(ffmpeg_for_cli)

    popen_kwargs = tio.get_popen_windows_kwargs()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            **popen_kwargs,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Falha ao iniciar WhisperX CLI: executavel externo ausente (normalmente ffmpeg no PATH)."
        ) from exc
    if pid_cb:
        pid_cb(proc.pid)
    if logger:
        logger.info("stage_start engine=whisperx_cli stage=Transcricao pid=%s cmd=%s", proc.pid, tio.summarize_cmd(cmd))
    if report_cb:
        report_cb("Transcricao", 0.0)

    stall_timeout, hard_timeout = tio.stage_timeouts("whisper", duration)
    started_at = time.time()
    last_progress_at = started_at
    last_output_at = started_at
    last_heartbeat_at = started_at
    timed_out_reason = None
    percent_re = re.compile(r"(\d+(?:\.\d+)?)%")
    last_percent = 0.0
    tail_lines = []
    q = py_queue.Queue()
    expected_rtf = 1.2
    try:
        expected_rtf = float(options.get("whisperx_cli_rtf_hint") or 1.2)
    except Exception:
        expected_rtf = 1.2
    expected_rtf = max(0.5, min(8.0, expected_rtf))

    def _reader():
        if not proc.stdout:
            q.put(None)
            return
        try:
            for line in proc.stdout:
                q.put(line)
        finally:
            q.put(None)

    threading.Thread(target=_reader, daemon=True).start()

    try:
        while True:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                break
            try:
                line = q.get(timeout=0.5)
            except py_queue.Empty:
                now = time.time()
                if now - started_at > hard_timeout:
                    timed_out_reason = f"timeout total ({int(hard_timeout)}s)"
                    proc.terminate()
                    break
                last_activity = max(last_progress_at, last_output_at)
                if now - last_activity > stall_timeout:
                    timed_out_reason = f"timeout sem progresso ({int(stall_timeout)}s)"
                    proc.terminate()
                    break
                if progress_cb and (now - last_heartbeat_at >= tio.HEARTBEAT_SECONDS):
                    elapsed = max(0.0, now - started_at)
                    total_audio = max(1.0, float(duration or 0.0))
                    estimated_total = max(1.0, total_audio * expected_rtf)
                    synthetic_percent = min(97.0, (elapsed / estimated_total) * 95.0)
                    displayed_percent = max(last_percent, synthetic_percent)
                    current_audio = max(0.0, min(total_audio, (displayed_percent / 100.0) * total_audio))
                    remaining_audio = max(0.0, total_audio - current_audio)
                    rate = (current_audio / elapsed) if elapsed > 0.0 else 0.0
                    eta_seconds = (remaining_audio / rate) if rate > 0.0 else None
                    progress_cb(
                        {
                            "percent": displayed_percent,
                            "speed": None,
                            "eta_seconds": eta_seconds,
                            "downloaded_bytes": None,
                            "total_bytes": None,
                            "current_audio_seconds": current_audio,
                            "total_audio_seconds": total_audio,
                            "remaining_audio_seconds": remaining_audio,
                            "elapsed_seconds": elapsed,
                            "transcribe_metrics": {
                                "elapsed_seconds": elapsed,
                                "current_audio_seconds": current_audio,
                                "remaining_audio_seconds": remaining_audio,
                                "total_audio_seconds": total_audio,
                                "eta_seconds": eta_seconds,
                            },
                            "message": f"Etapa 4/5: Transcricao ({displayed_percent:.1f}%) - WhisperX CLI",
                        }
                    )
                    last_heartbeat_at = now
                continue
            if line is None:
                break
            last_output_at = time.time()

            raw = str(line or "").strip()
            if raw:
                tail_lines.append(raw)
                if len(tail_lines) > 24:
                    tail_lines = tail_lines[-24:]

            pct = None
            for m in percent_re.finditer(raw):
                try:
                    v = float(m.group(1))
                except Exception:
                    continue
                if 0.0 <= v <= 100.0:
                    pct = v
            if pct is None:
                continue

            last_percent = max(last_percent, min(99.5, pct))
            last_progress_at = time.time()
            if report_cb:
                report_cb("Transcricao", last_percent)
            if progress_cb:
                elapsed = max(0.0, time.time() - started_at)
                total_audio = max(1.0, float(duration or 0.0))
                current_audio = max(0.0, min(total_audio, (last_percent / 100.0) * total_audio))
                remaining_audio = max(0.0, total_audio - current_audio)
                rate = (current_audio / elapsed) if elapsed > 0.0 else 0.0
                eta_seconds = (remaining_audio / rate) if rate > 0.0 else None
                progress_cb(
                    {
                        "percent": last_percent,
                        "speed": None,
                        "eta_seconds": eta_seconds,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "current_audio_seconds": current_audio,
                        "total_audio_seconds": total_audio,
                        "remaining_audio_seconds": remaining_audio,
                        "elapsed_seconds": elapsed,
                        "transcribe_metrics": {
                            "elapsed_seconds": elapsed,
                            "current_audio_seconds": current_audio,
                            "remaining_audio_seconds": remaining_audio,
                            "total_audio_seconds": total_audio,
                            "eta_seconds": eta_seconds,
                        },
                        "message": f"Etapa 4/5: Transcricao ({last_percent:.1f}%) - WhisperX CLI",
                    }
                )
    finally:
        if proc.stdout:
            proc.stdout.close()

    rc = proc.wait()
    elapsed = time.time() - started_at
    if logger:
        logger.info("stage_end engine=whisperx_cli stage=Transcricao pid=%s rc=%s elapsed=%.1fs", proc.pid, rc, elapsed)
    if timed_out_reason:
        raise RuntimeError(f"Falha na transcricao CLI ({timed_out_reason})")
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("Cancelado")
    if rc != 0:
        detail = " | ".join(tail_lines[-8:]) if tail_lines else "sem detalhe"
        low = detail.lower()
        if "filenotfounderror" in low or "[winerror 2]" in low:
            raise RuntimeError(
                f"Falha na transcricao CLI (rc={rc}): arquivo/executavel nao encontrado no ambiente. "
                f"Valide ffmpeg no PATH da sessao do Flask. detalhe={detail}"
            )
        raise RuntimeError(f"Falha na transcricao CLI (rc={rc} | {detail})")

    wav_base = os.path.splitext(os.path.basename(wav_path))[0]
    def _pick_generated(ext):
        direct = os.path.join(output_dir, f"{wav_base}.{ext}")
        if os.path.isfile(direct):
            return direct
        candidates = []
        try:
            for name in os.listdir(output_dir):
                low = name.lower()
                if not low.endswith(f".{ext}"):
                    continue
                if not low.startswith(wav_base.lower()):
                    continue
                full = os.path.join(output_dir, name)
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    mtime = 0.0
                candidates.append((mtime, full))
        except Exception:
            return ""
        if not candidates:
            return ""
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    generated = {
        "srt": _pick_generated("srt"),
        "txt": _pick_generated("txt"),
        "json": _pick_generated("json"),
    }
    if not os.path.isfile(generated["srt"]):
        raise RuntimeError("WhisperX CLI nao gerou arquivo .srt")
    if report_cb:
        report_cb("Transcricao", 100.0)
    if progress_cb:
        progress_cb(
            {
                "percent": 100.0,
                "speed": None,
                "eta_seconds": 0.0,
                "downloaded_bytes": None,
                "total_bytes": None,
                "message": "Etapa 4/5: Transcricao (100.0%) - WhisperX CLI",
            }
        )
    return generated


def _parse_chunk_index(value):
    if value in (None, ""):
        return None
    try:
        idx = int(str(value).strip())
    except Exception:
        return None
    return idx if idx >= 0 else None


def _parse_bool(value, default=False):
    if value in (None, ""):
        return bool(default)
    text = str(value).strip().lower()
    if text in ("1", "true", "on", "yes", "sim", "s"):
        return True
    if text in ("0", "false", "off", "no", "nao", "n"):
        return False
    return bool(default)


def _parse_int(value, default_value, min_value, max_value):
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = int(default_value)
    return max(int(min_value), min(int(max_value), parsed))


def _is_loop_chunk_error(exc):
    text = str(exc or "").strip().lower()
    return "loop detectado" in text


def _chunk_retry_patch(base_options, level):
    beam = _parse_int(base_options.get("beam_size"), default_value=5, min_value=1, max_value=10)
    patch = {}
    if level <= 1:
        patch["beam_size"] = str(max(1, min(beam, 3)))
        patch["transcribe_repetition_penalty"] = "1.10"
        patch["transcribe_no_repeat_ngram_size"] = "3"
        patch["transcribe_temperature"] = "0.0"
        return patch
    if level == 2:
        patch["beam_size"] = str(max(1, min(beam, 2)))
        patch["transcribe_repetition_penalty"] = "1.20"
        patch["transcribe_no_repeat_ngram_size"] = "4"
        patch["transcribe_temperature"] = "0.0"
        patch["transcribe_guidance_prompt"] = ""
        return patch
    patch["beam_size"] = "1"
    patch["transcribe_repetition_penalty"] = "1.35"
    patch["transcribe_no_repeat_ngram_size"] = "5"
    patch["transcribe_temperature"] = "0.0"
    patch["transcribe_guidance_prompt"] = ""
    return patch


_LIVE_SEGMENT_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*-->\s*(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s+.+?[—-]\s*(.+)$"
)


def _parse_clock_seconds(value):
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hh = float(parts[0])
        mm = float(parts[1])
        ss = float(parts[2])
    except Exception:
        return None
    return (hh * 3600.0) + (mm * 60.0) + ss


def _load_live_segments(live_path):
    if not live_path or not os.path.isfile(live_path):
        return []
    out = []
    with open(live_path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = str(raw or "").strip()
            if not line.startswith("["):
                continue
            m = _LIVE_SEGMENT_RE.match(line)
            if not m:
                continue
            start = _parse_clock_seconds(m.group(1))
            end = _parse_clock_seconds(m.group(2))
            text = str(m.group(3) or "").strip()
            if start is None or end is None or end <= start or not text:
                continue
            out.append((float(start), float(end), text))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def _recover_seed_chunks_from_live(live_path, chunks_plan, upto_index, logger=None):
    limit = _parse_chunk_index(upto_index)
    if limit is None or limit <= 0:
        return []
    segments = _load_live_segments(live_path)
    if not segments:
        return []

    plan = list(chunks_plan or [])
    if not plan:
        return []
    limit = min(int(limit), len(plan))

    by_idx = {}
    for idx in range(limit):
        chunk = plan[idx]
        chunk_start = float(chunk.get("start") or 0.0)
        non_start = float(chunk.get("non_overlap_start") or 0.0)
        if idx + 1 < len(plan):
            non_end = float(plan[idx + 1].get("non_overlap_start") or float("inf"))
        else:
            non_end = float("inf")
        collected = []
        for abs_start, abs_end, text in segments:
            if abs_end <= non_start or abs_start >= non_end:
                continue
            trim_start = max(abs_start, non_start)
            trim_end = min(abs_end, non_end)
            if trim_end <= trim_start:
                continue
            rel_start = max(0.0, trim_start - chunk_start)
            rel_end = max(rel_start + 0.01, trim_end - chunk_start)
            collected.append((rel_start, rel_end, text))
        if collected:
            by_idx[idx] = collected

    recovered = []
    for idx in sorted(by_idx.keys()):
        chunk = plan[idx]
        recovered.append(
            {
                "index": idx,
                "start": float(chunk.get("start") or 0.0),
                "non_overlap_start": float(chunk.get("non_overlap_start") or 0.0),
                "segments": by_idx[idx],
            }
        )
    if logger and recovered:
        logger.info(
            "chunk_resume_seed_from_live path=%s recovered_chunks=%s target_upto=%s",
            live_path,
            len(recovered),
            limit,
        )
    return recovered


def _run_chunked_backend(
    wav_path,
    output_base,
    model,
    backend,
    options,
    duration,
    report_stage,
    progress_cb=None,
    cancel_event=None,
    pid_cb=None,
    live_writer=None,
    checkpoint_path=None,
    single_chunk_index=None,
    logger=None,
):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg nao encontrado para transcricao em chunks")

    chunk_seconds, overlap_seconds = tchunk.parse_chunk_config(options)
    chunks_plan = tchunk.build_chunks(duration, chunk_seconds, overlap_seconds)
    if not chunks_plan:
        raise RuntimeError("Duracao invalida para transcricao")
    chunks_count = len(chunks_plan)
    run_single_idx = _parse_chunk_index(single_chunk_index)
    if run_single_idx is not None and not (0 <= run_single_idx < chunks_count):
        raise RuntimeError(f"Chunk invalido para refazer: {run_single_idx + 1} (total: {chunks_count})")
    resume_done_chunks = _parse_bool(options.get("transcribe_resume_chunks"), default=False)
    resume_from_chunk_idx = _parse_chunk_index(options.get("transcribe_resume_from_chunk_index"))
    resume_live_path = str(options.get("resume_live_path") or "").strip()
    max_loop_incidents = _parse_int(options.get("chunk_loop_max_incidents"), default_value=3, min_value=0, max_value=3)

    if logger:
        logger.info(
            "chunk_plan backend=%s chunks=%s chunk=%.1fs overlap=%.1fs duration=%.1fs single_chunk=%s checkpoint=%s",
            backend,
            len(chunks_plan),
            chunk_seconds,
            overlap_seconds,
            float(duration or 0.0),
            run_single_idx + 1 if run_single_idx is not None else "-",
            checkpoint_path or "-",
        )

    transcribe_started_at = time.time()

    checkpoint = None
    done_idx_set = set()
    seed_chunks_out = []
    if checkpoint_path:
        cp_existing = tcheck.load_checkpoint(checkpoint_path)
        keep_done = bool(run_single_idx is not None or resume_done_chunks)
        if run_single_idx is not None and not cp_existing:
            raise RuntimeError("Checkpoint de chunks nao encontrado para refazer chunk")
        if resume_done_chunks and not cp_existing and logger:
            logger.info("chunk_resume_checkpoint_missing path=%s -> iniciando sem retomada", checkpoint_path)
        checkpoint = tcheck.normalize_checkpoint(
            cp_existing,
            {
                "backend": backend,
                "model": str(model or ""),
                "wav_path": str(wav_path or ""),
                "chunk_seconds": float(chunk_seconds),
                "chunk_overlap_seconds": float(overlap_seconds),
                "duration": float(duration or 0.0),
            },
            chunks_plan,
            keep_done=keep_done,
        )
        for item in list(checkpoint.get("chunks") or []):
            try:
                item_idx = int(item.get("index"))
            except Exception:
                continue
            if str(item.get("status") or "").strip().lower() == "done":
                done_idx_set.add(item_idx)
        if (
            run_single_idx is None
            and resume_done_chunks
            and not cp_existing
            and resume_from_chunk_idx is not None
            and resume_from_chunk_idx > 0
        ):
            live_seed_path = resume_live_path
            if not live_seed_path:
                job_id = options.get("job_id")
                output_dir = options.get("output_dir") or options.get("transcribe_output_dir")
                if job_id and output_dir:
                    live_seed_path = get_live_path(job_id, output_dir)
            seed_chunks_out = _recover_seed_chunks_from_live(live_seed_path, chunks_plan, resume_from_chunk_idx, logger=logger)
            if seed_chunks_out:
                for seed_item in seed_chunks_out:
                    done_idx_set.add(int(seed_item["index"]))
                    tcheck.mark_chunk_done(checkpoint, int(seed_item["index"]), seed_item["segments"])
                if logger:
                    logger.info(
                        "chunk_resume_from_log applied resume_from=%s seeded_done=%s",
                        resume_from_chunk_idx + 1,
                        len(seed_chunks_out),
                    )
            elif logger:
                logger.warning(
                    "chunk_resume_from_log unavailable resume_from=%s live_path=%s -> retomada parcial desativada",
                    resume_from_chunk_idx + 1,
                    live_seed_path or "-",
                )
        tcheck.save_checkpoint(checkpoint_path, checkpoint)

    chunks_out = list(seed_chunks_out or [])
    chunk_rtf_ema = None
    with tempfile.TemporaryDirectory(prefix="mfaren_chunks_") as tmpdir:
        for chunk in chunks_plan:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Cancelado")

            idx = chunk["index"]
            count = chunk["count"]
            if run_single_idx is not None and idx != run_single_idx:
                continue
            if run_single_idx is None and resume_done_chunks and idx in done_idx_set:
                if logger:
                    logger.info("chunk_skip_resume index=%s/%s status=done", idx + 1, count)
                report_stage("Transcricao", ((idx + 1) / max(1, count)) * 100.0)
                continue
            start = chunk["start"]
            length = chunk["duration"]
            chunk_wav = os.path.join(tmpdir, f"chunk_{idx:04d}.wav")
            chunk_base = os.path.join(tmpdir, f"chunk_{idx:04d}")
            if logger:
                logger.info(
                    "chunk_start index=%s/%s start=%.3f duration=%.3f",
                    idx + 1,
                    count,
                    float(start),
                    float(length),
                )
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{length:.3f}",
                "-i",
                wav_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                chunk_wav,
            ]
            texec.run_ffmpeg_stage(
                cmd,
                length,
                f"Chunk {idx + 1}/{count}",
                progress_cb=None,
                cancel_event=cancel_event,
                pid_cb=pid_cb,
                report_cb=None,
                logger=logger,
            )

            offset_live = _OffsetLiveWriter(live_writer, start)

            def _chunk_report(_, stage_percent):
                chunk_progress = (idx + (float(stage_percent) / 100.0)) / max(1, count) * 100.0
                report_stage("Transcricao", chunk_progress)

            backend_progress_state = {"ts": time.time(), "best_local_percent": 0.0}

            def _chunk_progress(payload):
                if not progress_cb:
                    return
                out = dict(payload or {})
                synthetic = bool(out.pop("_synthetic", False))
                p = out.get("percent")
                chunk_progress = None
                if isinstance(p, (int, float)):
                    local_percent = max(0.0, min(100.0, float(p)))
                    if synthetic:
                        local_percent = max(float(backend_progress_state["best_local_percent"]), local_percent)
                    else:
                        backend_progress_state["ts"] = time.time()
                        backend_progress_state["best_local_percent"] = max(
                            float(backend_progress_state["best_local_percent"]),
                            local_percent,
                        )
                    chunk_progress = (idx + (local_percent / 100.0)) / max(1, count) * 100.0
                    out["percent"] = chunk_progress
                p_out = out.get("percent")
                if not isinstance(p_out, (int, float)):
                    p_out = 0.0
                    out["percent"] = p_out
                if isinstance(chunk_progress, (int, float)):
                    elapsed = max(0.0, time.time() - transcribe_started_at)
                    total_audio = max(1.0, float(duration or 0.0))
                    current_audio = max(0.0, min(total_audio, (float(chunk_progress) / 100.0) * total_audio))
                    remaining_audio = max(0.0, total_audio - current_audio)
                    rate = (current_audio / elapsed) if elapsed > 0.0 else 0.0
                    eta_seconds = (remaining_audio / rate) if rate > 0.0 else None
                    out["eta_seconds"] = eta_seconds
                    out["transcribe_metrics"] = {
                        "elapsed_seconds": elapsed,
                        "current_audio_seconds": current_audio,
                        "remaining_audio_seconds": remaining_audio,
                        "total_audio_seconds": total_audio,
                        "eta_seconds": eta_seconds,
                    }
                if synthetic:
                    out["message"] = (
                        f"Etapa 4/5: Transcricao ({float(p_out):.1f}%) - "
                        f"chunk {idx + 1}/{count} (estimando andamento...)"
                    )
                else:
                    out["message"] = f"Etapa 4/5: Transcricao ({float(p_out):.1f}%) - chunk {idx + 1}/{count}"
                progress_cb(out)
            chunk_segments = None
            loop_incidents = 0
            attempt = 1
            while True:
                attempt_options = dict(options)
                if loop_incidents > 0:
                    patch = _chunk_retry_patch(options, loop_incidents)
                    attempt_options.update(patch)
                    if logger:
                        logger.warning(
                            "chunk_retry_apply index=%s/%s loop_incident=%s/%s patch=%s",
                            idx + 1,
                            count,
                            loop_incidents,
                            max_loop_incidents,
                            patch,
                        )
                chunk_started_at = time.time()
                heartbeat_stop = threading.Event()
                heartbeat_thread = None
                if backend == "whisperx":
                    rtf_hint = float(chunk_rtf_ema) if isinstance(chunk_rtf_ema, (int, float)) and chunk_rtf_ema > 0.0 else 5.0

                    def _heartbeat():
                        while not heartbeat_stop.wait(2.5):
                            if cancel_event and cancel_event.is_set():
                                return
                            if (time.time() - float(backend_progress_state["ts"])) < 6.0:
                                continue
                            elapsed_chunk = max(0.0, time.time() - chunk_started_at)
                            est_total = max(20.0, float(length) * max(1.0, rtf_hint))
                            local_percent = min(97.0, (elapsed_chunk / est_total) * 95.0)
                            _chunk_progress({"percent": local_percent, "_synthetic": True})

                    _chunk_progress({"percent": 0.1, "_synthetic": True})
                    heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
                    heartbeat_thread.start()
                try:
                    chunk_segments = tback.transcribe_chunk(
                        backend,
                        chunk_wav,
                        chunk_base,
                        model,
                        attempt_options,
                        max(1.0, float(length)),
                        progress_cb=_chunk_progress,
                        cancel_event=cancel_event,
                        pid_cb=pid_cb,
                        report_cb=_chunk_report,
                        live_writer=offset_live,
                        logger=logger,
                    )
                    chunk_elapsed = max(0.001, time.time() - chunk_started_at)
                    chunk_rtf = chunk_elapsed / max(1.0, float(length))
                    if chunk_rtf_ema is None:
                        chunk_rtf_ema = chunk_rtf
                    else:
                        chunk_rtf_ema = (chunk_rtf_ema * 0.7) + (chunk_rtf * 0.3)
                    if logger:
                        logger.info(
                            "chunk_runtime index=%s/%s elapsed=%.1fs audio=%.1fs rtf=%.3f ema=%.3f",
                            idx + 1,
                            count,
                            chunk_elapsed,
                            float(length),
                            chunk_rtf,
                            float(chunk_rtf_ema),
                        )
                    if loop_incidents > 0 and logger:
                        logger.info(
                            "chunk_retry_recovered index=%s/%s incidents=%s resumed_original_config=next_chunks",
                            idx + 1,
                            count,
                            loop_incidents,
                        )
                    break
                except Exception as exc:
                    if trecov.is_cancel_exception(exc, cancel_event=cancel_event):
                        raise
                    is_loop = _is_loop_chunk_error(exc)
                    err_text = str(exc)
                    if checkpoint:
                        tcheck.mark_chunk_failed(
                            checkpoint,
                            idx,
                            f"{err_text} | attempt={attempt} loop_incidents={loop_incidents}",
                        )
                        tcheck.save_checkpoint(checkpoint_path, checkpoint)
                    if logger:
                        logger.error(
                            "chunk_failed index=%s/%s attempt=%s loop=%s error=%s",
                            idx + 1,
                            count,
                            attempt,
                            is_loop,
                            err_text,
                        )
                    if not is_loop:
                        raise RuntimeError(f"Falha no chunk {idx + 1}/{count}: {exc}") from exc
                    loop_incidents += 1
                    if loop_incidents > max_loop_incidents:
                        raise RuntimeError(
                            f"Falha no chunk {idx + 1}/{count}: loop detectado apos {max_loop_incidents} incidencias"
                        ) from exc
                    attempt += 1
                    continue
                finally:
                    heartbeat_stop.set()
                    if heartbeat_thread is not None:
                        heartbeat_thread.join(timeout=1.0)
            if checkpoint:
                tcheck.mark_chunk_done(checkpoint, idx, chunk_segments)
                tcheck.save_checkpoint(checkpoint_path, checkpoint)
            chunks_out.append(
                {
                    "index": idx,
                    "start": start,
                    "non_overlap_start": chunk["non_overlap_start"],
                    "segments": chunk_segments,
                }
            )
            if logger:
                logger.info("chunk_done index=%s/%s segments=%s", idx + 1, count, len(chunk_segments or []))

    if checkpoint:
        chunks_out = tcheck.collect_done_chunks(checkpoint)
        missing = tcheck.missing_chunk_indexes(checkpoint)
        if missing:
            missing_human = ", ".join([str(i + 1) for i in missing[:12]])
            if len(missing) > 12:
                missing_human = f"{missing_human}, ..."
            raise RuntimeError(f"Nao foi possivel remontar SRT final: faltam chunks ({missing_human})")

    merged_segments = tchunk.merge_chunk_segments(chunks_out, duration, logger=logger)
    if not merged_segments:
        raise RuntimeError("Transcricao em chunks nao gerou segmentos")
    _write_segments_to_srt(merged_segments, f"{output_base}.srt")
    report_stage("Transcricao", 100.0)
    return merged_segments


def _transcribe_single_media(
    input_path,
    options,
    model,
    models,
    output_dir,
    live_writer,
    glossary_loader=None,
    progress_cb=None,
    cancel_event=None,
    pid_cb=None,
    logger=None,
):
    if logger:
        logger.info("transcribe_file_start input=%s", input_path)

    base_name = sanitize_filename(os.path.splitext(os.path.basename(input_path))[0] or "arquivo")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    redo_from = (options.get("redo_from") or "").strip().lower()

    wav_raw = tio.project_stage_file(options, "convert", base_name, timestamp, "wav") or os.path.join(
        CONVERTIDOS_DIR, f"{base_name}_{timestamp}.wav"
    )
    wav_norm = tio.project_stage_file(options, "normalize", f"{base_name}_norm", timestamp, "wav") or os.path.join(
        CONVERTIDOS_DIR, f"{base_name}_{timestamp}_norm.wav"
    )
    wav_vad = tio.project_stage_file(options, "vad", f"{base_name}_vad", timestamp, "wav") or os.path.join(
        CONVERTIDOS_DIR, f"{base_name}_{timestamp}_vad.wav"
    )

    duration_src = tio.ffprobe_duration(input_path) or 1.0
    input_fp = tcache.sha256_file(input_path)
    convert_key = tcache.sha256_text(f"convert|{input_fp}")

    normalize_on = str(options.get("normalize", "on")).lower() in ("1", "true", "on", "yes", "sim", "s")
    vad_on = str(options.get("vad", "on")).lower() in ("1", "true", "on", "yes", "sim", "s")
    diarize_on = str(options.get("diarize") or "off").lower() in ("1", "true", "on", "yes", "sim", "s")
    speaker_name = tpost.speaker_from_media_path(input_path)
    glossary_raw = glossary_loader() if glossary_loader else options.get("transcribe_glossary")
    glossary_rules, guidance_prompt = _glossary_runtime_context(glossary_raw)
    if live_writer:
        live_writer.set_speaker_name(speaker_name)
        live_writer.set_glossary_rules(glossary_rules)
        if glossary_loader:
            live_writer.set_glossary_loader(glossary_loader)

    stages = [
        ("Conversao para WAV", 0.1),
        ("Normalizacao", 0.1),
        ("Executando VAD", 0.1),
        ("Transcricao", 0.65),
        ("Juncao", 0.05),
    ]
    total_weight = sum(w for _, w in stages)
    stage_index = {label: idx + 1 for idx, (label, _) in enumerate(stages)}
    stage_total = len(stages)

    def report_stage(stage_label, stage_percent, skipped=False, recovered=False):
        running = 0.0
        for label, weight in stages:
            if label == stage_label:
                running += (stage_percent / 100.0) * weight
                break
            running += weight
        overall = (running / total_weight) * 100.0
        if progress_cb:
            idx = stage_index.get(stage_label, 1)
            if skipped:
                msg = f"Etapa {idx}/{stage_total}: {stage_label} (pulado)"
            elif recovered:
                msg = f"Etapa {idx}/{stage_total}: {stage_label} (arquivo recuperado)"
            else:
                msg = f"Etapa {idx}/{stage_total}: {stage_label} ({stage_percent:.1f}%)"
            progress_cb(
                {
                    "percent": overall,
                    "speed": None,
                    "eta_seconds": None,
                    "downloaded_bytes": None,
                    "total_bytes": None,
                    "message": msg,
                }
            )

    if redo_from in ("convert", "normalize", "vad", "transcribe", "merge"):
        reuse_raw = options.get("reuse_wav_raw")
        if reuse_raw and _can_reuse_cached_wav(reuse_raw, duration_src, 0.80, 1.20, logger=logger, stage="convert-resume"):
            wav_raw = reuse_raw
            report_stage("Conversao para WAV", 100.0, recovered=True)
        else:
            redo_from = ""
    elif not redo_from:
        cached_convert = tcache.cache_get("convert", convert_key)
        cached_wav = (cached_convert or {}).get("files", {}).get("wav") if cached_convert else None
        if cached_wav and _can_reuse_cached_wav(cached_wav, duration_src, 0.80, 1.20, logger=logger, stage="convert") and tcache.materialize_cached_file(cached_wav, wav_raw):
            if logger:
                logger.info("cache_hit stage=convert input=%s src=%s dst=%s", input_path, cached_wav, wav_raw)
            report_stage("Conversao para WAV", 100.0, recovered=True)

    if not redo_from and not tio.verify_wav_file(wav_raw):
        texec.convert_to_wav(input_path, wav_raw, duration_src, progress_cb, cancel_event, pid_cb, report_stage, logger=logger)
        if not tio.verify_wav_file(wav_raw):
            raise RuntimeError("Arquivo WAV corrompido ou vazio")
        tcache.cache_put("convert", convert_key, {"wav": wav_raw}, meta={"input_fp": input_fp})

    duration_wav = tio.ffprobe_duration(wav_raw) or duration_src
    current_wav = wav_raw

    norm_key = tcache.sha256_text(f"normalize|{convert_key}")
    if redo_from in ("normalize", "vad", "transcribe", "merge"):
        reuse_norm = options.get("reuse_wav_norm")
        if normalize_on and reuse_norm and _can_reuse_cached_wav(reuse_norm, duration_wav, 0.80, 1.20, logger=logger, stage="normalize-resume"):
            current_wav = reuse_norm
            report_stage("Normalizacao", 100.0, recovered=True)
        elif normalize_on:
            texec.normalize_audio(current_wav, wav_norm, duration_wav, progress_cb, cancel_event, pid_cb, report_stage, logger=logger)
            current_wav = wav_norm
            tcache.cache_put("normalize", norm_key, {"wav": current_wav}, meta={"convert_key": convert_key})
        else:
            report_stage("Normalizacao", 100.0, skipped=True)
    else:
        if normalize_on:
            cached_norm = tcache.cache_get("normalize", norm_key)
            cached_norm_wav = (cached_norm or {}).get("files", {}).get("wav") if cached_norm else None
            if cached_norm_wav and _can_reuse_cached_wav(cached_norm_wav, duration_wav, 0.80, 1.20, logger=logger, stage="normalize") and tcache.materialize_cached_file(cached_norm_wav, wav_norm):
                if logger:
                    logger.info("cache_hit stage=normalize input=%s src=%s dst=%s", input_path, cached_norm_wav, wav_norm)
                current_wav = wav_norm
                report_stage("Normalizacao", 100.0, recovered=True)
            else:
                texec.normalize_audio(current_wav, wav_norm, duration_wav, progress_cb, cancel_event, pid_cb, report_stage, logger=logger)
                current_wav = wav_norm
                tcache.cache_put("normalize", norm_key, {"wav": current_wav}, meta={"convert_key": convert_key})
        else:
            report_stage("Normalizacao", 100.0, skipped=True)

    vad_source_key = norm_key if normalize_on else convert_key
    vad_threshold = str(options.get("vad_threshold", "-30"))
    vad_min_silence = str(options.get("vad_min_silence", "0.3"))
    vad_key = tcache.sha256_text(f"vad|{vad_source_key}|{vad_threshold}|{vad_min_silence}")
    if redo_from in ("vad", "transcribe", "merge"):
        reuse_vad = options.get("reuse_wav_vad")
        if vad_on and reuse_vad and _can_reuse_cached_wav(reuse_vad, duration_wav, 0.05, 1.20, logger=logger, stage="vad-resume"):
            current_wav = reuse_vad
            report_stage("Executando VAD", 100.0, recovered=True)
        elif vad_on:
            texec.apply_vad(current_wav, wav_vad, duration_wav, options, progress_cb, cancel_event, pid_cb, report_stage, logger=logger)
            current_wav = wav_vad
            tcache.cache_put("vad", vad_key, {"wav": current_wav}, meta={"source_key": vad_source_key})
        else:
            report_stage("Executando VAD", 100.0, skipped=True)
    else:
        if vad_on:
            cached_vad = tcache.cache_get("vad", vad_key)
            cached_vad_wav = (cached_vad or {}).get("files", {}).get("wav") if cached_vad else None
            if cached_vad_wav and _can_reuse_cached_wav(cached_vad_wav, duration_wav, 0.05, 1.20, logger=logger, stage="vad") and tcache.materialize_cached_file(cached_vad_wav, wav_vad):
                if logger:
                    logger.info("cache_hit stage=vad input=%s src=%s dst=%s", input_path, cached_vad_wav, wav_vad)
                current_wav = wav_vad
                report_stage("Executando VAD", 100.0, recovered=True)
            else:
                texec.apply_vad(current_wav, wav_vad, duration_wav, options, progress_cb, cancel_event, pid_cb, report_stage, logger=logger)
                current_wav = wav_vad
                tcache.cache_put("vad", vad_key, {"wav": current_wav}, meta={"source_key": vad_source_key})
        else:
            report_stage("Executando VAD", 100.0, skipped=True)

    backend = tback.normalize_backend_name(options.get("transcribe_backend_resolved") or options.get("transcribe_backend"))
    compare_all = bool(options.get("compare_all"))
    if compare_all and backend != "whisper_cpp":
        compare_all = False
        if logger:
            logger.warning("compare_all desativado para backend=%s (suportado apenas em whisper_cpp)", backend)

    transcribe_duration = tio.ffprobe_duration(current_wav) or duration_wav or duration_src or 1.0
    source_key = vad_key if vad_on else (norm_key if normalize_on else convert_key)
    chunk_seconds, overlap_seconds = tchunk.parse_chunk_config(options)
    redo_chunk_index = _parse_chunk_index(options.get("redo_chunk_index"))
    guided_mode = str(options.get("transcribe_guided_mode") or "").strip().lower()

    def _transcribe_one_model(model_input):
        resolved_model = tback.resolve_model(model_input, backend, models_cpp=models)
        model_tag = _model_tag_for_output(resolved_model, backend)
        if live_writer:
            live_writer.set_model(model_tag)
        glossary_raw_current = glossary_loader() if glossary_loader else glossary_raw
        glossary_rules_current, guidance_prompt_current = _glossary_runtime_context(glossary_raw_current)
        if live_writer:
            live_writer.set_glossary_rules(glossary_rules_current)
        runtime_options = dict(options)
        effective_chunk_seconds = float(chunk_seconds)
        effective_overlap_seconds = float(overlap_seconds)
        # Mantemos chunking tambem no preset rapido para checkpoint/anti-loop.
        runtime_options["chunk_seconds"] = str(effective_chunk_seconds)
        runtime_options["chunk_overlap_seconds"] = str(effective_overlap_seconds)
        manual_prompt = str(options.get("transcribe_initial_prompt") or "").strip()
        if guided_mode == "whisperx_cli_puro":
            runtime_options["transcribe_guidance_prompt"] = manual_prompt
        elif manual_prompt and guidance_prompt_current:
            runtime_options["transcribe_guidance_prompt"] = f"{manual_prompt}\n{guidance_prompt_current}"
        elif manual_prompt:
            runtime_options["transcribe_guidance_prompt"] = manual_prompt
        else:
            runtime_options["transcribe_guidance_prompt"] = guidance_prompt_current
        output_base = tio.project_stage_file(options, "transcribe", f"{base_name}_{model_tag}", timestamp, "") or os.path.join(
            output_dir, f"{base_name}_{timestamp}_{model_tag}"
        )
        output_base_dir = os.path.dirname(output_base) or output_dir
        os.makedirs(output_base_dir, exist_ok=True)
        txt_candidate = f"{output_base}.txt"
        srt_candidate = f"{output_base}.srt"
        json_candidate = f"{output_base}.json"
        output_json_on = _parse_bool(options.get("transcribe_output_json"), default=True)
        key_material = [
            "transcribe",
            source_key,
            f"backend={backend}",
            f"model={resolved_model}",
            f"language={options.get('language')}",
            f"device={runtime_options.get('transcribe_device')}",
            f"compute={runtime_options.get('transcribe_compute_type')}",
            f"whisperx_batch={runtime_options.get('whisperx_batch_size')}",
            f"diarize={options.get('diarize')}",
            f"beam={options.get('beam_size')}",
            f"max_len={options.get('max_len')}",
            f"chunk={effective_chunk_seconds}",
            f"overlap={effective_overlap_seconds}",
            f"guided={guided_mode or 'manual'}",
            f"glossary={tcache.sha256_text(str(glossary_raw_current or ''))}",
            f"prompt={tcache.sha256_text(str(runtime_options.get('transcribe_guidance_prompt') or ''))}",
        ]
        transcribe_key = tcache.sha256_text("|".join(key_material))
        checkpoint_path = str(runtime_options.get("redo_chunk_checkpoint") or "").strip()
        if not checkpoint_path:
            checkpoint_dir = tio.project_stage_dir(options, "transcribe") or os.path.dirname(output_base) or output_dir
            checkpoint_path = tcheck.default_checkpoint_path(checkpoint_dir, base_name, model_tag, transcribe_key)

        can_use_cache = redo_from not in ("transcribe", "merge")
        if can_use_cache:
            cached = tcache.cache_get("transcribe", transcribe_key)
            cached_txt = (cached or {}).get("files", {}).get("txt") if cached else None
            cached_srt = (cached or {}).get("files", {}).get("srt") if cached else None
            cached_json = (cached or {}).get("files", {}).get("json") if cached else None
            hit_txt = cached_txt and tcache.materialize_cached_file(cached_txt, txt_candidate)
            hit_srt = cached_srt and tcache.materialize_cached_file(cached_srt, srt_candidate)
            hit_json = True
            if output_json_on:
                if cached_json:
                    hit_json = bool(tcache.materialize_cached_file(cached_json, json_candidate))
                else:
                    hit_json = _ensure_json_from_srt(srt_candidate, json_candidate, logger=logger)
            if hit_txt and hit_srt and hit_json:
                report_stage("Transcricao", 100.0, recovered=True)
                if logger:
                    logger.info("cache_hit stage=transcribe input=%s backend=%s model=%s", input_path, backend, resolved_model)
                return txt_candidate, srt_candidate

        use_cli_puro = (
            backend == "whisperx"
            and guided_mode == "whisperx_cli_puro"
            and redo_chunk_index is None
        )
        if use_cli_puro:
            cli_outputs = _run_whisperx_cli_transcribe(
                current_wav,
                output_base_dir,
                resolved_model,
                runtime_options,
                transcribe_duration,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                pid_cb=pid_cb,
                report_cb=report_stage,
                logger=logger,
            )
            generated_srt = str(cli_outputs.get("srt") or "").strip()
            if not generated_srt or not os.path.isfile(generated_srt):
                raise RuntimeError("WhisperX CLI nao gerou .srt utilizavel")
            if os.path.normcase(os.path.abspath(generated_srt)) != os.path.normcase(os.path.abspath(srt_candidate)):
                tpost.rotate_old_if_exists(srt_candidate)
                shutil.copy2(generated_srt, srt_candidate)
            generated_json = str(cli_outputs.get("json") or "").strip()
            if output_json_on and generated_json and os.path.isfile(generated_json):
                if os.path.normcase(os.path.abspath(generated_json)) != os.path.normcase(os.path.abspath(json_candidate)):
                    tpost.rotate_old_if_exists(json_candidate)
                    shutil.copy2(generated_json, json_candidate)
        else:
            _run_chunked_backend(
                current_wav,
                output_base,
                resolved_model,
                backend,
                runtime_options,
                transcribe_duration,
                report_stage,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                pid_cb=pid_cb,
                live_writer=live_writer,
                checkpoint_path=checkpoint_path,
                single_chunk_index=redo_chunk_index,
                logger=logger,
            )
        if live_writer:
            live_writer.refresh_glossary(force=True)
            glossary_rules = live_writer.get_glossary_rules()
        else:
            glossary_rules = tgloss.parse_glossary(glossary_raw_current)
        tpost.postprocess_srt_to_txt(
            srt_candidate,
            txt_candidate,
            diarize_on,
            model_tag,
            timestamp,
            logger=logger,
            speaker_name=speaker_name,
            glossary_rules=glossary_rules,
        )
        if output_json_on:
            _ensure_json_from_srt(srt_candidate, json_candidate, logger=logger)
        files_to_cache = {"txt": txt_candidate, "srt": srt_candidate}
        if output_json_on and os.path.isfile(json_candidate):
            files_to_cache["json"] = json_candidate
        tcache.cache_put(
            "transcribe",
            transcribe_key,
            files_to_cache,
            meta={"source_key": source_key, "backend": backend, "model": resolved_model},
        )
        return txt_candidate, srt_candidate

    if compare_all:
        models_all = list_models()
        if not models_all:
            raise RuntimeError("Nenhum modelo .bin encontrado")
        produced = []
        for idx, mod in enumerate(models_all, start=1):
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Cancelado")
            txt_candidate, srt_candidate = _transcribe_one_model(mod)
            produced.append((mod, txt_candidate, srt_candidate))
            if progress_cb:
                pct = (idx / len(models_all)) * 100.0
                progress_cb(
                    {
                        "percent": pct,
                        "speed": None,
                        "eta_seconds": None,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "message": f"Modelo concluido: {os.path.basename(mod)}",
                    }
                )
        _, txt_path, srt_path = produced[-1]
    else:
        txt_path, srt_path = _transcribe_one_model(model)

    report_stage("Juncao", 0.0)
    merge_dir = tio.project_stage_dir(options, "merge")
    if merge_dir:
        stage_txt = os.path.join(merge_dir, os.path.basename(txt_path))
        stage_srt = os.path.join(merge_dir, os.path.basename(srt_path))
        shutil.copy2(txt_path, stage_txt)
        shutil.copy2(srt_path, stage_srt)
    report_stage("Juncao", 100.0)

    if os.path.normcase(os.path.abspath(os.path.dirname(txt_path))) != os.path.normcase(os.path.abspath(output_dir)):
        out_txt = os.path.join(output_dir, os.path.basename(txt_path))
        out_srt = os.path.join(output_dir, os.path.basename(srt_path))
        tpost.rotate_old_if_exists(out_txt)
        tpost.rotate_old_if_exists(out_srt)
        shutil.copy2(txt_path, out_txt)
        shutil.copy2(srt_path, out_srt)
        txt_path, srt_path = out_txt, out_srt

    if logger:
        logger.info("transcribe_file_done input=%s txt=%s srt=%s", input_path, txt_path, srt_path)
    return txt_path, srt_path


def transcribe_file(input_path, options, progress_cb=None, cancel_event=None, logger=None, pid_cb=None):
    os.makedirs(str(TRANSCRICOES_DIR), exist_ok=True)
    os.makedirs(str(CONVERTIDOS_DIR), exist_ok=True)

    if not tio.verify_ffmpeg():
        raise RuntimeError("FFmpeg nao encontrado")

    options = dict(options or {})
    options.setdefault("transcribe_backend", tback.DEFAULT_BACKEND)
    options.setdefault("chunk_seconds", str(tchunk.DEFAULT_CHUNK_SECONDS))
    options.setdefault("chunk_overlap_seconds", str(tchunk.DEFAULT_CHUNK_OVERLAP_SECONDS))
    options.setdefault("transcribe_device", "")
    options.setdefault("transcribe_compute_type", "")
    options.setdefault("whisperx_batch_size", "4")
    options.setdefault("transcribe_initial_prompt", "")
    options.setdefault("transcribe_output_json", "on")
    options.setdefault("transcribe_auto_recover", "on")
    options.setdefault("transcribe_auto_recover_retries", "1")
    options.setdefault("chunk_loop_max_incidents", "3")
    guided_mode = str(options.get("transcribe_guided_mode") or "").strip().lower()
    no_align_modes = {"whisperx_cuda_fast", "whisperx_cuda_balanced"}
    if guided_mode in no_align_modes:
        options["whisperx_align"] = "off"
    elif guided_mode == "whisperx_cli_puro":
        options["whisperx_align"] = "on"
    elif str(options.get("whisperx_align") or "").strip() == "":
        options["whisperx_align"] = "on"
    chunk_seconds, overlap_seconds = tchunk.parse_chunk_config(options)
    options["chunk_seconds"] = str(chunk_seconds)
    options["chunk_overlap_seconds"] = str(overlap_seconds)

    requested_backend = tback.normalize_backend_name(options.get("transcribe_backend"))
    resolved_backend, fallback_reason = tback.resolve_backend(requested_backend, logger=logger)
    options["transcribe_backend"] = requested_backend
    options["transcribe_backend_resolved"] = resolved_backend
    tback.verify_backend_environment(resolved_backend)
    requested_device = str(options.get("transcribe_device") or "").strip().lower()
    cuda_ok = bool(tback.cuda_available())
    runtime_device = "cpu"
    runtime_accel = "cpu"
    if resolved_backend in ("faster_whisper", "whisperx"):
        if requested_device == "cpu":
            runtime_device = "cpu"
            runtime_accel = "cpu"
        elif requested_device == "cuda":
            runtime_device = "cuda" if cuda_ok else "cpu"
            runtime_accel = "gpu_cuda" if cuda_ok else "gpu_no_cuda"
        else:
            runtime_device = "cuda" if cuda_ok else "cpu"
            runtime_accel = "gpu_cuda" if cuda_ok else "cpu"
    options["transcribe_runtime_device"] = runtime_device
    options["transcribe_runtime_accel"] = runtime_accel
    if fallback_reason and progress_cb:
        progress_cb(
            {
                "percent": 0.0,
                "speed": None,
                "eta_seconds": None,
                "downloaded_bytes": None,
                "total_bytes": None,
                "message": f"Backend ajustado: {fallback_reason}",
            }
        )

    models = list_models()
    model = options.get("model") or pick_default_model(models)
    if resolved_backend == "whisper_cpp" and not bool(options.get("compare_all")):
        model = tback.resolve_model(model, resolved_backend, models_cpp=models)

    profile = str(options.get("transcribe_profile") or "auto").strip().lower()
    if profile not in ("auto", "craig_multitrack", "single_channel"):
        profile = "auto"
    options["transcribe_profile"] = profile
    options.setdefault("normalize", "on")
    options.setdefault("vad", "off")
    options.setdefault("diarize", "on")
    if not options.get("beam_size"):
        options["beam_size"] = "5"

    diarize_on = str(options.get("diarize") or "off").lower() in ("1", "true", "on", "yes", "sim", "s")
    output_dir_value = options.get("transcribe_output_dir") or options.get("output_dir")
    output_dir: str = str(output_dir_value).strip() if output_dir_value is not None else str(TRANSCRICOES_DIR)
    if not output_dir:
        output_dir = str(TRANSCRICOES_DIR)
    os.makedirs(str(output_dir), exist_ok=True)
    job_id = options.get("job_id")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    live_path = get_live_path(job_id, output_dir) if job_id else os.path.join(output_dir, f"transcricao_{timestamp}_live.txt")

    cleanup_dir = None
    is_archive = False
    media_inputs = [input_path]

    if tio.is_archive_input(input_path):
        is_archive = True
        if profile in ("auto", "craig_multitrack"):
            options["normalize"] = "on"
            options["vad"] = "off"
            options["diarize"] = "off"
            diarize_on = False
        cleanup_dir = tio.extract_archive_to_temp(input_path)
        selected_rel = tio.resolve_archive_selected_relpaths(options, input_path)
        media_pairs = tio.collect_media_files_with_rel(cleanup_dir)
        if selected_rel:
            media_pairs = [p for p in media_pairs if p[0] in selected_rel]
        media_inputs = [full for _, full in media_pairs]
        if not media_inputs:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            if selected_rel:
                raise RuntimeError("Nenhum arquivo interno foi selecionado no ZIP")
            raise RuntimeError("ZIP sem arquivos de audio/video suportados")
    elif profile == "single_channel":
        # Mantem defaults para audio unico, sem sobrescrever escolhas explicitas do usuario/preset.
        if str(options.get("normalize") or "").strip() == "":
            options["normalize"] = "on"
        if str(options.get("vad") or "").strip() == "":
            options["vad"] = "off"
        if str(options.get("diarize") or "").strip() == "":
            options["diarize"] = "on"
        diarize_on = str(options.get("diarize") or "off").lower() in ("1", "true", "on", "yes", "sim", "s")

    live_writer = texec.LiveWriter(live_path, diarize_on, timestamp)
    glossary_loader = _build_runtime_glossary_loader(options, logger=logger)
    live_writer.set_glossary_loader(glossary_loader)

    results = []
    if logger:
        logger.info(
            "transcribe_job_start input=%s profile=%s backend=%s requested_backend=%s files=%s normalize=%s vad=%s diarize=%s chunk=%.1fs overlap=%.1fs output_dir=%s project_dir=%s",
            input_path,
            profile,
            options.get("transcribe_backend_resolved"),
            options.get("transcribe_backend"),
            len(media_inputs),
            options.get("normalize"),
            options.get("vad"),
            options.get("diarize"),
            float(options.get("chunk_seconds") or 0.0),
            float(options.get("chunk_overlap_seconds") or 0.0),
            output_dir,
            options.get("project_dir"),
        )

    try:
        total_files = len(media_inputs)
        archive_merge_mode = is_archive and total_files > 1
        redo_from = (options.get("redo_from") or "").strip().lower()

        if archive_merge_mode and redo_from == "merge":
            transcribe_dir = tio.project_stage_dir(options, "transcribe")
            if not transcribe_dir or not os.path.isdir(transcribe_dir):
                raise RuntimeError("Nao ha artefatos de transcricao para refazer juncao")
            for media_path in media_inputs:
                stem = sanitize_filename(os.path.splitext(os.path.basename(media_path))[0])
                candidates = []
                for name in os.listdir(transcribe_dir):
                    if not name.lower().endswith(".srt"):
                        continue
                    if stem.lower() not in name.lower():
                        continue
                    full = os.path.join(transcribe_dir, name)
                    candidates.append((os.path.getmtime(full), full))
                if not candidates:
                    raise RuntimeError(f"Sem SRT para refazer juncao: {os.path.basename(media_path)}")
                candidates.sort(key=lambda x: x[0], reverse=True)
                srt_path = candidates[0][1]
                txt_path = os.path.splitext(srt_path)[0] + ".txt"
                results.append({"txt_path": txt_path, "srt_path": srt_path, "media_path": media_path})
            if progress_cb:
                progress_cb(
                    {
                        "percent": 99.0,
                        "speed": None,
                        "eta_seconds": None,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "message": "Etapa 5/5: Juncao (0.0%)",
                    }
                )
            merged_txt, merged_srt = _merge_archive_transcripts(results, input_path, output_dir, timestamp, options=options, logger=logger)
            if progress_cb:
                progress_cb(
                    {
                        "percent": 100.0,
                        "speed": None,
                        "eta_seconds": 0,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "message": "Etapa 5/5: Juncao (100.0%)",
                    }
                )
            return merged_txt, merged_srt

        for index, media_path in enumerate(media_inputs, start=1):
            if cancel_event and cancel_event.is_set():
                break

            if progress_cb and total_files > 1:
                progress_cb(
                    {
                        "percent": ((index - 1) / total_files) * 100.0,
                        "speed": None,
                        "eta_seconds": None,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "message": f"Arquivo {index}/{total_files}: {os.path.basename(media_path)}",
                    }
                )

            def file_progress_cb(payload):
                if not progress_cb:
                    return
                file_name = os.path.basename(media_path)
                payload = dict(payload)
                payload["message"] = f"[{index}/{total_files}] {file_name} | {payload.get('message') or ''}".strip()
                if total_files > 1 and isinstance(payload.get("percent"), (int, float)):
                    payload["percent"] = (((index - 1) + (float(payload["percent"]) / 100.0)) / total_files) * 100.0
                progress_cb(payload)

            per_file_options = dict(options)
            if archive_merge_mode:
                per_file_options["diarize"] = "off"
                per_file_options["vad"] = "off"
            max_retries = trecov.parse_retry_limit(per_file_options, default=1, hard_max=3)
            attempt = 0
            while True:
                try:
                    txt_path, srt_path = _transcribe_single_media(
                        media_path,
                        per_file_options,
                        model,
                        models,
                        output_dir,
                        live_writer,
                        glossary_loader=glossary_loader,
                        progress_cb=file_progress_cb,
                        cancel_event=cancel_event,
                        pid_cb=pid_cb,
                        logger=logger,
                    )
                    break
                except Exception as exc:
                    if trecov.is_cancel_exception(exc, cancel_event=cancel_event):
                        raise
                    if attempt >= max_retries or not trecov.is_recoverable_failure(exc):
                        raise
                    attempt += 1
                    analysis = trecov.collect_incident_context(
                        media_path,
                        per_file_options,
                        getattr(live_writer, "path", None),
                        str(exc),
                    )
                    patch = trecov.build_retry_patch(per_file_options, media_path, analysis.get("reason"))
                    incident_path = trecov.write_incident_report(
                        per_file_options,
                        media_path,
                        analysis,
                        patch,
                        attempt_no=attempt,
                    )
                    per_file_options.update(patch)
                    note = (
                        f"=== AUTO-RECUPERACAO {attempt}/{max_retries} === "
                        f"motivo={analysis.get('reason')} top_ratio={analysis.get('repetition', {}).get('top_ratio', 0.0):.2f} "
                        f"incidente={incident_path}"
                    )
                    trecov.append_live_note(getattr(live_writer, "path", None), note)
                    if logger:
                        logger.warning(
                            "transcribe_recovery_trigger file=%s attempt=%s/%s reason=%s repeat=%s incident=%s patch=%s",
                            media_path,
                            attempt,
                            max_retries,
                            analysis.get("reason"),
                            analysis.get("repetition"),
                            incident_path,
                            patch,
                        )
                    if progress_cb:
                        progress_cb(
                            {
                                "percent": ((index - 1) / total_files) * 100.0 if total_files > 1 else 0.0,
                                "speed": None,
                                "eta_seconds": None,
                                "downloaded_bytes": None,
                                "total_bytes": None,
                                "message": f"[{index}/{total_files}] Auto-recuperacao {attempt}/{max_retries}: reexecutando transcricao",
                            }
                        )
            results.append({"txt_path": txt_path, "srt_path": srt_path, "media_path": media_path})

        if not results:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Cancelado")
            raise RuntimeError("Nenhuma faixa foi transcrita")

        if len(results) == 1:
            if logger:
                logger.info(
                    "transcribe_job_done input=%s files=1 txt=%s srt=%s",
                    input_path,
                    results[0]["txt_path"],
                    results[0]["srt_path"],
                )
            return results[0]["txt_path"], results[0]["srt_path"]

        if archive_merge_mode:
            if progress_cb:
                progress_cb(
                    {
                        "percent": 99.0,
                        "speed": None,
                        "eta_seconds": None,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "message": "Etapa 5/5: Juncao (0.0%)",
                    }
                )
            merged_txt, merged_srt = _merge_archive_transcripts(results, input_path, output_dir, timestamp, options=options, logger=logger)
            if progress_cb:
                progress_cb(
                    {
                        "percent": 100.0,
                        "speed": None,
                        "eta_seconds": 0,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "message": "Etapa 5/5: Juncao (100.0%)",
                    }
                )
            if logger:
                logger.info("transcribe_job_done input=%s files=%s txt=%s srt=%s", input_path, len(results), merged_txt, merged_srt)
            return merged_txt, merged_srt

        bundle_base = sanitize_filename(os.path.splitext(os.path.basename(input_path))[0] or "pacote")
        bundle_txt = os.path.join(output_dir, f"{bundle_base}_{timestamp}_bundle.txt")
        tpost.rotate_old_if_exists(bundle_txt)
        with open(bundle_txt, "w", encoding="utf-8") as f:
            f.write(f"Transcricao de pacote: {os.path.basename(input_path)}\n")
            f.write(f"Total de arquivos processados: {len(results)}\n\n")
            for idx, item in enumerate(results, start=1):
                f.write(f"{idx}. TXT: {item['txt_path']}\n")
                f.write(f"   SRT: {item['srt_path']}\n")
        if logger:
            logger.info("transcribe_job_done input=%s files=%s bundle=%s", input_path, len(results), bundle_txt)
        return bundle_txt, results[-1]["srt_path"]
    finally:
        live_writer.finalize()
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


