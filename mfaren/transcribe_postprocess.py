import os
import re
import time

from . import transcribe_glossary as tgloss


def parse_srt_timestamp(value):
    try:
        h, m, rest = value.split(":", 2)
        s, ms = rest.split(",", 1)
        return int(h) * 3600 + int(m) * 60 + float(f"{s}.{ms}")
    except Exception:
        return None


def format_srt_timestamp(sec):
    total_ms = int(round(max(0.0, sec) * 1000))
    h = total_ms // 3600000
    rem = total_ms % 3600000
    m = rem // 60000
    rem = rem % 60000
    s = rem // 1000
    ms = rem % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_time(sec):
    return time.strftime("%H:%M:%S", time.gmtime(max(0, sec)))


def rotate_old_if_exists(path):
    if not path or not os.path.exists(path):
        return
    base, ext = os.path.splitext(path)
    idx = 1
    while True:
        suffix = "_old" if idx == 1 else f"_old_{idx}"
        candidate = f"{base}{suffix}{ext}"
        if not os.path.exists(candidate):
            os.replace(path, candidate)
            return
        idx += 1


def normalize_for_repeat(text):
    base = re.sub(r"[^\w\s]", " ", str(text or "").lower(), flags=re.UNICODE)
    base = re.sub(r"\s+", " ", base).strip()
    return base


def is_internally_repetitive(text):
    norm = normalize_for_repeat(text)
    if len(norm) < 80:
        return False
    words = [w for w in norm.split(" ") if w]
    if len(words) < 12:
        return False
    uniq = len(set(words))
    if uniq <= 3:
        return True
    if (uniq / max(1, len(words))) < 0.35:
        return True
    # Detect chunk repetition inside one segment (e.g. same sentence repeated 4+ times).
    for n in range(4, min(12, max(4, len(words) // 2)) + 1):
        phrase = " ".join(words[:n])
        if not phrase:
            continue
        occurrences = norm.count(phrase)
        if occurrences >= 4:
            covered = (occurrences * n) / max(1, len(words))
            if covered >= 0.55:
                return True
    return False


def _trim_repetitive_tail(segments):
    if len(segments) < 24:
        return segments
    norms = [normalize_for_repeat(s[2]) for s in segments]
    max_window = min(160, len(segments))
    for window in range(max_window, 23, -1):
        tail_segments = segments[-window:]
        tail_norms = norms[-window:]
        counts = {}
        for n in tail_norms:
            if not n:
                continue
            counts[n] = counts.get(n, 0) + 1
        if len(counts) > 3:
            continue
        top = max(counts.values()) if counts else 0
        avg_dur = sum(max(0.0, float(e) - float(s)) for s, e, _ in tail_segments) / max(1, window)
        if top >= int(window * 0.45) and avg_dur <= 3.0:
            return segments[:-window]
    return segments


def clean_repetitive_segments(segments, logger=None):
    if not segments:
        return segments
    cleaned = []
    last_norm = ""
    repeat_count = 0
    alt_loop_count = 0
    dropped = 0
    for seg in segments:
        if len(seg) != 3:
            cleaned.append(seg)
            continue
        start, end, text = seg
        norm = normalize_for_repeat(text)
        if not norm:
            dropped += 1
            continue
        duration = max(0.0, float(end) - float(start))
        if is_internally_repetitive(text):
            dropped += 1
            continue
        words = [w for w in norm.split(" ") if w]
        short_phrase = len(words) <= 6 and len(norm) <= 64
        if norm == last_norm:
            repeat_count += 1
        else:
            repeat_count = 0
            last_norm = norm
        if len(cleaned) >= 3:
            a = normalize_for_repeat(cleaned[-3][2])
            b = normalize_for_repeat(cleaned[-2][2])
            c = normalize_for_repeat(cleaned[-1][2])
            # ABAB... oscillation
            if a and b and norm == b and c == a:
                alt_loop_count += 1
            else:
                alt_loop_count = 0
        if alt_loop_count >= 3:
            dropped += 1
            continue
        # Guardrail for long-loop hallucination: same short phrase repeated in
        # long consecutive segments (common in pathological chunk tails).
        if repeat_count >= 1 and short_phrase and duration >= 15.0:
            dropped += 1
            continue
        if repeat_count >= 2 and duration <= 20.0:
            dropped += 1
            continue
        cleaned.append((start, end, text))
    cleaned = _trim_repetitive_tail(cleaned)
    if logger and dropped:
        logger.warning("hallucination_filter dropped_segments=%s kept=%s", dropped, len(cleaned))
    return cleaned


def speaker_from_media_path(path):
    name = os.path.splitext(os.path.basename(path))[0]
    speaker = re.sub(r"^\d+[-_]*", "", name).strip(" _-")
    return speaker or name or "nao informado"


def parse_srt_segments(srt_path, logger=None):
    if not os.path.isfile(srt_path):
        return []
    with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
        lines = [line.rstrip("\n") for line in f]
    segments = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().isdigit():
            i += 1
            continue
        i += 1
        if i >= len(lines):
            break
        time_line = lines[i]
        i += 1
        if "-->" not in time_line:
            continue
        start_str, end_str = [p.strip() for p in time_line.split("-->", 1)]
        start = parse_srt_timestamp(start_str)
        end = parse_srt_timestamp(end_str)
        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        i += 1
        text = " ".join(text_lines).strip()
        if start is None or end is None or not text:
            continue
        segments.append((start, end, text))
    return clean_repetitive_segments(segments, logger=logger)


def write_merged_outputs(segments, txt_path, srt_path, timestamp):
    rotate_old_if_exists(srt_path)
    with open(srt_path, "w", encoding="utf-8") as srt:
        for idx, seg in enumerate(segments, start=1):
            srt.write(f"{idx}\n")
            srt.write(f"{format_srt_timestamp(seg['start'])} --> {format_srt_timestamp(seg['end'])}\n")
            srt.write(f"{seg['text']}\n\n")

    header = [
        f"=== INICIO DA TRANSCRICAO: {timestamp} ===",
        "Modelo: merged-craig | Diarizacao: True",
        "==========================================",
        "",
    ]
    rotate_old_if_exists(txt_path)
    with open(txt_path, "w", encoding="utf-8") as txt:
        txt.write("\n".join(header))
        for seg in segments:
            txt.write(f"[{format_time(seg['start'])} --> {format_time(seg['end'])}] {seg['speaker']} — {seg['text']}\n")


def postprocess_srt_to_txt(
    srt_path,
    txt_path,
    diarize_on,
    model_tag,
    timestamp,
    logger=None,
    speaker_name=None,
    glossary_rules=None,
):
    if not os.path.isfile(srt_path):
        return
    with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
        lines = [line.rstrip() for line in f]

    segments = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().isdigit():
            i += 1
            continue
        i += 1
        if i >= len(lines):
            break
        time_line = lines[i]
        i += 1
        if "-->" not in time_line:
            continue
        start_str, end_str = [p.strip() for p in time_line.split("-->")]
        start = parse_srt_timestamp(start_str)
        end = parse_srt_timestamp(end_str)
        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        i += 1
        text = " ".join(text_lines).strip()
        text = tgloss.apply_glossary(text, glossary_rules)
        if start is None or end is None or not text:
            continue
        segments.append((start, end, text))

    segments = clean_repetitive_segments(segments, logger=logger)
    # Keep .srt aligned with cleaned content to avoid keeping hallucinated tails.
    rotate_old_if_exists(srt_path)
    with open(srt_path, "w", encoding="utf-8") as srt:
        for idx, (start, end, text) in enumerate(segments, start=1):
            srt.write(f"{idx}\n")
            srt.write(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n")
            srt.write(f"{text}\n\n")

    speaker = 1
    fixed_speaker = str(speaker_name or "").strip() or "Falante 1"
    buffer = []
    chars = 0
    block_start = None
    last_end = 0.0
    lines_out = []

    for start, end, text in segments:
        if block_start is None:
            block_start = start
        gap = start - last_end
        if gap > 0.4 and buffer:
            label = f"Falante {speaker}" if diarize_on else fixed_speaker
            lines_out.append(f"[{format_time(block_start)} --> {format_time(last_end)}] {label} — {' '.join(buffer)}")
            speaker = 2 if speaker == 1 else 1
            buffer = []
            chars = 0
            block_start = start

        if buffer:
            chars += 1 + len(text)
        else:
            chars += len(text)
        buffer.append(text)

        duration = end - block_start
        if chars >= 300 or duration >= 30:
            label = f"Falante {speaker}" if diarize_on else fixed_speaker
            lines_out.append(f"[{format_time(block_start)} --> {format_time(end)}] {label} — {' '.join(buffer)}")
            speaker = 2 if speaker == 1 else 1
            buffer = []
            chars = 0
            block_start = end

        last_end = end

    if buffer:
        label = f"Falante {speaker}" if diarize_on else fixed_speaker
        lines_out.append(f"[{format_time(block_start)} --> {format_time(last_end)}] {label} — {' '.join(buffer)}")

    header = [
        f"=== INICIO DA TRANSCRICAO: {timestamp} ===",
        f"Modelo: {model_tag} | Diarizacao: {bool(diarize_on)}",
        "==========================================",
        "",
    ]
    rotate_old_if_exists(txt_path)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + lines_out))
