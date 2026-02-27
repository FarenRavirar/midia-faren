import json
import os
from datetime import datetime


CHECKPOINT_VERSION = 1


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_checkpoint_path(base_dir, base_name, model_tag, run_key):
    safe_base = str(base_name or "arquivo").strip() or "arquivo"
    safe_model = str(model_tag or "model").strip() or "model"
    safe_key = str(run_key or "")[:12] or "run"
    filename = f"{safe_base}_{safe_model}_{safe_key}_chunks.json"
    return os.path.join(str(base_dir or ""), filename)


def _entry_from_chunk(chunk):
    return {
        "index": int(chunk.get("index") or 0),
        "start": float(chunk.get("start") or 0.0),
        "duration": float(chunk.get("duration") or 0.0),
        "non_overlap_start": float(chunk.get("non_overlap_start") or 0.0),
        "status": "pending",
        "error": "",
        "segments": [],
        "updated_at": _now_text(),
    }


def new_checkpoint(meta, chunks_plan):
    return {
        "version": CHECKPOINT_VERSION,
        "meta": dict(meta or {}),
        "created_at": _now_text(),
        "updated_at": _now_text(),
        "chunks": [_entry_from_chunk(chunk) for chunk in list(chunks_plan or [])],
    }


def load_checkpoint(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("chunks"), list):
        return None
    return data


def save_checkpoint(path, data):
    if not path:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = dict(data or {})
    payload["updated_at"] = _now_text()
    temp = f"{path}.tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def _plan_signature(chunks_plan):
    sig = []
    for chunk in list(chunks_plan or []):
        sig.append(
            (
                int(chunk.get("index") or 0),
                round(float(chunk.get("start") or 0.0), 3),
                round(float(chunk.get("duration") or 0.0), 3),
                round(float(chunk.get("non_overlap_start") or 0.0), 3),
            )
        )
    return tuple(sig)


def normalize_checkpoint(existing, meta, chunks_plan, keep_done):
    if not existing:
        return new_checkpoint(meta, chunks_plan)

    old_sig = _plan_signature(existing.get("chunks") or [])
    new_sig = _plan_signature(chunks_plan)
    if old_sig != new_sig:
        return new_checkpoint(meta, chunks_plan)

    out = new_checkpoint(meta, chunks_plan)
    if not keep_done:
        return out

    by_idx = {}
    for item in list(existing.get("chunks") or []):
        try:
            idx = int(item.get("index"))
        except Exception:
            continue
        by_idx[idx] = item

    for item in out["chunks"]:
        old = by_idx.get(item["index"])
        if not old:
            continue
        status = str(old.get("status") or "").strip().lower()
        if status != "done":
            continue
        segs = old.get("segments") or []
        item["status"] = "done"
        item["segments"] = list(segs)
        item["error"] = ""
        item["updated_at"] = _now_text()
    return out


def _serialize_segments(segments):
    out = []
    for seg in list(segments or []):
        if not isinstance(seg, (list, tuple)) or len(seg) < 3:
            continue
        try:
            start = float(seg[0])
            end = float(seg[1])
        except Exception:
            continue
        text = str(seg[2] or "").strip()
        if not text:
            continue
        out.append([start, end, text])
    return out


def mark_chunk_done(checkpoint, chunk_index, segments):
    if not checkpoint:
        return
    for item in list(checkpoint.get("chunks") or []):
        try:
            item_idx = int(item.get("index"))
        except Exception:
            continue
        if item_idx != int(chunk_index):
            continue
        item["status"] = "done"
        item["error"] = ""
        item["segments"] = _serialize_segments(segments)
        item["updated_at"] = _now_text()
        return


def mark_chunk_failed(checkpoint, chunk_index, error_text):
    if not checkpoint:
        return
    for item in list(checkpoint.get("chunks") or []):
        try:
            item_idx = int(item.get("index"))
        except Exception:
            continue
        if item_idx != int(chunk_index):
            continue
        item["status"] = "failed"
        item["error"] = str(error_text or "").strip()
        item["updated_at"] = _now_text()
        return


def collect_done_chunks(checkpoint):
    chunks_out = []
    for item in list((checkpoint or {}).get("chunks") or []):
        if str(item.get("status") or "").strip().lower() != "done":
            continue
        segments = []
        for seg in list(item.get("segments") or []):
            if not isinstance(seg, (list, tuple)) or len(seg) < 3:
                continue
            try:
                start = float(seg[0])
                end = float(seg[1])
            except Exception:
                continue
            text = str(seg[2] or "").strip()
            if not text:
                continue
            segments.append((start, end, text))
        chunks_out.append(
            {
                "index": int(item.get("index") or 0),
                "start": float(item.get("start") or 0.0),
                "non_overlap_start": float(item.get("non_overlap_start") or 0.0),
                "segments": segments,
            }
        )
    chunks_out.sort(key=lambda c: int(c.get("index") or 0))
    return chunks_out


def missing_chunk_indexes(checkpoint):
    missing = []
    for item in list((checkpoint or {}).get("chunks") or []):
        status = str(item.get("status") or "").strip().lower()
        if status != "done":
            missing.append(int(item.get("index") or 0))
    return missing
