import json
import os
import re
from collections import deque
from datetime import datetime

from . import transcribe_io as tio
from . import transcribe_postprocess as tpost


def parse_bool(value, default=True):
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "on", "yes", "sim", "s"):
        return True
    if text in ("0", "false", "off", "no", "nao", "n"):
        return False
    return bool(default)


def parse_retry_limit(options, default=1, hard_max=3):
    opts = dict(options or {})
    if not parse_bool(opts.get("transcribe_auto_recover"), default=True):
        return 0
    raw = opts.get("transcribe_auto_recover_retries")
    if raw in (None, ""):
        return int(default)
    try:
        val = int(str(raw).strip())
    except Exception:
        val = int(default)
    return max(0, min(int(hard_max), val))


def is_cancel_exception(exc, cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        return True
    msg = str(exc or "").strip().lower()
    return msg == "cancelado" or msg.startswith("cancelado ")


def is_recoverable_failure(exc):
    msg = str(exc or "").strip().lower()
    if not msg:
        return False
    if "cancelado" in msg:
        return False
    keys = (
        "loop detectado",
        "timestamp fora da faixa",
        "nao gerou segmentos",
        "timeout sem progresso",
        "timeout total",
    )
    return any(k in msg for k in keys)


def _tail_lines(path, max_lines=180, max_chars=28000):
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
    except Exception:
        return []
    if len(data) > max_chars:
        data = data[-max_chars:]
    lines = data.splitlines()
    return lines[-max_lines:]


def _live_payload_text(line):
    text = str(line or "").strip()
    if not text:
        return ""
    if "—" in text:
        return text.split("—", 1)[1].strip()
    return text


def _extract_tail_fragments(live_tail, srt_tail, txt_tail):
    out = []

    for line in list(live_tail or []):
        payload = _live_payload_text(line)
        norm = tpost.normalize_for_repeat(payload)
        if norm:
            out.append(norm)

    for line in list(srt_tail or []):
        text = str(line or "").strip()
        if not text:
            continue
        if re.match(r"^\d+$", text):
            continue
        if "-->" in text:
            continue
        norm = tpost.normalize_for_repeat(text)
        if norm:
            out.append(norm)

    for line in list(txt_tail or []):
        text = str(line or "").strip()
        if not text:
            continue
        norm = tpost.normalize_for_repeat(text)
        if norm:
            out.append(norm)

    return out[-240:]


def detect_repetition(live_tail, srt_tail, txt_tail):
    fragments = _extract_tail_fragments(live_tail, srt_tail, txt_tail)
    if not fragments:
        return {
            "suspected": False,
            "total": 0,
            "unique": 0,
            "top_count": 0,
            "top_text": "",
            "top_ratio": 0.0,
        }

    counts = {}
    for item in fragments:
        counts[item] = counts.get(item, 0) + 1
    unique = len(counts)
    top_text, top_count = max(counts.items(), key=lambda p: p[1])
    total = len(fragments)
    ratio = float(top_count) / max(1, total)
    suspected = total >= 12 and unique <= 4 and ratio >= 0.45
    return {
        "suspected": suspected,
        "total": total,
        "unique": unique,
        "top_count": top_count,
        "top_text": top_text,
        "top_ratio": ratio,
    }


def _latest_stage_file(project_dir, stage_key, stem_hint, ext):
    project_dir_text = str(project_dir or "").strip()
    if not project_dir_text or not os.path.isdir(project_dir_text):
        return None
    stage_key_text = str(stage_key or "").strip()
    stage_name = tio.STAGE_DIR_NAMES.get(stage_key_text, stage_key_text)
    stage_name_text = str(stage_name or "").strip()
    if not stage_name_text:
        return None
    stage_dir = os.path.join(project_dir_text, stage_name_text)
    if not os.path.isdir(stage_dir):
        return None
    candidates = []
    needle = str(stem_hint or "").strip().lower()
    for name in os.listdir(stage_dir):
        if not name.lower().endswith(str(ext or "").lower()):
            continue
        if needle and needle not in name.lower():
            continue
        full = os.path.join(stage_dir, name)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        candidates.append((mtime, full))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p[0], reverse=True)
    return candidates[0][1]


def collect_incident_context(media_path, options, live_path, error_text):
    opts = dict(options or {})
    project_dir = opts.get("project_dir")
    stem_hint = os.path.splitext(os.path.basename(str(media_path or "")))[0]
    srt_path = _latest_stage_file(project_dir, "transcribe", stem_hint, ".srt")
    txt_path = _latest_stage_file(project_dir, "transcribe", stem_hint, ".txt")
    live_tail = _tail_lines(live_path, max_lines=160)
    srt_tail = _tail_lines(srt_path, max_lines=120)
    txt_tail = _tail_lines(txt_path, max_lines=120)
    log_tail = _tail_lines(os.path.join("logs", "app.log"), max_lines=120)
    repetition = detect_repetition(live_tail, srt_tail, txt_tail)
    msg = str(error_text or "").strip().lower()
    reason = "generic"
    if "loop detectado" in msg or repetition.get("suspected"):
        reason = "loop"
    elif "timestamp fora da faixa" in msg:
        reason = "timestamp"
    elif "nao gerou segmentos" in msg:
        reason = "empty_output"
    elif "timeout" in msg:
        reason = "timeout"
    return {
        "reason": reason,
        "error": str(error_text or ""),
        "repetition": repetition,
        "live_path": live_path,
        "srt_path": srt_path,
        "txt_path": txt_path,
        "live_tail": live_tail,
        "srt_tail": srt_tail,
        "txt_tail": txt_tail,
        "log_tail": log_tail[-60:],
    }


def _safe_float(value, default_value):
    try:
        return float(value)
    except Exception:
        return float(default_value)


def _safe_int(value, default_value):
    try:
        return int(str(value).strip())
    except Exception:
        return int(default_value)


def build_retry_patch(options, media_path, reason):
    opts = dict(options or {})
    patch = {"redo_from": "transcribe"}
    project_dir = opts.get("project_dir")
    stem_hint = os.path.splitext(os.path.basename(str(media_path or "")))[0]
    reuse_raw = _latest_stage_file(project_dir, "convert", stem_hint, ".wav")
    reuse_norm = _latest_stage_file(project_dir, "normalize", stem_hint, ".wav")
    reuse_vad = _latest_stage_file(project_dir, "vad", stem_hint, ".wav")
    patch["reuse_wav_raw"] = str(reuse_raw or "")
    patch["reuse_wav_norm"] = str(reuse_norm or "")
    patch["reuse_wav_vad"] = str(reuse_vad or "")

    chunk = _safe_float(opts.get("chunk_seconds", "600"), 600.0)
    overlap = _safe_float(opts.get("chunk_overlap_seconds", "1.5"), 1.5)
    beam = _safe_int(opts.get("beam_size", "5"), 5)
    max_len = _safe_int(opts.get("max_len", "42"), 42)

    patch["chunk_seconds"] = str(max(60.0, min(chunk, 180.0)))
    patch["chunk_overlap_seconds"] = str(max(overlap, 3.0))
    patch["beam_size"] = str(max(1, min(beam, 3)))
    patch["max_len"] = str(max(20, min(max_len, 32)))

    if reason == "timestamp":
        patch["chunk_seconds"] = str(max(60.0, min(chunk, 120.0)))
        patch["chunk_overlap_seconds"] = str(max(overlap, 4.0))

    patch["transcribe_recovery_reason"] = str(reason or "generic")
    return patch


def write_incident_report(options, media_path, analysis, retry_patch, attempt_no):
    opts = dict(options or {})
    base_dir = opts.get("project_dir") or opts.get("output_dir") or "logs"
    inc_dir = os.path.join(base_dir, "incidentes_transcricao")
    os.makedirs(inc_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    stem = os.path.splitext(os.path.basename(str(media_path or "arquivo")))[0]
    path = os.path.join(inc_dir, f"{stem}_{stamp}_tentativa_{int(attempt_no)}.json")
    payload = {
        "created_at": stamp,
        "media_path": media_path,
        "analysis": analysis,
        "retry_patch": retry_patch,
        "attempt_no": int(attempt_no),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def append_live_note(live_path, text):
    if not live_path:
        return
    try:
        os.makedirs(os.path.dirname(live_path), exist_ok=True)
        with open(live_path, "a", encoding="utf-8") as f:
            f.write(f"\n{text}\n")
    except Exception:
        return


def infer_chunk_from_app_log(media_path, log_path=None, max_lines=50000):
    target_name = os.path.basename(str(media_path or "")).strip().lower()
    if not target_name:
        return None
    path = str(log_path or os.path.join("logs", "app.log"))
    if not os.path.isfile(path):
        return None

    file_re = re.compile(r"transcribe_file_start input=(.+)$", flags=re.IGNORECASE)
    done_re = re.compile(r"chunk_done index=(\d+)\/(\d+)", flags=re.IGNORECASE)
    fail_re = re.compile(r"chunk_failed index=(\d+)\/(\d+)(?:\s+error=(.*))?", flags=re.IGNORECASE)
    start_re = re.compile(r"chunk_start index=(\d+)\/(\d+)", flags=re.IGNORECASE)

    lines = deque(maxlen=max(200, int(max_lines or 50000)))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                lines.append(line.rstrip("\n"))
    except Exception:
        return None

    current_target = False
    target_stats = {"last_done": None, "failed": None, "total": None, "error": "", "last_start": None}
    global_stats = {"last_done": None, "failed": None, "total": None, "error": "", "last_start": None}

    for line in lines:
        m_file = file_re.search(line)
        if m_file:
            raw = str(m_file.group(1) or "").strip().strip('"')
            current_target = os.path.basename(raw).strip().lower() == target_name
            continue

        m_done = done_re.search(line)
        if m_done:
            idx = max(0, int(m_done.group(1)) - 1)
            total = max(1, int(m_done.group(2)))
            global_stats["last_done"] = idx
            global_stats["total"] = total
            if current_target:
                target_stats["last_done"] = idx
                target_stats["total"] = total
            continue

        m_fail = fail_re.search(line)
        if m_fail:
            idx = max(0, int(m_fail.group(1)) - 1)
            total = max(1, int(m_fail.group(2)))
            err = str(m_fail.group(3) or "").strip()
            global_stats["failed"] = idx
            global_stats["total"] = total
            global_stats["error"] = err
            if current_target:
                target_stats["failed"] = idx
                target_stats["total"] = total
                target_stats["error"] = err
            continue

        m_start = start_re.search(line)
        if m_start:
            idx = max(0, int(m_start.group(1)) - 1)
            total = max(1, int(m_start.group(2)))
            global_stats["last_start"] = idx
            global_stats["total"] = total
            if current_target:
                target_stats["last_start"] = idx
                target_stats["total"] = total
            continue

    stats = target_stats if (
        target_stats["last_done"] is not None or target_stats["failed"] is not None or target_stats["last_start"] is not None
    ) else global_stats
    if stats["last_done"] is None and stats["failed"] is None and stats["last_start"] is None:
        return None

    total = stats["total"]
    if total is not None:
        total = int(total)
    suggested = None
    if stats["failed"] is not None:
        suggested = int(stats["failed"])
    elif stats["last_done"] is not None:
        suggested = int(stats["last_done"]) + 1
    elif stats["last_start"] is not None:
        suggested = int(stats["last_start"])
    if suggested is not None and total:
        suggested = max(0, min(int(total) - 1, int(suggested)))

    return {
        "source": "app.log",
        "target_name": target_name,
        "used_global_fallback": stats is global_stats,
        "total_chunks": total,
        "last_done_chunk_index": stats["last_done"],
        "failed_chunk_index": stats["failed"],
        "failed_error": stats["error"],
        "last_started_chunk_index": stats["last_start"],
        "suggested_chunk_index": suggested,
    }
