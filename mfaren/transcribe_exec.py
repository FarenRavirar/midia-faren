import os
import re
import subprocess
import threading
import time
import queue as py_queue
from collections import deque

from .ffmpeg import find_ffmpeg, normalize_ffmpeg_progress
from .progress import parse_ffmpeg_progress
from . import transcribe_glossary as tgloss
from . import transcribe_io as tio
from . import transcribe_postprocess as tpost


def run_ffmpeg_stage(
    cmd,
    duration,
    stage_label,
    progress_cb=None,
    cancel_event=None,
    pid_cb=None,
    report_cb=None,
    logger=None,
):
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    popen_kwargs = tio.get_popen_windows_kwargs()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **popen_kwargs,
    )
    if pid_cb:
        pid_cb(proc.pid)
    if logger:
        logger.info("stage_start engine=ffmpeg stage=%s pid=%s cmd=%s", stage_label, proc.pid, tio.summarize_cmd(cmd))

    stall_timeout, hard_timeout = tio.stage_timeouts("ffmpeg", duration)
    started_at = time.time()
    last_progress_at = started_at
    last_heartbeat_at = started_at
    last_payload = {
        "percent": 0.0,
        "speed": None,
        "eta_seconds": None,
        "downloaded_bytes": None,
        "total_bytes": None,
        "message": f"{stage_label} (0.0%)",
    }

    q = py_queue.Queue()

    def _reader():
        if not proc.stdout:
            q.put(None)
            return
        try:
            for line in proc.stdout:
                q.put(line)
        finally:
            q.put(None)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    ff_progress = {}
    tail_lines = []
    timed_out_reason = None
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
                if now - last_progress_at > stall_timeout:
                    timed_out_reason = f"timeout sem progresso ({int(stall_timeout)}s)"
                    proc.terminate()
                    break
                if progress_cb and (now - last_progress_at >= tio.HEARTBEAT_SECONDS) and (
                    now - last_heartbeat_at >= tio.HEARTBEAT_SECONDS
                ):
                    progress_cb(dict(last_payload))
                    last_heartbeat_at = now
                continue
            if line is None:
                break
            parsed = parse_ffmpeg_progress(line)
            if not parsed:
                raw = (line or "").strip()
                if raw:
                    tail_lines.append(raw)
                    if len(tail_lines) > 12:
                        tail_lines = tail_lines[-12:]
                continue
            ff_progress.update(parsed)
            if not parsed.get("progress"):
                continue
            normalized = normalize_ffmpeg_progress(ff_progress, duration=duration)
            ff_progress = {}
            percent = normalized.get("percent") or 0.0
            if report_cb:
                report_cb(stage_label, percent)
            normalized["message"] = f"{stage_label} ({percent:.1f}%)"
            last_payload = normalized
            last_progress_at = time.time()
            if progress_cb:
                progress_cb(normalized)
    finally:
        if proc.stdout:
            proc.stdout.close()

    rc = proc.wait()
    elapsed = time.time() - started_at
    if logger:
        logger.info("stage_end engine=ffmpeg stage=%s pid=%s rc=%s elapsed=%.1fs", stage_label, proc.pid, rc, elapsed)
    if timed_out_reason:
        raise RuntimeError(f"Falha na etapa: {stage_label} ({timed_out_reason})")
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("Cancelado")
    if rc != 0:
        detail = " | ".join(tail_lines[-5:]) if tail_lines else "sem detalhe"
        if logger:
            logger.error("stage_fail engine=ffmpeg stage=%s pid=%s rc=%s detail=%s", stage_label, proc.pid, rc, detail)
        raise RuntimeError(f"Falha na etapa: {stage_label} ({detail})")


def convert_to_wav(input_path, output_path, duration, progress_cb=None, cancel_event=None, pid_cb=None, report_cb=None, logger=None):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg nao encontrado")
    cmd = [ffmpeg, "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", output_path]
    run_ffmpeg_stage(cmd, duration, "Conversao para WAV", progress_cb, cancel_event, pid_cb, report_cb, logger=logger)


def normalize_audio(input_path, output_path, duration, progress_cb=None, cancel_event=None, pid_cb=None, report_cb=None, logger=None):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg nao encontrado")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        input_path,
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        output_path,
    ]
    run_ffmpeg_stage(cmd, duration, "Normalizacao", progress_cb, cancel_event, pid_cb, report_cb, logger=logger)


def apply_vad(input_path, output_path, duration, options, progress_cb=None, cancel_event=None, pid_cb=None, report_cb=None, logger=None):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg nao encontrado")
    threshold = options.get("vad_threshold", "-30")
    min_silence = options.get("vad_min_silence", "0.3")
    filter_str = (
        f"silenceremove=start_periods=1:start_threshold={threshold}dB:start_silence=0.1:"
        f"stop_periods=1:stop_threshold={threshold}dB:stop_silence={min_silence}"
    )
    cmd = [ffmpeg, "-y", "-i", input_path, "-af", filter_str, output_path]
    run_ffmpeg_stage(cmd, duration, "Executando VAD", progress_cb, cancel_event, pid_cb, report_cb, logger=logger)


CTRL_C_EXIT_CODE_WIN = tio.CTRL_C_EXIT_CODE_WIN


def run_whisper(
    wav_path,
    output_base,
    model,
    options,
    duration,
    progress_cb=None,
    cancel_event=None,
    pid_cb=None,
    report_cb=None,
    live_writer=None,
    logger=None,
):
    whisper_exe = tio.find_whisper_exe()
    if not whisper_exe:
        raise RuntimeError("Executavel do whisper nao encontrado")

    language = options.get("language", "pt") or "pt"
    threads = str(options.get("threads") or "6")
    beam_size = str(options.get("beam_size") or "5")
    max_len = str(options.get("max_len") or "42")
    diarize = str(options.get("diarize") or "off").lower() in ("1", "true", "on", "yes", "sim", "s")
    guidance_prompt = str(options.get("transcribe_guidance_prompt") or "").strip()

    cmd = [
        whisper_exe,
        "-m",
        model,
        "-f",
        wav_path,
        "-l",
        language,
        "-t",
        threads,
        "-bs",
        beam_size,
        "-ml",
        max_len,
        "-sow",
        "-osrt",
        "-otxt",
        "-of",
        output_base,
    ]
    if diarize:
        cmd.append("-tdrz")
    if guidance_prompt and logger:
        logger.info("whisper_cpp_context_prompt_ignored len=%s", len(guidance_prompt))

    popen_kwargs = tio.get_popen_windows_kwargs()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
    )
    if pid_cb:
        pid_cb(proc.pid)
    if logger:
        logger.info("stage_start engine=whisper stage=Transcricao pid=%s cmd=%s", proc.pid, tio.summarize_cmd(cmd))
    if report_cb:
        report_cb("Transcricao", 0.0)
    if progress_cb:
        progress_cb(
            {
                "percent": 0.0,
                "speed": None,
                "eta_seconds": None,
                "downloaded_bytes": None,
                "total_bytes": None,
                "message": "Etapa 4/5: Transcricao (0.0%)",
            }
        )

    start_time = time.time()
    try:
        total_duration = max(1.0, float(duration or 0.0))
    except Exception:
        total_duration = 1.0
    source_duration = total_duration
    max_ts = source_duration * 1.20
    last_end = 0.0
    stall_timeout, hard_timeout = tio.stage_timeouts("whisper", duration)
    last_progress_at = start_time
    last_heartbeat_at = start_time
    timed_out_reason = None
    terminated_for_hallucination = False
    last_payload = {
        "percent": 0.0,
        "speed": None,
        "eta_seconds": None,
        "downloaded_bytes": None,
        "total_bytes": None,
        "message": "Etapa 4/5: Transcricao (0.0%)",
    }
    pattern = re.compile(r"\[(\d{2}):(\d{2}):(\d{2}\.\d{3}) --> (\d{2}):(\d{2}):(\d{2}\.\d{3})\]")
    q = py_queue.Queue()
    tail_lines = []
    hall_norms = deque(maxlen=48)
    out_of_range_streak = 0

    def _reader():
        if not proc.stdout:
            q.put(None)
            return
        try:
            for line in proc.stdout:
                q.put(line)
        finally:
            q.put(None)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    try:
        while True:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                break
            try:
                line = q.get(timeout=0.5)
            except py_queue.Empty:
                now = time.time()
                if now - start_time > hard_timeout:
                    timed_out_reason = f"timeout total ({int(hard_timeout)}s)"
                    proc.terminate()
                    break
                if now - last_progress_at > stall_timeout:
                    timed_out_reason = f"timeout sem progresso ({int(stall_timeout)}s)"
                    proc.terminate()
                    break
                if progress_cb and (now - last_progress_at >= tio.HEARTBEAT_SECONDS) and (
                    now - last_heartbeat_at >= tio.HEARTBEAT_SECONDS
                ):
                    progress_cb(dict(last_payload))
                    last_heartbeat_at = now
                continue
            if line is None:
                break
            match = pattern.search(line)
            if not match:
                raw = (line or "").strip()
                if raw:
                    tail_lines.append(raw)
                    if len(tail_lines) > 16:
                        tail_lines = tail_lines[-16:]
                continue

            h1, m1, s1 = float(match.group(1)), float(match.group(2)), float(match.group(3))
            h2, m2, s2 = float(match.group(4)), float(match.group(5)), float(match.group(6))
            start_time_seg = h1 * 3600 + m1 * 60 + s1
            end_time = h2 * 3600 + m2 * 60 + s2

            if end_time > max_ts:
                out_of_range_streak += 1
                if out_of_range_streak >= 12:
                    terminated_for_hallucination = True
                    if logger:
                        logger.warning(
                            "whisper_hallucination_abort reason=timestamp_out_of_range end=%.2fs limit=%.2fs pid=%s",
                            end_time,
                            max_ts,
                            proc.pid,
                        )
                    proc.terminate()
                    break
                continue
            out_of_range_streak = 0

            text = line.split("]", 1)[-1].strip()
            if live_writer:
                live_writer.handle_segment(start_time_seg, end_time, text)

            norm = tpost.normalize_for_repeat(text)
            if norm:
                hall_norms.append(norm)
                if len(hall_norms) >= 24:
                    counts = {}
                    for n in hall_norms:
                        counts[n] = counts.get(n, 0) + 1
                    uniq = len(counts)
                    top = max(counts.values()) if counts else 0
                    if uniq <= 3 and top >= int(len(hall_norms) * 0.6):
                        terminated_for_hallucination = True
                        if logger:
                            logger.warning(
                                "whisper_hallucination_abort reason=repetition_window uniq=%s top=%s/%s pid=%s",
                                uniq,
                                top,
                                len(hall_norms),
                                proc.pid,
                            )
                        proc.terminate()
                        break

            if end_time <= last_end:
                continue

            last_end = end_time
            percent = min(99.5, max(0.0, (last_end / total_duration) * 100.0))
            elapsed = time.time() - start_time
            eta = None
            if last_end > 0.1:
                remaining = max(total_duration - last_end, 0.0)
                eta = (elapsed / last_end) * remaining
            if report_cb:
                report_cb("Transcricao", percent)
            if progress_cb:
                payload = {
                    "percent": percent,
                    "speed": None,
                    "eta_seconds": eta,
                    "downloaded_bytes": None,
                    "total_bytes": None,
                    "message": f"Transcricao ({os.path.basename(model)}) {percent:.1f}%",
                }
                progress_cb(payload)
                last_payload = payload
            last_progress_at = time.time()
    finally:
        if proc.stdout:
            proc.stdout.close()

    rc = proc.wait()
    elapsed = time.time() - start_time
    if logger:
        logger.info("stage_end engine=whisper stage=Transcricao pid=%s rc=%s elapsed=%.1fs", proc.pid, rc, elapsed)
    if timed_out_reason:
        raise RuntimeError(f"Falha na transcricao ({timed_out_reason})")
    if terminated_for_hallucination:
        raise RuntimeError("Falha na transcricao (loop detectado)")
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("Cancelado")

    if rc != 0:
        detail = " | ".join(tail_lines[-6:]) if tail_lines else "sem detalhe"
        if logger:
            logger.error(
                "stage_fail engine=whisper stage=Transcricao pid=%s rc=%s detail=%s",
                proc.pid,
                rc,
                detail,
            )
        if rc == CTRL_C_EXIT_CODE_WIN:
            raise RuntimeError(f"Falha na transcricao (processo interrompido: rc=0x{rc:08X})")
        raise RuntimeError(f"Falha na transcricao (rc={rc} | {detail})")

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
                "message": f"Transcricao ({os.path.basename(model)}) 100.0%",
            }
        )

class LiveWriter:
    def __init__(self, path, diarize_on, timestamp, logger=None):
        self.path = path
        self.diarize_on = diarize_on
        self.logger = logger
        self.speaker = 1
        self.speaker_name = "Falante 1"
        self.glossary_rules = []
        self.glossary_loader = None
        self.glossary_refresh_interval = 2.0
        self._glossary_last_refresh = 0.0
        self._glossary_last_signature = tuple()
        self.buffer = []
        self.chars = 0
        self.block_start = None
        self.last_end = 0.0
        self.model_tag = None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(f"=== INICIO DA TRANSCRICAO: {timestamp} ===\n")
            f.write(f"Modelo: (ao vivo) | Diarizacao: {bool(diarize_on)}\n")
            f.write("==========================================\n\n")

    def set_model(self, model_tag):
        if model_tag and model_tag != self.model_tag:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"\n=== MODELO: {model_tag} ===\n")
            self.model_tag = model_tag

    def set_speaker_name(self, speaker_name):
        name = str(speaker_name or "").strip()
        if name:
            self.speaker_name = name

    @staticmethod
    def _glossary_signature(rules):
        signature = []
        for rule in list(rules or []):
            signature.append((str(rule.get("source") or ""), str(rule.get("target") or "")))
        return tuple(signature)

    def set_glossary_rules(self, rules):
        normalized = list(rules or [])
        self.glossary_rules = normalized
        self._glossary_last_signature = self._glossary_signature(normalized)

    def set_glossary_loader(self, loader, refresh_interval=2.0):
        self.glossary_loader = loader if callable(loader) else None
        try:
            interval = float(refresh_interval)
        except Exception:
            interval = 2.0
        self.glossary_refresh_interval = max(0.2, interval)
        self._glossary_last_refresh = 0.0
        self.refresh_glossary(force=True)

    def refresh_glossary(self, force=False):
        if not self.glossary_loader:
            return
        now = time.monotonic()
        if not force and (now - self._glossary_last_refresh) < self.glossary_refresh_interval:
            return
        self._glossary_last_refresh = now

        try:
            loaded = self.glossary_loader()
        except Exception:
            if self.logger:
                self.logger.warning("glossary_reload_failed", exc_info=True)
            return

        if isinstance(loaded, list) and all(isinstance(item, dict) for item in loaded):
            rules = list(loaded)
        else:
            rules = tgloss.parse_glossary(loaded)

        signature = self._glossary_signature(rules)
        if signature == self._glossary_last_signature:
            return

        self.glossary_rules = rules
        self._glossary_last_signature = signature
        if self.logger:
            self.logger.info("glossary_reload_applied rules=%s", len(rules))

    def get_glossary_rules(self):
        return list(self.glossary_rules)

    def _flush_block(self, end_time):
        if not self.buffer or self.block_start is None:
            return
        label = f"Falante {self.speaker}" if self.diarize_on else self.speaker_name
        line = f"[{tpost.format_time(self.block_start)} --> {tpost.format_time(end_time)}] {label} — {' '.join(self.buffer)}"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{line}\n")
            f.flush()
        self.speaker = 2 if self.speaker == 1 else 1
        self.buffer = []
        self.chars = 0
        self.block_start = end_time

    def handle_segment(self, start, end, text):
        if not text:
            return
        self.refresh_glossary(force=False)
        text = tgloss.apply_glossary(text, self.glossary_rules)
        if self.block_start is None:
            self.block_start = start
        gap = start - self.last_end
        if gap > 0.4 and self.buffer:
            self._flush_block(self.last_end)
        if self.buffer:
            self.chars += 1 + len(text)
        else:
            self.chars += len(text)
        self.buffer.append(text)
        duration = end - (self.block_start or start)
        if self.chars >= 300 or duration >= 30:
            self._flush_block(end)
        self.last_end = end

    def finalize(self):
        if self.buffer and self.block_start is not None:
            self._flush_block(self.last_end)






