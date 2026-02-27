import json
import logging
import os
import queue
import shutil
import time
import zipfile

from flask import Flask, Response, jsonify, render_template, request

from mfaren.db import init_db
from mfaren.jobs import JobManager
from mfaren.presets import validate_options
from mfaren.settings import get_setting, get_settings, set_setting, set_settings
from mfaren.util import open_folder, sanitize_filename
from mfaren import transcribe_recovery as trecov
from mfaren.transcriber import get_live_path, list_models, pick_default_model
from mfaren.ytdlp import find_ytdlp, is_youtube_url, list_entries

APP_ROOT = os.path.dirname(os.path.abspath(__file__))


def _resolve_env_path(env_key, default_path):
    raw = str(os.environ.get(env_key, "") or "").strip()
    if not raw:
        return os.path.abspath(default_path)
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    return os.path.abspath(os.path.join(APP_ROOT, raw))


UPLOADS_DIR = _resolve_env_path("MFAREN_UPLOADS_DIR", os.path.join(APP_ROOT, "data", "uploads"))
TRANSCRIBE_CACHE_DIR = _resolve_env_path(
    "MFAREN_TRANSCRIBE_CACHE_DIR", os.path.join(APP_ROOT, "data", "transcribe_cache")
)
TRANSCRIBE_MANIFEST_PATH = _resolve_env_path(
    "MFAREN_TRANSCRIBE_MANIFEST_PATH", os.path.join(APP_ROOT, "data", "transcribe_manifest.json")
)

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join("logs", "app.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

init_db()
job_manager = JobManager()
client_logger = logging.getLogger("client")

TRANSCRIBE_STAGE_DIRS = {
    "convert": "convertido",
    "normalize": "normalizacao",
    "vad": "vad",
    "transcribe": "transcricao",
    "merge": "juncao",
}

MEDIA_EXTENSIONS = {
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


@app.after_request
def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _default_output_dir():
    default_dir = "C:\\"
    if not os.path.isdir(default_dir):
        default_dir = os.path.join(APP_ROOT, "downloads")
        os.makedirs(default_dir, exist_ok=True)
    return default_dir


def _is_archive_file(path):
    low = (path or "").lower()
    return low.endswith(".zip") or low.endswith(".aup.zip")


def _stage_latest_file(project_dir, stage_key, stem_hint, ext):
    project_dir = str(project_dir or "")
    stage_key = str(stage_key or "")
    stage_name = TRANSCRIBE_STAGE_DIRS.get(stage_key, stage_key) or stage_key
    stage_dir = os.path.join(project_dir, str(stage_name))
    if not os.path.isdir(stage_dir):
        return None
    candidates = []
    for name in os.listdir(stage_dir):
        if not name.lower().endswith(ext.lower()):
            continue
        if stem_hint and stem_hint.lower() not in name.lower():
            continue
        full = os.path.join(stage_dir, name)
        candidates.append((os.path.getmtime(full), full))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _stage_latest_chunk_checkpoint(project_dir, stem_hint):
    project_dir = str(project_dir or "")
    stage_dir = os.path.join(project_dir, TRANSCRIBE_STAGE_DIRS.get("transcribe", "transcribe"))
    if not os.path.isdir(stage_dir):
        return None
    candidates = []
    for name in os.listdir(stage_dir):
        lower = name.lower()
        if not lower.endswith("_chunks.json"):
            continue
        if stem_hint and stem_hint.lower() not in lower:
            continue
        full = os.path.join(stage_dir, name)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        candidates.append((mtime, full))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _chunk_checkpoint_summary(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return None
    chunks = data.get("chunks") if isinstance(data, dict) else None
    if not isinstance(chunks, list):
        return None

    total = len(chunks)
    done = []
    failed = []
    for item in chunks:
        if not isinstance(item, dict):
            continue
        raw_idx = item.get("index")
        if raw_idx in (None, ""):
            continue
        try:
            idx = int(str(raw_idx).strip())
        except Exception:
            continue
        if idx < 0:
            continue
        status = str(item.get("status") or "").strip().lower()
        err = str(item.get("error") or "").strip()
        if status == "done":
            done.append(idx + 1)
        elif status == "failed":
            failed.append({"index": idx + 1, "error": err})

    done.sort()
    failed.sort(key=lambda x: x["index"])
    last_done = done[-1] if done else 0
    loop_failed = [f["index"] for f in failed if "loop detectado" in str(f.get("error") or "").lower()]
    failed_first = failed[0]["index"] if failed else None
    suggested = failed_first if failed_first else min(total, last_done + 1) if total else 1
    if suggested < 1:
        suggested = 1

    return {
        "checkpoint_path": path,
        "total_chunks": total,
        "last_done_chunk_index": last_done,
        "done_chunks": done,
        "failed_chunks": failed,
        "loop_failed_chunks": loop_failed,
        "suggested_chunk_index": suggested,
    }


def _extract_stage_from_message(message):
    text = str(message or "").lower()
    if "convers" in text and "wav" in text:
        return "convert"
    if "normal" in text:
        return "normalize"
    if "vad" in text:
        return "vad"
    if "transcri" in text:
        return "transcribe"
    if "junc" in text or "merge" in text:
        return "merge"
    return "transcribe"


def _project_base_from_dirname(name):
    raw = str(name or "")
    if "_" not in raw:
        return raw
    return raw.split("_", 1)[1]


def _active_project_dirs():
    active = set()
    for job in job_manager.list_jobs():
        if job.get("status") not in {"queued", "running", "paused"}:
            continue
        try:
            opts = json.loads(job.get("options") or "{}")
        except Exception:
            opts = {}
        p = opts.get("project_dir")
        if p:
            active.add(os.path.normcase(os.path.abspath(str(p))))
    return active


def _cleanup_duplicate_upload_projects(upload_dir, base_name, keep=2):
    if not os.path.isdir(upload_dir):
        return
    active = _active_project_dirs()
    base_name = str(base_name or "")
    items = []
    for entry in os.scandir(upload_dir):
        if not entry.is_dir():
            continue
        if _project_base_from_dirname(entry.name) != base_name:
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        items.append((mtime, entry.path))
    if len(items) <= keep:
        return
    items.sort(key=lambda x: x[0], reverse=True)
    for _, path in items[keep:]:
        norm = os.path.normcase(os.path.abspath(path))
        if norm in active:
            continue
        try:
            shutil.rmtree(path, ignore_errors=True)
            logging.getLogger("api").info("cleanup uploads: removed duplicate project dir %s", path)
        except Exception:
            logging.getLogger("api").warning("cleanup uploads: failed to remove %s", path, exc_info=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert")
def convert():
    return render_template("convert.html")


@app.route("/transcribe")
def transcribe():
    return render_template("transcribe.html")


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        mode = request.args.get("mode", "audio")
        data = get_settings(mode)
        last_mode = get_setting("last_mode", "audio")
        output_dir = get_setting("output_dir", _default_output_dir())
        if mode == "transcribe":
            data.setdefault("transcribe_guided_mode", "craig_long_best")
            data.setdefault("transcribe_profile", "auto")
            data.setdefault("transcribe_backend", "faster_whisper")
            data.setdefault("transcribe_device", "")
            data.setdefault("transcribe_compute_type", "")
            data.setdefault("whisperx_batch_size", "4")
            data.setdefault("transcribe_initial_prompt", "")
            data.setdefault("transcribe_output_json", "on")
            data.setdefault("normalize", "on")
            data.setdefault("vad", "off")
            data.setdefault("diarize", "on")
            data.setdefault("beam_size", "5")
            data.setdefault("chunk_seconds", "300")
            data.setdefault("chunk_overlap_seconds", "1.5")
            data.setdefault("transcribe_glossary", "")
            data.setdefault("transcribe_auto_recover", "on")
            data.setdefault("transcribe_auto_recover_retries", "1")
            data.setdefault("chunk_loop_max_incidents", "3")
            backend = str(data.get("transcribe_backend") or "faster_whisper").strip().lower()
            if not data.get("model"):
                if backend == "whisper_cpp":
                    models = list_models()
                    data["model"] = pick_default_model(models) if models else ""
                else:
                    data["model"] = "large-v3"
        elif mode in ("audio", "video"):
            data.setdefault("normalize", "off")
            if mode == "video":
                data.setdefault("video_accel", "off")
        elif mode in ("mixagem", "craig_notebook"):
            data.setdefault("mix_output_format", data.get("notebook_output_format") or "m4a")
            data.setdefault("mix_target_bitrate_kbps", data.get("notebook_target_bitrate_kbps") or "96")
            data.setdefault("mix_max_size_mb", data.get("notebook_max_size_mb") or "190")
            data.setdefault("normalize", "off")
        if "cookies_file" not in data or not data.get("cookies_file"):
            default_cookie = os.path.join("data", "www.youtube.com_cookies.txt")
            if os.path.isfile(default_cookie):
                data["cookies_file"] = default_cookie
        data.setdefault("yt_client", "tv")
        data.setdefault("use_cookies", "on")
        data.setdefault("remote_components", "on")
        data.setdefault("js_runtime", "node:C:\\Program Files\\nodejs\\node.exe")
        return jsonify({"mode": mode, "last_mode": last_mode, "output_dir": output_dir, "data": data})

    payload = request.get_json(force=True) or {}
    mode = payload.get("mode")
    data = payload.get("data", {})
    output_dir = payload.get("output_dir")
    last_mode = payload.get("last_mode")

    logging.getLogger("api").info("settings POST mode=%s output_dir=%s last_mode=%s data=%s", mode, output_dir, last_mode, data)
    if mode:
        existing = get_settings(mode)
        if not isinstance(existing, dict):
            existing = {}
        merged = dict(existing)
        if isinstance(data, dict):
            # Evita cruzamento de payload entre telas (ex.: mode=transcribe com data.mode=video).
            # Sempre persistimos o modo canônico da rota.
            data["mode"] = mode
            merged.update(data)
        data = merged
        if not data.get("cookies_file"):
            default_cookie = os.path.join("data", "www.youtube.com_cookies.txt")
            if os.path.isfile(default_cookie):
                data["cookies_file"] = default_cookie
        if mode in ("audio", "video"):
            data.setdefault("normalize", "off")
            if mode == "video":
                data.setdefault("video_accel", "off")
        data.setdefault("yt_client", "tv")
        data["use_cookies"] = "on"
        data["remote_components"] = "on"
        data.setdefault("js_runtime", "node:C:\\Program Files\\nodejs\\node.exe")
        set_settings(mode, data)
    if output_dir is not None:
        set_setting("output_dir", output_dir)
    if last_mode:
        set_setting("last_mode", last_mode)

    return jsonify({"ok": True})


@app.route("/api/browse-folder", methods=["POST"])
def api_browse_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        initial_dir = get_setting("output_dir", _default_output_dir())
        folder = filedialog.askdirectory(
            title="Selecionar pasta",
            mustexist=True,
            initialdir=initial_dir if os.path.isdir(initial_dir) else _default_output_dir(),
        )
        root.destroy()
        if not folder:
            return jsonify({"ok": False, "error": "Selecao cancelada"}), 400
        if not os.path.isdir(folder):
            return jsonify({"ok": False, "error": "Pasta invalida"}), 400
        set_setting("output_dir", folder)
        return jsonify({"ok": True, "path": folder})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/browse-cookies", methods=["POST"])
def api_browse_cookies():
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        file_path = filedialog.askopenfilename(
            title="Selecionar cookies.txt",
            filetypes=[("Cookies", "cookies.txt"), ("Texto", "*.txt"), ("Todos", "*.*")],
        )
        root.destroy()
        if not file_path:
            return jsonify({"ok": False, "error": "Selecao cancelada"}), 400
        if not os.path.isfile(file_path):
            return jsonify({"ok": False, "error": "Arquivo invalido"}), 400
        return jsonify({"ok": True, "path": file_path})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/transcribe/pick-files", methods=["POST"])
def api_transcribe_pick_files():
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        initial_dir = get_setting("output_dir", _default_output_dir())
        file_paths = filedialog.askopenfilenames(
            title="Selecionar arquivos para transcricao",
            initialdir=initial_dir if os.path.isdir(initial_dir) else _default_output_dir(),
            filetypes=[("Arquivos de midia/ZIP", "*.*")],
        )
        root.destroy()
        if not file_paths:
            return jsonify({"ok": False, "error": "Selecao cancelada"}), 400

        files = []
        for path in list(file_paths):
            full = os.path.abspath(str(path or ""))
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(full)[1].lower()
            if ext not in MEDIA_EXTENSIONS and not _is_archive_file(full):
                continue
            try:
                size = int(os.path.getsize(full))
            except OSError:
                size = 0
            files.append({"path": full, "name": os.path.basename(full), "size": size})

        if not files:
            return jsonify({"ok": False, "error": "Nenhum arquivo valido selecionado"}), 400

        source_dir = os.path.dirname(files[0]["path"])
        return jsonify({"ok": True, "files": files, "source_dir": source_dir})
    except Exception as exc:
        logging.getLogger("api").exception("api_transcribe_pick_files failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/jobs", methods=["GET", "POST"])
def api_jobs():
    if request.method == "GET":
        return jsonify(job_manager.list_jobs())

    try:
        payload = request.get_json(force=True) or {}
        items = payload.get("items", [])
        options = payload.get("options", {})
        output_dir = payload.get("output_dir") or get_setting("output_dir", _default_output_dir())
        options["output_dir"] = output_dir
        if not options.get("mode"):
            return jsonify({"error": "Modo invalido"}), 400

        validate_options(options)

        created_ids = []
        ytdlp = find_ytdlp()
        for item in items:
            if ytdlp and is_youtube_url(item):
                try:
                    entries = list_entries(ytdlp, item)
                except Exception:
                    entries = []
                if entries:
                    parent_id = job_manager.create_parent_job(item, options)
                    valid_entries = [e for e in entries if e.get("url")]
                    child_urls = [e["url"] for e in valid_entries]
                    child_ids = job_manager.create_jobs(
                        child_urls,
                        options,
                        source_type="url",
                        parent_job_id=parent_id,
                        meta=valid_entries,
                    )
                    created_ids.extend(child_ids)
                    continue
            created_ids.extend(job_manager.create_jobs([item], options, source_type="url"))

        return jsonify({"ok": True, "ids": created_ids})
    except ValueError as exc:
        logging.getLogger("api").warning("api_jobs validation failed: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logging.getLogger("api").exception("api_jobs failed")
        return jsonify({"error": "Falha ao criar jobs"}), 500


@app.route("/api/transcribe/start-local", methods=["POST"])
def api_transcribe_start_local():
    payload = request.get_json(force=True) or {}
    options = payload.get("options") or {}
    if not isinstance(options, dict):
        return jsonify({"ok": False, "error": "Opcoes invalidas"}), 400
    if options.get("mode") != "transcribe":
        options["mode"] = "transcribe"

    file_paths_raw = payload.get("file_paths") or []
    file_paths = []
    for item in list(file_paths_raw):
        path = os.path.abspath(str(item or ""))
        if not path or not os.path.isfile(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext not in MEDIA_EXTENSIONS and not _is_archive_file(path):
            continue
        file_paths.append(path)
    if not file_paths:
        return jsonify({"ok": False, "error": "Nenhum arquivo local valido enviado"}), 400

    output_dir = payload.get("output_dir")
    if not output_dir:
        output_dir = os.path.dirname(file_paths[0])
    if not output_dir or not os.path.isdir(output_dir):
        return jsonify({"ok": False, "error": "Pasta de saida invalida"}), 400
    options["output_dir"] = output_dir
    set_setting("output_dir", output_dir)
    set_setting("last_mode", "transcribe")

    validate_options(options)

    upload_dir = UPLOADS_DIR
    os.makedirs(upload_dir, exist_ok=True)
    project_name_base = sanitize_filename(os.path.splitext(os.path.basename(file_paths[0]))[0] or "projeto")
    project_name = f"{int(time.time())}_{project_name_base or 'projeto'}"
    project_dir = os.path.join(upload_dir, project_name)
    os.makedirs(project_dir, exist_ok=True)
    options["project_dir"] = project_dir
    options["project_name"] = project_name
    _cleanup_duplicate_upload_projects(upload_dir, project_name_base, keep=2)

    fingerprint = "|".join(sorted([str(p).lower() for p in file_paths]))
    options["request_fingerprint"] = fingerprint
    active = {"queued", "running", "paused"}
    for j in job_manager.list_jobs():
        if j.get("mode") != "transcribe":
            continue
        if j.get("source_type") != "local":
            continue
        if j.get("status") not in active:
            continue
        try:
            jopts = json.loads(j.get("options") or "{}")
        except Exception:
            jopts = {}
        if jopts.get("request_fingerprint") == fingerprint:
            try:
                shutil.rmtree(project_dir, ignore_errors=True)
            except Exception:
                pass
            return jsonify({"ok": False, "error": "Transcrição já em execução para este lote."}), 409

    items = [os.path.basename(p) for p in file_paths]
    meta = [{"title": os.path.splitext(os.path.basename(p))[0], "channel": None} for p in file_paths]
    ids = job_manager.create_jobs(items, options, source_type="local", input_paths=file_paths, meta=meta)
    return jsonify({"ok": True, "ids": ids, "output_dir": output_dir, "project_dir": project_dir})


@app.route("/api/convert", methods=["POST"])
def api_convert_files():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    options = json.loads(request.form.get("options", "{}"))
    archive_selections = request.form.get("archive_selections")
    if archive_selections:
        try:
            options["archive_selections"] = json.loads(archive_selections)
        except Exception:
            options["archive_selections"] = {}
    output_dir = request.form.get("output_dir") or get_setting("output_dir", _default_output_dir())
    options["output_dir"] = output_dir
    if not options.get("mode"):
        return jsonify({"error": "Modo invalido"}), 400

    validate_options(options)

    upload_dir = UPLOADS_DIR
    os.makedirs(upload_dir, exist_ok=True)
    project_name_base = sanitize_filename(os.path.splitext(files[0].filename or "projeto")[0] if files else "projeto")
    project_name = f"{int(time.time())}_{project_name_base or 'projeto'}"
    project_dir = os.path.join(upload_dir, project_name)
    os.makedirs(project_dir, exist_ok=True)
    _cleanup_duplicate_upload_projects(upload_dir, project_name_base, keep=2)
    logging.getLogger("api").info(
        "convert_start mode=%s files=%s project_dir=%s output_dir=%s",
        options.get("mode"),
        len(files),
        project_dir,
        output_dir,
    )

    options["project_dir"] = project_dir
    options["project_name"] = project_name

    input_paths = []
    meta = []
    for f in files:
        name = sanitize_filename(f.filename or "arquivo")
        path = os.path.join(project_dir, name)
        f.save(path)
        input_paths.append(path)
        title = os.path.splitext(name)[0] if name else "nao informado"
        meta.append({"title": title, "channel": None})

    items = [os.path.basename(p) for p in input_paths]
    if options.get("mode") == "transcribe":
        fingerprint = "|".join(sorted([str(os.path.basename(p)).lower() for p in input_paths]))
        options["request_fingerprint"] = fingerprint
        active = {"queued", "running", "paused"}
        for j in job_manager.list_jobs():
            if j.get("mode") != "transcribe":
                continue
            if j.get("source_type") != "local":
                continue
            if j.get("status") not in active:
                continue
            try:
                jopts = json.loads(j.get("options") or "{}")
            except Exception:
                jopts = {}
            if jopts.get("request_fingerprint") == fingerprint:
                try:
                    shutil.rmtree(project_dir, ignore_errors=True)
                except Exception:
                    pass
                return jsonify({"ok": False, "error": "Transcrição já em execução para este lote."}), 409
    job_ids = job_manager.create_jobs(items, options, source_type="local", input_paths=input_paths, meta=meta)
    logging.getLogger("api").info(
        "convert_enqueued mode=%s jobs=%s project_dir=%s",
        options.get("mode"),
        len(job_ids),
        project_dir,
    )
    return jsonify({"ok": True, "ids": job_ids, "project_dir": project_dir})


@app.route("/api/archive/entries", methods=["POST"])
def api_archive_entries():
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "Arquivo não enviado"}), 400
    filename = f.filename or ""
    if not _is_archive_file(filename):
        return jsonify({"ok": False, "error": "Arquivo não é ZIP"}), 400
    tmp_path = None
    try:
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            tmp_path = tmp.name
            f.save(tmp_path)

        entries = []
        with zipfile.ZipFile(tmp_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = str(info.filename or "").replace("\\", "/")
                ext = os.path.splitext(name)[1].lower()
                if ext not in MEDIA_EXTENSIONS:
                    continue
                entries.append({"name": name, "size": int(getattr(info, "file_size", 0) or 0)})
        entries.sort(key=lambda x: x["name"].lower())
        return jsonify({"ok": True, "entries": entries})
    except Exception as exc:
        logging.getLogger("api").exception("api_archive_entries failed")
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@app.route("/api/transcriber/models", methods=["GET"])
def api_transcriber_models():
    models = list_models()
    default_model = pick_default_model(models) if models else None
    return jsonify({"models": models, "default": default_model})


@app.route("/api/transcribe/<job_id>/live")
def api_transcribe_live(job_id):
    output_dir = None
    job = job_manager.get_job(job_id)
    if job:
        try:
            options = json.loads(job.get("options") or "{}")
            output_dir = options.get("output_dir") or options.get("transcribe_output_dir")
        except Exception:
            output_dir = None
    if not output_dir:
        output_dir = get_setting("output_dir", _default_output_dir())
    live_path = get_live_path(job_id, output_dir)

    def gen():
        last_pos = 0
        sent_wait = False
        sent_initial_tail = False
        try:
            while True:
                if os.path.isfile(live_path):
                    if not sent_initial_tail:
                        with open(live_path, "r", encoding="utf-8", errors="replace") as f:
                            data = f.read()
                            last_pos = f.tell()
                        if len(data) > 120000:
                            data = data[-120000:]
                        lines = data.splitlines()
                        for line in lines[-30:]:
                            payload = {"line": line}
                            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        sent_initial_tail = True
                        sent_wait = False
                    else:
                        with open(live_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(last_pos)
                            chunk = f.read()
                            last_pos = f.tell()
                        if chunk:
                            for line in chunk.splitlines():
                                payload = {"line": line}
                                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            sent_wait = False
                else:
                    if not sent_wait:
                        payload = {"message": "Aguardando transcrição iniciar..."}
                        yield f"event: status\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        sent_wait = True
                yield "event: ping\ndata: {}\n\n"
                time.sleep(1.0)
        except GeneratorExit:
            return

    return Response(gen(), mimetype="text/event-stream")


@app.route("/api/transcribe/resume-last", methods=["POST"])
def api_transcribe_resume_last():
    jobs = job_manager.list_jobs()
    active = [j for j in jobs if j.get("mode") == "transcribe" and j.get("status") in {"queued", "running", "paused"}]
    if active:
        return jsonify({"ok": False, "error": "Já existe transcrição ativa. Cancele/conclua antes de retomar."}), 409

    candidates = [j for j in jobs if j.get("mode") == "transcribe" and j.get("source_type") == "local"]
    if not candidates:
        return jsonify({"ok": False, "error": "Nenhum job de transcrição anterior encontrado."}), 404
    # agrupa por projeto e escolhe o mais recente
    by_project = {}
    for j in candidates:
        try:
            opts = json.loads(j.get("options") or "{}")
        except Exception:
            opts = {}
        pdir = opts.get("project_dir")
        if not pdir or not os.path.isdir(pdir):
            continue
        key = os.path.normcase(os.path.abspath(str(pdir)))
        by_project.setdefault(key, []).append((j, opts))
    if not by_project:
        return jsonify({"ok": False, "error": "Projeto anterior não encontrado para retomada."}), 404

    project_key = sorted(
        by_project.keys(),
        key=lambda k: max(str(item[0].get("created_at") or "") for item in by_project[k]),
        reverse=True,
    )[0]
    project_rows = by_project[project_key]

    # dedupe por input_path (pega a versão mais recente por arquivo)
    latest_by_input = {}
    for j, opts in sorted(project_rows, key=lambda t: str(t[0].get("created_at") or ""), reverse=True):
        ip = str(j.get("input_path") or "")
        if not ip or not os.path.isfile(ip):
            continue
        if ip not in latest_by_input:
            latest_by_input[ip] = (j, opts)
    if not latest_by_input:
        return jsonify({"ok": False, "error": "Arquivos do projeto não existem mais para retomada."}), 404

    ids = []
    resumed_from_ids = []
    resume_details = []
    for ip, (j, opts) in latest_by_input.items():
        stage = _extract_stage_from_message(j.get("message"))
        stem_hint = os.path.splitext(os.path.basename(ip))[0]
        ropts = dict(opts)
        ropts["redo_from"] = stage
        ropts["reuse_wav_raw"] = _stage_latest_file(project_key, "convert", stem_hint, ".wav")
        ropts["reuse_wav_norm"] = _stage_latest_file(project_key, "normalize", stem_hint, ".wav")
        ropts["reuse_wav_vad"] = _stage_latest_file(project_key, "vad", stem_hint, ".wav")
        if stage == "transcribe":
            checkpoint_path = _stage_latest_chunk_checkpoint(project_key, stem_hint)
            if checkpoint_path:
                ropts["redo_chunk_checkpoint"] = checkpoint_path
                ropts["transcribe_resume_chunks"] = "on"
                ropts["transcribe_resume_origin"] = "checkpoint"
                ropts.pop("redo_chunk_index", None)
                ropts.pop("transcribe_resume_from_chunk_index", None)
                ropts.pop("resume_live_path", None)
                resume_details.append(
                    {
                        "input_path": ip,
                        "origin": "checkpoint",
                        "suggested_chunk_index": None,
                    }
                )
            else:
                ropts.pop("redo_chunk_checkpoint", None)
                inferred = trecov.infer_chunk_from_app_log(ip)
                inferred_idx = (inferred or {}).get("suggested_chunk_index")
                if inferred_idx is not None:
                    ropts["transcribe_resume_chunks"] = "on"
                    ropts["transcribe_resume_from_chunk_index"] = int(inferred_idx)
                    ropts["transcribe_resume_origin"] = "log"
                    output_dir = ropts.get("output_dir") or ropts.get("transcribe_output_dir")
                    live_path = get_live_path(j.get("id"), output_dir) if output_dir else None
                    if live_path and os.path.isfile(live_path):
                        ropts["resume_live_path"] = live_path
                    resume_details.append(
                        {
                            "input_path": ip,
                            "origin": "log",
                            "suggested_chunk_index": int(inferred_idx + 1),
                        }
                    )
                else:
                    ropts.pop("transcribe_resume_chunks", None)
                    ropts.pop("transcribe_resume_from_chunk_index", None)
                    ropts.pop("transcribe_resume_origin", None)
                    ropts.pop("resume_live_path", None)
                    resume_details.append(
                        {
                            "input_path": ip,
                            "origin": "none",
                            "suggested_chunk_index": None,
                        }
                    )
        else:
            ropts.pop("redo_chunk_checkpoint", None)
            ropts.pop("redo_chunk_index", None)
            ropts.pop("transcribe_resume_chunks", None)
            ropts.pop("transcribe_resume_from_chunk_index", None)
            ropts.pop("transcribe_resume_origin", None)
            ropts.pop("resume_live_path", None)
            resume_details.append(
                {
                    "input_path": ip,
                    "origin": "none",
                    "suggested_chunk_index": None,
                }
            )
        title = j.get("title") or stem_hint or "arquivo"
        created = job_manager.create_jobs(
            [os.path.basename(ip)],
            ropts,
            source_type="local",
            input_paths=[ip],
            meta=[{"title": title, "channel": j.get("channel")}],
        )
        ids.extend(created)
        resumed_from_ids.append(j.get("id"))

    return jsonify(
        {
            "ok": True,
            "ids": ids,
            "resumed_from_job_ids": resumed_from_ids,
            "project_dir": project_key,
            "resume_details": resume_details,
        }
    )


@app.route("/api/transcribe/cleanup", methods=["POST"])
def api_transcribe_cleanup():
    upload_dir = UPLOADS_DIR
    cache_dir = TRANSCRIBE_CACHE_DIR
    removed_dirs = 0
    active_project_dirs = set()
    for j in job_manager.list_jobs():
        if j.get("status") not in {"queued", "running", "paused"}:
            continue
        try:
            opts = json.loads(j.get("options") or "{}")
        except Exception:
            opts = {}
        p = opts.get("project_dir")
        if p:
            active_project_dirs.add(os.path.normcase(os.path.abspath(str(p))))

    manifest_path = TRANSCRIBE_MANIFEST_PATH
    if os.path.isdir(cache_dir):
        shutil.rmtree(cache_dir, ignore_errors=True)
        removed_dirs += 1
    if os.path.isfile(manifest_path):
        try:
            os.remove(manifest_path)
            removed_dirs += 1
        except OSError:
            pass

    if os.path.isdir(upload_dir):
        for entry in os.scandir(upload_dir):
            if not entry.is_dir():
                continue
            p = os.path.normcase(os.path.abspath(entry.path))
            if p in active_project_dirs:
                continue
            shutil.rmtree(entry.path, ignore_errors=True)
            removed_dirs += 1

    return jsonify({"ok": True, "removed": removed_dirs})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel(job_id):
    ok = job_manager.cancel_job(job_id)
    return jsonify({"ok": ok})


@app.route("/api/jobs/<job_id>/repeat", methods=["POST"])
def api_repeat(job_id):
    ids = job_manager.repeat_job(job_id)
    if not ids:
        return jsonify({"ok": False, "error": "Nao foi possivel repetir"}), 400
    return jsonify({"ok": True, "ids": ids})


@app.route("/api/jobs/<job_id>/pause", methods=["POST"])
def api_pause_job(job_id):
    ok = job_manager.pause_job(job_id)
    if not ok:
        return jsonify({"ok": False, "error": "Nao foi possivel pausar"}), 400
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/resume", methods=["POST"])
def api_resume_job(job_id):
    ok = job_manager.resume_job(job_id)
    if not ok:
        return jsonify({"ok": False, "error": "Nao foi possivel retomar"}), 400
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/start-now", methods=["POST"])
def api_start_now(job_id):
    ok = job_manager.start_now(job_id)
    if not ok:
        return jsonify({"ok": False, "error": "Nao foi possivel priorizar"}), 400
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/start-group", methods=["POST"])
def api_start_group(job_id):
    ok = job_manager.start_group(job_id)
    if not ok:
        return jsonify({"ok": False, "error": "Nao ha itens para iniciar"}), 400
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/delete", methods=["POST"])
def api_delete(job_id):
    try:
        ok = job_manager.delete_job(job_id)
        if not ok:
            return jsonify({"ok": False, "error": "Nao foi possivel remover"}), 400
        return jsonify({"ok": True})
    except Exception as exc:
        logging.getLogger("api").exception("delete failed for job_id=%s", job_id)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/jobs/<job_id>/redo/<stage>", methods=["POST"])
def api_redo_stage(job_id, stage):
    allowed = {"convert", "normalize", "vad", "transcribe", "merge", "chunk"}
    if stage not in allowed:
        return jsonify({"ok": False, "error": "Etapa invalida"}), 400
    payload = request.get_json(silent=True) or {}
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado"}), 404
    if job.get("source_type") != "local":
        return jsonify({"ok": False, "error": "Refazer etapa disponivel apenas para jobs locais"}), 400
    try:
        options = json.loads(job.get("options") or "{}")
    except Exception:
        options = {}
    input_path = job.get("input_path")
    if not input_path or not os.path.isfile(input_path):
        return jsonify({"ok": False, "error": "Arquivo de entrada nao encontrado"}), 400
    project_dir = options.get("project_dir")
    if not project_dir or not os.path.isdir(project_dir):
        return jsonify({"ok": False, "error": "Projeto sem pasta de etapas"}), 400

    redo_options = dict(options)
    stem_hint = sanitize_filename(os.path.splitext(os.path.basename(input_path))[0])
    redo_stage = stage
    chunk_one_based = None
    if stage == "chunk":
        raw_chunk = payload.get("chunk_index")
        try:
            chunk_one_based = int(str(raw_chunk).strip())
        except Exception:
            return jsonify({"ok": False, "error": "chunk_index invalido. Use inteiro >= 1"}), 400
        if chunk_one_based < 1:
            return jsonify({"ok": False, "error": "chunk_index invalido. Use inteiro >= 1"}), 400
        checkpoint_path = _stage_latest_chunk_checkpoint(project_dir, stem_hint)
        if not checkpoint_path or not os.path.isfile(checkpoint_path):
            return jsonify({"ok": False, "error": "Checkpoint por chunk nao encontrado para este job"}), 400
        redo_stage = "transcribe"
        redo_options["redo_chunk_index"] = int(chunk_one_based - 1)
        redo_options["redo_chunk_checkpoint"] = checkpoint_path
        redo_options["transcribe_auto_recover"] = "off"
        redo_options["transcribe_auto_recover_retries"] = "0"
    else:
        redo_options.pop("redo_chunk_index", None)
        redo_options.pop("redo_chunk_checkpoint", None)

    redo_options["redo_from"] = redo_stage
    if redo_stage in {"normalize", "vad", "transcribe", "merge"}:
        redo_options["reuse_wav_raw"] = _stage_latest_file(project_dir, "convert", stem_hint, ".wav")
    if redo_stage in {"vad", "transcribe", "merge"}:
        redo_options["reuse_wav_norm"] = _stage_latest_file(project_dir, "normalize", stem_hint, ".wav")
    if redo_stage in {"transcribe", "merge"}:
        redo_options["reuse_wav_vad"] = _stage_latest_file(project_dir, "vad", stem_hint, ".wav")
    if redo_stage == "merge" and not _is_archive_file(input_path):
        redo_options["redo_from"] = "transcribe"

    ids = job_manager.create_jobs(
        [os.path.basename(input_path)],
        redo_options,
        source_type="local",
        input_paths=[input_path],
        meta=[{"title": job.get("title"), "channel": job.get("channel")}],
    )
    out = {"ok": True, "ids": ids}
    if chunk_one_based is not None:
        out["chunk_index"] = chunk_one_based
    return jsonify(out)


@app.route("/api/jobs/<job_id>/chunk-summary", methods=["GET"])
def api_job_chunk_summary(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado"}), 404
    if job.get("source_type") != "local":
        return jsonify({"ok": False, "error": "Resumo de chunk disponivel apenas para jobs locais"}), 400
    try:
        options = json.loads(job.get("options") or "{}")
    except Exception:
        options = {}
    input_path = job.get("input_path")
    project_dir = options.get("project_dir")
    if not input_path or not os.path.isfile(input_path):
        return jsonify({"ok": False, "error": "Arquivo de entrada nao encontrado"}), 400
    if not project_dir or not os.path.isdir(project_dir):
        return jsonify({"ok": False, "error": "Projeto sem pasta de etapas"}), 400

    stem_hint = sanitize_filename(os.path.splitext(os.path.basename(input_path))[0])
    checkpoint_path = _stage_latest_chunk_checkpoint(project_dir, stem_hint)
    summary = _chunk_checkpoint_summary(checkpoint_path)
    if not summary:
        inferred = trecov.infer_chunk_from_app_log(input_path)
        if not inferred:
            return jsonify({"ok": False, "error": "Checkpoint por chunk nao encontrado"}), 404
        total = int(inferred.get("total_chunks") or 0)
        last_done_zero = inferred.get("last_done_chunk_index")
        failed_zero = inferred.get("failed_chunk_index")
        suggested_zero = inferred.get("suggested_chunk_index")
        last_done = int(last_done_zero + 1) if isinstance(last_done_zero, int) else 0
        failed_chunks = []
        if isinstance(failed_zero, int):
            failed_chunks.append(
                {"index": int(failed_zero + 1), "error": str(inferred.get("failed_error") or "falha detectada por log")}
            )
        loop_failed = []
        if failed_chunks and "loop detectado" in str(failed_chunks[0].get("error") or "").lower():
            loop_failed = [failed_chunks[0]["index"]]
        suggested_one = int(suggested_zero + 1) if isinstance(suggested_zero, int) else (1 if total else 0)
        done_chunks = list(range(1, max(0, last_done) + 1)) if last_done > 0 else []
        return jsonify(
            {
                "ok": True,
                "checkpoint_path": None,
                "total_chunks": total,
                "last_done_chunk_index": last_done,
                "done_chunks": done_chunks,
                "failed_chunks": failed_chunks,
                "loop_failed_chunks": loop_failed,
                "suggested_chunk_index": suggested_one,
                "source": "app.log",
            }
        )
    return jsonify({"ok": True, **summary})


@app.route("/api/jobs/<job_id>/ok", methods=["POST"])
def api_job_ok(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado"}), 404
    try:
        options = json.loads(job.get("options") or "{}")
    except Exception:
        options = {}
    project_dir = options.get("project_dir")
    if project_dir and os.path.isdir(project_dir):
        try:
            shutil.rmtree(project_dir, ignore_errors=True)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Nao foi possivel limpar projeto: {exc}"}), 500
    job_manager.delete_job(job_id)
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/open-result", methods=["POST"])
def api_open_result(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado"}), 404
    output_path = job.get("output_path")
    if output_path and open_folder(output_path):
        return jsonify({"ok": True})
    try:
        options = json.loads(job.get("options") or "{}")
    except Exception:
        options = {}
    output_dir = options.get("output_dir") or get_setting("output_dir", _default_output_dir())
    if output_dir and open_folder(output_dir):
        return jsonify({"ok": True, "fallback": True})
    return jsonify({"ok": False, "error": "Resultado indisponivel"}), 400


@app.route("/api/queue/pause", methods=["POST"])
def api_pause_queue():
    job_manager.pause_queue()
    return jsonify({"ok": True})


@app.route("/api/queue/resume", methods=["POST"])
def api_resume_queue():
    job_manager.resume_queue()
    return jsonify({"ok": True})


@app.route("/api/queue/cancel-all", methods=["POST"])
def api_cancel_queue():
    job_manager.cancel_queue()
    return jsonify({"ok": True})


@app.route("/api/queue/clear-all", methods=["POST"])
def api_clear_queue():
    job_manager.clear_queue()
    return jsonify({"ok": True})


@app.route("/api/stream")
def api_stream():
    q = job_manager.register_client()

    def gen():
        last_ping = time.time()
        try:
            while True:
                try:
                    event = q.get(timeout=1.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    if time.time() - last_ping >= 10:
                        yield "event: ping\ndata: {}\n\n"
                        last_ping = time.time()
        finally:
            job_manager.unregister_client(q)

    return Response(gen(), mimetype="text/event-stream")


@app.route("/api/open-last-folder", methods=["POST"])
def api_open_last():
    jobs = job_manager.list_jobs()
    for job in jobs:
        if job.get("status") == "done" and job.get("output_path"):
            if open_folder(job.get("output_path")):
                return jsonify({"ok": True})
    output_dir = get_setting("output_dir", _default_output_dir())
    if output_dir and open_folder(output_dir):
        return jsonify({"ok": True, "fallback": True, "path": output_dir})
    return jsonify({"ok": False, "error": "Nenhuma pasta disponivel"}), 400


@app.route("/api/reset-log", methods=["POST"])
def api_reset_log():
    try:
        log_path = os.path.join("logs", "app.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/client-log", methods=["POST"])
def api_client_log():
    payload = request.get_json(force=True) or {}
    level = payload.get("level", "info")
    message = payload.get("message", "")
    if level == "error":
        client_logger.error(message)
    elif level == "warning":
        client_logger.warning(message)
    else:
        client_logger.info(message)
    return jsonify({"ok": True})


if __name__ == "__main__":
    debug_flag = str(os.environ.get("MFAREN_DEBUG", "0")).strip().lower() in ("1", "true", "on", "yes", "sim", "s")
    app.run(debug=debug_flag, threaded=True, use_reloader=debug_flag)
