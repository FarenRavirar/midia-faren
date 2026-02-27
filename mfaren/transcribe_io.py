import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from .ffmpeg import find_ffmpeg

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSCRIBER_ROOT = os.path.join(APP_ROOT, "transcriber")
TRANSCRICOES_DIR: str = os.path.join(TRANSCRIBER_ROOT, "transcricoes")
CONVERTIDOS_DIR: str = os.path.join(TRANSCRIBER_ROOT, "convertidos")

DEFAULT_MODEL_DIRS = [
    os.path.join("C:\\", "midia-faren", "models"),
    os.path.join(TRANSCRIBER_ROOT, "models"),
]

WHISPER_RELEASE_DIRS = [
    os.path.join(TRANSCRIBER_ROOT, "whisper-cublas-12.4.0-bin-x64", "Release"),
    os.path.join(TRANSCRIBER_ROOT, "whisper-cublas-11.8.0-bin-x64", "Release"),
]

MEDIA_EXTENSIONS = {
    ".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac", ".opus", ".webm", ".mp4", ".mkv", ".mov",
}

STAGE_DIR_NAMES = {
    "convert": "convertido",
    "normalize": "normalizacao",
    "vad": "vad",
    "transcribe": "transcricao",
    "merge": "juncao",
}

HEARTBEAT_SECONDS = 5.0
CTRL_C_EXIT_CODE_WIN = 0xC000013A


def get_popen_windows_kwargs():
    kwargs = {}
    if os.name != "nt":
        return kwargs
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    kwargs["startupinfo"] = startupinfo
    creationflags = 0
    creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags
    return kwargs


def summarize_cmd(cmd, max_chars=480):
    parts = []
    for item in cmd:
        text = str(item)
        if len(text) > 120:
            text = f"{text[:117]}..."
        if " " in text:
            text = f'"{text}"'
        parts.append(text)
    out = " ".join(parts)
    if len(out) > max_chars:
        return f"{out[: max_chars - 3]}..."
    return out


def stage_timeouts(engine, duration):
    try:
        dur = max(0.0, float(duration or 0.0))
    except Exception:
        dur = 0.0
    if engine == "ffmpeg":
        stall_timeout = max(180.0, min(1800.0, dur * 2.0 + 120.0))
        hard_timeout = max(900.0, min(21600.0, dur * 8.0 + 600.0))
        return stall_timeout, hard_timeout
    stall_timeout = max(600.0, min(5400.0, dur * 4.0 + 300.0))
    hard_timeout = max(1800.0, min(86400.0, dur * 20.0 + 1800.0))
    return stall_timeout, hard_timeout


def project_stage_dir(options, stage_key):
    project_dir = str(options.get("project_dir") or "")
    if not project_dir:
        return None
    stage_name = STAGE_DIR_NAMES.get(str(stage_key or ""), str(stage_key or "")) or str(stage_key or "")
    path = os.path.join(project_dir, stage_name)
    os.makedirs(path, exist_ok=True)
    return path


def project_stage_file(options, stage_key, base_name, timestamp, ext):
    stage_dir = project_stage_dir(options, stage_key)
    if not stage_dir:
        return None
    if ext:
        return os.path.join(stage_dir, f"{base_name}_{timestamp}.{ext}")
    return os.path.join(stage_dir, f"{base_name}_{timestamp}")


def find_whisper_exe():
    for base in WHISPER_RELEASE_DIRS:
        for name in ("whisper-cli.exe", "main.exe"):
            candidate = os.path.join(base, name)
            if os.path.isfile(candidate):
                return candidate
    return None


def verify_ffmpeg():
    return find_ffmpeg() is not None


def verify_dlls_nvidia(whisper_exe):
    if not whisper_exe:
        return False
    base = os.path.dirname(whisper_exe)
    required = ["cublas64_12.dll", "cudart64_12.dll", "cublasLt64_12.dll"]
    missing = [dll for dll in required if not os.path.isfile(os.path.join(base, dll))]
    return not missing


def verify_wav_file(path):
    return os.path.isfile(path) and os.path.getsize(path) >= 1024


def list_models():
    models = []
    for base in DEFAULT_MODEL_DIRS:
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            if name.lower().endswith(".bin"):
                models.append(os.path.join(base, name))
    return sorted(models)


def pick_default_model(models):
    for m in models:
        if "large-v3" in os.path.basename(m).lower():
            return m
    return models[0] if models else None


def get_live_path(job_id, output_dir=None):
    from .util import sanitize_filename

    safe_id = sanitize_filename(job_id or "job")
    base_dir = output_dir or TRANSCRICOES_DIR
    return os.path.join(base_dir, f"{safe_id}_live.txt")


def is_archive_input(path):
    lower = (path or "").lower()
    return lower.endswith(".zip") or lower.endswith(".aup.zip")


def extract_archive_to_temp(archive_path):
    temp_dir = tempfile.mkdtemp(prefix="mfaren_zip_")
    with zipfile.ZipFile(archive_path, "r") as zf:
        zf.extractall(temp_dir)
    return temp_dir


def collect_media_files(root_dir):
    media = []
    for base, _, files in os.walk(root_dir):
        for name in files:
            if os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS:
                media.append(os.path.join(base, name))
    media.sort()
    return media


def collect_media_files_with_rel(root_dir):
    pairs = []
    for base, _, files in os.walk(root_dir):
        for name in files:
            if os.path.splitext(name)[1].lower() not in MEDIA_EXTENSIONS:
                continue
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root_dir).replace("\\", "/")
            pairs.append((rel, full))
    pairs.sort(key=lambda p: p[0].lower())
    return pairs


def resolve_archive_selected_relpaths(options, archive_input_path):
    selections = options.get("archive_selections") or {}
    if not isinstance(selections, dict):
        return set()
    value = selections.get(os.path.basename(str(archive_input_path or "")))
    if not value or not isinstance(value, list):
        return set()
    return {str(v).replace("\\", "/").strip() for v in value if str(v).strip()}


def ffprobe_duration(path):
    def _ffprobe_candidates():
        ffmpeg_bin = find_ffmpeg()
        if ffmpeg_bin:
            ffmpeg_bin = str(ffmpeg_bin)
            if os.path.isfile(ffmpeg_bin):
                ffprobe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
                yield os.path.join(os.path.dirname(ffmpeg_bin), ffprobe_name)
            else:
                yield "ffprobe"
        yield "ffprobe"

    def _run_ffprobe(ffprobe_bin):
        env = os.environ.copy()
        if os.path.isfile(ffprobe_bin):
            ffprobe_dir = os.path.dirname(ffprobe_bin)
            current = str(env.get("PATH") or "")
            known = [p.strip().lower() for p in current.split(os.pathsep) if p.strip()]
            if ffprobe_dir and ffprobe_dir.lower() not in known:
                env["PATH"] = f"{ffprobe_dir}{os.pathsep}{current}" if current else ffprobe_dir
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        out = subprocess.check_output(cmd, env=env, timeout=20, **get_popen_windows_kwargs())
        text = out.decode("utf-8", errors="replace").strip() if isinstance(out, (bytes, bytearray)) else str(out).strip()
        return float(text)

    for ffprobe_bin in _ffprobe_candidates():
        try:
            duration = _run_ffprobe(ffprobe_bin)
            if duration > 0.0:
                return duration
        except Exception:
            continue

    ffmpeg_bin = find_ffmpeg()
    if not ffmpeg_bin:
        return None
    try:
        probe = subprocess.run(
            [str(ffmpeg_bin), "-hide_banner", "-i", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            **get_popen_windows_kwargs(),
        )
        text = str(probe.stderr or "")
        m = re.search(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", text)
        if not m:
            return None
        hh = float(m.group(1))
        mm = float(m.group(2))
        ss = float(m.group(3))
        duration = (hh * 3600.0) + (mm * 60.0) + ss
        return duration if duration > 0.0 else None
    except Exception:
        return None

