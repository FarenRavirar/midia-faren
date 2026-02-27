import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_env_path(env_key, default_rel):
    raw = str(os.environ.get(env_key, "") or "").strip()
    if not raw:
        return os.path.join(APP_ROOT, default_rel)
    if os.path.isabs(raw):
        return raw
    return os.path.join(APP_ROOT, raw)


CACHE_ROOT = _resolve_env_path("MFAREN_TRANSCRIBE_CACHE_DIR", os.path.join("data", "transcribe_cache"))
MANIFEST_PATH = _resolve_env_path("MFAREN_TRANSCRIBE_MANIFEST_PATH", os.path.join("data", "transcribe_manifest.json"))
_manifest_lock = threading.Lock()
_STAMP_FMT = "%Y-%m-%d %H:%M:%S"
_CACHE_NAME_TS_RE = re.compile(r"^(?P<base>.+?)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_.+)?$")


def sha256_text(value):
    return hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_manifest():
    if not os.path.isfile(MANIFEST_PATH):
        return {"version": 1, "entries": {}}
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "entries": {}}
        data.setdefault("version", 1)
        data.setdefault("entries", {})
        return data
    except Exception:
        return {"version": 1, "entries": {}}


def _save_manifest(data):
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    temp_path = f"{MANIFEST_PATH}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, MANIFEST_PATH)


def _entry_primary_file(entry):
    files = (entry or {}).get("files") or {}
    if not isinstance(files, dict) or not files:
        return None
    preferred = files.get("wav")
    if preferred:
        return preferred
    for _, path in sorted(files.items()):
        if path:
            return path
    return None


def _entry_sort_value(entry):
    path = _entry_primary_file(entry)
    if path and os.path.isfile(path):
        try:
            return float(os.path.getmtime(path))
        except Exception:
            pass
    text = str((entry or {}).get("updated_at") or "").strip()
    if text:
        try:
            return datetime.strptime(text, _STAMP_FMT).timestamp()
        except Exception:
            pass
    return 0.0


def _entry_group_key(stage, entry):
    path = _entry_primary_file(entry)
    if not path:
        return None
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _CACHE_NAME_TS_RE.match(stem)
    if m:
        stem = m.group("base")
    stem = str(stem or "").strip().lower()
    if not stem:
        return None
    return f"{str(stage or '').strip().lower()}::{stem}"


def _remove_stage_entry_files(stage, key, entry):
    stage_dir = os.path.join(CACHE_ROOT, str(stage or ""), str(key or ""))
    shutil.rmtree(stage_dir, ignore_errors=True)
    files = (entry or {}).get("files") or {}
    for _, path in list(files.items()):
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError:
            continue


def _prune_manifest_stage(manifest, stage, keep=2):
    keep_n = max(1, int(keep))
    entries = (manifest or {}).get("entries") or {}
    groups = {}
    for key, entry in list(entries.items()):
        if str((entry or {}).get("stage") or "") != str(stage):
            continue
        g = _entry_group_key(stage, entry)
        if not g:
            continue
        groups.setdefault(g, []).append((key, entry, _entry_sort_value(entry)))

    removed = 0
    for _, items in groups.items():
        items.sort(key=lambda x: x[2], reverse=True)
        for key, entry, _ in items[keep_n:]:
            if key in entries:
                entries.pop(key, None)
                _remove_stage_entry_files(stage, key, entry)
                removed += 1
    return removed


def prune_cache_stage(stage, keep=2):
    with _manifest_lock:
        manifest = _load_manifest()
        removed = _prune_manifest_stage(manifest, stage, keep=keep)
        if removed:
            _save_manifest(manifest)
        return removed


def cache_get(stage, key):
    with _manifest_lock:
        manifest = _load_manifest()
        entry = manifest.get("entries", {}).get(key)
    if not entry or entry.get("stage") != stage:
        return None
    files = entry.get("files") or {}
    for _, path in files.items():
        if not os.path.isfile(path):
            return None
    return entry


def cache_put(stage, key, files, meta=None):
    os.makedirs(CACHE_ROOT, exist_ok=True)
    stage_dir = os.path.join(CACHE_ROOT, stage, key)
    os.makedirs(stage_dir, exist_ok=True)
    stored = {}
    for name, src in (files or {}).items():
        if not src or not os.path.isfile(src):
            continue
        dst = os.path.join(stage_dir, os.path.basename(src))
        if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
            shutil.copy2(src, dst)
        stored[name] = dst
    if not stored:
        return None
    with _manifest_lock:
        manifest = _load_manifest()
        manifest.setdefault("entries", {})
        manifest["entries"][key] = {
            "stage": stage,
            "files": stored,
            "meta": meta or {},
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if stage in ("convert", "normalize"):
            _prune_manifest_stage(manifest, stage, keep=2)
        _save_manifest(manifest)
    return stored


def materialize_cached_file(src, dst):
    if not src or not os.path.isfile(src):
        return False
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(dst):
        return True
    try:
        os.link(src, dst)
        return True
    except Exception:
        pass
    try:
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False
