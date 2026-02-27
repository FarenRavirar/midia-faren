import json
import os
import re
import subprocess
import time
from typing import Any

from .progress import parse_ytdlp_progress

YT_DLP_CANDIDATES = [
    os.path.join("tools", "yt-dlp.exe"),
    "yt-dlp",
]


def find_ytdlp():
    for path in YT_DLP_CANDIDATES:
        if os.path.isfile(path) or path == "yt-dlp":
            return path
    return None


def is_youtube_url(url):
    return bool(re.search(r"(youtube\.com|youtu\.be)", url, re.IGNORECASE))


def get_metadata(ytdlp, url):
    cmd = [ytdlp, "-J", url]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Erro ao obter metadados")
    data = json.loads(proc.stdout)
    entries = []
    if "entries" in data and data["entries"]:
        for entry in data["entries"]:
            if not entry:
                continue
            url_value = entry.get("webpage_url") or entry.get("url")
            if url_value and not url_value.startswith("http"):
                url_value = f"https://www.youtube.com/watch?v={url_value}"
            entries.append(
                {
                    "title": entry.get("title") or "n\u00e3o informado",
                    "channel": entry.get("channel") or entry.get("uploader") or "n\u00e3o informado",
                    "duration": entry.get("duration"),
                    "url": url_value,
                }
            )
    first = entries[0] if entries else data
    title = first.get("title") or "n\u00e3o informado"
    channel = first.get("channel") or first.get("uploader") or "n\u00e3o informado"
    duration = first.get("duration")
    return {"title": title, "channel": channel, "duration": duration, "entries": entries}


def list_entries(ytdlp, url):
    cmd = [ytdlp, "-J", "--flat-playlist", url]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Erro ao obter entradas")
    data = json.loads(proc.stdout)
    entries = []
    if "entries" in data and data["entries"]:
        for entry in data["entries"]:
            if not entry:
                continue
            url_value = entry.get("webpage_url") or entry.get("url")
            if url_value and not url_value.startswith("http"):
                url_value = f"https://www.youtube.com/watch?v={url_value}"
            entries.append(
                {
                    "title": entry.get("title") or "n\u00e3o informado",
                    "channel": entry.get("channel") or entry.get("uploader") or "n\u00e3o informado",
                    "url": url_value,
                }
            )
    return entries


def _normalize_runtime(js_runtime):
    if not js_runtime:
        return None, None
    if ":" not in js_runtime:
        return js_runtime, None
    runtime, path = js_runtime.split(":", 1)
    runtime = runtime.strip().lower()
    if runtime == "nodejs":
        runtime = "node"
    return runtime, path.strip() or None


def _download_with_exe(
    ytdlp,
    url,
    output_path,
    progress_cb=None,
    cancel_event=None,
    pid_cb=None,
    extra_args=None,
    cookies_file=None,
    js_runtime=None,
    remote_components=None,
):
    extra_args = extra_args or []
    cmd = [
        ytdlp,
        "--newline",
        "--progress",
        "-f",
        "bestvideo+bestaudio/best",
        "--merge-output-format",
        "mkv",
    ]
    runtime, path = _normalize_runtime(js_runtime)
    if runtime:
        if path:
            cmd += ["--js-runtimes", f"{runtime}:{path}"]
        else:
            cmd += ["--js-runtimes", runtime]
    if remote_components == "on":
        cmd += ["--remote-components", "ejs:github"]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    cmd += extra_args + [
        "-o",
        output_path,
        url,
    ]
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
        last_lines = []
        if proc.stdout is None:
            return proc.wait(), proc.pid, last_lines
        for line in proc.stdout:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                return 1, proc.pid, last_lines
            if progress_cb:
                payload = parse_ytdlp_progress(line)
                if payload:
                    progress_cb(payload)
            if line:
                last_lines.append(line.strip())
                if len(last_lines) > 20:
                    last_lines = last_lines[-20:]
        return proc.wait(), proc.pid, last_lines
    finally:
        if proc.stdout:
            proc.stdout.close()


def _download_with_module(
    url,
    output_path,
    progress_cb=None,
    cancel_event=None,
    logger=None,
    cookies=None,
    extractor_args=None,
    js_runtime=None,
    remote_components=None,
):
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        if logger:
            logger.info("yt_dlp module not available: %s", exc)
        return 1, [f"yt_dlp module not available: {exc}"]

    last_lines = []

    def _hook(d):
        if cancel_event and cancel_event.is_set():
            raise Exception("cancelado")
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes")
            speed = d.get("speed")
            eta = d.get("eta")
            speed_text = None
            if speed:
                speed_text = f"{speed / (1024 * 1024):.2f}MiB/s"
            payload = {
                "percent": None,
                "speed": speed_text,
                "eta_seconds": eta,
                "downloaded_bytes": downloaded,
                "total_bytes": total,
                "message": "Baixando",
            }
            if progress_cb:
                progress_cb(payload)
        elif status == "finished":
            if progress_cb:
                progress_cb({"message": "Finalizando"})

    opts: dict[str, Any] = {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mkv",
        "outtmpl": output_path,
        "progress_hooks": [_hook],
        "noprogress": True,
    }
    if cookies:
        opts["cookiefile"] = cookies
    if extractor_args:
        opts["extractor_args"] = {"youtube": [extractor_args.replace("youtube:", "")]}
    runtime, path = _normalize_runtime(js_runtime)
    if runtime:
        if path:
            opts["js_runtimes"] = {runtime: {"path": path}}
        else:
            opts["js_runtimes"] = {runtime: {}}
    if remote_components == "on":
        opts["remote_components"] = {"ejs": "github"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
            ydl.download([url])
        return 0, last_lines
    except Exception as exc:
        last_lines.append(str(exc))
        return 1, last_lines


def _make_extractor_args(client, token):
    if not client or client == "default":
        return None
    arg = f"youtube:player-client={client}"
    if token:
        if "gvs+" in token:
            arg += f";po_token={token}"
        else:
            arg += f";po_token={client}.gvs+{token}"
    return arg


def download_with_fallback(url, output_path, progress_cb=None, cancel_event=None, pid_cb=None, logger=None, options=None):
    attempts = []
    ytdlp = find_ytdlp()
    options = options or {}
    client = options.get("yt_client", "default")
    po_token = options.get("po_token") or ""
    use_cookies = options.get("use_cookies", "auto")
    cookies_file = options.get("cookies_file") or None
    js_runtime = options.get("js_runtime") or None
    remote_components = options.get("remote_components", "on")
    extractor_arg = _make_extractor_args(client, po_token)

    # 1) exe + cookies-from-browser vivaldi
    if ytdlp:
        extra = []
        if extractor_arg:
            extra += ["--extractor-args", extractor_arg]
        if use_cookies in ("auto", "on"):
            attempts.append(("exe+cookies-vivaldi", {"extra": extra + ["--cookies-from-browser", "vivaldi"]}))
            attempts.append(("exe+cookies-chrome", {"extra": extra + ["--cookies-from-browser", "chrome"]}))
        attempts.append(("exe+no-cookies", {"extra": extra}))
        if cookies_file:
            attempts.insert(0, ("exe+cookies-file", {"extra": extra, "cookies_file": cookies_file}))

    # 2) python module + browser cookies (if available)
    if use_cookies in ("auto", "on"):
        attempts.append(("py+cookies-vivaldi", {"module": True, "cookies_browser": "vivaldi"}))
    attempts.append(("py+no-cookies", {"module": True, "cookies_browser": None}))

    last_error = "sem detalhes"
    for name, cfg in attempts:
        if logger:
            logger.info("yt-dlp attempt: %s", name)
        try:
            if cfg.get("module"):
                cookies_path = None
                if cfg.get("cookies_browser"):
                    try:
                        import browser_cookie3  # type: ignore

                        if cfg["cookies_browser"] == "vivaldi":
                            cj = browser_cookie3.vivaldi()
                        else:
                            cj = browser_cookie3.chrome()
                        temp_path = os.path.join("data", f"cookies_{int(time.time())}.txt")
                        os.makedirs("data", exist_ok=True)
                        with open(temp_path, "w", encoding="utf-8") as f:
                            for c in cj:
                                f.write(
                                    f"{c.domain}\tTRUE\t{c.path}\t{'TRUE' if c.secure else 'FALSE'}\t"
                                    f"{c.expires}\t{c.name}\t{c.value}\n"
                                )
                        cookies_path = temp_path
                        if logger:
                            logger.info("cookies extraidos: %s", temp_path)
                    except Exception as exc:
                        if logger:
                            logger.warning("falha ao extrair cookies: %s", exc)
                        cookies_path = None
                rc, lines = _download_with_module(
                    url,
                    output_path,
                    progress_cb=progress_cb,
                    cancel_event=cancel_event,
                    logger=logger,
                    cookies=cookies_path,
                    extractor_args=extractor_arg,
                    js_runtime=js_runtime,
                    remote_components=remote_components,
                )
                if rc == 0:
                    return 0, None, []
                last_error = " | ".join(lines[-5:]) if lines else "sem detalhes"
            else:
                rc, pid, lines = _download_with_exe(
                    ytdlp,
                    url,
                    output_path,
                    progress_cb=progress_cb,
                    cancel_event=cancel_event,
                    pid_cb=pid_cb,
                    extra_args=cfg.get("extra", []),
                    cookies_file=cfg.get("cookies_file"),
                    js_runtime=js_runtime,
                    remote_components=remote_components,
                )
                if rc == 0:
                    return 0, pid, []
                last_error = " | ".join(lines[-5:]) if lines else "sem detalhes"
        except Exception as exc:
            last_error = str(exc)
        if logger:
            logger.warning("yt-dlp attempt failed: %s | %s", name, last_error)

    return 1, None, [last_error]
