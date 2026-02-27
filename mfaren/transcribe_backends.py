import importlib
import importlib.metadata as importlib_metadata
from importlib import util as importlib_util
import logging
import os
import threading
import time
import warnings
from collections import deque

from . import transcribe_exec as texec
from . import transcribe_io as tio
from . import transcribe_postprocess as tpost


SUPPORTED_BACKENDS = ("whisper_cpp", "faster_whisper", "whisperx")
DEFAULT_BACKEND = "faster_whisper"

_MODEL_LOCK = threading.Lock()
_FASTER_MODEL_CACHE = {}
_WHISPERX_MODEL_CACHE = {}
_WHISPERX_ALIGN_CACHE = {}
_TORCHCODEC_PROBE_DONE = False
_THIRD_PARTY_LOGGERS_CONFIGURED = False
_WARNING_FILTERS_CONFIGURED = False

# Filtros aplicados cedo (import-time) para evitar poluicao massiva de log
# durante o carregamento do stack WhisperX/Pyannote em Windows.
warnings.filterwarnings(
    "ignore",
    message=r"(?s).*torchcodec is not installed correctly.*",
    category=UserWarning,
    module=r"pyannote\.audio\.core\.io",
)
warnings.filterwarnings(
    "ignore",
    message=r"(?s).*TensorFloat-32 \(TF32\) has been disabled.*",
    category=Warning,
    module=r"pyannote\.audio\.utils\.reproducibility",
)


def normalize_backend_name(value):
    name = str(value or "").strip().lower()
    if name in SUPPORTED_BACKENDS:
        return name
    return DEFAULT_BACKEND


def resolve_backend(requested, logger=None):
    wanted = normalize_backend_name(requested)
    if wanted == "faster_whisper":
        if _has_faster_whisper():
            return "faster_whisper", None
        if _has_whisper_cpp():
            reason = "faster-whisper indisponivel; fallback para whisper_cpp"
            if logger:
                logger.warning(reason)
            return "whisper_cpp", reason
        raise RuntimeError("Backend faster-whisper indisponivel e whisper_cpp nao encontrado")

    if wanted == "whisperx":
        if _has_whisperx():
            return "whisperx", None
        if _has_faster_whisper():
            reason = "whisperx indisponivel; fallback para faster_whisper"
            if logger:
                logger.warning(reason)
            return "faster_whisper", reason
        if _has_whisper_cpp():
            reason = "whisperx indisponivel; fallback para whisper_cpp"
            if logger:
                logger.warning(reason)
            return "whisper_cpp", reason
        raise RuntimeError("Backend whisperx indisponivel e nenhum fallback encontrado")

    if _has_whisper_cpp():
        return "whisper_cpp", None
    if _has_faster_whisper():
        reason = "whisper_cpp indisponivel; fallback para faster_whisper"
        if logger:
            logger.warning(reason)
        return "faster_whisper", reason
    raise RuntimeError("Backend whisper_cpp indisponivel e faster-whisper nao instalado")


def resolve_model(model, backend, models_cpp=None):
    models_cpp = models_cpp or []
    if backend == "whisper_cpp":
        if model and os.path.isfile(str(model)):
            return str(model)
        pick = tio.pick_default_model(models_cpp)
        if pick and os.path.isfile(pick):
            return pick
        raise RuntimeError("Nenhum modelo .bin encontrado para whisper_cpp")

    text = str(model or "").strip()
    if text and os.path.isfile(text):
        text = os.path.basename(text)
    lower = text.lower()
    if lower.endswith(".bin"):
        text = text[:-4]
        if text.lower().startswith("ggml-"):
            text = text[5:]
    if not text:
        return "large-v3"
    return text


def verify_backend_environment(backend):
    if backend == "whisper_cpp":
        whisper_exe = tio.find_whisper_exe()
        if not whisper_exe:
            raise RuntimeError("Executavel do whisper nao encontrado")
        if not tio.verify_dlls_nvidia(whisper_exe):
            raise RuntimeError("DLLs NVIDIA ausentes na pasta do whisper")
    elif backend == "faster_whisper":
        if not _has_faster_whisper():
            raise RuntimeError("Dependencia faster-whisper nao instalada")
    elif backend == "whisperx":
        if not _has_whisperx():
            raise RuntimeError("Dependencia whisperx nao instalada")
    else:
        raise RuntimeError(f"Backend invalido: {backend}")


def transcribe_chunk(
    backend,
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
    backend = normalize_backend_name(backend)
    if backend == "whisper_cpp":
        return _transcribe_chunk_whisper_cpp(
            wav_path,
            output_base,
            model,
            options,
            duration,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            pid_cb=pid_cb,
            report_cb=report_cb,
            live_writer=live_writer,
            logger=logger,
        )
    if backend == "faster_whisper":
        return _transcribe_chunk_faster_whisper(
            wav_path,
            model,
            options,
            duration,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            report_cb=report_cb,
            live_writer=live_writer,
            logger=logger,
        )
    if backend == "whisperx":
        return _transcribe_chunk_whisperx(
            wav_path,
            model,
            options,
            duration,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            report_cb=report_cb,
            live_writer=live_writer,
            logger=logger,
        )
    raise RuntimeError(f"Backend nao suportado: {backend}")


def _has_whisper_cpp():
    whisper_exe = tio.find_whisper_exe()
    if not whisper_exe:
        return False
    return tio.verify_dlls_nvidia(whisper_exe)


def _has_faster_whisper():
    try:
        importlib.import_module("faster_whisper")
    except Exception:
        return False
    return True


def _has_whisperx():
    try:
        importlib.import_module("whisperx")
    except Exception:
        return False
    return True


def _transcribe_chunk_whisper_cpp(
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
    texec.run_whisper(
        wav_path,
        output_base,
        model,
        options,
        duration,
        progress_cb=progress_cb,
        cancel_event=cancel_event,
        pid_cb=pid_cb,
        report_cb=report_cb,
        live_writer=live_writer,
        logger=logger,
    )
    srt_path = f"{output_base}.srt"
    segments = tpost.parse_srt_segments(srt_path, logger=logger)
    if not segments:
        raise RuntimeError("Transcricao em whisper_cpp nao gerou segmentos")
    return segments


def _model_device_and_compute(options, default_compute_cpu="int8", logger=None):
    device = str(options.get("transcribe_device") or "").strip().lower()
    compute_type = str(options.get("transcribe_compute_type") or "").strip().lower()
    cuda_available = _torch_cuda_available()
    if device == "auto":
        device = ""
    if compute_type == "auto":
        compute_type = ""
    if device == "cuda" and not cuda_available:
        raise RuntimeError(
            "CUDA solicitado, mas indisponivel no PyTorch atual. "
            "Reinstale torch/torchaudio com suporte CUDA."
        )
    if not device:
        device = "cuda" if cuda_available else "cpu"
    if device != "cuda" and compute_type in ("float16", "bfloat16", "int8_float16"):
        raise RuntimeError(
            f"Compute type '{compute_type}' requer CUDA, mas o dispositivo atual e '{device}'."
        )
    if not compute_type:
        compute_type = "float16" if device == "cuda" else default_compute_cpu
    return device, compute_type


def _torch_cuda_available():
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def cuda_available():
    return _torch_cuda_available()


def _torch_cuda_vram_gb(logger=None):
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        idx = int(torch.cuda.current_device())
        props = torch.cuda.get_device_properties(idx)
        total = float(getattr(props, "total_memory", 0.0) or 0.0)
        if total <= 0.0:
            return None
        return total / float(1024 ** 3)
    except Exception:
        if logger:
            logger.debug("cuda_vram_probe_failed", exc_info=True)
        return None


def _autocap_whisperx_batch_size(requested, device, logger=None):
    requested_i = _parse_int(requested, default_value=4, min_value=1, max_value=32)
    if str(device or "").strip().lower() != "cuda":
        return requested_i
    vram_gb = _torch_cuda_vram_gb(logger=logger)
    if not isinstance(vram_gb, (int, float)) or vram_gb <= 0.0:
        return requested_i
    if vram_gb <= 4.5:
        cap = 1
    elif vram_gb <= 6.5:
        cap = 2
    elif vram_gb <= 8.5:
        cap = 4
    elif vram_gb <= 12.5:
        cap = 6
    else:
        cap = 8
    effective = max(1, min(requested_i, cap))
    if logger and effective != requested_i:
        logger.info(
            "whisperx_batch_autocap requested=%s effective=%s vram=%.1fGB device=%s",
            requested_i,
            effective,
            float(vram_gb),
            device,
        )
    return effective


def effective_whisperx_batch_size(options, logger=None, resolved_device=None):
    opts = dict(options or {})
    requested = opts.get("whisperx_batch_size")
    device = str(resolved_device or "").strip().lower()
    if not device:
        device, _ = _model_device_and_compute(opts, default_compute_cpu="int8", logger=logger)
    return _autocap_whisperx_batch_size(requested, device, logger=logger)


def _parse_int(value, default_value, min_value, max_value):
    try:
        parsed = int(str(value))
    except Exception:
        parsed = default_value
    return max(min_value, min(max_value, parsed))


def _parse_language(options):
    language = str(options.get("language", "pt") or "pt").strip().lower()
    if language == "auto":
        return None
    return language


def _probe_optional_whisperx_stack(logger=None):
    global _TORCHCODEC_PROBE_DONE
    if _TORCHCODEC_PROBE_DONE:
        return
    _TORCHCODEC_PROBE_DONE = True
    try:
        version = importlib_metadata.version("torchcodec")
        if logger:
            logger.info("whisperx_env torchcodec=installed version=%s", version)
    except Exception as exc:
        if logger:
            reason = str(exc or "").splitlines()[0] if str(exc or "").strip() else type(exc).__name__
            logger.info("whisperx_env torchcodec=unavailable reason=%s", reason)


def _quiet_known_noisy_loggers():
    global _THIRD_PARTY_LOGGERS_CONFIGURED
    if _THIRD_PARTY_LOGGERS_CONFIGURED:
        return
    _THIRD_PARTY_LOGGERS_CONFIGURED = True
    logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)
    logging.getLogger("lightning.pytorch.utilities.migration.utils").setLevel(logging.WARNING)
    logging.getLogger("whisperx.vads.pyannote").setLevel(logging.WARNING)


def _configure_warning_filters():
    global _WARNING_FILTERS_CONFIGURED
    if _WARNING_FILTERS_CONFIGURED:
        return
    _WARNING_FILTERS_CONFIGURED = True
    warnings.filterwarnings(
        "ignore",
        message=r"(?s).*torchcodec is not installed correctly.*",
        category=UserWarning,
        module=r"pyannote\.audio\.core\.io",
    )
    warnings.filterwarnings(
        "ignore",
        message=r"(?s).*TensorFloat-32 \(TF32\) has been disabled.*",
        category=Warning,
        module=r"pyannote\.audio\.utils\.reproducibility",
    )


def _disable_transformers_torchcodec_when_missing(logger=None):
    try:
        has_torchcodec = importlib_util.find_spec("torchcodec") is not None
    except Exception:
        has_torchcodec = False
    if has_torchcodec:
        return
    try:
        imp_utils = importlib.import_module("transformers.utils.import_utils")
        if getattr(imp_utils, "_torchcodec_available", False):
            setattr(imp_utils, "_torchcodec_available", False)
            if logger:
                logger.info("transformers_fix torchcodec_available=false (package ausente)")
    except Exception:
        if logger:
            logger.debug("transformers_fix skip", exc_info=True)


def _guard_segment_loop(segments_norm, text, logger=None):
    norm = tpost.normalize_for_repeat(text)
    if not norm:
        return False
    segments_norm.append(norm)
    if len(segments_norm) < 24:
        return False
    counts = {}
    for item in segments_norm:
        counts[item] = counts.get(item, 0) + 1
    uniq = len(counts)
    top = max(counts.values()) if counts else 0
    if uniq <= 3 and top >= int(len(segments_norm) * 0.6):
        if logger:
            logger.warning("backend_guard_abort reason=repetition_window uniq=%s top=%s/%s", uniq, top, len(segments_norm))
        return True
    return False


def _is_short_phrase(text):
    norm = tpost.normalize_for_repeat(text)
    if not norm:
        return False, ""
    words = [w for w in norm.split(" ") if w]
    return (len(words) <= 6 and len(norm) <= 64), norm


def _transcribe_chunk_faster_whisper(
    wav_path,
    model,
    options,
    duration,
    progress_cb=None,
    cancel_event=None,
    report_cb=None,
    live_writer=None,
    logger=None,
):
    WhisperModel = importlib.import_module("faster_whisper").WhisperModel

    started_at = time.time()
    if logger:
        logger.info("stage_start engine=faster_whisper stage=Transcricao pid=%s model=%s", os.getpid(), model)

    device, compute_type = _model_device_and_compute(options, logger=logger)
    threads = _parse_int(options.get("threads"), default_value=6, min_value=1, max_value=32)
    beam_size = _parse_int(options.get("beam_size"), default_value=5, min_value=1, max_value=10)
    language = _parse_language(options)
    guidance_prompt = str(options.get("transcribe_guidance_prompt") or "").strip()

    cache_key = (str(model), device, compute_type, threads)
    with _MODEL_LOCK:
        fw_model = _FASTER_MODEL_CACHE.get(cache_key)
        if fw_model is None:
            fw_model = WhisperModel(str(model), device=device, compute_type=compute_type, cpu_threads=threads)
            _FASTER_MODEL_CACHE[cache_key] = fw_model

    if report_cb:
        report_cb("Transcricao", 0.0)

    try:
        transcribe_kwargs = {
            "language": language,
            "beam_size": beam_size,
            "vad_filter": False,
            "condition_on_previous_text": False,
        }
        if guidance_prompt:
            transcribe_kwargs["initial_prompt"] = guidance_prompt

        segments_iter, _ = fw_model.transcribe(
            wav_path,
            **transcribe_kwargs,
        )
        segments = []
        hall_norms = deque(maxlen=48)
        short_loop_norm = ""
        short_loop_streak = 0
        max_ts = max(1.0, float(duration or 0.0)) * 1.20
        out_of_range_streak = 0

        for seg in segments_iter:
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Cancelado")
            start = float(getattr(seg, "start", 0.0) or 0.0)
            end = float(getattr(seg, "end", 0.0) or 0.0)
            text = str(getattr(seg, "text", "") or "").strip()
            if not text or end <= start:
                continue
            if end > max_ts:
                out_of_range_streak += 1
                if out_of_range_streak >= 12:
                    raise RuntimeError("Falha na transcricao (timestamp fora da faixa)")
                continue
            out_of_range_streak = 0
            if _guard_segment_loop(hall_norms, text, logger=logger):
                raise RuntimeError("Falha na transcricao (loop detectado)")
            seg_duration = max(0.0, end - start)
            is_short, norm = _is_short_phrase(text)
            if is_short and seg_duration >= 15.0:
                if norm == short_loop_norm:
                    short_loop_streak += 1
                else:
                    short_loop_norm = norm
                    short_loop_streak = 1
                if short_loop_streak >= 3:
                    if logger:
                        logger.warning(
                            "backend_guard_abort reason=short_phrase_loop streak=%s text=%s",
                            short_loop_streak,
                            text[:120],
                        )
                    raise RuntimeError("Falha na transcricao (loop detectado)")
            else:
                short_loop_norm = ""
                short_loop_streak = 0

            segments.append((start, end, text))
            if live_writer:
                live_writer.handle_segment(start, end, text)

            percent = min(99.5, max(0.0, (end / max(1.0, float(duration or 1.0))) * 100.0))
            if report_cb:
                report_cb("Transcricao", percent)
            if progress_cb:
                progress_cb(
                    {
                        "percent": percent,
                        "speed": None,
                        "eta_seconds": None,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "message": f"Transcricao ({model}) {percent:.1f}%",
                    }
                )

        if not segments:
            raise RuntimeError("Transcricao em faster-whisper nao gerou segmentos")
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
                    "message": f"Transcricao ({model}) 100.0%",
                }
            )
        if logger:
            logger.info("stage_end engine=faster_whisper stage=Transcricao pid=%s rc=0 elapsed=%.1fs", os.getpid(), time.time() - started_at)
        return segments
    except Exception as exc:
        if logger:
            msg = str(exc or "").strip().lower()
            canceled = (cancel_event and cancel_event.is_set()) or msg == "cancelado" or msg.startswith("cancelado ")
            if canceled:
                logger.info("stage_cancel engine=faster_whisper stage=Transcricao pid=%s elapsed=%.1fs", os.getpid(), time.time() - started_at)
            else:
                logger.error("stage_fail engine=faster_whisper stage=Transcricao pid=%s elapsed=%.1fs", os.getpid(), time.time() - started_at, exc_info=True)
        raise


def _transcribe_chunk_whisperx(
    wav_path,
    model,
    options,
    duration,
    progress_cb=None,
    cancel_event=None,
    report_cb=None,
    live_writer=None,
    logger=None,
):
    _probe_optional_whisperx_stack(logger=logger)
    _quiet_known_noisy_loggers()
    _configure_warning_filters()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"torchcodec is not installed correctly so built-in audio decoding will fail\..*",
            category=UserWarning,
        )
        whisperx = importlib.import_module("whisperx")

    started_at = time.time()
    if logger:
        logger.info("stage_start engine=whisperx stage=Transcricao pid=%s model=%s", os.getpid(), model)

    device, compute_type = _model_device_and_compute(options, default_compute_cpu="int8", logger=logger)
    language = _parse_language(options)
    batch_size = effective_whisperx_batch_size(options, logger=logger, resolved_device=device)
    guidance_prompt = str(options.get("transcribe_guidance_prompt") or "").strip()

    cache_key = (str(model), device, compute_type, language or "auto")
    with _MODEL_LOCK:
        wx_model = _WHISPERX_MODEL_CACHE.get(cache_key)
        if wx_model is None:
            _disable_transformers_torchcodec_when_missing(logger=logger)
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"torchcodec is not installed correctly so built-in audio decoding will fail\..*",
                    category=UserWarning,
                )
                try:
                    wx_model = whisperx.load_model(str(model), device, compute_type=compute_type, language=language)
                except ModuleNotFoundError as exc:
                    # Em processo antigo, transformers pode manter _torchcodec_available=True
                    # mesmo apos remover pacote. Corrigimos e tentamos 1 vez.
                    msg = str(exc or "")
                    if "Could not import module 'Pipeline'" in msg or "requirements defined correctly" in msg:
                        _disable_transformers_torchcodec_when_missing(logger=logger)
                        wx_model = whisperx.load_model(str(model), device, compute_type=compute_type, language=language)
                    else:
                        raise
            _WHISPERX_MODEL_CACHE[cache_key] = wx_model

    if report_cb:
        report_cb("Transcricao", 0.0)

    try:
        audio = whisperx.load_audio(wav_path)
        transcribe_kwargs = {"batch_size": batch_size, "language": language}
        if guidance_prompt:
            transcribe_kwargs["initial_prompt"] = guidance_prompt
        try:
            result = wx_model.transcribe(audio, **transcribe_kwargs)
        except TypeError:
            transcribe_kwargs.pop("initial_prompt", None)
            result = wx_model.transcribe(audio, **transcribe_kwargs)
        segments_in = list((result or {}).get("segments") or [])
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Cancelado")

        do_align = str(options.get("whisperx_align", "on")).strip().lower() in ("1", "true", "on", "yes", "sim", "s")
        if do_align and segments_in:
            lang_for_align = (result or {}).get("language") or language
            try:
                align_key = (lang_for_align or "auto", device)
                with _MODEL_LOCK:
                    align_obj = _WHISPERX_ALIGN_CACHE.get(align_key)
                    if align_obj is None:
                        with warnings.catch_warnings():
                            warnings.filterwarnings(
                                "ignore",
                                message=r"torchcodec is not installed correctly so built-in audio decoding will fail\..*",
                                category=UserWarning,
                            )
                            align_obj = whisperx.load_align_model(language_code=lang_for_align, device=device)
                        _WHISPERX_ALIGN_CACHE[align_key] = align_obj
                model_a, metadata = align_obj
                result = whisperx.align(segments_in, model_a, metadata, audio, device, return_char_alignments=False)
                segments_in = list((result or {}).get("segments") or [])
            except Exception:
                if logger:
                    logger.warning("whisperx_align_failed", exc_info=True)

        segments = []
        hall_norms = deque(maxlen=48)
        short_loop_norm = ""
        short_loop_streak = 0
        max_ts = max(1.0, float(duration or 0.0)) * 1.20
        out_of_range_streak = 0
        total = max(1, len(segments_in))
        for idx, seg in enumerate(segments_in, start=1):
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("Cancelado")
            start = float(seg.get("start") or 0.0)
            end = float(seg.get("end") or 0.0)
            text = str(seg.get("text") or "").strip()
            if not text or end <= start:
                continue
            if end > max_ts:
                out_of_range_streak += 1
                if out_of_range_streak >= 12:
                    raise RuntimeError("Falha na transcricao (timestamp fora da faixa)")
                continue
            out_of_range_streak = 0
            if _guard_segment_loop(hall_norms, text, logger=logger):
                raise RuntimeError("Falha na transcricao (loop detectado)")
            seg_duration = max(0.0, end - start)
            is_short, norm = _is_short_phrase(text)
            if is_short and seg_duration >= 15.0:
                if norm == short_loop_norm:
                    short_loop_streak += 1
                else:
                    short_loop_norm = norm
                    short_loop_streak = 1
                if short_loop_streak >= 3:
                    if logger:
                        logger.warning(
                            "backend_guard_abort reason=short_phrase_loop streak=%s text=%s",
                            short_loop_streak,
                            text[:120],
                        )
                    raise RuntimeError("Falha na transcricao (loop detectado)")
            else:
                short_loop_norm = ""
                short_loop_streak = 0

            segments.append((start, end, text))
            if live_writer:
                live_writer.handle_segment(start, end, text)

            percent = min(99.5, max(0.0, (idx / total) * 100.0))
            if report_cb:
                report_cb("Transcricao", percent)
            if progress_cb:
                progress_cb(
                    {
                        "percent": percent,
                        "speed": None,
                        "eta_seconds": None,
                        "downloaded_bytes": None,
                        "total_bytes": None,
                        "message": f"Transcricao ({model}) {percent:.1f}%",
                    }
                )

        if not segments:
            raise RuntimeError("Transcricao em whisperx nao gerou segmentos")
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
                    "message": f"Transcricao ({model}) 100.0%",
                }
            )
        if logger:
            logger.info("stage_end engine=whisperx stage=Transcricao pid=%s rc=0 elapsed=%.1fs", os.getpid(), time.time() - started_at)
        return segments
    except Exception as exc:
        if logger:
            msg = str(exc or "").strip().lower()
            canceled = (cancel_event and cancel_event.is_set()) or msg == "cancelado" or msg.startswith("cancelado ")
            if canceled:
                logger.info("stage_cancel engine=whisperx stage=Transcricao pid=%s elapsed=%.1fs", os.getpid(), time.time() - started_at)
            else:
                logger.error("stage_fail engine=whisperx stage=Transcricao pid=%s elapsed=%.1fs", os.getpid(), time.time() - started_at, exc_info=True)
        raise
