import os
import os
import subprocess
from functools import lru_cache

from .progress import parse_ffmpeg_progress

FFMPEG_CANDIDATES = [
    os.path.join("tools", "ffmpeg", "bin", "ffmpeg.exe"),
    os.path.join(
        "C:\\tools\\ffmpeg\\ffmpeg-7.1.1-full_build-shared\\bin",
        "ffmpeg.exe",
    ),
    "ffmpeg",
]


def _is_on(value):
    return str(value or "").strip().lower() in ("on", "1", "true", "yes", "sim", "s")


def _normalize_audio_filter():
    return "loudnorm=I=-16:TP=-1.5:LRA=11"


def _normalize_video_accel(value):
    text = str(value or "").strip().lower()
    if text in ("", "off", "0", "false", "no", "nao", "n"):
        return "off"
    if text in ("on", "1", "true", "yes", "sim", "s", "auto"):
        return "auto"
    if text in ("cuda", "force_cuda", "forcar_cuda"):
        return "cuda"
    return "off"


@lru_cache(maxsize=16)
def _ffmpeg_encoder_set(ffmpeg_bin):
    cmd = [str(ffmpeg_bin), "-hide_banner", "-encoders"]
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True, "encoding": "utf-8", "errors": "replace"}
    try:
        proc = subprocess.run(cmd, **kwargs)
    except Exception:
        return set()
    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    encoders = set()
    for line in str(out).splitlines():
        line = line.strip()
        if not line or line.startswith("Encoders:"):
            continue
        if line.startswith("--"):
            continue
        # Example: " V....D h264_nvenc           NVIDIA NVENC H.264 encoder"
        parts = line.split()
        if len(parts) >= 2:
            encoders.add(parts[1].strip().lower())
    return encoders


def _video_encoder_for_request(ffmpeg_bin, codec, accel_mode):
    c = str(codec or "").strip().lower()
    mode = _normalize_video_accel(accel_mode)
    if c == "h264":
        preferred = "h264_nvenc"
    elif c == "h265":
        preferred = "hevc_nvenc"
    elif c == "av1":
        preferred = "av1_nvenc"
    else:
        preferred = ""
    fallback_map = {"h264": "libx264", "h265": "libx265", "vp9": "libvpx-vp9", "av1": "libaom-av1"}
    fallback = fallback_map.get(c, "libx264")

    if mode == "off" or not preferred:
        return fallback, "cpu", "gpu_off_or_unsupported"

    encoders = _ffmpeg_encoder_set(str(ffmpeg_bin))
    if preferred in encoders:
        return preferred, "gpu_cuda", "nvenc_ok"

    if mode == "cuda":
        raise RuntimeError(
            f"GPU/CUDA forcado, mas encoder '{preferred}' nao esta disponivel no ffmpeg atual."
        )
    # Auto requested but encoder unavailable => fallback CPU.
    return fallback, "cpu", "nvenc_missing_fallback_cpu"


def infer_runtime_accel(options, ffmpeg_bin=None):
    mode = str((options or {}).get("mode") or "").strip().lower()
    if mode == "video":
        codec = str((options or {}).get("codec") or "").strip().lower()
        accel = _normalize_video_accel((options or {}).get("video_accel"))
        try:
            _, runtime, _ = _video_encoder_for_request(ffmpeg_bin or find_ffmpeg() or "ffmpeg", codec, accel)
            return runtime
        except Exception:
            return "cpu"
    if mode in ("audio", "image", "transcribe", "mixagem", "craig_notebook"):
        return "cpu"
    return "cpu"


def find_ffmpeg():
    for path in FFMPEG_CANDIDATES:
        if os.path.isfile(path) or path == "ffmpeg":
            return path
    return None


def build_audio_cmd(ffmpeg, input_path, output_path, options):
    fmt = options.get("format")
    bitrate = options.get("bitrate")
    mode = options.get("audio_mode")
    cmd = [ffmpeg, "-y", "-i", input_path]
    if _is_on(options.get("normalize")):
        cmd += ["-af", _normalize_audio_filter()]
    if fmt == "wav":
        cmd += ["-vn", output_path]
        return cmd
    if mode == "vbr":
        if bitrate and bitrate.startswith("q"):
            qval = bitrate.replace("q", "")
            cmd += ["-vn", "-q:a", qval]
        else:
            cmd += ["-vn", "-q:a", "4"]
    else:
        cmd += ["-vn", "-b:a", f"{bitrate}k"]
    cmd.append(output_path)
    return cmd


def build_video_cmd(ffmpeg, input_path, output_path, options):
    requested_codec = str(options.get("codec") or "h264").strip().lower()
    accel_mode = _normalize_video_accel(options.get("video_accel"))
    codec, _, _ = _video_encoder_for_request(ffmpeg, requested_codec, accel_mode)
    resolution = options.get("resolution")
    bitrate = options.get("bitrate")
    custom_bitrate = options.get("custom_bitrate")
    strip_audio = str(options.get("strip_audio") or "off").strip().lower() in ("on", "1", "true", "yes", "sim", "s")
    normalize_audio = _is_on(options.get("normalize"))
    crf = options.get("crf")
    preset = options.get("preset")

    cmd = [ffmpeg, "-y", "-i", input_path, "-c:v", codec]

    resize_w = options.get("resize_width")
    resize_h = options.get("resize_height")
    resize_mode = options.get("resize_mode", "contain")
    vf = None
    if resize_w or resize_h:
        vf = _build_resize_filter(resize_w, resize_h, resize_mode)
    elif resolution and resolution != "best":
        vf = f"scale=-2:{resolution}"
    if vf:
        cmd += ["-vf", vf]

    if bitrate == "custom" and custom_bitrate:
        cmd += ["-b:v", f"{custom_bitrate}k"]
    elif bitrate and bitrate not in ("auto", "custom"):
        cmd += ["-b:v", f"{bitrate}k"]

    if crf:
        cmd += ["-crf", str(crf)]

    if preset:
        cmd += ["-preset", preset]

    if strip_audio:
        cmd += ["-an"]
    elif normalize_audio:
        cmd += ["-af", _normalize_audio_filter()]

    cmd.append(output_path)
    return cmd


def _build_resize_filter(width, height, mode):
    try:
        w = int(width) if width not in (None, "", "0") else None
    except (TypeError, ValueError):
        w = None
    try:
        h = int(height) if height not in (None, "", "0") else None
    except (TypeError, ValueError):
        h = None
    if not w and not h:
        return None
    if w and not h:
        return f"scale={w}:-2"
    if h and not w:
        return f"scale=-2:{h}"
    if mode == "stretch":
        return f"scale={w}:{h}"
    if mode == "cover":
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"


def build_image_cmd(ffmpeg, input_path, output_path, options):
    fmt = options.get("image_format", "jpg")
    quality = options.get("image_quality")
    resize_w = options.get("image_width")
    resize_h = options.get("image_height")
    resize_mode = options.get("image_resize_mode", "contain")
    cmd = [ffmpeg, "-y", "-i", input_path, "-frames:v", "1"]
    vf = _build_resize_filter(resize_w, resize_h, resize_mode)
    if vf:
        cmd += ["-vf", vf]
    if quality not in (None, "") and fmt in ("jpg", "webp", "avif"):
        try:
            q = int(quality)
            q = max(1, min(100, q))
            ff_q = int(round(2 + (31 - 2) * (1 - (q / 100.0))))
            cmd += ["-q:v", str(ff_q)]
        except (TypeError, ValueError):
            pass
    cmd.append(output_path)
    return cmd


def run_ffmpeg(cmd, progress_cb=None, cancel_event=None, pid_cb=None):
    if progress_cb:
        cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if pid_cb:
        pid_cb(proc.pid)
    try:
        if not progress_cb:
            proc.wait()
            return proc.returncode, proc.pid
        progress = {}
        if proc.stdout is None:
            return proc.wait(), proc.pid
        for line in proc.stdout:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                return 1, proc.pid
            parsed = parse_ffmpeg_progress(line)
            if not parsed:
                continue
            progress.update(parsed)
            if parsed.get("progress"):
                progress_cb(progress)
                progress = {}
        return proc.wait(), proc.pid
    finally:
        if proc.stdout:
            proc.stdout.close()


def normalize_ffmpeg_progress(progress, duration=None):
    out_time_ms = progress.get("out_time_ms")
    speed = progress.get("speed")
    total_size = progress.get("total_size")
    percent = None
    eta_seconds = None
    total_bytes = None
    elapsed = None
    if out_time_ms and duration:
        try:
            elapsed = int(out_time_ms) / 1000000.0
            percent = min(100.0, max(0.0, (elapsed / duration) * 100.0))
            if isinstance(speed, str) and speed.endswith("x"):
                try:
                    speed_x = float(speed[:-1])
                    if speed_x > 0:
                        eta_seconds = max(0.0, (duration - elapsed) / speed_x)
                except ValueError:
                    eta_seconds = None
            if percent and total_size:
                total_bytes = int((int(total_size) * 100.0) / percent)
        except ValueError:
            percent = None
    return {
        "percent": percent,
        "speed": speed,
        "eta_seconds": eta_seconds,
        "downloaded_bytes": total_size,
        "total_bytes": total_bytes,
        "message": "Convertendo",
    }
