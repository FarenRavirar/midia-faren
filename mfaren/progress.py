import re


def _to_bytes(value, unit):
    factor = {
        "B": 1,
        "KiB": 1024,
        "MiB": 1024 ** 2,
        "GiB": 1024 ** 3,
        "TiB": 1024 ** 4,
        "KB": 1000,
        "MB": 1000 ** 2,
        "GB": 1000 ** 3,
        "TB": 1000 ** 4,
    }.get(unit, 1)
    return int(float(value) * factor)


def parse_ytdlp_progress(line):
    line = line.strip()
    if not line:
        return None

    if line.startswith("[download]"):
        percent = None
        percent_match = re.search(r"(\d+\.?\d*)%", line)
        if percent_match:
            try:
                percent = float(percent_match.group(1))
            except ValueError:
                percent = None

        speed = None
        speed_match = re.search(r"at\s+([^\s]+/s)", line)
        if speed_match:
            speed = speed_match.group(1)

        eta_seconds = None
        eta_match = re.search(r"ETA\s+([^\s]+)", line)
        if eta_match:
            eta_str = eta_match.group(1)
            if ":" in eta_str:
                parts = eta_str.split(":")
                try:
                    if len(parts) == 2:
                        eta_seconds = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        eta_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                except ValueError:
                    eta_seconds = None
            else:
                try:
                    eta_seconds = int(eta_str)
                except ValueError:
                    eta_seconds = None

        downloaded_bytes = None
        total_bytes = None
        total_match = re.search(r"of\s+([0-9\.]+)([KMGTP]i?B)", line)
        if total_match:
            total_bytes = _to_bytes(total_match.group(1), total_match.group(2))
        downloaded_match = re.search(r"\s([0-9\.]+)([KMGTP]i?B)\s+of", line)
        if downloaded_match:
            downloaded_bytes = _to_bytes(downloaded_match.group(1), downloaded_match.group(2))

        return {
            "percent": percent,
            "speed": speed,
            "eta_seconds": eta_seconds,
            "downloaded_bytes": downloaded_bytes,
            "total_bytes": total_bytes,
            "message": "Baixando",
        }

    if line.startswith("[ffmpeg]") or line.startswith("[Merger]") or line.startswith("[ExtractAudio]"):
        return {
            "percent": None,
            "speed": None,
            "eta_seconds": None,
            "downloaded_bytes": None,
            "total_bytes": None,
            "message": "Pós-processamento",
        }

    return None


def parse_ffmpeg_progress(line):
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, value = line.split("=", 1)
    if key == "speed":
        return {"speed": value}
    if key == "out_time_ms":
        try:
            return {"out_time_ms": int(value)}
        except ValueError:
            return None
    if key == "total_size":
        try:
            return {"total_size": int(value)}
        except ValueError:
            return None
    if key == "progress" and value in ("continue", "end"):
        return {"progress": value}
    return None
