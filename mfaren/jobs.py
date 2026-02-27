import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from collections import deque

from .db import get_cursor
from .downloader import process_job
from . import transcribe_io as tio
from . import transcribe_recovery as trecov
from .util import kill_process_tree, suspend_process, resume_process


class JobManager:
    def __init__(self):
        self.queue = queue.Queue()
        self.jobs = {}
        self.clients = []
        self.lock = threading.Lock()
        self.backlog = {}
        self.runtime_hints = {}
        self.paused = threading.Event()
        self.priority = deque()
        run_main = os.environ.get("WERKZEUG_RUN_MAIN")
        self._worker_enabled = (run_main is None) or (run_main == "true")
        if self._worker_enabled:
            self._load_jobs_from_db()
            self.worker = threading.Thread(target=self._worker_loop, daemon=True)
            self.worker.start()
        else:
            self.worker = None

    def _normalize_process_accel_mode(self, value):
        text = str(value or "").strip().lower()
        if text in ("cpu", "gpu_no_cuda", "gpu_cuda"):
            return text
        return None

    def _process_accel_label(self, mode):
        key = self._normalize_process_accel_mode(mode)
        if key == "gpu_cuda":
            return "Processo Usando GPU Com CUDA"
        if key == "gpu_no_cuda":
            return "Processo Usando GPU sem CUDA"
        if key == "cpu":
            return "Processo Usando CPU"
        return ""

    def _infer_process_accel_mode(self, options):
        if not isinstance(options, dict):
            return None

        explicit = self._normalize_process_accel_mode(
            options.get("process_accel_mode")
            or options.get("runtime_accel")
            or options.get("transcribe_runtime_accel")
        )
        if explicit:
            return explicit

        mode = str(options.get("mode") or "").strip().lower()
        if mode in ("audio", "image", "mixagem", "craig_notebook"):
            return "cpu"
        if mode == "video":
            return "cpu"
        if mode != "transcribe":
            return None

        runtime_device = str(options.get("transcribe_runtime_device") or "").strip().lower()
        requested_device = str(options.get("transcribe_device") or "").strip().lower()
        backend = str(options.get("transcribe_backend_resolved") or options.get("transcribe_backend") or "").strip().lower()

        if runtime_device == "cuda":
            return "gpu_cuda"
        if runtime_device == "cpu":
            if requested_device == "cuda" and backend in ("faster_whisper", "whisperx"):
                return "gpu_no_cuda"
            return "cpu"
        if requested_device == "cpu":
            return "cpu"
        if requested_device == "cuda":
            return "gpu_cuda"
        if backend == "whisper_cpp":
            return "cpu"
        return None

    def _set_runtime_hint(self, job_id, mode):
        key = self._normalize_process_accel_mode(mode)
        if not key:
            return False
        with self.lock:
            prev = self.runtime_hints.get(job_id)
            self.runtime_hints[job_id] = key
        return prev != key

    def _runtime_hint_for(self, job_id):
        with self.lock:
            return self.runtime_hints.get(job_id)

    def _inject_runtime_hint(self, job):
        if not job:
            return job
        out = dict(job)
        mode = self._runtime_hint_for(out.get("id"))
        if not mode:
            try:
                opts = json.loads(out.get("options") or "{}")
            except Exception:
                opts = {}
            mode = self._infer_process_accel_mode(opts)
            if mode:
                self._set_runtime_hint(out.get("id"), mode)
        if mode:
            out["process_accel_mode"] = mode
            out["process_accel_label"] = self._process_accel_label(mode)
        return out

    def _stage_latest_file(self, project_dir, stage_name, stem_hint, ext):
        if not project_dir or not os.path.isdir(project_dir):
            return None
        stage_dir = os.path.join(project_dir, stage_name)
        if not os.path.isdir(stage_dir):
            return None
        candidates = []
        for name in os.listdir(stage_dir):
            if not name.lower().endswith(ext.lower()):
                continue
            if stem_hint and stem_hint.lower() not in name.lower():
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

    def _stage_latest_chunk_checkpoint(self, project_dir, stem_hint):
        if not project_dir or not os.path.isdir(project_dir):
            return None
        stage_dir = os.path.join(project_dir, "transcricao")
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

    def _extract_stage_from_message(self, message):
        msg = str(message or "")
        m = re.search(r"Etapa\s+(\d+)\/(\d+):\s*([^()]+)", msg, flags=re.IGNORECASE)
        if not m:
            return None
        label = m.group(3).strip().lower()
        if "convers" in label and "wav" in label:
            return "convert"
        if "normal" in label:
            return "normalize"
        if "vad" in label:
            return "vad"
        if "transcri" in label:
            return "transcribe"
        if "junc" in label or "merge" in label:
            return "merge"
        return None

    def _prepare_resume_options(self, job):
        try:
            options = json.loads(job.get("options") or "{}")
        except Exception:
            return job.get("options")
        if str(job.get("mode") or "") != "transcribe":
            return job.get("options")

        project_dir = options.get("project_dir")
        if not project_dir or not os.path.isdir(project_dir):
            return job.get("options")

        stage = self._extract_stage_from_message(job.get("message")) or "transcribe"
        options["redo_from"] = stage

        input_path = job.get("input_path") or ""
        stem_hint = ""
        if input_path:
            stem_hint = os.path.splitext(os.path.basename(input_path))[0]
        elif job.get("title"):
            stem_hint = str(job.get("title"))

        options["reuse_wav_raw"] = self._stage_latest_file(project_dir, "convertido", stem_hint, ".wav")
        options["reuse_wav_norm"] = self._stage_latest_file(project_dir, "normalizacao", stem_hint, ".wav")
        options["reuse_wav_vad"] = self._stage_latest_file(project_dir, "vad", stem_hint, ".wav")
        if stage == "transcribe":
            checkpoint_path = self._stage_latest_chunk_checkpoint(project_dir, stem_hint)
            if checkpoint_path:
                options["redo_chunk_checkpoint"] = checkpoint_path
                options["transcribe_resume_chunks"] = "on"
                options["transcribe_resume_origin"] = "checkpoint"
                options.pop("redo_chunk_index", None)
                options.pop("transcribe_resume_from_chunk_index", None)
                options.pop("resume_live_path", None)
            else:
                options.pop("redo_chunk_checkpoint", None)
                inferred = trecov.infer_chunk_from_app_log(input_path)
                inferred_idx = (inferred or {}).get("suggested_chunk_index")
                if inferred_idx is not None:
                    options["transcribe_resume_chunks"] = "on"
                    options["transcribe_resume_from_chunk_index"] = int(inferred_idx)
                    options["transcribe_resume_origin"] = "log"
                    output_dir = options.get("output_dir") or options.get("transcribe_output_dir")
                    live_path = tio.get_live_path(job.get("id"), output_dir) if output_dir else None
                    if live_path and os.path.isfile(live_path):
                        options["resume_live_path"] = live_path
                else:
                    options.pop("transcribe_resume_chunks", None)
                    options.pop("transcribe_resume_from_chunk_index", None)
                    options.pop("transcribe_resume_origin", None)
                    options.pop("resume_live_path", None)
        else:
            options.pop("redo_chunk_checkpoint", None)
            options.pop("redo_chunk_index", None)
            options.pop("transcribe_resume_chunks", None)
            options.pop("transcribe_resume_from_chunk_index", None)
            options.pop("transcribe_resume_origin", None)
            options.pop("resume_live_path", None)
        return json.dumps(options, ensure_ascii=False)

    def _load_jobs_from_db(self):
        with get_cursor() as cur:
            cur.execute("SELECT * FROM jobs ORDER BY created_at ASC")
            rows = [dict(r) for r in cur.fetchall()]

        for row in rows:
            row["cancel"] = threading.Event()
            self.jobs[row["id"]] = row

        for job in rows:
            status = job.get("status")
            if status == "group":
                continue
            if status in ("queued", "running"):
                updates = {"status": "queued", "message": "Retomando apos reinicio", "pid": None}
                resumed_options = self._prepare_resume_options(job)
                if resumed_options and resumed_options != job.get("options"):
                    updates["options"] = resumed_options
                    job["options"] = resumed_options
                self._update_job(job["id"], **updates)
                job.update(updates)
                self.queue.put(job["id"])
            elif status == "paused":
                self._update_job(job["id"], pid=None)
                job["pid"] = None

    def register_client(self):
        q = queue.Queue()
        with self.lock:
            self.clients.append(q)
            backlog_events = self._get_backlog_events()
        for event in backlog_events:
            q.put(event)
        return q

    def unregister_client(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def broadcast(self, event):
        job = event.get("job")
        if job and job.get("id"):
            self._append_backlog(job["id"], event)
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            q.put(event)

    def _append_backlog(self, job_id, event, limit=50):
        with self.lock:
            self.backlog.setdefault(job_id, []).append(event)
            if len(self.backlog[job_id]) > limit:
                self.backlog[job_id] = self.backlog[job_id][-limit:]

    def _clear_backlog(self, job_ids):
        with self.lock:
            for jid in job_ids:
                if jid in self.backlog:
                    del self.backlog[jid]

    def _get_backlog_events(self):
        events = []
        for items in self.backlog.values():
            events.extend(items)
        return events

    def create_parent_job(self, url, options):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "created_at": now,
            "updated_at": now,
            "status": "group",
            "mode": options.get("mode"),
            "url": url,
            "source_type": "group",
            "input_path": None,
            "output_path": None,
            "title": None,
            "channel": None,
            "percent": 0.0,
            "speed": None,
            "eta": None,
            "size": None,
            "message": "Agrupado",
            "options": json.dumps(options, ensure_ascii=False),
            "parent_job_id": None,
            "pid": None,
            "last_event_at": None,
            "cancel": threading.Event(),
        }
        self.jobs[job_id] = job
        self._insert_job(job)
        self.broadcast({"type": "job", "job": self.get_job(job_id)})
        return job_id

    def create_jobs(self, items, options, source_type="url", input_paths=None, parent_job_id=None, meta=None):
        created = []
        input_paths = input_paths or []
        meta = meta or []
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for idx, item in enumerate(items):
            item_meta = meta[idx] if idx < len(meta) else {}
            job_id = str(uuid.uuid4())
            job = {
                "id": job_id,
                "created_at": now,
                "updated_at": now,
                "status": "queued",
                "mode": options.get("mode"),
                "url": item if source_type == "url" else None,
                "source_type": source_type,
                "input_path": input_paths[idx] if source_type == "local" else None,
                "output_path": None,
                "title": item_meta.get("title"),
                "channel": item_meta.get("channel"),
                "percent": 0.0,
                "speed": None,
                "eta": None,
                "size": None,
                "message": "Na fila",
                "options": json.dumps(options, ensure_ascii=False),
                "parent_job_id": parent_job_id,
                "pid": None,
                "last_event_at": None,
                "cancel": threading.Event(),
            }
            self.jobs[job_id] = job
            self._insert_job(job)
            self.queue.put(job_id)
            created.append(job_id)
        return created

    def cancel_job(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return False
        job["cancel"].set()
        pid = job.get("pid")
        if pid:
            kill_process_tree(pid)
        if job["status"] == "queued":
            self._update_job(job_id, status="canceled", message="Cancelado", percent=0)
            self.broadcast({"type": "job", "job": self.get_job(job_id)})
        elif job["status"] == "running":
            self._update_job(job_id, status="canceled", message="Cancelado", percent=job.get("percent") or 0)
            self.broadcast({"type": "job", "job": self.get_job(job_id)})
        return True

    def pause_job(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.get("status") == "running":
            if job.get("pid"):
                suspend_process(job["pid"])
            self._update_job(job_id, status="paused", message="Pausado")
            self.broadcast({"type": "job", "job": self.get_job(job_id)})
            return True
        if job.get("status") == "queued":
            self._update_job(job_id, status="paused", message="Pausado")
            self.broadcast({"type": "job", "job": self.get_job(job_id)})
            return True
        return False

    def resume_job(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.get("status") == "paused":
            if job.get("pid"):
                resume_process(job["pid"])
                self._update_job(job_id, status="running", message="Retomado")
            else:
                self._update_job(job_id, status="queued", message="Na fila")
                self.queue.put(job_id)
            self.broadcast({"type": "job", "job": self.get_job(job_id)})
            return True
        return False

    def start_now(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.get("status") not in ("queued", "paused"):
            return False
        self._update_job(job_id, status="queued", message="Na fila (prioridade)")
        with self.lock:
            if job_id not in self.priority:
                self.priority.appendleft(job_id)
        self.broadcast({"type": "job", "job": self.get_job(job_id)})
        return True

    def start_group(self, parent_id):
        with get_cursor() as cur:
            cur.execute(
                "SELECT id FROM jobs WHERE parent_job_id = ? AND status IN ('queued','paused') ORDER BY created_at ASC",
                (parent_id,),
            )
            rows = cur.fetchall()
        if not rows:
            return False
        with self.lock:
            for row in rows[::-1]:
                jid = row[0]
                if jid not in self.priority:
                    self.priority.appendleft(jid)
        for row in rows:
            self._update_job(row[0], status="queued", message="Na fila (prioridade)")
        self.broadcast({"type": "group_start", "parent_job_id": parent_id})
        return True

    def repeat_job(self, job_id):
        job = self.get_job(job_id)
        if not job:
            return None
        options = json.loads(job.get("options") or "{}")
        url = job.get("url")
        if job.get("source_type") == "url" and url:
            return self.create_jobs([url], options, source_type="url")
        return None

    def delete_job(self, job_id):
        job = self.get_job(job_id)
        if not job:
            return True
        removed_ids = [job_id]
        # always request cancel first; DB status may be stale during races
        self.cancel_job(job_id)
        if job.get("status") == "group":
            with get_cursor() as cur:
                cur.execute("SELECT id FROM jobs WHERE parent_job_id = ?", (job_id,))
                child_rows = cur.fetchall()
            for row in child_rows:
                removed_ids.append(row[0])
                self.cancel_job(row[0])
            with get_cursor() as cur:
                cur.execute("DELETE FROM jobs WHERE parent_job_id = ?", (job_id,))
                cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            with self.lock:
                if job_id in self.jobs:
                    del self.jobs[job_id]
                child_ids = [row[0] for row in child_rows]
                for jid in child_ids:
                    if jid in self.jobs:
                        del self.jobs[jid]
                    self.runtime_hints.pop(jid, None)
                self.runtime_hints.pop(job_id, None)
                self.priority = deque([jid for jid in self.priority if jid != job_id and jid not in child_ids])
            self._clear_backlog(removed_ids)
            for jid in removed_ids:
                self.broadcast({"type": "job_removed", "job_id": jid})
            return True
        with get_cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        with self.lock:
            if job_id in self.jobs:
                del self.jobs[job_id]
            self.runtime_hints.pop(job_id, None)
            self.priority = deque([jid for jid in self.priority if jid != job_id])
        self._clear_backlog(removed_ids)
        self.broadcast({"type": "job_removed", "job_id": job_id})
        return True

    def pause_queue(self):
        self.paused.set()

    def resume_queue(self):
        self.paused.clear()

    def cancel_queue(self):
        jobs = self.list_jobs()
        for job in jobs:
            if job.get("status") in ("queued", "running", "paused"):
                self.cancel_job(job["id"])
        return True

    def clear_queue(self):
        jobs = self.list_jobs()
        groups = [j for j in jobs if j.get("status") == "group"]
        singles = [j for j in jobs if j.get("status") != "group"]
        for job in groups:
            self.delete_job(job["id"])
        for job in singles:
            self.delete_job(job["id"])
        return True

    def get_job(self, job_id):
        with get_cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return self._inject_runtime_hint(dict(row)) if row else None

    def list_jobs(self):
        with get_cursor() as cur:
            cur.execute("SELECT * FROM jobs ORDER BY created_at DESC")
            return [self._inject_runtime_hint(dict(row)) for row in cur.fetchall()]

    def _insert_job(self, job):
        sql = """
            INSERT INTO jobs(
                id, created_at, updated_at, status, mode, url, source_type, input_path,
                output_path, title, channel, percent, speed, eta, size, message, options,
                parent_job_id, pid, last_event_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            job["id"],
            job["created_at"],
            job["updated_at"],
            job["status"],
            job["mode"],
            job["url"],
            job["source_type"],
            job["input_path"],
            job["output_path"],
            job["title"],
            job["channel"],
            job["percent"],
            job["speed"],
            job["eta"],
            job["size"],
            job["message"],
            job["options"],
            job["parent_job_id"],
            job["pid"],
            job["last_event_at"],
        )
        for attempt in range(6):
            try:
                with get_cursor() as cur:
                    cur.execute(sql, params)
                return
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc).lower() and attempt < 5:
                    time.sleep(0.15 * (attempt + 1))
                    continue
                raise

    def _update_job(self, job_id, **updates):
        updates["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        keys = ", ".join([f"{k} = ?" for k in updates])
        values = list(updates.values()) + [job_id]
        for attempt in range(6):
            try:
                with get_cursor() as cur:
                    cur.execute(f"UPDATE jobs SET {keys} WHERE id = ?", values)
                return
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc).lower() and attempt < 5:
                    time.sleep(0.15 * (attempt + 1))
                    continue
                raise

    def _worker_loop(self):
        while True:
            job_id = None
            with self.lock:
                if self.priority:
                    job_id = self.priority.popleft()
            if job_id is None:
                job_id = self.queue.get()
            while self.paused.is_set():
                time.sleep(0.5)
            job = self.jobs.get(job_id)
            if not job:
                continue
            if job["cancel"].is_set():
                continue
            if job.get("status") == "paused":
                self.queue.put(job_id)
                time.sleep(0.5)
                continue
            if job.get("status") == "group":
                continue
            self._run_job(job)

    def _run_job(self, job):
        logger = logging.getLogger("jobs")
        self._update_job(job["id"], status="running", message="Iniciando")
        self.broadcast({"type": "job", "job": self.get_job(job["id"])})
        options = json.loads(job["options"]) if job.get("options") else {}
        initial_accel = self._infer_process_accel_mode(options)
        if self._set_runtime_hint(job["id"], initial_accel):
            logger.info("process_accel_changed job_id=%s mode=%s", job["id"], initial_accel)

        def pid_cb(pid):
            if pid:
                try:
                    self._update_job(job["id"], pid=pid)
                except Exception:
                    logger.warning("pid update failed job_id=%s pid=%s", job["id"], pid, exc_info=True)

        def progress_cb(payload):
            if job["cancel"].is_set():
                return
            current = self.get_job(job["id"]) or {}
            accel_mode = self._normalize_process_accel_mode(
                (payload or {}).get("process_accel_mode") or (payload or {}).get("runtime_accel")
            )
            if not accel_mode:
                accel_mode = self._infer_process_accel_mode(options)
            if self._set_runtime_hint(job["id"], accel_mode):
                logger.info("process_accel_changed job_id=%s mode=%s", job["id"], accel_mode)
            percent = payload.get("percent")
            if percent is None and payload.get("downloaded_bytes") and payload.get("total_bytes"):
                try:
                    percent = (payload["downloaded_bytes"] / payload["total_bytes"]) * 100.0
                except Exception:
                    percent = None
            message = payload.get("message")
            speed = payload.get("speed")
            eta = payload.get("eta_seconds")
            size = payload.get("total_bytes")
            if size is None:
                size = payload.get("downloaded_bytes")
            mix_metrics = payload.get("mix_metrics")
            if isinstance(mix_metrics, dict):
                def _safe_metric(value, fallback):
                    if value is None:
                        return float(fallback)
                    try:
                        return float(str(value))
                    except Exception:
                        return float(fallback)

                elapsed = _safe_metric(mix_metrics.get("elapsed_seconds"), 0.0)
                remaining = _safe_metric(mix_metrics.get("remaining_seconds"), -1.0)
                total_estimated = _safe_metric(mix_metrics.get("total_estimated_seconds"), -1.0)
                overall_pct = _safe_metric(mix_metrics.get("overall_percent"), percent if percent is not None else 0.0)
                stage_pct = _safe_metric(mix_metrics.get("stage_percent"), 0.0)
                stage_idx = int(_safe_metric(mix_metrics.get("stage_index"), 0.0))
                stage_total = int(_safe_metric(mix_metrics.get("stage_total"), 0.0))
                eta_raw = mix_metrics.get("eta_seconds")
                eta_m = _safe_metric(eta_raw, -1.0) if eta_raw is not None else -1.0
                eta_final = eta_m if eta_m >= 0.0 else None
                stalled = _safe_metric(mix_metrics.get("stalled_seconds"), 0.0)
                speed = (
                    f"mix:{elapsed:.3f}|{remaining:.3f}|{total_estimated:.3f}|"
                    f"{overall_pct:.3f}|{stage_idx}|{stage_total}|{stage_pct:.3f}|"
                    f"{(eta_final if eta_final is not None else -1.0):.3f}|{stalled:.3f}"
                )
                eta = eta_final
            metrics = payload.get("transcribe_metrics")
            if not isinstance(mix_metrics, dict) and isinstance(metrics, dict):
                def _safe_metric(value, fallback):
                    if value is None:
                        return float(fallback)
                    try:
                        return float(str(value))
                    except Exception:
                        return float(fallback)

                elapsed = _safe_metric(metrics.get("elapsed_seconds"), 0.0)
                current_audio = _safe_metric(metrics.get("current_audio_seconds"), 0.0)
                remaining_audio = _safe_metric(metrics.get("remaining_audio_seconds"), 0.0)
                total_audio = _safe_metric(metrics.get("total_audio_seconds"), 0.0)
                eta_raw = metrics.get("eta_seconds")
                eta_m = _safe_metric(eta_raw, -1.0) if eta_raw is not None else -1.0
                eta_final = eta_m if eta_m >= 0.0 else None
                speed = f"trx:{elapsed:.3f}|{current_audio:.3f}|{remaining_audio:.3f}|{total_audio:.3f}|{(eta_final if eta_final is not None else -1.0):.3f}"
                eta = eta_final
                size = remaining_audio
            try:
                self._update_job(
                    job["id"],
                    percent=percent if percent is not None else current.get("percent"),
                    speed=speed if speed is not None else current.get("speed"),
                    eta=eta if eta is not None else current.get("eta"),
                    size=size if size is not None else current.get("size"),
                    message=message or current.get("message"),
                    last_event_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                self.broadcast({"type": "job", "job": self.get_job(job["id"])})
            except Exception:
                logger.warning("progress update failed job_id=%s", job["id"], exc_info=True)

        try:
            output_path, meta = process_job(job, options, progress_cb, job["cancel"], logger, pid_cb)
            if job["cancel"].is_set():
                self._update_job(job["id"], status="canceled", message="Cancelado")
            else:
                self._update_job(
                    job["id"],
                    status="done",
                    message="Concluído",
                    percent=100.0,
                    output_path=output_path,
                    title=meta.get("title"),
                    channel=meta.get("channel"),
                    pid=None,
                )
        except Exception as exc:
            msg = str(exc or "").strip().lower()
            canceled = job["cancel"].is_set() or msg == "cancelado" or msg.startswith("cancelado ")
            if canceled:
                logger.info("Job canceled job_id=%s", job["id"])
                self._update_job(job["id"], status="canceled", message="Cancelado", pid=None)
            else:
                logger.exception("Job failed")
                self._update_job(job["id"], status="error", message=str(exc), pid=None)
        finally:
            self.broadcast({"type": "job", "job": self.get_job(job["id"])})
