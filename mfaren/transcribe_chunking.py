import math

from . import transcribe_postprocess as tpost


DEFAULT_CHUNK_SECONDS = 300.0
DEFAULT_CHUNK_OVERLAP_SECONDS = 1.5
MIN_CHUNK_SECONDS = 30.0
MAX_CHUNK_SECONDS = 3600.0


def parse_duration_seconds(value):
    if value in (None, ""):
        raise ValueError("duracao vazia")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        raise ValueError("duracao vazia")
    if ":" not in text:
        return float(text)
    parts = [p.strip() for p in text.split(":")]
    if len(parts) not in (2, 3):
        raise ValueError("formato de duracao invalido")
    try:
        nums = [float(p) for p in parts]
    except Exception as exc:
        raise ValueError("formato de duracao invalido") from exc
    if len(nums) == 2:
        mm, ss = nums
        return (mm * 60.0) + ss
    hh, mm, ss = nums
    return (hh * 3600.0) + (mm * 60.0) + ss


def parse_chunk_config(options):
    options = options or {}
    try:
        chunk_seconds = parse_duration_seconds(options.get("chunk_seconds") or DEFAULT_CHUNK_SECONDS)
    except Exception:
        chunk_seconds = DEFAULT_CHUNK_SECONDS
    try:
        overlap_seconds = parse_duration_seconds(options.get("chunk_overlap_seconds") or DEFAULT_CHUNK_OVERLAP_SECONDS)
    except Exception:
        overlap_seconds = DEFAULT_CHUNK_OVERLAP_SECONDS

    chunk_seconds = max(MIN_CHUNK_SECONDS, min(MAX_CHUNK_SECONDS, chunk_seconds))
    overlap_seconds = max(0.0, overlap_seconds)
    overlap_limit = max(0.0, min(30.0, chunk_seconds * 0.45))
    overlap_seconds = min(overlap_seconds, overlap_limit)
    return chunk_seconds, overlap_seconds


def build_chunks(total_duration, chunk_seconds, overlap_seconds):
    total = max(0.0, float(total_duration or 0.0))
    if total <= 0.0:
        return []

    chunk_seconds = max(1.0, float(chunk_seconds or DEFAULT_CHUNK_SECONDS))
    overlap_seconds = max(0.0, float(overlap_seconds or 0.0))
    overlap_seconds = min(overlap_seconds, chunk_seconds * 0.45)

    if total <= chunk_seconds:
        return [
            {
                "index": 0,
                "count": 1,
                "start": 0.0,
                "duration": total,
                "non_overlap_start": 0.0,
            }
        ]

    chunks = []
    cursor = 0.0
    index = 0
    count = int(math.ceil(total / chunk_seconds))
    while cursor < total:
        start = cursor
        non_overlap_start = cursor
        if index > 0:
            start = max(0.0, cursor - overlap_seconds)
        end = min(total, cursor + chunk_seconds)
        duration = max(1.0, end - start)
        chunks.append(
            {
                "index": index,
                "count": count,
                "start": start,
                "duration": duration,
                "non_overlap_start": non_overlap_start,
            }
        )
        cursor += chunk_seconds
        index += 1
    return chunks


def merge_chunk_segments(chunks, source_duration, logger=None):
    merged = []
    max_ts = max(1.0, float(source_duration or 0.0)) * 1.20
    dropped_overlap = 0
    dropped_invalid = 0
    dropped_repeat = 0

    for chunk in chunks:
        start_offset = float(chunk.get("start") or 0.0)
        non_overlap_start = float(chunk.get("non_overlap_start") or 0.0)
        for seg in chunk.get("segments") or []:
            if len(seg) < 3:
                dropped_invalid += 1
                continue
            rel_start, rel_end, text = seg[0], seg[1], seg[2]
            try:
                abs_start = float(rel_start) + start_offset
                abs_end = float(rel_end) + start_offset
            except Exception:
                dropped_invalid += 1
                continue
            text = str(text or "").strip()
            if not text:
                dropped_invalid += 1
                continue
            if abs_end <= abs_start:
                dropped_invalid += 1
                continue
            if abs_end > max_ts:
                dropped_invalid += 1
                continue

            if non_overlap_start > 0.0:
                if abs_end <= non_overlap_start:
                    dropped_overlap += 1
                    continue
                if abs_start < non_overlap_start < abs_end:
                    abs_start = non_overlap_start

            if merged:
                prev_start, prev_end, prev_text = merged[-1]
                if abs_start <= prev_end:
                    same = tpost.normalize_for_repeat(text) == tpost.normalize_for_repeat(prev_text)
                    if same and abs_end <= (prev_end + 1.0):
                        dropped_repeat += 1
                        continue
                    abs_start = max(abs_start, prev_end + 0.01)
                    if abs_end <= abs_start:
                        dropped_invalid += 1
                        continue

            merged.append((abs_start, abs_end, text))

    cleaned = tpost.clean_repetitive_segments(merged, logger=logger)
    if logger:
        logger.info(
            "chunk_merge_summary kept=%s dropped_overlap=%s dropped_invalid=%s dropped_repeat=%s",
            len(cleaned),
            dropped_overlap,
            dropped_invalid,
            dropped_repeat,
        )
    return cleaned
