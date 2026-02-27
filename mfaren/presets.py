from . import transcribe_chunking as tchunk


AUDIO_FORMATS = ["mp3", "wav", "ogg"]
AUDIO_BITRATES = ["96", "128", "160", "192", "256", "320"]
AUDIO_MODES = ["cbr", "vbr"]

VIDEO_CONTAINERS = ["mp4", "avi", "webm"]
VIDEO_CODECS = ["h264", "h265", "vp9", "av1"]
VIDEO_CONTAINER_CODECS = {
    "mp4": {"h264", "h265", "av1"},
    "avi": {"h264", "h265"},
    "webm": {"vp9", "av1"},
}
VIDEO_RESOLUTIONS = ["best", "720", "1080", "1440", "2160"]
VIDEO_BITRATES = ["auto", "1000", "2000", "4000", "6000", "8000", "12000", "20000", "custom"]

IMAGE_FORMATS = ["jpg", "png", "webp", "avif"]
IMAGE_RESIZE_MODES = ["contain", "cover", "stretch"]

AUDIO_PRESETS = [
    {"label": "MP3 192k CBR", "format": "mp3", "bitrate": "192", "audio_mode": "cbr"},
    {"label": "MP3 320k CBR", "format": "mp3", "bitrate": "320", "audio_mode": "cbr"},
    {"label": "OGG VBR q5", "format": "ogg", "bitrate": "q5", "audio_mode": "vbr"},
]

VIDEO_PRESETS = [
    {"label": "MP4 H.264 1080p", "container": "mp4", "codec": "h264", "resolution": "1080", "bitrate": "auto"},
    {"label": "WEBM VP9 1080p", "container": "webm", "codec": "vp9", "resolution": "1080", "bitrate": "auto"},
]


def _validate_custom_bitrate(value, min_k=100, max_k=50000):
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ValueError("Bitrate custom invalido")
    if num < min_k or num > max_k:
        raise ValueError("Bitrate custom fora do limite")


def validate_audio(options):
    fmt = options.get("format")
    bitrate = options.get("bitrate")
    mode = options.get("audio_mode")
    if fmt not in AUDIO_FORMATS:
        raise ValueError("Formato de audio invalido")
    if mode not in AUDIO_MODES:
        raise ValueError("Modo de audio invalido")
    if mode == "vbr" and bitrate and str(bitrate).startswith("q"):
        pass
    elif bitrate not in AUDIO_BITRATES:
        raise ValueError("Bitrate de audio invalido")
    normalize = options.get("normalize")
    if normalize not in (None, "", "on", "off", True, False, 0, 1, "0", "1"):
        raise ValueError("Opcao de normalizacao invalida")


def validate_video(options):
    container = options.get("container")
    codec = options.get("codec")
    resolution = options.get("resolution")
    bitrate = options.get("bitrate")
    custom_bitrate = options.get("custom_bitrate")
    strip_audio = options.get("strip_audio")
    video_accel = str(options.get("video_accel") or "").strip().lower()
    if container not in VIDEO_CONTAINERS:
        raise ValueError("Container de video invalido")
    if codec not in VIDEO_CODECS:
        raise ValueError("Codec de video invalido")
    allowed_codecs = VIDEO_CONTAINER_CODECS.get(container, set())
    if allowed_codecs and codec not in allowed_codecs:
        raise ValueError("Codec invalido para o container selecionado")
    if resolution not in VIDEO_RESOLUTIONS:
        raise ValueError("Resolucao invalida")
    if bitrate not in VIDEO_BITRATES:
        raise ValueError("Bitrate invalido")
    if bitrate == "custom":
        _validate_custom_bitrate(custom_bitrate)
    if strip_audio not in (None, "", "on", "off", True, False, 0, 1, "0", "1"):
        raise ValueError("Opcao de remover audio invalida")
    if video_accel not in ("", "off", "auto", "cuda"):
        raise ValueError("Opcao de aceleracao GPU invalida")
    normalize = options.get("normalize")
    if normalize not in (None, "", "on", "off", True, False, 0, 1, "0", "1"):
        raise ValueError("Opcao de normalizacao invalida")


def validate_image(options):
    fmt = options.get("image_format")
    if fmt not in IMAGE_FORMATS:
        raise ValueError("Formato de imagem invalido")
    resize_mode = options.get("image_resize_mode", "contain")
    if resize_mode not in IMAGE_RESIZE_MODES:
        raise ValueError("Modo de redimensionamento invalido")
    quality = options.get("image_quality")
    if quality not in (None, ""):
        try:
            q = int(quality)
        except (TypeError, ValueError):
            raise ValueError("Qualidade de imagem invalida")
        if q < 1 or q > 100:
            raise ValueError("Qualidade de imagem fora do limite")


def validate_transcribe(options):
    backend = options.get("transcribe_backend")
    if backend not in (None, "", "whisper_cpp", "faster_whisper", "whisperx"):
        raise ValueError("Backend de transcricao invalido")
    model = options.get("model")
    if model and not isinstance(model, str):
        raise ValueError("Modelo invalido")
    compare_all = options.get("compare_all")
    if compare_all not in (None, "", False, True, 0, 1, "0", "1"):
        raise ValueError("Modo de comparacao invalido")
    normalize = options.get("normalize")
    if normalize not in (None, "", "on", "off", True, False, 0, 1, "0", "1"):
        raise ValueError("Normalizacao invalida")
    vad = options.get("vad")
    if vad not in (None, "", "on", "off", True, False, 0, 1, "0", "1"):
        raise ValueError("VAD invalido")
    vad_threshold = options.get("vad_threshold")
    if vad_threshold not in (None, ""):
        try:
            float(vad_threshold)
        except (TypeError, ValueError):
            raise ValueError("VAD threshold invalido")
    vad_min_silence = options.get("vad_min_silence")
    if vad_min_silence not in (None, ""):
        try:
            float(vad_min_silence)
        except (TypeError, ValueError):
            raise ValueError("VAD min silencio invalido")
    language = options.get("language", "pt")
    if language and not isinstance(language, str):
        raise ValueError("Idioma invalido")
    threads = options.get("threads")
    if threads not in (None, "", 0):
        try:
            t = int(threads)
        except (TypeError, ValueError):
            raise ValueError("Threads invalido")
        if t < 1 or t > 32:
            raise ValueError("Threads fora do limite")
    beam = options.get("beam_size")
    if beam not in (None, "", 0):
        try:
            b = int(beam)
        except (TypeError, ValueError):
            raise ValueError("Beam size invalido")
        if b < 1 or b > 10:
            raise ValueError("Beam size fora do limite")
    device = str(options.get("transcribe_device") or "").strip().lower()
    if device not in ("", "auto", "cpu", "cuda"):
        raise ValueError("Dispositivo de transcricao invalido")
    compute_type = str(options.get("transcribe_compute_type") or "").strip().lower()
    allowed_compute = ("", "auto", "float16", "int8", "float32", "int8_float16", "int8_float32", "bfloat16")
    if compute_type not in allowed_compute:
        raise ValueError("Compute type de transcricao invalido")
    wx_batch = options.get("whisperx_batch_size")
    if wx_batch not in (None, ""):
        try:
            bsz = int(str(wx_batch))
        except (TypeError, ValueError):
            raise ValueError("Batch size do WhisperX invalido")
        if bsz < 1 or bsz > 32:
            raise ValueError("Batch size do WhisperX fora do limite")
    initial_prompt = options.get("transcribe_initial_prompt")
    if initial_prompt not in (None, "") and not isinstance(initial_prompt, str):
        raise ValueError("Prompt inicial de transcricao invalido")
    output_json = options.get("transcribe_output_json")
    if output_json not in (None, "", "on", "off", True, False, 0, 1, "0", "1"):
        raise ValueError("Opcao de saida JSON invalida")
    max_len = options.get("max_len")
    if max_len not in (None, "", 0):
        try:
            ml = int(max_len)
        except (TypeError, ValueError):
            raise ValueError("Max len invalido")
        if ml < 0 or ml > 200:
            raise ValueError("Max len fora do limite")
    chunk_seconds = options.get("chunk_seconds")
    if chunk_seconds not in (None, "", 0):
        try:
            ch = float(tchunk.parse_duration_seconds(chunk_seconds))
        except (TypeError, ValueError):
            raise ValueError("Chunk seconds invalido")
        if ch < 30 or ch > 3600:
            raise ValueError("Chunk seconds fora do limite")
    chunk_overlap_seconds = options.get("chunk_overlap_seconds")
    if chunk_overlap_seconds not in (None, "", 0):
        try:
            ov = float(chunk_overlap_seconds)
        except (TypeError, ValueError):
            raise ValueError("Chunk overlap invalido")
        if ov < 0 or ov > 120:
            raise ValueError("Chunk overlap fora do limite")
    glossary = options.get("transcribe_glossary")
    if glossary not in (None, "") and not isinstance(glossary, str):
        raise ValueError("Glossario de transcricao invalido")
    auto_recover = options.get("transcribe_auto_recover")
    if auto_recover not in (None, "", "on", "off", True, False, 0, 1, "0", "1"):
        raise ValueError("Auto recuperacao invalida")
    auto_recover_retries = options.get("transcribe_auto_recover_retries")
    if auto_recover_retries not in (None, ""):
        try:
            retries = int(str(auto_recover_retries))
        except (TypeError, ValueError):
            raise ValueError("Tentativas de auto recuperacao invalida")
        if retries < 0 or retries > 3:
            raise ValueError("Tentativas de auto recuperacao fora do limite")


def validate_mixagem(options):
    output_format = str(options.get("mix_output_format") or options.get("notebook_output_format") or "m4a").strip().lower()
    if output_format not in ("m4a", "mp3", "wav"):
        raise ValueError("Formato de mixagem invalido")

    bitrate = options.get("mix_target_bitrate_kbps") or options.get("notebook_target_bitrate_kbps")
    if bitrate not in (None, ""):
        try:
            kbps = int(str(bitrate).strip())
        except (TypeError, ValueError):
            raise ValueError("Bitrate alvo de mixagem invalido")
        if kbps < 24 or kbps > 512:
            raise ValueError("Bitrate alvo de mixagem fora do limite")

    max_size = options.get("mix_max_size_mb") or options.get("notebook_max_size_mb")
    if max_size not in (None, ""):
        try:
            mb = int(str(max_size).strip())
        except (TypeError, ValueError):
            raise ValueError("Limite MB de mixagem invalido")
        if mb < 1 or mb > 2048:
            raise ValueError("Limite MB de mixagem fora do limite")
    normalize = options.get("normalize")
    if normalize not in (None, "", "on", "off", True, False, 0, 1, "0", "1"):
        raise ValueError("Opcao de normalizacao invalida")


def validate_options(options):
    mode = options.get("mode")
    if mode == "audio":
        validate_audio(options)
    elif mode == "video":
        validate_video(options)
    elif mode == "image":
        validate_image(options)
    elif mode == "transcribe":
        validate_transcribe(options)
    elif mode in ("mixagem", "craig_notebook"):
        validate_mixagem(options)
    else:
        raise ValueError("Modo invalido")
