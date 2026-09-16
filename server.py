#!/usr/bin/env python3
"""Local web UI for downloading videos from YouTube and Niconico via yt-dlp.

Run:  python server.py
Then open the printed address in a browser, including from a phone on the same network.

Cross-platform: Windows, macOS and Linux. No third-party Python packages required,
only the yt-dlp and ffmpeg executables.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Japanese and Cyrillic titles must survive a legacy console codepage (cp1251, cp932, ...)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
JOBDIR = BASE / ".jobs"
CONFIG_PATH = BASE / "config.json"

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8777,
    "download_dir": "downloads",
    "cookies_file": "",
    "cookies_from_browser": "",
    "ytdlp": "",
    "ffmpeg": "",
}

MEDIA_EXT = (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".wav", ".flac")
AUDIO_EXT = (".m4a", ".mp3", ".opus", ".wav", ".flac")

# Content types for inline playback. Browsers refuse to play a file served as
# application/octet-stream, so the extension has to be mapped explicitly.
MIME_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}
VCODEC_FILTER = {"h264": "[vcodec^=avc1]", "vp9": "[vcodec^=vp9]", "av1": "[vcodec^=av01]"}
ACODEC_FILTER = {"aac": "[acodec^=mp4a]", "opus": "[acodec^=opus]"}

# Directories to search for yt-dlp/ffmpeg when they are not on PATH.
EXTRA_TOOL_DIRS = [
    Path.home() / "scoop" / "shims",                          # Windows, scoop
    Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links",
    Path("C:/Program Files/ffmpeg/bin"),
    Path("/opt/homebrew/bin"),                                # macOS, Apple Silicon
    Path("/usr/local/bin"),                                   # macOS Intel, Linux
    Path("/usr/bin"),
    Path("/snap/bin"),
]


def load_config():
    """Read config.json, creating it from defaults on first run."""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"! Could not read config.json ({exc}); using defaults")
    else:
        try:
            CONFIG_PATH.write_text(
                json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
            print("Created config.json with default settings.")
        except OSError:
            pass
    return cfg


CFG = load_config()

# A relative download_dir is resolved against the project folder, so the app stays
# portable: clone it anywhere and it still knows where to put files.
_raw_dir = Path(CFG["download_dir"] or "downloads").expanduser()
DOWNLOAD_DIR = (_raw_dir if _raw_dir.is_absolute() else BASE / _raw_dir).resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOBDIR.mkdir(exist_ok=True)


def find_tool(name, configured):
    """Locate an executable: config value, then PATH, then well-known install dirs."""
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return str(candidate.resolve())

    found = shutil.which(name)
    if found:
        return found

    for directory in EXTRA_TOOL_DIRS:
        for filename in (name, f"{name}.exe"):
            candidate = directory / filename
            if candidate.exists():
                return str(candidate)
    return None


YTDLP = find_tool("yt-dlp", CFG["ytdlp"])
FFMPEG = find_tool("ffmpeg", CFG["ffmpeg"])
FFPROBE = find_tool("ffprobe", "")

# Hide the console window that would otherwise flash on every subprocess on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

JOBS = {}
JOBS_LOCK = threading.Lock()
PROBE_CACHE = {}


# --------------------------------------------------------------------------- yt-dlp

def run_env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if FFMPEG:
        env["PATH"] = str(Path(FFMPEG).parent) + os.pathsep + env.get("PATH", "")
    return env


def cookie_args():
    """Cookies are required for most Niconico videos and some age-gated YouTube ones."""
    cookies_file = CFG.get("cookies_file")
    if cookies_file:
        path = Path(cookies_file).expanduser()
        if path.exists():
            return ["--cookies", str(path.resolve())]
    if CFG.get("cookies_from_browser"):
        return ["--cookies-from-browser", CFG["cookies_from_browser"]]
    return []


def base_args():
    args = [YTDLP, "--ignore-config", "--no-playlist", "--no-warnings"]
    if FFMPEG:
        args += ["--ffmpeg-location", str(Path(FFMPEG).parent)]
    return args + cookie_args()


def short_codec(codec):
    """Turn a codec string such as 'avc1.4d401e' into a readable 'h264'."""
    if not codec or codec == "none":
        return None
    head = codec.split(".")[0].lower()
    return {"avc1": "h264", "mp4a": "aac", "vp09": "vp9", "av01": "av1"}.get(head, head)


def clean_error(text):
    """Pull the most informative single line out of yt-dlp output."""
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    errors = [line for line in lines if line.startswith("ERROR")]
    return (errors[-1] if errors else lines[-1])[:400]


def build_selector(opts):
    """Turn the UI choices into a yt-dlp -f selector."""
    explicit = (opts.get("format_id") or "").strip()
    if explicit:
        return explicit

    afilter = ACODEC_FILTER.get(opts.get("acodec"), "")
    if opts.get("mode") == "audio":
        return f"ba{afilter}/ba/b"

    height = opts.get("height") or "best"
    hfilter = "" if height == "best" else f"[height<={int(height)}]"
    vfilter = VCODEC_FILTER.get(opts.get("vcodec"), "")

    # Strictest first, loosest last: a download must never fail merely because a
    # codec preference could not be satisfied.
    return "/".join([
        f"bv*{hfilter}{vfilter}+ba{afilter}",
        f"bv*{hfilter}{vfilter}+ba",
        f"bv*{hfilter}+ba{afilter}",
        f"bv*{hfilter}+ba",
        f"b{hfilter}",
        "b",
    ])


def probe(url):
    """Fetch metadata and the real format list for a URL (cached for 5 minutes)."""
    cached = PROBE_CACHE.get(url)
    if cached and time.time() - cached[0] < 300:
        return cached[1]

    proc = subprocess.run(
        base_args() + ["-J", url],
        capture_output=True, encoding="utf-8", errors="replace",
        env=run_env(), timeout=180, creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(clean_error(proc.stderr) or "yt-dlp could not read the page")

    raw = json.loads(proc.stdout)
    formats, heights, vcodecs, acodecs = [], set(), set(), set()

    for fmt in raw.get("formats", []):
        if fmt.get("protocol") == "mhtml" or fmt.get("format_note") == "storyboard":
            continue
        entry = {
            "format_id": fmt.get("format_id"),
            "ext": fmt.get("ext"),
            "height": fmt.get("height"),
            "fps": fmt.get("fps"),
            "vcodec": short_codec(fmt.get("vcodec")),
            "acodec": short_codec(fmt.get("acodec")),
            "filesize": fmt.get("filesize") or fmt.get("filesize_approx"),
            "tbr": fmt.get("tbr"),
            "note": fmt.get("format_note"),
        }
        formats.append(entry)
        if fmt.get("height"):
            heights.add(fmt["height"])
        if entry["vcodec"]:
            vcodecs.add(entry["vcodec"])
        if entry["acodec"]:
            acodecs.add(entry["acodec"])

    info = {
        "title": raw.get("title"),
        "uploader": raw.get("uploader") or raw.get("channel") or raw.get("uploader_id"),
        "duration": raw.get("duration"),
        "thumbnail": raw.get("thumbnail"),
        "extractor": raw.get("extractor_key"),
        "webpage_url": raw.get("webpage_url") or url,
        "heights": sorted(heights, reverse=True),
        "vcodecs": sorted(vcodecs),
        "acodecs": sorted(acodecs),
        "formats": formats,
    }
    PROBE_CACHE[url] = (time.time(), info)
    return info


# --------------------------------------------------------------------------- downloads

PROG_PREFIX = "PROG|"


def start_job(url, opts):
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "url": url,
        "title": opts.get("title") or url,
        "status": "running",
        "stage": "Preparing",
        "percent": 0,
        "speed": None,
        "eta": None,
        "downloaded": None,
        "total": None,
        "file": None,
        "error": None,
        "created": time.time(),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=download_worker, args=(job_id, url, opts), daemon=True).start()
    return job_id


def download_worker(job_id, url, opts):
    job = JOBS[job_id]
    # yt-dlp writes the final path here; parsing it out of stdout is far less reliable.
    donefile = JOBDIR / f"{job_id}.txt"

    cmd = base_args() + [
        "--newline",
        "--concurrent-fragments", "4",
        "--progress-template",
        PROG_PREFIX + "%(progress.status)s|%(progress.downloaded_bytes)s|"
        "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
        "%(progress.speed)s|%(progress.eta)s",
        "--print-to-file", "after_move:filepath", str(donefile),
        "-f", build_selector(opts),
        "-o", str(DOWNLOAD_DIR / "%(title)s [%(id)s].%(ext)s"),
    ]

    if opts.get("mode") == "audio":
        cmd += ["-x", "--audio-format", opts.get("audio_format") or "m4a"]
    else:
        cmd += ["--merge-output-format", opts.get("container") or "mp4"]
    if opts.get("metadata"):
        cmd += ["--embed-metadata"]
    if opts.get("thumbnail"):
        cmd += ["--write-thumbnail"]
    if opts.get("subtitles"):
        cmd += ["--write-subs", "--write-auto-subs", "--sub-langs", "en,ja,ru,-live_chat"]
    cmd.append(url)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", env=run_env(),
            creationflags=NO_WINDOW,
        )
    except Exception as exc:
        finish(job, "error", error=str(exc))
        return

    job["proc"] = proc
    tail = []

    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith(PROG_PREFIX):
            apply_progress(job, line)
            continue
        if line:
            tail.append(line)
            del tail[:-40]                      # keep only the last lines for errors
        if "[Merger]" in line:
            job.update(stage="Merging tracks", percent=99)
        elif "[ExtractAudio]" in line:
            job.update(stage="Extracting audio", percent=99)
        elif "[download] Destination:" in line:
            job["stage"] = "Downloading"

    proc.wait()

    if job.get("canceled"):
        finish(job, "canceled")
    elif proc.returncode != 0:
        finish(job, "error", error=clean_error("\n".join(tail)))
    else:
        final = None
        if donefile.exists():
            paths = [l.strip() for l in
                     donefile.read_text(encoding="utf-8", errors="replace").splitlines()
                     if l.strip()]
            final = paths[-1] if paths else None
        finish(job, "done", file=final)

    donefile.unlink(missing_ok=True)


def apply_progress(job, line):
    parts = line.split("|")
    if len(parts) < 7:
        return
    _, status, got, total, total_est, speed, eta = parts[:7]

    def num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None     # yt-dlp prints 'NA' for unknown values

    got = num(got)
    total = num(total) or num(total_est)
    if got and total:
        job["percent"] = round(min(got / total * 100, 100), 1)
    job["downloaded"] = got
    job["total"] = total
    job["speed"] = num(speed)
    job["eta"] = num(eta)
    if status == "finished":
        job["percent"] = 100


def finish(job, status, file=None, error=None):
    job.update(status=status, finished=time.time(), proc=None)
    if file:
        path = Path(file)
        suffix = path.suffix.lower()
        job["file"] = path.name
        job["kind"] = "audio" if suffix in AUDIO_EXT else "video"
        job["codecs"] = file_codecs(path) if suffix in MEDIA_EXT else None
        try:
            job["size"] = path.stat().st_size
        except OSError:
            job["size"] = None
    if error:
        job["error"] = error
    job["stage"] = {
        "done": "Done", "canceled": "Canceled", "error": "Failed",
    }.get(status, job.get("stage"))
    if status == "done":
        job["percent"] = 100


def cancel_job(job_id):
    job = JOBS.get(job_id)
    if not job or job["status"] != "running":
        return False
    job["canceled"] = True
    proc = job.get("proc")
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    return True


# --------------------------------------------------------------------------- files

def file_codecs(path):
    """Report a file's real codecs, which is how opus-in-mp4 becomes visible.

    Such a file is valid and plays in media players, but most video editors cannot
    decode Opus inside an MP4 container and will import it as video with no sound.
    """
    if not FFPROBE:
        return None
    try:
        proc = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
             "-of", "json", str(path)],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=NO_WINDOW,
        )
        streams = json.loads(proc.stdout).get("streams", [])
    except Exception:
        return None

    found = {"video": None, "audio": None}
    for stream in streams:
        kind = stream.get("codec_type")
        if kind in found and not found[kind]:
            found[kind] = stream.get("codec_name")
    return found


def safe_path(name):
    """Resolve a requested filename, refusing anything outside the download folder."""
    candidate = (DOWNLOAD_DIR / os.path.basename(name)).resolve()
    if candidate.parent != DOWNLOAD_DIR or not candidate.is_file():
        return None
    return candidate


# --------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "VideoDownloader/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass                                    # keep the console readable

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # ------------------------------------------------------------------ GET

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self._static(WEB / "index.html", "text/html; charset=utf-8")

        if path == "/api/state":
            with JOBS_LOCK:
                jobs = [{k: v for k, v in job.items() if k != "proc"}
                        for job in sorted(JOBS.values(),
                                          key=lambda j: j["created"], reverse=True)]
            return self._json(200, {"jobs": jobs[:20]})

        if path == "/api/config":
            return self._json(200, {
                "download_dir": str(DOWNLOAD_DIR),
                "cookies": bool(cookie_args()),
                "ffmpeg": bool(FFMPEG),
            })

        if path.startswith("/dl/"):
            return self._serve_download(urllib.parse.unquote(path[4:]))

        if path.startswith("/media/"):
            return self._serve_media(urllib.parse.unquote(path[7:]))

        return self._json(404, {"error": "not found"})

    def _static(self, path, ctype):
        if not path.exists():
            return self._json(404, {"error": f"{path.name} not found"})
        self._send(200, path.read_bytes(), ctype)

    def _serve_download(self, name):
        """Stream a finished file, so a phone can pull it off the machine."""
        path = safe_path(name)
        if not path:
            return self._json(404, {"error": "file not found"})

        quoted = urllib.parse.quote(path.name)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile, 256 * 1024)

    def _serve_media(self, name):
        """Serve a file for inline playback, honouring Range requests.

        Without 206 responses a browser cannot seek, and Safari refuses to start
        playback at all, so range handling is not optional here.
        """
        path = safe_path(name)
        if not path:
            return self._json(404, {"error": "file not found"})

        size = path.stat().st_size
        ctype = MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
        start, end, status = 0, size - 1, 200

        header = self.headers.get("Range", "")
        if header.startswith("bytes="):
            first, _, last = header[6:].split(",")[0].strip().partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:
                    start = max(size - int(last), 0)     # suffix range: trailing N bytes
            except ValueError:
                start, end = 0, size - 1
            else:
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return

        # Seeking makes browsers abort requests mid-flight; that is normal, not an error.
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (ConnectionError, OSError):
            pass

    # ------------------------------------------------------------------ POST

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            data = self._read_json()
        except Exception:
            return self._json(400, {"error": "malformed JSON"})

        if path == "/api/probe":
            url = (data.get("url") or "").strip()
            if not url:
                return self._json(400, {"error": "no URL given"})
            try:
                return self._json(200, probe(url))
            except subprocess.TimeoutExpired:
                return self._json(504, {"error": "yt-dlp did not respond within 3 minutes"})
            except Exception as exc:
                return self._json(502, {"error": str(exc)})

        if path == "/api/download":
            url = (data.get("url") or "").strip()
            if not url:
                return self._json(400, {"error": "no URL given"})
            return self._json(200, {"id": start_job(url, data)})

        if path == "/api/cancel":
            return self._json(200, {"ok": cancel_job(data.get("id"))})

        if path == "/api/forget":
            with JOBS_LOCK:
                for job_id in [i for i, job in JOBS.items() if job["status"] != "running"]:
                    JOBS.pop(job_id, None)
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "not found"})


# --------------------------------------------------------------------------- startup

class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind a port that is already in
    # use. Both then sit on the port and requests go to whichever the OS picks, so
    # a stale instance keeps answering with old code. Disabling reuse there makes a
    # duplicate launch fail loudly instead of silently misbehaving.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def lan_addresses():
    """Every address the page may be reachable at from other devices.

    A single address cannot be guessed reliably: with a VPN running (Radmin,
    OpenVPN, ...) the interface holding the default route is the VPN rather than
    the home network, so list every candidate with private subnets first.
    """
    found = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))           # no packet is sent; this only picks a route
        found.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in found:
                found.append(addr)
    except OSError:
        pass

    usable = [a for a in found if not a.startswith(("127.", "169.254."))]
    return sorted(usable, key=lambda a: not a.startswith(("192.168.", "10.")))


def main():
    if not YTDLP:
        print("! yt-dlp not found. Install it and restart:")
        print("    Windows:  scoop install yt-dlp")
        print("    macOS:    brew install yt-dlp")
        print("    Linux:    pipx install yt-dlp")
        sys.exit(1)
    if not FFMPEG:
        print("! ffmpeg not found - video and audio tracks cannot be merged without it.")
        print("    Windows: scoop install ffmpeg | macOS: brew install ffmpeg")

    port = int(CFG["port"])
    try:
        server = Server((CFG["host"], port), Handler)
    except OSError as exc:
        print(f"! Cannot listen on port {port}: {exc}")
        print("  Another copy may already be running, or change \"port\" in config.json.")
        sys.exit(1)

    print("=" * 60)
    print("  Video Downloader is running")
    print("=" * 60)
    print(f"  This machine  : http://localhost:{port}")
    if CFG["host"] == "0.0.0.0":
        addresses = lan_addresses()
        if addresses:
            print(f"  Other devices : http://{addresses[0]}:{port}   (same network)")
            for extra in addresses[1:]:
                print(f"                  http://{extra}:{port}   (alternative)")
    print(f"  Download dir  : {DOWNLOAD_DIR}")
    print(f"  Cookies       : {'loaded' if cookie_args() else 'none (Niconico may refuse)'}")
    print("=" * 60)
    print("  Press Ctrl+C to stop")
    print(flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
