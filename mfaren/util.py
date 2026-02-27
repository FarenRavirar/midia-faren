import os
import re
import subprocess


INVALID_CHARS = r"<>:\"/\|?*"


def sanitize_filename(name):
    if not name:
        return "n\u00e3o informado"
    name = re.sub(r"\s+", " ", name).strip()
    name = "".join("_" if ch in INVALID_CHARS else ch for ch in name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    return name or "n\u00e3o informado"


def make_output_name(title, channel):
    title = title or "n\u00e3o informado"
    channel = channel or "n\u00e3o informado"
    return sanitize_filename(f"{title} - {channel}")


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def open_folder(path):
    if not path:
        return False
    if os.path.isdir(path):
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    if os.path.isfile(path):
        os.startfile(os.path.dirname(path))  # type: ignore[attr-defined]
        return True
    return False


def kill_process_tree(pid):
    if not pid:
        return False
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
        return True
    except Exception:
        return False


def suspend_process(pid):
    if not pid:
        return False
    try:
        subprocess.run(
            ["powershell", "-Command", f"Suspend-Process -Id {pid} -ErrorAction SilentlyContinue"],
            capture_output=True,
            text=True,
        )
        return True
    except Exception:
        return False


def resume_process(pid):
    if not pid:
        return False
    try:
        subprocess.run(
            ["powershell", "-Command", f"Resume-Process -Id {pid} -ErrorAction SilentlyContinue"],
            capture_output=True,
            text=True,
        )
        return True
    except Exception:
        return False


def human_bytes(size_bytes):
    if size_bytes is None:
        return "-"
    try:
        size_bytes = float(size_bytes)
    except (TypeError, ValueError):
        return "-"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def human_time(seconds):
    if seconds is None:
        return "-"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "-"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
