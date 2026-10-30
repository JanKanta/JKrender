#!/usr/bin/env python3
"""JKrender 0.7.2 alpha — one Nuke render, split into chunks across several Nuke processes.

Started by the Nuke integration with a job file:  python JKrender.py <render folder>/job.json

Every worker is one Nuke process that opens the script ONCE and then renders chunk after chunk,
so a chunk costs no script load. Chunks are handed out as workers free up, so no worker sits idle
while another still has a long list. Each finished frame is checked on disk (missing, empty,
truncated EXR / PNG / JPEG / DPX, suspiciously small).
"""

import collections
import concurrent.futures
import datetime
import json
import math
import os
import queue as pyqueue
import re
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

# numpy and OpenEXR for the EXR preview, fetched into lib/ on the first Nuke start (see jkrender_deps).
# Appended, so a Python that has its own copies keeps using them.
try:
    import jkrender_deps

    for _lib in jkrender_deps.lib_dirs():
        if os.path.isdir(_lib):
            sys.path.append(_lib)
except ImportError:
    pass

try:
    from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets
except ImportError:
    raise SystemExit("PySide6 is required. Install it with: python -m pip install PySide6 psutil")

try:
    import psutil
except ImportError:
    psutil = None

APP_VERSION = "0.7.2 alpha"
HOME_DIR = Path.home() / ".jkrender"
SETTINGS_FILE = HOME_DIR / "settings.json"
WORKER_SCRIPT = Path(__file__).resolve().with_name("jkrender_worker.py")

# Movie containers are one file for the whole range, so they render in one piece on one worker.
MOVIE_EXTS = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".webm", ".gif"}
MAX_ATTEMPTS = 3  # a chunk is rendered up to 3 times (each retry on a fresh Nuke) before it counts as failed
SPAWN_GAP = 0.3  # seconds between Nuke starts, so licence checkout and script loads do not all hit at once
NUKE_ENV_KEYS = ("NUKE_PATH", "PYTHONPATH", "OCIO")
SECRET_ENV_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
JKR_LINE = re.compile(r"JKR (READY|START|FRAME|CHUNK|FAIL|ERROR|BYE)\b ?(.*)$")
PROBLEMS = ("missing", "empty", "truncated", "unreadable")

DEFAULT_SETTINGS = {"auto_start": False, "max_renders": 3, "auto_tune": True, "ram_guard": True, "ram_limit": 90,
                    "test_frames": True, "test_pause": False, "auto_chunk": True}
TUNE_MIN_FRAMES = 80  # shorter renders are over before trying two splits pays off
APP_PORT = int(os.environ.get("JKRENDER_PORT") or 54360)  # a second JKrender hands its render to the one already open, through this local port
MAX_RENDERS = 3  # renders that may share the PC at once (Settings > Max renders at once)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def read_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return settings


def write_settings(settings):
    try:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception:
        pass


def is_secret_env(name):
    return any(w in name.upper() for w in SECRET_ENV_WORDS)


def nuke_render_env(job_env):
    """This app's environment with the sending Nuke session's search paths and AYON context."""
    env = dict(os.environ)
    for name in list(env):
        if name in NUKE_ENV_KEYS or (name.startswith("AYON_") and not is_secret_env(name)):
            env.pop(name)
    env.update({k: v for k, v in (job_env or {}).items() if v})
    return env


def hidden():
    """Popen kwargs that stop Windows from opening a console window for each Nuke."""
    if sys.platform.startswith("win"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000), "startupinfo": si}
    return {}


def classify_error(text):
    lines = [l.strip().lower() for l in (text or "").splitlines() if l.strip()]
    err = [
        l for l in lines
        if any(w in l for w in ("error", "traceback", "exception", "failed", "cannot", "can't", "unable"))
    ] or lines[-15:]

    def has(*needles):
        return any(n in l for l in err for n in needles)

    if has("out of memory", "bad allocation", "bad_alloc", "cuda error"):
        return "Out of memory"
    if any(("cannot open" in l or "can't open" in l or "permission denied" in l) and "write" in l for l in err) or has(
        "write error", "cannot write", "disk full", "no space left"
    ):
        return "Write error (output locked or not writable)"
    if has("no such file", "file not found", "unable to open", "cannot open"):
        return "Missing file"
    if has("error interpreting this plugin"):
        return "Startup script error (init.py / menu.py on NUKE_PATH)"
    if has("unknown command", "plugin", "gizmo"):
        return "Plugin / gizmo"
    if any(("license" in l or "licence" in l) and any(w in l for w in ("error", "fail", "unavailable", "expired")) for l in err):
        return "Licence"
    return "Nuke error"


def parse_frames(text):
    """"1001-1100", "1001-1010, 1050", "1001 1005" -> sorted unique frame numbers."""
    frames = set()
    for part in re.split(r"[,\s]+", str(text or "").strip()):
        m = re.match(r"^(-?\d+)\s*(?:-|:)\s*(-?\d+)$", part)
        if m:
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            frames.update(range(a, b + 1))
        elif re.match(r"^-?\d+$", part):
            frames.add(int(part))
    return sorted(frames)


def frames_to_spec(frames):
    out = []
    run = []
    for f in sorted(frames):
        if run and f == run[-1] + 1:
            run.append(f)
            continue
        if run:
            out.append(str(run[0]) if len(run) == 1 else "%d-%d" % (run[0], run[-1]))
        run = [f]
    if run:
        out.append(str(run[0]) if len(run) == 1 else "%d-%d" % (run[0], run[-1]))
    return ",".join(out)


def frame_path(pattern, frame):
    path = re.sub(r"%(\d*)d", lambda m: str(frame).zfill(int(m.group(1) or 0)), pattern)
    return re.sub(r"#+", lambda m: str(frame).zfill(len(m.group(0))), path)


def fmt_secs(secs):
    if secs is None:
        return "—"
    secs = int(round(secs))
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def fmt_size(n):
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0


def auto_plan(n_frames, movie):
    """Workers, threads, RAM and chunk size for this machine.

    Nuke uses many threads poorly on one frame, so several processes with 4 threads each get
    much more out of a 16-64 thread CPU than one process with all of them."""
    cpu = os.cpu_count() or 8
    ram = psutil.virtual_memory().total / 1024 ** 3 if psutil else 32.0
    if movie or n_frames <= 1:
        return {"workers": 1, "threads": cpu, "ram": max(2, int(ram * 0.7)), "chunk": max(1, n_frames)}
    workers = max(1, min(8, cpu // 4, n_frames, int(ram * 0.75 // 6) or 1))
    return {
        "workers": workers,
        "threads": max(2, cpu // workers),
        "ram": max(2, int(ram * 0.7 / workers)),
        "chunk": max(1, min(10, math.ceil(n_frames / (workers * 4)))),
    }


# ----------------------------------------------------------------------------
# Output check
# ----------------------------------------------------------------------------
EXR_LINES_PER_BLOCK = {0: 1, 1: 1, 2: 1, 3: 16, 4: 32, 5: 16, 6: 32, 7: 32, 8: 32, 9: 256}


def _exr_complete(f, size):
    """True / False for single-part scanline EXRs, None when the layout is not one this can check.

    An EXR that was cut off (Nuke killed, disk full, sync client) has zeros or out-of-file
    offsets in its block table, or its last block runs past the end of the file."""
    data = f.read(min(size, 1 << 20))
    version = struct.unpack("<I", data[4:8])[0]
    if version & (0x200 | 0x800 | 0x1000):  # tiled, deep, multipart
        return None
    pos, attrs = 8, {}
    while True:
        end = data.index(b"\0", pos)
        name = data[pos:end]
        if not name:
            pos = end + 1
            break
        tend = data.index(b"\0", end + 1)
        n = struct.unpack("<i", data[tend + 1:tend + 5])[0]
        attrs[name] = data[tend + 5:tend + 5 + n]
        pos = tend + 5 + n
    xmin, ymin, xmax, ymax = struct.unpack("<4i", attrs[b"dataWindow"])
    lines = EXR_LINES_PER_BLOCK.get(attrs[b"compression"][0])
    if lines is None:
        return None
    blocks = int(math.ceil((ymax - ymin + 1) / float(lines)))
    f.seek(pos)
    table = f.read(8 * blocks)
    if len(table) < 8 * blocks:
        return False
    offsets = struct.unpack("<%dQ" % blocks, table)
    if any(o == 0 or o >= size for o in offsets):
        return False
    last = max(offsets)
    f.seek(last)
    head = f.read(8)
    if len(head) < 8:
        return False
    return last + 8 + struct.unpack("<iI", head)[1] <= size


def check_file(path):
    """(status, size, note). status: ok / missing / empty / truncated / unreadable."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return "missing", None, ""
    if size == 0:
        return "empty", 0, ""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            if ext == ".exr":
                if head[:4] != b"\x76\x2f\x31\x01":
                    return "unreadable", size, "not an EXR"
                f.seek(0)
                ok = _exr_complete(f, size)
                if ok is False:
                    return "truncated", size, "EXR block table incomplete"
                return "ok", size, "" if ok else "header only (tiled / multipart / deep)"
            if ext == ".png":
                if head[:4] != b"\x89PNG":
                    return "unreadable", size, "not a PNG"
                f.seek(max(0, size - 12))
                return ("ok", size, "") if b"IEND" in f.read() else ("truncated", size, "no IEND")
            if ext in (".jpg", ".jpeg"):
                if head[:2] != b"\xff\xd8":
                    return "unreadable", size, "not a JPEG"
                f.seek(max(0, size - 2))
                return ("ok", size, "") if f.read() == b"\xff\xd9" else ("truncated", size, "no end marker")
            if ext == ".dpx":
                if head[:4] not in (b"SDPX", b"XPDS"):
                    return "unreadable", size, "not a DPX"
                f.seek(16)  # header field: total file size
                raw = f.read(4)
                total = struct.unpack(">I" if head[:4] == b"SDPX" else "<I", raw)[0]
                return ("ok", size, "") if size >= total else ("truncated", size, "shorter than its header says")
            if ext in (".tif", ".tiff"):
                ok = head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
                return ("ok", size, "") if ok else ("unreadable", size, "not a TIFF")
            magic = {
                ".hdr": (b"#?RADIANCE", b"#?RGBE"),
                ".cin": (b"\x80\x2a\x5f\xd7", b"\xd7\x5f\x2a\x80"),
                ".sgi": (b"\x01\xda",), ".rgb": (b"\x01\xda",), ".rgba": (b"\x01\xda",), ".bw": (b"\x01\xda",),
                ".psd": (b"8BPS",),
                ".pic": (b"\x53\x80\xf6\x34",),
                ".xpm": (b"/* XPM */",),
            }.get(ext)
            if magic and not any(head.startswith(m) for m in magic):
                return "unreadable", size, "not a %s file" % ext[1:].upper()
            # TGA, YUV, movie files and anything else: present and not empty is all this can tell.

    except Exception as exc:
        return "unreadable", size, str(exc)[:120]
    return "ok", size, ""


def _exr_rgb(path):
    import numpy as np
    import OpenEXR

    with OpenEXR.File(path) as f:
        ch = f.channels()
        for key in ("RGBA", "RGB"):
            if key in ch:
                a = np.asarray(ch[key].pixels, dtype=np.float32)
                return a[:, :, :3]
        names = [k for c in "RGB" for k in ch if k == c or k.endswith("." + c)]
        if len(names) >= 3:
            return np.stack([np.asarray(ch[k].pixels, dtype=np.float32) for k in names[:3]], -1)
        a = np.asarray(next(iter(ch.values())).pixels, dtype=np.float32)
        return np.repeat(a[:, :, None], 3, axis=2) if a.ndim == 2 else a[:, :, :3]


def preview_image(path, max_w, max_h):
    """QImage of a rendered frame; EXR is shown linear -> sRGB, clipped to 0-1."""
    if path.lower().endswith(".exr"):
        try:
            import numpy as np

            a = _exr_rgb(path)
        except ImportError:
            raise RuntimeError("the EXR preview needs numpy and OpenEXR.\nRestart Nuke to download them.")
        h, w = a.shape[:2]
        step = max(1, int(math.ceil(max(w / max(1, max_w), h / max(1, max_h)))))
        a = np.clip(np.nan_to_num(a[::step, ::step], nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
        a = np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055)
        rgb = np.ascontiguousarray((a * 255.0 + 0.5).astype(np.uint8))
        hh, ww = rgb.shape[:2]
        return QtGui.QImage(rgb.tobytes(), ww, hh, 3 * ww, QtGui.QImage.Format_RGB888).copy()
    image = QtGui.QImage(path)
    if image.isNull():
        raise RuntimeError("Qt cannot read this file type")
    return image.scaled(max_w, max_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)


# ----------------------------------------------------------------------------
# Movie files (one container for the whole range)
# ----------------------------------------------------------------------------
# Nuke's mov64_codec value -> the codec name ffprobe reports
MOV_CODECS = {"appr": "prores", "ap4x": "prores", "ap4h": "prores", "apch": "prores", "apcn": "prores",
              "apcs": "prores", "apco": "prores", "AVdn": "dnxhd", "avc1": "h264", "h264": "h264",
              "mp4v": "mpeg4", "mp1v": "mpeg1video", "rle": "qtrle", "mjpa": "mjpeg", "jpeg": "mjpeg"}
# Words of Nuke's ProRes profile -> ffprobe's profile name, most specific first
PRORES_PROFILES = (("XQ", "4444 XQ"), ("4:4:4:4", "4444"), ("HQ", "HQ"), ("LT", "LT"), ("Proxy", "Proxy"),
                   ("4:2:2", "Standard"))


def find_tool(name):
    """ffprobe / ffmpeg on PATH, or where WinGet puts them."""
    found = shutil.which(name)
    if not found and sys.platform.startswith("win"):
        link = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / (name + ".exe")
        found = str(link) if link.is_file() else None
    return found


def _rate(text):
    try:
        a, b = str(text).split("/")
        return float(a) / float(b) if float(b) else 0.0
    except Exception:
        try:
            return float(text)
        except Exception:
            return 0.0


def probe_movie(path):
    """What ffprobe says about a movie file. "error" is set when it is missing or cannot be read."""
    info = {"path": path, "error": None}
    try:
        info["size"] = os.path.getsize(path)
        info["modified"] = os.path.getmtime(path)
    except OSError:
        info["error"] = "missing"
        return info
    exe = find_tool("ffprobe")
    if not exe:
        info["no_probe"] = True
        return info
    try:
        run = subprocess.run([exe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, **hidden())
        data = json.loads(run.stdout or "{}")
    except Exception as exc:
        info["error"] = "ffprobe failed: %s" % exc
        return info
    streams = data.get("streams") or []
    video = next((st for st in streams if st.get("codec_type") == "video"), None)
    if run.returncode != 0 or video is None:
        last = (run.stderr or "").strip().splitlines()
        info["error"] = "not playable: %s" % (last[-1] if last else "no video stream (render not finished?)")
        return info
    fmt = data.get("format") or {}
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    fps = _rate(video.get("r_frame_rate") or video.get("avg_frame_rate"))
    duration = float(video.get("duration") or fmt.get("duration") or 0)
    frames = int(video.get("nb_frames") or 0) or int(round(duration * fps))
    timecode = tags.get("timecode") or (video.get("tags") or {}).get("timecode") or next(
        ((st.get("tags") or {}).get("timecode") for st in streams if (st.get("tags") or {}).get("timecode")), None)
    info.update({
        "container": fmt.get("format_long_name") or fmt.get("format_name"),
        "duration": duration, "bitrate": float(fmt.get("bit_rate") or 0), "frames": frames, "fps": fps,
        "start": float(video.get("start_time") or 0),
        "timecode": timecode, "encoder": tags.get("encoder") or (video.get("tags") or {}).get("encoder"),
        "codec": video.get("codec_name"), "codec_long": video.get("codec_long_name"), "profile": video.get("profile"),
        "width": video.get("width"), "height": video.get("height"), "sar": video.get("sample_aspect_ratio"),
        "pix_fmt": video.get("pix_fmt"), "bits": video.get("bits_per_raw_sample"),
        "range": video.get("color_range"), "matrix": video.get("color_space"),
        "transfer": video.get("color_transfer"), "primaries": video.get("color_primaries"),
        "field_order": video.get("field_order"),
        "audio": [(st.get("codec_name"), st.get("channels"), st.get("sample_rate"))
                  for st in streams if st.get("codec_type") == "audio"],
    })
    return info


def movie_checks(info, expected):
    """[(what, "ok" / "warn" / "bad", text)] for a probed movie against what the render should give."""
    if info is None:
        return []
    if info.get("error"):
        return [("File", "bad", info["error"])]
    out = [("File", "ok", "%s on disk" % fmt_size(info.get("size")))]
    if info.get("no_probe"):
        out.append(("Details", "warn", "ffprobe not found; install ffmpeg to check the movie"))
        return out
    want = expected.get("frames")
    if want:
        got = info.get("frames") or 0
        out.append(("Frames", "ok" if got == want else "bad", "%d of %d" % (got, want)))
    fps = expected.get("fps")
    if fps:
        ok = abs((info.get("fps") or 0) - fps) < 0.01
        out.append(("FPS", "ok" if ok else "warn", "%.3g (expected %.3g)" % (info.get("fps") or 0, fps)))
    if expected.get("width") and expected.get("height"):
        ok = (info.get("width"), info.get("height")) == (expected["width"], expected["height"])
        out.append(("Resolution", "ok" if ok else "warn", "%sx%s (script %dx%d)" % (
            info.get("width"), info.get("height"), expected["width"], expected["height"])))
    codec = MOV_CODECS.get((expected.get("codec") or "").strip().split("\t")[0])
    if codec:
        out.append(("Codec", "ok" if info.get("codec") == codec else "warn",
                    "%s (Write: %s)" % (info.get("codec"), codec)))
    if codec == "prores" and expected.get("profile"):
        want_profile = next((p for word, p in PRORES_PROFILES if word in expected["profile"]), None)
        if want_profile:
            got = info.get("profile") or ""
            out.append(("Profile", "ok" if got == want_profile else "warn",
                        "%s (Write: %s)" % (got or "?", expected["profile"])))
    if expected.get("timecode"):
        out.append(("Timecode", "ok" if info.get("timecode") else "warn", info.get("timecode") or "none written"))
    return out


def movie_seek(info, index):
    """Where to seek for frame `index` (0-based).

    ffmpeg returns the first frame that starts at or after the seek time, so aim a quarter frame
    before the frame starts. Aiming at its middle shows the next frame, and nothing for the last one."""
    return float(info.get("start") or 0) + max(0.0, index - 0.25) / info["fps"]


def movie_frame(path, seconds, width):
    """One frame of a movie as a QImage, via ffmpeg."""
    exe = find_tool("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found")
    run = subprocess.run([exe, "-v", "error", "-ss", "%.3f" % max(0.0, seconds), "-i", path, "-frames:v", "1",
                          "-vf", "scale=%d:-2" % width, "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True, timeout=60, **hidden())
    image = QtGui.QImage.fromData(run.stdout)
    if image.isNull():
        raise RuntimeError((run.stderr or b"").decode("utf-8", "replace").strip()[-200:] or "no frame")
    return image


# ----------------------------------------------------------------------------
# Render engine (runs in the GUI thread; only pipe readers and file checks use threads)
# ----------------------------------------------------------------------------
class Chunk:
    def __init__(self, cid, frames, whole_range=False):
        self.id = cid
        self.name = "C%02d" % cid
        self.frames = list(frames)  # frames still to render
        self.all_frames = list(frames)
        self.whole_range = whole_range
        self.test = False  # one of the test frames that go first
        self.state = "queued"  # queued / rendering / done / failed
        self.worker = None
        self.attempts = 0
        self.started = None
        self.ended = None
        self.error = ""

    @property
    def spec(self):
        return frames_to_spec(self.all_frames)


class Frame:
    __slots__ = ("state", "worker", "secs", "started", "checks", "small", "chunk")

    def __init__(self):
        self.state = "queued"  # queued / rendering / done / failed / bad / skipped
        self.worker = None
        self.secs = None
        self.started = None
        self.checks = {}  # output index -> (status, size, note)
        self.small = False
        self.chunk = None


class Worker:
    def __init__(self, wid, proc, log_path, threads, ram):
        self.id = wid
        self.name = "W%02d" % wid
        self.proc = proc
        self.threads = threads
        self.ram = ram
        self.state = "loading"  # loading / idle / busy / quitting / gone
        self.retire = False
        self.chunk = None
        self.frame = None
        self.frame_started = None
        self.spawned = time.time()
        self.ready_at = None
        self.load_secs = None
        self.frames_done = 0
        self.frame_secs = []
        self.segments = []  # [kind, label, t0, t1 or None, state]
        self.tail = collections.deque(maxlen=200)
        self.log = open(log_path, "a", encoding="utf-8", errors="replace")
        self.log_path = log_path
        self.cpu = 0.0
        self.rss = 0.0
        self.exit_code = None
        try:
            self.ps = psutil.Process(proc.pid) if psutil else None
            if self.ps:
                self.ps.cpu_percent(None)
        except Exception:
            self.ps = None
        self.segments.append(["load", "loading", self.spawned, None, "loading"])

    def send(self, line):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            return True
        except Exception:
            return False

    def close_segment(self, state):
        if self.segments and self.segments[-1][3] is None:
            self.segments[-1][3] = time.time()
            self.segments[-1][4] = state


class RenderEngine:
    def __init__(self, job, job_dir, settings):
        self.job = job
        self.dir = Path(job_dir)
        self.log_dir = self.dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.events = pyqueue.Queue()
        self.checker = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.outputs = []  # [(write name, path pattern)]
        for w in job.get("writes", []):
            pattern = w.get("output") or ""
            views = w.get("views") or ["main"]
            if "%V" in pattern or "%v" in pattern:
                for v in views:
                    self.outputs.append((w["name"], pattern.replace("%V", v).replace("%v", v[:1])))
            else:
                self.outputs.append((w["name"], pattern))
        self.movie = any(
            os.path.splitext(w.get("output") or "")[1].lower() in MOVIE_EXTS
            or (w.get("file_type") or "").lower() in ("mov", "ffmpeg", "mxf")
            for w in job.get("writes", [])
        )
        self._small_dirty = False
        self.range_text = self.default_range()
        frames = parse_frames(self.range_text)
        plan = auto_plan(len(frames), self.movie)
        self.workers_target = plan["workers"]
        self.threads = plan["threads"]
        self.ram = plan["ram"]
        self.chunk_size = plan["chunk"]
        self.state = "ready"  # ready / starting / rendering / paused / finishing / done / failed / stopped
        self.error = ""
        self.frames = {}
        self.chunks = []
        self.workers = {}
        self.next_wid = 1
        self.next_cid = 1
        self.last_spawn = 0.0
        self.started = None
        self.ended = None
        self.paused_secs = 0.0
        self.paused_at = None
        self.ever_ready = False
        self.load_failures = 0
        self.crashes = 0
        self.done_times = collections.deque(maxlen=400)
        self.pauses = []  # [(start, end)] of every pause in this run
        self.log = collections.deque(maxlen=40000)  # (worker name or "", text, level)
        self.new_log = []
        self.pending_checks = 0
        self.manager = None  # RenderManager that shares Nuke processes between renders
        self.number = 1
        self.name = Path(job.get("script") or "render").stem  # e.g. CTH_075_0060_Comp_v0004
        self.write_names = ", ".join(w["name"] for w in job.get("writes", []))
        self.everything = False
        self.resume_state = "ready"
        self.movie_info = None  # probe_movie() of the movie file, for movie output
        self.proc_cap = None  # auto-tune's process count for this render, None = whatever the manager gives
        self.tune = None  # auto-tune in progress
        self.test = None  # test frames in progress
        self.notice = None  # (text, "ok" / "warn" / "bad") for the banner while rendering
        self.rebuild_plan()

    # ---- planning -----------------------------------------------------------
    def frame_list(self):
        return sorted(self.frames)

    def rebuild_plan(self, frames=None):
        frames = parse_frames(self.range_text) if frames is None else frames
        self.frames = {f: Frame() for f in frames}
        self.chunks = []
        self.next_cid = 1
        self._chunk(frames)

    def _chunk(self, frames):
        frames = sorted(frames)
        if not frames:
            return []
        if self.movie:
            pieces = [frames]
        else:
            size = max(1, int(self.chunk_size))
            pieces = [frames[i:i + size] for i in range(0, len(frames), size)]
        made = []
        for piece in pieces:
            c = Chunk(self.next_cid, piece, whole_range=self.movie)
            self.next_cid += 1
            self.chunks.append(c)
            made.append(c)
            for f in piece:
                self.frames[f].chunk = c.name
        self.chunks.sort(key=lambda c: (min(c.all_frames), c.id))  # the table lists them in frame order
        return made

    def set_chunk_size(self, size):
        """New size for chunks nobody has started yet."""
        self.chunk_size = max(1, int(size))
        queued = [c for c in self.chunks if c.state == "queued" and c.attempts == 0]
        frames = [f for c in queued for f in c.frames]
        self.chunks = [c for c in self.chunks if c not in queued]
        self._chunk(frames)

    def default_range(self):
        """The range Nuke sent, with the slate frame in front of it when Settings asks for one.

        Projects start at 1001 with the slate on 1000; the script's range leaves the slate out."""
        text = "%s-%s" % (self.job.get("first"), self.job.get("last"))
        slate = int(self.settings.get("slate_frame", 1000))
        if self.settings.get("slate") and slate < int(self.job.get("first")):
            text = frames_to_spec([slate] + parse_frames(text))  # 1000 + 1001-1100 -> 1000-1100
        return text

    def set_range(self, text):
        if self.state not in ("ready", "stopped", "done", "failed"):
            return False
        frames = parse_frames(text)
        if not frames:
            return False
        self.range_text = text
        self.rebuild_plan(frames)
        self.state = "ready"
        return True

    def output_files(self, frame):
        return [frame_path(p, frame) for _, p in self.outputs]

    # ---- control ------------------------------------------------------------
    def nothing_to_render(self):
        return not any(fr.state in ("queued", "failed", "bad") for fr in self.frames.values())

    def start(self, everything=False):
        """everything=True renders the whole range again, including frames already on disk."""
        if self.state in ("rendering", "starting", "finishing"):
            return
        if everything:
            self.rebuild_plan()
        elif self.state in ("done", "failed", "stopped"):
            todo = [f for f, fr in self.frames.items() if fr.state not in ("done", "skipped")]
            if self.state == "done" or not todo:
                self.rebuild_plan()
            else:
                for c in self.chunks:
                    if c.state in ("failed", "rendering"):
                        c.state, c.attempts, c.error = "queued", 0, ""
                for f in todo:
                    self.frames[f].state = "queued"
        for fr in self.frames.values():
            if fr.state == "queued":  # a check from an earlier run says nothing about the frame about to be rendered
                fr.checks, fr.small = {}, False
        if not everything:
            self.scan_disk()  # frames already on disk and passing the check are never rendered twice
        if all(w.state == "gone" for w in self.workers.values()):
            # Stop closed every Nuke; the new run starts its own processes from W01 on a fresh timeline.
            self.workers = {}
            self.next_wid = 1
        self.error = ""
        self.state = "rendering"
        self.started = time.time()
        self.ended = None
        self.paused_secs = 0.0
        self.paused_at = None
        self.pauses = []
        self.done_times.clear()
        self.notice = None
        self.proc_cap = None
        if self.manager:
            self.threads = self.manager.threads
        self._make_test_chunks()
        self.note("Render started: %d frames in %d chunks, %d threads and %d GB per Nuke process"
                  % (sum(len(c.frames) for c in self.chunks if c.state == "queued"),
                     sum(1 for c in self.chunks if c.state == "queued"), self.threads, self.ram))
        self._tune_start()

    def scan_disk(self):
        """Mark frames the Write already rendered as done, so only the rest gets rendered.

        A frame counts when every output file exists and passes the check; a broken one
        (empty, cut off) stays queued and is rendered again."""
        if self.movie:
            return self._scan_movie()
        todo = [f for f, fr in self.frames.items() if fr.state == "queued"]
        checks = dict(zip(todo, self.checker.map(lambda f: [check_file(p) for p in self.output_files(f)], todo)))
        found = broken = 0
        for f in todo:
            results = checks[f]
            if all(r[0] == "ok" for r in results):
                fr = self.frames[f]
                fr.state, fr.worker, fr.secs = "done", "on disk", None
                fr.checks = dict(enumerate(results))
                found += 1
            elif any(r[0] != "missing" for r in results):
                self.frames[f].checks = {i: r for i, r in enumerate(results) if r[0] != "missing"}  # shows why
                broken += 1
        for c in self.chunks:
            if c.state == "queued":
                c.frames = [f for f in c.frames if self.frames[f].state == "queued"]
                if not c.frames:
                    c.state = "done"
        self._small_dirty = True
        if found:
            self.note("%d of %d frames are already on disk and pass the check; they count as done" % (found, len(self.frames)), "ok")
        if broken:
            self.note("!! %d frame(s) on disk are broken (empty or cut off) and will be rendered again" % broken, "err")
        return found

    def pause(self):
        """Freeze every Nuke right where it is, mid-frame. RAM stays in use, the CPU goes idle."""
        if self.state == "rendering":
            self.state = "paused"
            self.paused_at = time.time()
            n = self._freeze(True)
            self.note("Paused: %d Nuke process(es) frozen mid-frame. Resume continues exactly where they stopped." % n)

    def resume(self):
        if self.state == "paused":
            self._freeze(False)
            self._end_pause()
            self.state = "rendering"
            self.note("Resumed")

    def _end_pause(self):
        if self.paused_at is not None:
            now = time.time()
            self.pauses.append((self.paused_at, now))
            self.paused_secs += now - self.paused_at
            self.paused_at = None

    def _freeze(self, on):
        n = 0
        for w in self.workers.values():
            if w.state == "gone" or not w.ps:
                continue
            try:
                procs = [w.ps] + w.ps.children(recursive=True)
            except Exception:
                procs = [w.ps]
            for proc in procs:
                try:
                    proc.suspend() if on else proc.resume()
                except Exception:
                    pass
            n += 1
        return n

    def paused_between(self, a, b):
        """Seconds of [a, b] the render spent paused."""
        spans = list(self.pauses) + ([(self.paused_at, time.time())] if self.paused_at else [])
        return sum(max(0.0, min(b, p1) - max(a, p0)) for p0, p1 in spans)

    def stop(self, reason="Stopped by user"):
        self._end_pause()
        for w in list(self.workers.values()):
            self._kill(w, requeue=True, note="stopped")
        self._clean_temp()
        self.state = "stopped"
        self.ended = time.time()
        self.note(reason)

    def set_workers(self, n):
        self.workers_target = max(0, int(n))
        if self.state in ("rendering", "paused"):
            self._balance_workers()

    def rerender(self, frames):
        frames = sorted(set(frames) & set(self.frames))
        if not frames:
            return
        for c in self.chunks:
            if c.state == "queued":
                c.frames = [f for f in c.frames if f not in frames]
        for f in frames:
            fr = self.frames[f]
            fr.state, fr.secs, fr.checks, fr.small, fr.worker = "queued", None, {}, False, None
        self.chunks = [c for c in self.chunks if c.state != "queued" or c.frames]
        made = self._chunk(frames)
        self.note("Queued %d frame(s) again in %d chunk(s)" % (len(frames), len(made)))
        if self.state in ("ready", "done", "failed", "stopped", "finishing"):
            self.state = "rendering"
            self.started = self.started or time.time()
            self.ended = None

    def requeue_chunk(self, chunk):
        if chunk.state == "rendering":
            return
        self.rerender(chunk.all_frames)

    # ---- workers ------------------------------------------------------------
    def _process_size(self):
        """Threads and RAM of one Nuke process. A movie cannot be split, so its one process gets the whole PC."""
        if self.movie:
            plan = auto_plan(1, True)
            return plan["threads"], plan["ram"]
        return self.threads, self.ram

    def _command(self):
        names = "|".join(w["name"] for w in self.job.get("writes", []))
        script = self.job.get("snapshot") or self.job.get("script")
        original = self.job.get("script") if self.job.get("snapshot") else ""
        threads, ram = self._process_size()
        return [
            self.job.get("nuke_exe") or "Nuke",
            "-i", "-m", str(threads), "-c", "%dG" % ram,
            "-t", str(WORKER_SCRIPT), script, original, names,
        ]

    def _spawn(self):
        wid = self.manager.take_wid() if self.manager else self.next_wid
        self.next_wid = max(self.next_wid, wid) + 1
        cmd = self._command()
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        kwargs.update(hidden())
        kwargs["env"] = nuke_render_env(self.job.get("env"))
        script_dir = Path(self.job.get("script") or "").parent
        if script_dir.is_dir():
            kwargs["cwd"] = str(script_dir)
        log_path = self.log_dir / ("worker_%02d.log" % wid)
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except Exception as exc:
            self.error = "Could not start Nuke: %s" % exc
            self.note("!! " + self.error, "err")
            self.state = "failed"
            return None
        w = Worker(wid, proc, log_path, *self._process_size())
        w.log.write("=== %s | %s ===\n" % (datetime.datetime.now().isoformat(), subprocess.list2cmdline(cmd)))
        self.workers[wid] = w
        self.last_spawn = time.time()
        threading.Thread(target=self._reader, args=(w,), daemon=True).start()
        self.note("%s started (pid %d, %d threads, %d GB)" % (w.name, proc.pid, w.threads, w.ram))
        return w

    def _reader(self, w):
        try:
            for line in w.proc.stdout:
                self.events.put(("line", w.id, line))
        except Exception as exc:
            self.events.put(("line", w.id, "[output read error: %s]\n" % exc))
        finally:
            try:
                w.proc.wait(timeout=30)
            except Exception:
                pass
            self.events.put(("exit", w.id, w.proc.returncode))

    def _kill(self, w, requeue, note):
        if w.state == "gone":
            return
        try:
            w.proc.kill()
        except Exception:
            pass
        if w.frame is not None:
            self._delete_partial(w.frame, w.frame_started)
            if w.frame in self.frames and self.frames[w.frame].state == "rendering":
                self.frames[w.frame].state = "queued"
        if w.chunk is not None and requeue:
            self._requeue(w.chunk, count_attempt=False)
        w.close_segment(note)
        w.state = "gone"
        w.chunk = None
        w.frame = None

    def _delete_partial(self, frame, since):
        for p in self.output_files(frame):
            try:
                if since is None or os.path.getmtime(p) >= since - 1:
                    os.remove(p)
                    self.note("Deleted half-written %s" % os.path.basename(p))
            except OSError:
                pass

    def _clean_temp(self):
        """Nuke writes each frame to a temp file (e.g. -3181466249204226848.tmp) next to the output and
        renames it when done. A killed Nuke leaves it behind. Only call this when no Nuke is running."""
        for w in self.workers.values():
            try:
                w.proc.wait(timeout=5)  # Windows keeps the file locked until the process is gone
            except Exception:
                pass
        since = (self.started or time.time()) - 5
        removed = 0
        for folder in {os.path.dirname(p) for _, p in self.outputs}:
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                if re.match(r"^-?\d+\.tmp$", name):
                    path = os.path.join(folder, name)
                    try:
                        if os.path.getmtime(path) >= since:
                            os.remove(path)
                            removed += 1
                    except OSError:
                        pass
        if removed:
            self.note("Removed %d temp file(s) Nuke left in the output folder" % removed)

    def _requeue(self, chunk, count_attempt=True, error=""):
        chunk.frames = [f for f in chunk.frames if self.frames[f].state not in ("done", "skipped")]
        for f in chunk.frames:
            if self.frames[f].state == "rendering":
                self.frames[f].state = "queued"
        chunk.worker = None
        if count_attempt:
            chunk.attempts += 1
            chunk.error = error or chunk.error
        if not chunk.frames:
            chunk.state = "done"
        elif count_attempt and chunk.attempts >= MAX_ATTEMPTS:
            chunk.state = "failed"
            for f in chunk.frames:
                self.frames[f].state = "failed"
            self.note("!! %s failed %d times and is given up: %s" % (chunk.name, chunk.attempts, chunk.error), "err")
        else:
            chunk.state = "queued"

    def _live(self):
        return [w for w in self.workers.values() if w.state in ("loading", "idle", "busy") and not w.retire]

    def need(self):
        """Nuke processes this render could use right now."""
        if self.state == "paused":  # frozen processes keep their place
            return sum(1 for w in self.workers.values() if w.state != "gone")
        if self.state != "rendering":
            return 0
        busy = sum(1 for w in self._live() if w.state == "busy")
        return busy + sum(1 for c in self.chunks if c.state == "queued")

    def is_tail(self):
        """Rendering its last chunks: nothing left to hand out, so the next render may start loading."""
        return self.state == "rendering" and not any(c.state == "queued" for c in self.chunks)

    def _may_spawn(self):
        return self.manager is None or self.manager.free_slots() > 0

    def _balance_workers(self):
        live = self._live()
        extra = len(live) - self.workers_target
        if extra > 0:
            # Idle and still-loading ones go first; busy ones finish their chunk.
            for w in sorted(live, key=lambda w: {"idle": 0, "loading": 1, "busy": 2}[w.state])[:extra]:
                if w.state == "busy":
                    w.retire = True
                else:
                    self._quit(w, "not needed")

    def _quit(self, w, why):
        w.retire = True
        if w.state == "loading":
            self._kill(w, requeue=False, note="retired")
            self.note("%s closed (%s)" % (w.name, why))
            return
        w.state = "quitting"
        w.close_segment("done")
        w.send("QUIT")
        self.note("%s closing (%s)" % (w.name, why))

    # ---- event handling -----------------------------------------------------
    def note(self, text, level="ev"):
        entry = ("", "%s  %s" % (datetime.datetime.now().strftime("%H:%M:%S"), text), level)
        self.log.append(entry)
        self.new_log.append(entry)

    def tick(self):
        changed = False
        for _ in range(5000):
            try:
                ev = self.events.get_nowait()
            except pyqueue.Empty:
                break
            changed = True
            kind = ev[0]
            if kind == "line":
                self._on_line(self.workers.get(ev[1]), ev[2])
            elif kind == "exit":
                self._on_exit(self.workers.get(ev[1]), ev[2])
            elif kind == "check":
                self._on_check(*ev[1:])
        if self._small_dirty:
            self._flag_small()
        if self.state in ("rendering", "paused"):
            self._schedule()
            self._test_step()
            self._tune_step()
            changed = True
        elif self.state == "finishing" and self.pending_checks <= 0:
            self._finish()
            changed = True
        for w in self.workers.values():
            if w.state != "gone" and w.ps:
                try:
                    w.rss = w.ps.memory_info().rss / 1024 ** 3
                except Exception:
                    pass
        return changed

    def _on_line(self, w, line):
        if w is None:
            return
        w.log.write(line)
        w.tail.append(line)
        m = JKR_LINE.search(line.rstrip("\r\n"))
        if not m:
            text = line.rstrip()
            if text and text != ".":
                low = text.lower()
                level = "err" if any(x in low for x in ("error", "traceback", "exception")) else (
                    "warn" if "warning" in low else "")
                entry = (w.name, text, level)
                self.log.append(entry)
                self.new_log.append(entry)
            return
        kind, rest = m.group(1), m.group(2).split()
        now = time.time()
        if kind == "READY":
            w.state = "idle"
            w.ready_at = now
            w.load_secs = now - w.spawned - self.paused_between(w.spawned, now)
            w.close_segment("loaded")
            self.ever_ready = True
            self.note("%s ready, script opened in %.1f s" % (w.name, w.load_secs))
            if w.retire:
                self._quit(w, "not needed")
        elif kind == "START":
            f = int(rest[0])
            w.frame, w.frame_started = f, now
            if f in self.frames:
                self.frames[f].state = "rendering"
                self.frames[f].worker = w.name
                self.frames[f].started = now
        elif kind == "FRAME":
            f, secs = int(rest[0]), float(rest[1])
            secs = max(0.0, secs - self.paused_between(now - secs, now))  # Nuke's clock ran on while frozen
            w.frame = None
            w.frames_done += 1
            w.frame_secs.append(secs)
            if self.state == "rendering" or self.state == "paused":
                self.done_times.append(self.elapsed())  # render clock, so a pause does not drag the speed down
            fr = self.frames.get(f)
            if fr is not None:
                fr.state, fr.secs, fr.worker = "done", secs, w.name
                self._submit_check(f)
            if w.chunk and w.chunk.whole_range:
                nxt = f + 1
                if nxt in self.frames:
                    w.frame, w.frame_started = nxt, now
                    self.frames[nxt].state = "rendering"
                    self.frames[nxt].worker = w.name
        elif kind == "CHUNK":
            c = w.chunk
            if c is not None:
                c.state = "done"
                c.ended = now
                c.frames = []
            w.close_segment("done")
            w.chunk = None
            w.frame = None
            w.state = "idle"
            budget = w.ram * 1.3
            if w.rss > budget:
                self._quit(w, "recycled: %.1f GB RAM, over its %d GB budget" % (w.rss, w.ram))
            elif w.retire:
                self._quit(w, "not needed")
        elif kind == "FAIL":
            c = w.chunk
            frame = int(rest[1]) if len(rest) > 1 else None
            msg = " ".join(rest[2:])
            reason = classify_error("".join(w.tail))
            self.note("!! %s frame %s failed on %s: %s (%s)" % (c.name if c else "?", frame, w.name, msg, reason), "err")
            if frame in self.frames:
                self.frames[frame].state = "queued"
            w.close_segment("failed")
            w.chunk = None
            w.frame = None
            w.state = "idle"
            if c is not None:
                self._requeue(c, error="%s: %s" % (reason, msg))
            # The retry gets a fresh Nuke: whatever broke stays with this one.
            self._quit(w, "after a failed frame")
        elif kind == "ERROR":
            w.error = " ".join(rest)
        elif kind == "BYE":
            pass

    def _on_exit(self, w, code):
        if w is None:
            return
        w.exit_code = code
        try:
            w.log.close()
        except Exception:
            pass
        if w.state == "gone":
            return
        tail = "".join(w.tail)
        was_loading = w.state == "loading"
        expected = w.state == "quitting"
        if w.chunk is not None:
            if w.frame is not None:
                self._delete_partial(w.frame, w.frame_started)
            self._requeue(w.chunk, error=classify_error(tail) + " (Nuke exited, code %s)" % code)
        w.close_segment("done" if expected else "crashed")
        w.state = "gone"
        w.chunk = None
        w.frame = None
        if expected:
            return
        if was_loading:
            self.load_failures += 1
            reason = classify_error(tail)
            self.note("!! %s exited while opening the script (code %s): %s" % (w.name, code, reason), "err")
            if not self.ever_ready and self.load_failures >= min(2, max(1, self.workers_target)):
                self.error = "Nuke could not open the script: %s. See the %s log." % (reason, w.name)
                self.stop("Render stopped: " + self.error)
                self.state = "failed"
        else:
            self.crashes += 1
            self.note("!! %s exited unexpectedly (code %s): %s" % (w.name, code, classify_error(tail)), "err")
            if self.crashes > 3 * max(1, self.workers_target) + 3:
                self.error = "Nuke keeps crashing (%d times). See the worker logs." % self.crashes
                self.stop("Render stopped: " + self.error)
                self.state = "failed"

    # ---- movie output ---------------------------------------------------------
    def movie_path(self):
        return self.outputs[0][1] if self.outputs else ""

    def movie_expected(self):
        w = (self.job.get("writes") or [{}])[0]
        mov = w.get("movie") or {}
        return {"frames": len(self.frames), "fps": float(mov.get("fps") or self.job.get("fps") or 0),
                "width": self.job.get("width"), "height": self.job.get("height"),
                "codec": mov.get("codec"), "profile": mov.get("profile"), "timecode": mov.get("write_timecode")}

    def movie_verdict(self):
        """"ok", "warn", "bad" or None (nothing checked yet)."""
        checks = movie_checks(self.movie_info, self.movie_expected())
        if not checks:
            return None
        states = [c[1] for c in checks]
        return "bad" if "bad" in states else ("warn" if "warn" in states else "ok")

    def probe(self):
        self.movie_info = probe_movie(self.movie_path())
        return self.movie_info

    def _scan_movie(self):
        """A finished, playable movie with the right frame count counts as rendered."""
        info = self.probe()
        if info.get("error") == "missing":
            self.movie_info = None
            return 0
        if self.movie_verdict() == "bad" or info.get("no_probe"):
            if info.get("error"):
                self.note("!! %s on disk is not usable (%s); it will be rendered again"
                          % (os.path.basename(self.movie_path()), info["error"]), "err")
            return 0
        for fr in self.frames.values():
            fr.state, fr.worker = "done", "on disk"
        for c in self.chunks:
            c.frames, c.state = [], "done"
        self.note("%s is already on disk and complete; it counts as done" % os.path.basename(self.movie_path()), "ok")
        return len(self.frames)

    def _submit_check(self, frame):
        if self.movie:
            return  # one file for the whole range: it is probed when the render ends
        for i, p in enumerate(self.output_files(frame)):
            self.pending_checks += 1
            fut = self.checker.submit(check_file, p)
            fut.add_done_callback(lambda fu, f=frame, i=i: self.events.put(("check", f, i, fu.result())))

    def _on_check(self, frame, idx, result):
        self.pending_checks -= 1
        fr = self.frames.get(frame)
        if fr is None:
            return
        fr.checks[idx] = result
        if result[0] in PROBLEMS and fr.state in ("done", "skipped"):
            fr.state = "bad"
            self.note("!! frame %d: %s %s %s" % (frame, result[0], os.path.basename(self.output_files(frame)[idx]), result[2]), "err")
        self._small_dirty = True

    def _flag_small(self):
        """A frame far smaller than the median of its sequence is often black or half empty."""
        self._small_dirty = False
        medians = {}
        for idx in range(len(self.outputs)):
            sizes = [fr.checks[idx][1] for fr in self.frames.values() if fr.checks.get(idx, ("",))[0] == "ok"]
            if len(sizes) >= 5:
                medians[idx] = statistics.median(sizes)
        for fr in self.frames.values():
            fr.small = any(c[0] == "ok" and i in medians and c[1] < 0.4 * medians[i] for i, c in fr.checks.items())

    def verify_all(self):
        if self.movie:
            self.probe()
            return
        for f in self.frames:
            self._submit_check(f)

    # ---- test frames ----------------------------------------------------------
    def _make_test_chunks(self):
        """First, middle and last frame go first, each on its own, so a broken output shows up in seconds."""
        self.test = None
        if not self.settings.get("test_frames", True) or self.movie:
            return
        queued = sorted(f for f, fr in self.frames.items() if fr.state == "queued")
        if len(queued) < 6:
            return
        picks = sorted({queued[0], queued[len(queued) // 2], queued[-1]})
        for c in list(self.chunks):
            if c.state == "queued" and set(c.frames) & set(picks):
                c.frames = [f for f in c.frames if f not in picks]
                c.all_frames = [f for f in c.all_frames if f not in picks]
                if not c.all_frames:
                    self.chunks.remove(c)
        for f in picks:
            c = Chunk(self.next_cid, [f])
            c.test = True
            self.next_cid += 1
            self.chunks.append(c)
            self.frames[f].chunk = c.name
        self.chunks.sort(key=lambda c: (min(c.all_frames), c.id))
        self.test = {"frames": picks, "reported": False}
        self.note("Test frames first: %s" % ", ".join(str(f) for f in picks))

    def _test_step(self):
        t = self.test
        if not t or t["reported"]:
            return
        frames = [self.frames[f] for f in t["frames"]]
        if any(fr.state in ("queued", "rendering") for fr in frames):
            return
        if any(fr.state == "done" and not fr.checks for fr in frames):
            return  # their output check is still running
        t["reported"] = True
        bad = [f for f in t["frames"] if self.frames[f].state in ("bad", "failed")]
        if bad:
            self.notice = ("Test frame %s is broken on disk. The render is paused: look at it in Output, then "
                           "Resume or Stop." % ", ".join(str(f) for f in bad), "bad")
            self.note("!! " + self.notice[0], "err")
            self.pause()
            return
        secs = [self.frames[f].secs for f in t["frames"] if self.frames[f].secs]
        left = sum(1 for fr in self.frames.values() if fr.state in ("queued", "rendering"))
        procs = max(1, self.proc_cap or (self.manager.budget() if self.manager else self.workers_target))
        text = "Test frames %s are fine" % ", ".join(str(f) for f in t["frames"])
        if secs:
            avg = sum(secs) / len(secs)
            text += " (%.1f s each). The other %d frames should take about %s." % (avg, left, fmt_secs(left * avg / procs))
        self.note(text, "ok")
        if self.settings.get("test_pause"):
            self.notice = (text + " Paused: press Resume to render the rest.", "ok")
            self.pause()
        else:
            self.notice = (text, "ok")

    # ---- auto-tune ------------------------------------------------------------
    def _tune_start(self):
        """Try two splits of the PC on the first frames (e.g. 8 x 4 threads, then 4 x 8) and keep the faster.

        A comp that one Nuke uses well renders faster with fewer, wider processes; one that it uses
        badly, with more narrow ones. The winner is remembered for this script."""
        self.tune = None
        if not self.settings.get("auto_tune", True) or self.movie or self.manager is None:
            return
        remembered = (self.settings.get("tuned") or {}).get(self.name)
        if remembered:
            self.threads, self.proc_cap = int(remembered["threads"]), int(remembered["procs"])
            self.note("Using the split found earlier for this script: %d Nuke processes x %d threads"
                      % (self.proc_cap, self.threads))
            return
        queued = sum(len(c.frames) for c in self.chunks if c.state == "queued")
        if queued < TUNE_MIN_FRAMES:
            return
        if any(e is not self and e.state in ("rendering", "paused", "finishing") for e in self.manager.engines):
            self.note("Auto-tune skipped: other renders share the PC")
            return
        procs, threads = self.manager.budget(), self.manager.threads
        cpu = os.cpu_count() or 8
        wide = (max(1, procs // 2), min(cpu, threads * 2))
        if wide[0] == procs:
            return
        self.tune = {"configs": [(procs, threads), wide], "i": 0, "phase": "warm", "rates": [], "budget": procs}
        self.proc_cap = procs
        self.note("Auto-tune: comparing %d x %d threads with %d x %d threads on the first frames"
                  % (procs, threads, wide[0], wide[1]))

    def _tune_abort(self, why):
        """Stop comparing and go back to the first split; nothing is remembered."""
        procs, threads = self.tune["configs"][0]
        self.note("Auto-tune stopped (%s); rendering on with %d x %d threads" % (why, procs, threads))
        self.tune = None
        self._tune_apply(procs, threads)

    def _tune_apply(self, procs, threads):
        self.threads, self.proc_cap = threads, procs
        for w in list(self.workers.values()):
            if w.state in ("loading", "idle", "busy") and w.threads != threads and not w.retire:
                if w.state == "busy":
                    w.retire = True  # finishes its chunk first
                else:
                    self._quit(w, "auto-tune")

    def _tune_step(self):
        t = self.tune
        if not t or self.state != "rendering":
            return
        if any(e is not self and e.state in ("rendering", "paused") for e in self.manager.engines):
            self._tune_abort("another render started")
            return
        if self.manager.budget() < t["budget"]:
            self._tune_abort("the RAM guard lowered the number of processes")
            return
        procs, threads = t["configs"][t["i"]]
        done = len(self.done_times)
        if t["phase"] == "warm":
            ready = [w for w in self.workers.values()
                     if w.state in ("idle", "busy") and w.threads == threads and not w.retire]
            if len(ready) >= procs and all(w.frames_done >= 1 for w in ready):
                t.update(phase="measure", t0=self.elapsed(), d0=done)
            return
        need = max(8, 2 * procs)
        if done - t["d0"] < need:
            return
        rate = (done - t["d0"]) / max(0.1, self.elapsed() - t["t0"])
        t["rates"].append(rate)
        left = sum(len(c.frames) for c in self.chunks if c.state == "queued")
        if t["i"] + 1 < len(t["configs"]):
            procs2 = t["configs"][t["i"] + 1][0]
            if left < max(8, 2 * procs2) + 2 * procs2:
                self._tune_abort("too few frames left to try %d processes" % procs2)
                return
            t["i"] += 1
            t["phase"] = "warm"
            procs2, threads2 = t["configs"][t["i"]]
            self.note("Auto-tune: %d x %d threads makes %.2f frames/s; now trying %d x %d threads"
                      % (procs, threads, rate, procs2, threads2))
            self._tune_apply(procs2, threads2)
            return
        best = max(range(len(t["rates"])), key=lambda i: t["rates"][i])
        procs_b, threads_b = t["configs"][best]
        summary = ", ".join("%d x %d threads %.2f fr/s" % (c[0], c[1], r) for c, r in zip(t["configs"], t["rates"]))
        self.note("Auto-tune: %s. Keeping %d x %d threads for this script." % (summary, procs_b, threads_b), "ok")
        self._tune_apply(procs_b, threads_b)
        tuned = dict(self.settings.get("tuned") or {})
        tuned[self.name] = {"procs": procs_b, "threads": threads_b}
        self.settings["tuned"] = tuned
        write_settings(self.settings)
        self.tune = None

    def _schedule(self):
        now = time.time()
        for w in list(self.workers.values()):
            if w.state in ("loading", "idle", "busy") and w.ps:
                try:
                    w.cpu = w.ps.cpu_percent(None) / 100.0
                except Exception:
                    pass
        if self.state == "rendering":
            # Chunks go out in frame order; a retried one goes first, so a problem shows up again quickly.
            queued = sorted((c for c in self.chunks if c.state == "queued"),
                            key=lambda c: (not c.test, c.attempts == 0, min(c.frames)))
            for w in [w for w in self.workers.values() if w.state == "idle" and not w.retire]:
                if not queued:
                    break
                self._dispatch(w, self._tail_split(queued.pop(0)))
            live = self._live()
            busy = sum(1 for w in live if w.state == "busy")
            loading = sum(1 for w in live if w.state in ("loading", "idle"))
            queued_left = sum(1 for c in self.chunks if c.state == "queued")
            want = min(self.workers_target, busy + queued_left)
            if (len(live) < want and now - self.last_spawn >= SPAWN_GAP and loading < queued_left + 1
                    and self._may_spawn()):
                self._spawn()
            self._balance_workers()
            # Nothing left to hand out: close idle workers right away so their RAM is free.
            if not queued_left:
                for w in [w for w in self.workers.values() if w.state == "idle"]:
                    self._quit(w, "no chunks left")
        if self.state == "rendering" and not any(c.state in ("queued", "rendering") for c in self.chunks):
            self.state = "finishing"
            for w in list(self.workers.values()):
                if w.state in ("idle", "loading"):
                    self._quit(w, "render finished")

    def _tail_split(self, chunk):
        """Hand out at most remaining frames / (2 x processes) at a time.

        Early on that is the whole chunk; towards the end chunks shrink down to single frames,
        so every process finishes at about the same time instead of one working through a long
        last chunk while the others wait. The rest of a split chunk goes back to the queue."""
        if self.movie or chunk.test or chunk.attempts or not self.settings.get("auto_chunk", True):
            return chunk
        remaining = sum(len(c.frames) for c in self.chunks if c.state == "queued")
        procs = max(1, sum(1 for w in self.workers.values() if w.state in ("loading", "idle", "busy") and not w.retire))
        size = max(1, int(math.ceil(remaining / (2.0 * procs))))
        if len(chunk.frames) <= size:
            return chunk
        head, tail = chunk.frames[:size], chunk.frames[size:]
        rest = Chunk(self.next_cid, tail)
        self.next_cid += 1
        chunk.frames = head
        chunk.all_frames = [f for f in chunk.all_frames if f not in set(tail)]
        for f in tail:
            self.frames[f].chunk = rest.name
        self.chunks.append(rest)
        self.chunks.sort(key=lambda c: (min(c.all_frames), c.id))
        return chunk

    def _dispatch(self, w, chunk):
        chunk.state = "rendering"
        chunk.worker = w.name
        chunk.started = chunk.started or time.time()
        w.chunk = chunk
        w.state = "busy"
        for f in chunk.frames:
            self.frames[f].chunk = chunk.name
        if chunk.whole_range:
            line = "RANGE %s %d %d" % (chunk.name, min(chunk.frames), max(chunk.frames))
        else:
            line = "RENDER %s %s" % (chunk.name, frames_to_spec(chunk.frames))
        w.segments.append(["chunk", chunk.name, time.time(), None, "rendering"])
        if not w.send(line):
            self._requeue(chunk, count_attempt=False)
            w.close_segment("failed")
            w.chunk = None
            w.state = "gone"

    def _finish(self):
        self.ended = time.time()
        self._clean_temp()
        if self.movie:
            self.probe()
            for what, state, text in movie_checks(self.movie_info, self.movie_expected()):
                if state != "ok":
                    self.note("%s %s: %s" % ("!!" if state == "bad" else "!", what, text),
                              "err" if state == "bad" else "warn")
            if self.movie_verdict() == "bad":
                for fr in self.frames.values():
                    fr.state = "bad"
        failed = [c for c in self.chunks if c.state == "failed"]
        bad = [f for f, fr in self.frames.items() if fr.state == "bad"]
        if failed:
            self.state = "failed"
            self.error = "%d chunk(s) failed: %s" % (len(failed), failed[0].error)
        else:
            self.state = "done"
        self.note("Render %s in %s. %d frames, %d bad on disk." % (
            "finished" if not failed else "ended with failures", fmt_secs(self.elapsed()), len(self.frames), len(bad)),
            "err" if failed or bad else "ok")
        self.write_report()

    # ---- numbers ------------------------------------------------------------
    def elapsed(self):
        if not self.started:
            return 0.0
        end = self.ended or time.time()
        paused = self.paused_secs + ((time.time() - self.paused_at) if self.state == "paused" and self.paused_at else 0)
        return max(0.0, end - self.started - paused)

    def counts(self):
        c = collections.Counter(fr.state for fr in self.frames.values())
        return c

    def rate(self):
        """Frames per second over the last minute (or since start)."""
        now = self.elapsed()
        recent = [t for t in self.done_times if now - t <= 60]
        if len(recent) >= 3:
            span = max(1.0, now - recent[0]) if len(recent) == len(self.done_times) else 60.0
            return len(recent) / span
        el = self.elapsed()
        return (len(self.done_times) / el) if el > 0 and self.done_times else 0.0

    def eta(self):
        c = self.counts()
        left = c["queued"] + c["rendering"]
        r = self.rate()
        return left / r if r > 0 and left else (0.0 if not left else None)

    def frame_secs(self):
        return [fr.secs for fr in self.frames.values() if fr.secs is not None]

    def write_report(self):
        secs = self.frame_secs()
        report = {
            "state": self.state,
            "error": self.error,
            "started": self.started,
            "ended": self.ended,
            "elapsed": self.elapsed(),
            "frames": len(self.frames),
            "counts": dict(self.counts()),
            "workers": self.workers_target,
            "threads": self.threads,
            "ram_gb": self.ram,
            "chunk_size": self.chunk_size,
            "chunks": len(self.chunks),
            "frame_secs_sum": sum(secs),
            "frame_secs_avg": (sum(secs) / len(secs)) if secs else None,
            "bad_frames": [f for f, fr in self.frames.items() if fr.state == "bad"],
        }
        try:
            (self.dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        except Exception:
            pass

    def shutdown(self):
        for w in list(self.workers.values()):
            if w.state != "gone":
                self._kill(w, requeue=False, note="closed")
        self.checker.shutdown(wait=False, cancel_futures=True)


class RenderManager:
    """Every render in the window shares one budget of Nuke processes.

    Renders start one after another (Settings > Max renders at once allows up to 3 side by side).
    A render on its last chunks no longer counts, so the next one starts loading while it finishes.
    Free processes are dealt out in turns, oldest render first, so 8 become 8, 4+4 or 3+3+2."""

    def __init__(self, settings):
        self.settings = settings
        self.engines = []
        self.waiting = []
        plan = auto_plan(10 ** 6, False)
        self.total = int(settings.get("processes") or plan["workers"])
        self.threads = int(settings.get("threads") or plan["threads"])
        self.ram = int(settings.get("ram") or plan["ram"])
        self.next_number = 1
        self.pauses = []  # times when every render was paused: the timeline leaves them out
        self.paused_at = None
        self.ram_cap = None  # RAM guard's lower process count while memory is short
        self._ram_checked = 0.0
        self._ram_ok_since = None
        self._last_kill = 0.0

    @property
    def max_renders(self):
        return max(1, min(MAX_RENDERS, int(self.settings.get("max_renders") or MAX_RENDERS)))

    def add(self, job, job_dir):
        e = RenderEngine(job, job_dir, self.settings)
        e.manager = self
        e.number = self.next_number
        self.next_number += 1
        e.threads, e.ram = self.threads, self.ram
        e.workers_target = 0
        e.scan_disk()  # frames the Write already rendered show as done straight away
        self.engines.append(e)
        return e

    def remove(self, e):
        if e in self.waiting:
            self.waiting.remove(e)
        e.shutdown()
        if e in self.engines:
            self.engines.remove(e)

    def running(self):
        return [e for e in self.engines if e.state in ("rendering", "paused", "finishing", "waiting")]

    def live_count(self):
        return sum(1 for e in self.engines for w in e.workers.values() if w.state != "gone")

    def budget(self):
        """Processes the renders may use now: the total, unless the RAM guard holds it lower."""
        return max(1, min(self.total, self.ram_cap or self.total))

    def free_slots(self):
        return self.budget() - self.live_count()

    @staticmethod
    def ram_percent():
        return psutil.virtual_memory().percent if psutil else 0.0

    def _note_running(self, text, level="ev"):
        for e in self.engines:
            if e.state in ("rendering", "paused", "finishing"):
                e.note(text, level)

    def _ram_guard(self):
        """Fewer Nuke processes while RAM is nearly full, so Windows never starts swapping."""
        now = time.time()
        if not psutil or now - self._ram_checked < 2:
            return
        self._ram_checked = now
        if not self.settings.get("ram_guard", True):
            self.ram_cap = None
            return
        limit = float(self.settings.get("ram_limit", 90))
        pct = self.ram_percent()
        live = self.live_count()
        rendering = any(e.state == "rendering" for e in self.engines)
        if pct >= limit and live > 1 and rendering:
            # One process fewer at a time, and only once the last one has closed (busy ones finish their chunk).
            cap = max(1, min(self.budget(), live) - 1)
            if self.ram_cap is None or (live <= self.ram_cap and cap < self.ram_cap):
                self.ram_cap = cap
                self._note_running("!! RAM %.0f %% (limit %.0f %%): down to %d Nuke processes until it drops"
                                   % (pct, limit, cap), "warn")
            self._ram_ok_since = None
            if pct >= min(98.0, limit + 6) and now - self._last_kill > 5:
                # Nearly out of memory: one process gives up its chunk now instead of after it.
                busy = [(e, w) for e in self.engines for w in e.workers.values() if w.state == "busy"]
                if busy:
                    e, w = min(busy, key=lambda ew: ew[1].frame_started or 0)
                    e.note("!! RAM %.0f %%: %s closed now, its chunk goes back to the queue" % (pct, w.name), "err")
                    e._kill(w, requeue=True, note="stopped")
                    self._last_kill = now
        elif self.ram_cap is not None and pct < limit - 8:
            if self._ram_ok_since is None:
                self._ram_ok_since = now
            elif now - self._ram_ok_since >= 15:
                self.ram_cap += 1
                self._ram_ok_since = now
                if self.ram_cap >= self.total:
                    self.ram_cap = None
                    self._note_running("RAM is %.0f %% again: back to %d Nuke processes" % (pct, self.total), "ok")
                else:
                    self._note_running("RAM is %.0f %%: up to %d Nuke processes" % (pct, self.ram_cap))
        else:
            self._ram_ok_since = None

    def take_wid(self):
        """Process numbers run on across renders (W01-W08, then W09...), so every process has its own name.
        They start again at W01 once no render has any process on its timeline."""
        if not any(e.workers for e in self.engines):
            self._next_wid = 1
        wid = getattr(self, "_next_wid", 1)
        self._next_wid = wid + 1
        return wid

    def set_budget(self, total=None, threads=None, ram=None):
        if total is not None:
            self.total = max(1, int(total))
        if threads is not None:
            self.threads = int(threads)
        if ram is not None:
            self.ram = int(ram)
        for e in self.engines:
            e.threads, e.ram = self.threads, self.ram

    def request_start(self, e, everything=False):
        if e.state in ("rendering", "paused", "finishing", "waiting"):
            return
        e.everything = everything
        e.resume_state = e.state
        e.state = "waiting"
        self.waiting.append(e)
        self._admit()
        if e.state == "waiting":
            e.note("Waiting: %d render(s) already use the PC (Settings > Max renders at once)" % self.max_renders)

    def cancel(self, e):
        if e in self.waiting:
            self.waiting.remove(e)
            e.state = e.resume_state
            e.note("No longer waiting to render")

    def pause_all(self):
        for e in self.engines:
            e.pause()

    def resume_all(self):
        for e in self.engines:
            e.resume()

    def paused_between(self, a, b):
        spans = list(self.pauses) + ([(self.paused_at, time.time())] if self.paused_at else [])
        return sum(max(0.0, min(b, p1) - max(a, p0)) for p0, p1 in spans)

    def tick(self):
        changed = False
        for e in list(self.engines):
            changed = e.tick() or changed
        self._ram_guard()
        self._admit()
        self._allocate()
        all_paused = any(e.state == "paused" for e in self.engines) and not any(
            e.state in ("rendering", "finishing") for e in self.engines)
        if all_paused and self.paused_at is None:
            self.paused_at = time.time()
        elif not all_paused and self.paused_at is not None:
            self.pauses.append((self.paused_at, time.time()))
            self.paused_at = None
        return changed

    def _admit(self):
        busy = [e for e in self.engines if e.state in ("rendering", "paused") and not e.is_tail()]
        while self.waiting and len(busy) < self.max_renders:
            e = self.waiting.pop(0)
            e.state = e.resume_state
            e.start(everything=e.everything)
            if e.state == "rendering":
                busy.append(e)

    def _allocate(self):
        active = sorted((e for e in self.engines if e.state in ("rendering", "paused")),
                        key=lambda e: (e.started or 0, e.number))
        needs = {e: min(e.need(), e.proc_cap) if e.proc_cap else e.need() for e in active}
        share = {e: 0 for e in active}
        left = self.budget()
        while left > 0:
            gave = False
            for e in active:
                if left and share[e] < needs[e]:
                    share[e] += 1
                    left -= 1
                    gave = True
            if not gave:
                break
        for e in active:
            if e.workers_target != share[e]:
                e.set_workers(share[e])

    def shutdown(self):
        for e in self.engines:
            e.shutdown()


# ----------------------------------------------------------------------------
# Look
# ----------------------------------------------------------------------------
# Nuke's own interface: neutral greys with its orange highlight.
C = {
    "bg": "#282828", "panel": "#323232", "panel2": "#3b3b3b", "line": "#1c1c1c",
    "text": "#cdcdcd", "dim": "#8c8c8c", "accent": "#f7931e", "ram": "#d2d2d2",
    "queued": "#4a4a4a", "rendering": "#f7931e", "done": "#7fae58", "failed": "#d4503f",
    "bad": "#d4503f", "skipped": "#556b45", "small": "#e3c63d", "loading": "#626262", "stopped": "#7b6f60",
}
STATE_LABELS = {
    "ready": "Ready", "waiting": "Waiting", "rendering": "Rendering", "paused": "Paused", "finishing": "Checking output",
    "done": "Done", "failed": "Failed", "stopped": "Stopped", "starting": "Starting",
}

STYLE = """
* { color: %(text)s; font-family: "Segoe UI", sans-serif; font-size: 9pt; }
QMainWindow, QWidget#root { background: %(bg)s; }
QTabWidget::pane { border: 1px solid %(line)s; background: %(panel)s; top: -1px; }
QTabBar::tab { background: %(bg)s; padding: 6px 16px; border: 1px solid %(line)s; border-bottom: none; margin-right: 2px; color: %(dim)s; }
QTabBar::tab:selected { background: %(panel)s; color: %(text)s; border-top: 2px solid %(accent)s; }
QPushButton { background: %(panel2)s; border: 1px solid %(line)s; padding: 5px 14px; border-radius: 3px; }
QPushButton:hover { border-color: %(accent)s; }
QPushButton:disabled { color: #5e5e5e; }
QPushButton#primary { background: %(accent)s; color: #111; font-weight: 600; border: none; }
QPushButton#primary:disabled { background: #5c4426; color: #2a2a2a; }
QSpinBox, QLineEdit, QComboBox { background: %(panel2)s; border: 1px solid %(line)s; padding: 3px 5px; border-radius: 3px; }
QSpinBox:disabled, QLineEdit:disabled, QComboBox:disabled { background: %(bg)s; color: #666666; border-color: #2e2e2e; }
QCheckBox:disabled, QLabel:disabled { color: #666666; }
QTableWidget, QTableView, QPlainTextEdit, QTextBrowser { background: %(panel)s; border: none; gridline-color: %(line)s;
    selection-background-color: #6b4a22; alternate-background-color: #2d2d2d; }
QHeaderView::section { background: %(panel2)s; border: none; border-right: 1px solid %(line)s; padding: 4px 6px; color: %(dim)s; }
QProgressBar { background: %(panel2)s; border: none; border-radius: 3px; height: 8px; text-align: center; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }
QSplitter::handle { background: %(line)s; }
QCheckBox::indicator { width: 13px; height: 13px; }
QToolTip { background: %(panel2)s; color: %(text)s; border: 1px solid %(line)s; }
QLabel#dim { color: %(dim)s; }
QLabel#big { font-size: 15pt; font-weight: 600; }
QLabel#tileValue { font-size: 14pt; font-weight: 600; }
""" % C


def frame_color(fr):
    if fr.state == "done" and fr.small:
        return QtGui.QColor(C["small"])
    return QtGui.QColor(C.get(fr.state, C["queued"]))


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------
class FrameMap(QtWidgets.QWidget):
    """The render on one line: every chunk is a block, every frame a cell inside it, coloured by state."""

    frameClicked = QtCore.Signal(int)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setMouseTracking(True)
        self.setFixedHeight(30)
        self._cells = []
        self._blocks = []
        self._pulse = 0

    def _layout(self):
        e = self.engine
        frames = e.frame_list() if e else []
        groups = []  # [chunk name, [frames]] in frame order
        for f in frames:
            name = e.frames[f].chunk
            if groups and groups[-1][0] == name:
                groups[-1][1].append(f)
            else:
                groups.append([name, [f]])
        w = float(max(1, self.width()))
        # The gap between chunks shrinks when there are very many, so frames keep most of the width.
        gap = next((g for g in (4, 3, 2, 1) if g * len(groups) <= w * 0.2), 0)
        per = (w - gap * max(0, len(groups) - 1)) / float(max(1, len(frames)))
        h = float(self.height())
        self._cells, self._blocks = [], []
        x = 0.0
        for name, fs in groups:
            self._blocks.append((name, QtCore.QRectF(x, 0, per * len(fs), h)))
            for i, f in enumerate(fs):
                self._cells.append((f, QtCore.QRectF(x + i * per, 0, per, h)))
            x += per * len(fs) + gap

    def paintEvent(self, event):
        self._layout()
        self._pulse = (self._pulse + 1) % 20
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(C["panel"]))
        split = self._cells and self._cells[0][1].width() >= 5  # wide enough to show each frame
        for f, rect in self._cells:
            fr = self.engine.frames[f]
            color = frame_color(fr)
            if fr.state == "rendering":
                color = color.lighter(100 + abs(10 - self._pulse) * 4)
            p.fillRect(rect.adjusted(0, 0, -1 if split else 0, 0), color)
        font = p.font()
        font.setPointSizeF(7.5)
        p.setFont(font)
        for name, rect in self._blocks:
            p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 110), 1))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRect(rect.adjusted(0, 0, -1, -1))
            if name and rect.width() >= p.fontMetrics().horizontalAdvance(name) + 8:
                p.setPen(QtGui.QColor(0, 0, 0, 150))
                p.drawText(rect, QtCore.Qt.AlignCenter, name)
        p.end()

    def _frame_at(self, pos):
        for f, rect in self._cells:
            if rect.contains(QtCore.QPointF(pos)):
                return f
        return None

    def mouseMoveEvent(self, event):
        f = self._frame_at(event.position())
        if f is None:
            return
        fr = self.engine.frames[f]
        lines = ["Frame %d — %s" % (f, fr.state), "Chunk %s" % (fr.chunk or "—")]
        if fr.worker:
            lines.append("Worker %s" % fr.worker)
        if fr.secs is not None:
            lines.append("%.2f s" % fr.secs)
        for i, chk in sorted(fr.checks.items()):
            lines.append("%s  %s  %s" % (chk[0], fmt_size(chk[1]), chk[2]))
        if fr.small:
            lines.append("Much smaller than the other frames")
        QtWidgets.QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)

    def mousePressEvent(self, event):
        f = self._frame_at(event.position())
        if f is not None:
            self.frameClicked.emit(f)


class Gantt(QtWidgets.QWidget):
    """One row per Nuke process of every render, grouped by render; the picked render is bright.

    Script load in grey, each chunk as a block."""

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.selected = None
        self.show_all = True  # False: only the picked render's processes
        self.setMouseTracking(True)
        self.setMinimumHeight(120)
        self._blocks = []

    def paintEvent(self, event):
        m = self.manager
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(C["panel"]))
        engines = m.engines if self.show_all or self.selected is None else [self.selected]
        groups = [(e, sorted(e.workers.values(), key=lambda w: w.id)) for e in engines]
        groups = [(e, ws) for e, ws in groups if ws]
        self._blocks = []
        if not groups:
            p.setPen(QtGui.QColor(C["dim"]))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "Workers appear here when a render starts")
            p.end()
            return
        now = m.paused_at or time.time()

        def clock(t):  # wall time with every all-renders pause cut out
            return t - m.paused_between(0, min(t, now))

        def end_of(e, b):
            cap = e.paused_at if e.state == "paused" and e.paused_at else now
            return min(b or cap, cap)

        workers = [w for _, ws in groups for w in ws]
        running = any(e.state in ("rendering", "paused", "finishing") for e, _ in groups)
        t0 = clock(min(w.spawned for w in workers))
        t1 = max([clock(now) if running else 0] +
                 [clock(end_of(e, s[3])) for e, ws in groups for w in ws for s in w.segments])
        span = max(1.0, t1 - t0)
        many = len(m.engines) > 1
        left, top, right, gap = 46, 6, 8, (6 if len(groups) > 1 else 0)
        avail = self.height() - top - 22 - gap * (len(groups) - 1)
        row_h = max(5.0, min(22.0, avail / float(len(workers))))
        width = self.width() - left - right
        font = p.font()
        font.setPointSizeF(7.5)
        p.setFont(font)
        y = top
        for gi, (e, ws) in enumerate(groups):
            dim = many and self.selected is not None and e is not self.selected
            p.setOpacity(0.35 if dim else 1.0)
            group_top = y
            for w in ws:
                p.setPen(QtGui.QColor(C["text"] if w.state != "gone" else C["dim"]))
                if row_h >= 9:
                    label = w.name
                    p.drawText(QtCore.QRectF(0, y, left - 6, row_h), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, label)
                for kind, label, a, b, state in w.segments:
                    if a > now:
                        continue
                    a, b = clock(a), clock(end_of(e, b))
                    x = left + (a - t0) / span * width
                    bw = max(1.5, (b - a) / span * width)
                    rect = QtCore.QRectF(x, y + 1.5, bw - 1, row_h - 3)
                    if kind == "load":
                        color = QtGui.QColor(C["loading"])
                    elif state in ("rendering", "done", "failed"):
                        color = QtGui.QColor(C[state])
                    else:  # stopped, closed, retired: the chunk went back to the queue
                        color = QtGui.QColor(C["stopped"])
                    if state == "crashed":
                        color = QtGui.QColor(C["failed"])
                    p.setPen(QtCore.Qt.NoPen)
                    p.setBrush(color)
                    p.drawRoundedRect(rect, 2, 2)
                    if kind == "chunk" and bw > 30 and row_h >= 12:
                        p.setPen(QtGui.QColor("#0e0f11"))
                        p.drawText(rect, QtCore.Qt.AlignCenter, label)
                    self._blocks.append((rect, e, w, kind, label, a, b, state))
                y += row_h
            group = QtCore.QRectF(0, group_top, self.width(), y - group_top)
            p.setOpacity(1.0)
            if e.state == "paused":
                # The Nuke processes of this render are frozen.
                p.fillRect(group, QtGui.QColor(0, 0, 0, 120))
                big = p.font()
                big.setPointSizeF(14 if row_h * len(ws) > 40 else 9)
                big.setBold(True)
                p.setFont(big)
                fm = p.fontMetrics()
                box = QtCore.QRectF(0, 0, fm.horizontalAdvance("PAUSED") + 30, fm.height() + 10)
                box.moveCenter(group.center())
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(C["bg"]))
                p.drawRoundedRect(box, 6, 6)
                p.setPen(QtGui.QColor(C["accent"]))
                p.drawText(box, QtCore.Qt.AlignCenter, "PAUSED")
                p.setFont(font)
            if gi < len(groups) - 1:
                y += gap
                p.setPen(QtGui.QColor(C["line"]))
                p.drawLine(QtCore.QPointF(0, y - gap / 2.0), QtCore.QPointF(self.width(), y - gap / 2.0))
        # time axis
        p.setPen(QtGui.QColor(C["dim"]))
        axis_y = self.height() - 16
        step = next((s for s in (5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200) if span / s <= 8), 14400)
        t = 0
        while t <= span:
            x = left + t / span * width
            p.drawLine(QtCore.QPointF(x, axis_y), QtCore.QPointF(x, axis_y + 3))
            p.drawText(QtCore.QRectF(x - 30, axis_y + 2, 60, 14), QtCore.Qt.AlignCenter, fmt_secs(t))
            t += step
        p.end()

    def mouseMoveEvent(self, event):
        pos = event.position()
        for rect, e, w, kind, label, a, b, state in self._blocks:
            if rect.contains(pos):
                who = "%s (%s)" % (w.name, e.name)
                if kind == "load":
                    text = "%s opening the script: %.1f s" % (who, b - a)
                else:
                    c = next((c for c in e.chunks if c.name == label), None)
                    text = "%s on %s — %s\nframes %s\n%s" % (label, who, state, c.spec if c else "?", fmt_secs(b - a))
                QtWidgets.QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QtWidgets.QToolTip.hideText()


class RenderCard(QtWidgets.QFrame):
    """One render at the top of the window: name, progress and state. Click to look at it."""

    clicked = QtCore.Signal(object)
    closeRequested = QtCore.Signal(object)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setFixedWidth(250)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(10, 6, 6, 7)
        lay.setHorizontalSpacing(6)
        lay.setVerticalSpacing(2)
        self.badge = QtWidgets.QLabel(str(engine.number))
        self.badge.setFixedSize(20, 20)
        self.badge.setAlignment(QtCore.Qt.AlignCenter)
        self.name = QtWidgets.QLabel(engine.name)
        self.name.setStyleSheet("font-weight: 600; background: transparent;")
        self.close_btn = QtWidgets.QToolButton()
        self.close_btn.setText("×")
        self.close_btn.setToolTip("Close this render")
        self.close_btn.setStyleSheet("QToolButton { border: none; color: %s; font-size: 12pt; background: transparent; }"
                                     "QToolButton:hover { color: %s; }" % (C["dim"], C["text"]))
        self.close_btn.clicked.connect(lambda: self.closeRequested.emit(self.engine))
        self.write = QtWidgets.QLabel(engine.write_names)
        self.write.setStyleSheet("color: %s; background: transparent;" % C["dim"])
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("background: transparent;")
        lay.addWidget(self.badge, 0, 0)
        lay.addWidget(self.name, 0, 1)
        lay.addWidget(self.close_btn, 0, 2)
        lay.addWidget(self.write, 1, 1, 1, 2)
        lay.addWidget(self.bar, 2, 0, 1, 3)
        lay.addWidget(self.status, 3, 0, 1, 3)
        lay.setColumnStretch(1, 1)

    def refresh(self, selected, closable):
        e = self.engine
        c = e.counts()
        total = max(1, len(e.frames))
        finished = c["done"] + c["skipped"] + c["bad"]
        self.bar.setMaximum(total)
        self.bar.setValue(finished)
        color = {"rendering": C["rendering"], "finishing": C["rendering"], "done": C["done"], "failed": C["failed"],
                 "paused": C["accent"], "stopped": C["accent"], "waiting": C["dim"]}.get(e.state, C["dim"])
        self.status.setText('<span style="color:%s">%s</span>&nbsp;&nbsp;<span style="color:%s">%d / %d</span>'
                            % (color, STATE_LABELS.get(e.state, e.state), C["dim"], finished, total))
        self.badge.setStyleSheet("background: %s; color: %s; border-radius: 3px; font-weight: 600;"
                                 % (C["accent"] if selected else C["line"], "#111" if selected else C["text"]))
        self.setStyleSheet("RenderCard { background: %s; border: 1px solid %s; border-radius: 5px; }"
                           % (C["panel2"] if selected else C["panel"], C["accent"] if selected else C["line"]))
        self.close_btn.setVisible(closable)

    def mousePressEvent(self, event):
        self.clicked.emit(self.engine)


class Graph(QtWidgets.QWidget):
    """Line history of a few series over the last few minutes."""

    def __init__(self, title, series, max_value=None, unit="%"):
        super().__init__()
        self.title = title
        self.series = series  # [(key, label, color)]
        self.max_value = max_value
        self.unit = unit
        self.data = {k: collections.deque(maxlen=300) for k, _, _ in series}
        self.setMinimumHeight(130)

    def add(self, values):
        for k in self.data:
            self.data[k].append(values.get(k, 0.0) or 0.0)
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(C["panel2"]))
        r = self.rect().adjusted(8, 22, -8, -8)
        top = self.max_value or max([1.0] + [v for d in self.data.values() for v in d]) * 1.15
        p.setPen(QtGui.QPen(QtGui.QColor(C["line"]), 1))
        for i in range(1, 4):
            y = r.top() + r.height() * i / 4.0
            p.drawLine(QtCore.QPointF(r.left(), y), QtCore.QPointF(r.right(), y))
        x_text = 8
        p.setPen(QtGui.QColor(C["dim"]))
        p.drawText(QtCore.QRectF(8, 3, 200, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self.title)
        x_text = 8 + p.fontMetrics().horizontalAdvance(self.title) + 16
        for key, label, color in self.series:
            d = self.data[key]
            if len(d) >= 2:
                path = QtGui.QPainterPath()
                n = d.maxlen
                for i, v in enumerate(d):
                    x = r.right() - (len(d) - 1 - i) * r.width() / float(n - 1)
                    y = r.bottom() - min(1.0, v / top) * r.height()
                    path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
                p.setPen(QtGui.QPen(QtGui.QColor(color), 1.6))
                p.drawPath(path)
            value = d[-1] if d else 0
            text = "%s %.0f%s" % (label, value, self.unit) if self.unit == "%" else "%s %.1f %s" % (label, value, self.unit)
            p.setPen(QtGui.QColor(color))
            p.drawText(QtCore.QRectF(x_text, 3, 220, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)
            x_text += p.fontMetrics().horizontalAdvance(text) + 14
        p.end()


class CoreGrid(QtWidgets.QWidget):
    """Load of every logical CPU thread."""

    def __init__(self):
        super().__init__()
        self.values = []
        self.setMinimumHeight(56)

    def set_values(self, values):
        self.values = values
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(C["panel2"]))
        n = len(self.values)
        if not n:
            p.end()
            return
        r = self.rect().adjusted(8, 20, -8, -6)
        p.setPen(QtGui.QColor(C["dim"]))
        p.drawText(QtCore.QRectF(8, 2, 400, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   "CPU threads (%d)" % n)
        bw = r.width() / float(n)
        for i, v in enumerate(self.values):
            x = r.left() + i * bw
            p.fillRect(QtCore.QRectF(x + 1, r.top(), bw - 2, r.height()), QtGui.QColor(C["queued"]))
            h = r.height() * min(100.0, v) / 100.0
            color = QtGui.QColor(C["done"] if v < 70 else (C["accent"] if v < 92 else C["failed"]))
            p.fillRect(QtCore.QRectF(x + 1, r.bottom() - h, bw - 2, h), color)
        p.end()


class Tile(QtWidgets.QFrame):
    def __init__(self, title):
        super().__init__()
        self.setStyleSheet("QFrame { background: %s; border-radius: 4px; }" % C["panel2"])
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        t = QtWidgets.QLabel(title)
        t.setObjectName("dim")
        self.value = QtWidgets.QLabel("—")
        self.value.setObjectName("tileValue")
        self.sub = QtWidgets.QLabel("")
        self.sub.setObjectName("dim")
        lay.addWidget(t)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)

    def set(self, value, sub=""):
        self.value.setText(value)
        self.sub.setText(sub)


class ZoomView(QtWidgets.QWidget):
    """An image fitted to the widget. Mouse wheel zooms around the cursor, drag pans, double-click fits again.

    The view is kept as a zoom factor and a centre point in image fractions, so it stays put when
    the next frame of a playing movie (or a sharper still of the same frame) replaces the image."""

    def __init__(self):
        super().__init__()
        self.image = None
        self.text = ""
        self.zoom = 1.0  # 1 = whole image fits
        self.center = QtCore.QPointF(0.5, 0.5)
        self._drag = None
        self.setMouseTracking(True)
        self.setMinimumSize(420, 260)

    def set_image(self, image):
        self.image = image if image is not None and not image.isNull() else None
        self.text = ""
        self.update()

    def set_text(self, text):
        self.image = None
        self.text = text
        self.update()

    def reset_view(self):
        self.zoom, self.center = 1.0, QtCore.QPointF(0.5, 0.5)
        self.update()

    def _scale(self):
        fit = min(self.width() / float(self.image.width()), self.height() / float(self.image.height()))
        return fit * self.zoom

    def _target(self):
        sc = self._scale()
        w, h = self.image.width() * sc, self.image.height() * sc
        x = self.width() / 2.0 - self.center.x() * w
        y = self.height() / 2.0 - self.center.y() * h
        return QtCore.QRectF(x, y, w, h)

    def _clamp(self):
        half_w = min(0.5, 0.5 / self.zoom)
        half_h = min(0.5, 0.5 / self.zoom)
        self.center = QtCore.QPointF(min(1 - half_w, max(half_w, self.center.x())),
                                     min(1 - half_h, max(half_h, self.center.y())))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor("#0d0e10"))
        if self.image is None:
            p.setPen(QtGui.QColor(C["dim"]))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, self.text)
            p.end()
            return
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, self.zoom < 4)
        p.drawImage(self._target(), self.image)
        if self.zoom > 1.001:
            label = "%d %%" % round(self._scale() * 100)
            p.setPen(QtGui.QColor(C["text"]))
            p.fillRect(QtCore.QRectF(6, 6, 58, 18), QtGui.QColor(0, 0, 0, 150))
            p.drawText(QtCore.QRectF(6, 6, 58, 18), QtCore.Qt.AlignCenter, label)
        p.end()

    def wheelEvent(self, event):
        if self.image is None:
            return
        pos = event.position()
        before = self._target()
        fx = (pos.x() - before.x()) / before.width()
        fy = (pos.y() - before.y()) / before.height()
        step = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.zoom = max(1.0, min(64.0, self.zoom * step))
        # Keep the image point under the cursor where it is.
        sc = self._scale()
        w, h = self.image.width() * sc, self.image.height() * sc
        self.center = QtCore.QPointF(fx - (pos.x() - self.width() / 2.0) / w, fy - (pos.y() - self.height() / 2.0) / h)
        self._clamp()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.image is not None and self.zoom > 1.001:
            self._drag = (event.position(), QtCore.QPointF(self.center))
            self.setCursor(QtCore.Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag and self.image is not None:
            start, center = self._drag
            target = self._target()
            d = event.position() - start
            self.center = QtCore.QPointF(center.x() - d.x() / target.width(), center.y() - d.y() / target.height())
            self._clamp()
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.setCursor(QtCore.Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, event):
        self.reset_view()


def play_icon(playing):
    """Pause bars while playing, a play triangle otherwise."""
    pix = QtGui.QPixmap(32, 32)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(C["accent"]))
    if playing:
        p.drawRoundedRect(QtCore.QRectF(8, 7, 6, 18), 1.5, 1.5)
        p.drawRoundedRect(QtCore.QRectF(18, 7, 6, 18), 1.5, 1.5)
    else:
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(10, 6), QtCore.QPointF(26, 16), QtCore.QPointF(10, 26)]))
    p.end()
    return QtGui.QIcon(pix)


class FramesModel(QtCore.QAbstractTableModel):
    COLS = ["Frame", "State", "Chunk", "Worker", "Time", "Size", "Check"]

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.mode = "All"
        self.rows = []
        self.refilter()

    def refilter(self):
        self.beginResetModel()
        frames = self.engine.frame_list()
        if self.mode == "Problems":
            frames = [f for f in frames if self._problem(self.engine.frames[f])]
        elif self.mode == "Rendered":
            frames = [f for f in frames if self.engine.frames[f].state in ("done", "bad", "skipped")]
        self.rows = frames
        self.endResetModel()

    @staticmethod
    def _problem(fr):
        return fr.state in ("bad", "failed") or fr.small

    def touch(self):
        if self.mode == "Problems":
            now = [f for f in self.engine.frame_list() if self._problem(self.engine.frames[f])]
            if now != self.rows:
                self.refilter()
                return
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(self.COLS) - 1))

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return len(self.COLS)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole and orientation == QtCore.Qt.Horizontal:
            return self.COLS[section]
        return None

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        f = self.rows[index.row()]
        fr = self.engine.frames.get(f)
        if fr is None:
            return None
        col = index.column()
        if role == QtCore.Qt.DisplayRole:
            if col == 0:
                return str(f)
            if col == 1:
                return fr.state
            if col == 2:
                return fr.chunk or ""
            if col == 3:
                return fr.worker or ""
            if col == 4:
                return "%.2f s" % fr.secs if fr.secs is not None else ""
            if col == 5:
                sizes = [c[1] for c in fr.checks.values() if c[1] is not None]
                return fmt_size(sum(sizes)) if sizes else ""
            if col == 6:
                if not fr.checks:
                    return ""
                bad = [c for c in fr.checks.values() if c[0] != "ok"]
                if bad:
                    return "%s %s" % (bad[0][0], bad[0][2])
                return "small (check it)" if fr.small else "ok"
        if role == QtCore.Qt.ForegroundRole and col in (1, 6):
            if fr.state in ("bad", "failed"):
                return QtGui.QColor(C["failed"])
            if fr.small:
                return QtGui.QColor(C["small"])
            if fr.state in ("done", "skipped"):
                return QtGui.QColor(C["done"])
            if fr.state == "rendering":
                return QtGui.QColor(C["rendering"])
        if role == QtCore.Qt.UserRole:
            return f
        return None


# ----------------------------------------------------------------------------
# Window
# ----------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, job_path, exit_when_done=False):
        super().__init__()
        self.settings = read_settings()
        self.manager = RenderManager(self.settings)
        self.exit_when_done = exit_when_done
        self.cards = {}
        self._last_states = {}
        self._log_filter = "All"
        self._preview_frame = None
        self._gpu = None
        self._io_last = None
        self._net_last = None
        self.server = None
        self.setWindowTitle("JKrender %s" % APP_VERSION)
        self.resize(1320, 900)
        job_path = Path(job_path)
        # Before the window shows: frames the Write already rendered appear as done.
        self.engine = self.manager.add(json.loads(job_path.read_text(encoding="utf-8")), job_path.parent)
        self._build()
        self._add_card(self.engine)
        self.select(self.engine)
        if psutil:
            psutil.cpu_percent(percpu=True)
        if shutil.which("nvidia-smi"):
            threading.Thread(target=self._gpu_loop, daemon=True).start()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)
        self.slow = QtCore.QTimer(self)
        self.slow.timeout.connect(self._slow_tick)
        self.slow.start(1000)
        if self.settings.get("auto_start", False) and not self.engine.nothing_to_render():
            QtCore.QTimer.singleShot(300, self._start)

    # ---- renders ------------------------------------------------------------
    def listen(self):
        """Become the JKrender every later render from Nuke is handed to."""
        self.server = QtNetwork.QTcpServer(self)
        self.server.newConnection.connect(self._on_connection)
        return self.server.listen(QtNetwork.QHostAddress.LocalHost, APP_PORT)

    def _on_connection(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.data = bytearray()
            sock.readyRead.connect(lambda sock=sock: self._on_message(sock))

    def _on_message(self, sock):
        """One line of JSON in, "OK" back. Qt drops a connection whose sender half-closes, so no shutdown."""
        sock.data.extend(bytes(sock.readAll()))
        if b"\n" not in sock.data:
            return
        try:
            job_path = json.loads(bytes(sock.data).decode("utf-8"))["job"]
            reply = b"OK"
        except Exception as exc:
            job_path, reply = None, ("ERROR %s" % exc).encode("utf-8")
        sock.write(reply)
        sock.flush()
        sock.disconnectFromHost()
        if job_path:
            QtCore.QTimer.singleShot(0, lambda: self.add_job(job_path))

    def add_job(self, job_path):
        job_path = Path(job_path)
        job = json.loads(job_path.read_text(encoding="utf-8"))
        outputs = sorted(w.get("output") or "" for w in job.get("writes", []))
        for e in list(self.manager.engines):
            if sorted(w.get("output") or "" for w in e.job.get("writes", [])) != outputs:
                continue
            if e.state in ("rendering", "paused", "finishing", "waiting"):
                self.select(e)
                self._bring_to_front()
                QtWidgets.QMessageBox.information(self, "JKrender", "%s is already rendering here." % e.write_names)
                return
            self._remove_engine(e)  # the newer send wins: it carries a fresh snapshot of the script
        e = self.manager.add(job, job_path.parent)
        self._add_card(e)
        self.select(e)
        self._bring_to_front()
        if self.settings.get("auto_start", False) and not e.nothing_to_render():
            self.manager.request_start(e)
            self._sync_controls()

    def _bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _add_card(self, e):
        card = RenderCard(e)
        card.clicked.connect(self.select)
        card.closeRequested.connect(self._close_card)
        self.cards_row.insertWidget(self.cards_row.count() - 1, card)
        self.cards[e] = card
        self._refresh_cards()

    def _refresh_cards(self):
        for e, card in self.cards.items():
            card.refresh(e is self.engine, len(self.cards) > 1)

    def _close_card(self, e):
        if e.state in ("rendering", "paused", "finishing", "waiting"):
            ask = QtWidgets.QMessageBox.question(
                self, "JKrender", "%s is still rendering.\n\nStop it and close it?" % e.name,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if ask != QtWidgets.QMessageBox.Yes:
                return
            if e.state == "waiting":
                self.manager.cancel(e)
            else:
                e.stop("Stopped: render closed")
        e.write_report()
        self._remove_engine(e)

    def _remove_engine(self, e):
        card = self.cards.pop(e, None)
        if card is not None:
            card.setParent(None)
            card.deleteLater()
        self._last_states.pop(e, None)
        self.manager.remove(e)
        if e is self.engine and self.manager.engines:
            self.select(self.manager.engines[0])
        self._refresh_cards()
        self._sync_controls()

    def select(self, e):
        """Show one render: header, frame map, chunks, output, log and details all follow it."""
        if hasattr(self, "_play_timer"):
            self._movie_stop()
        self.engine = e
        self._last_states[e] = e.state  # shown below; the next tick need not show it again
        self.frame_map.engine = e
        self.frames_model.engine = e
        self.frames_model.refilter()
        self.gantt.selected = e
        self.title.setText(e.name)
        self.subtitle.setText("%s  ·  %s" % (e.write_names, e.job.get("script", "")))
        for box, value in ((self.s_chunk, e.chunk_size),):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        self.range_edit.setText(e.range_text)
        self.chunk_table.setRowCount(0)
        self._chunk_cols_sized = False
        self._preview_frame = None
        self.preview.setPixmap(QtGui.QPixmap())
        self.preview.setText("Click a frame to see it")
        self.preview_info.setText("")
        self.out_pages.setCurrentIndex(1 if e.movie else 0)
        if e.movie:
            self._movie_size_last = None
            self._update_movie()
            self._movie_show(0.5)
        self._log_refilter()
        self._state_changed()
        self._refresh_cards()
        self._slow_tick()
        self.frame_map.update()
        self.gantt.update()

    # ---- layout -------------------------------------------------------------
    def _build(self):
        root = QtWidgets.QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QtWidgets.QVBoxLayout(root)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # one card per render; click a card to look at that render
        self.cards_row = QtWidgets.QHBoxLayout()
        self.cards_row.setSpacing(8)
        self.cards_row.addStretch(1)
        lay.addLayout(self.cards_row)

        # header
        head = QtWidgets.QHBoxLayout()
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(0)
        self.title = QtWidgets.QLabel("")
        self.title.setObjectName("big")
        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setObjectName("dim")
        self.subtitle.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.state_chip = QtWidgets.QLabel("Ready")
        self.state_chip.setAlignment(QtCore.Qt.AlignCenter)
        self.state_chip.setMinimumWidth(140)
        head.addWidget(self.state_chip)
        lay.addLayout(head)

        # numbers
        stats = QtWidgets.QHBoxLayout()
        stats.setSpacing(8)
        self.t_frames = Tile("Frames")
        self.t_elapsed = Tile("Elapsed")
        self.t_eta = Tile("Remaining")
        self.t_speed = Tile("Speed")
        self.t_avg = Tile("Frame time")
        self.t_workers = Tile("Nuke processes")
        self.t_check = Tile("Output check")
        for t in (self.t_frames, self.t_elapsed, self.t_eta, self.t_speed, self.t_avg, self.t_workers, self.t_check):
            stats.addWidget(t, 1)
        lay.addLayout(stats)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(8)
        lay.addWidget(self.progress)
        self.frame_map = FrameMap(self.engine)
        self.frame_map.frameClicked.connect(self._show_frame)
        lay.addWidget(self.frame_map)
        legend = QtWidgets.QLabel(
            " ".join('<span style="color:%s">■</span> %s&nbsp;&nbsp;' % (C[k], t) for k, t in (
                ("queued", "queued"), ("rendering", "rendering"), ("done", "done"),
                ("small", "suspiciously small"), ("failed", "failed / broken"))))
        legend.setObjectName("dim")
        lay.addWidget(legend)

        # controls
        ctl = QtWidgets.QHBoxLayout()
        ctl.setSpacing(6)
        self.b_start = QtWidgets.QPushButton("Render")
        self.b_start.setObjectName("primary")
        self.b_pause = QtWidgets.QPushButton("Pause")
        self.b_stop = QtWidgets.QPushButton("Stop")
        self.b_start.clicked.connect(self._start)
        self.b_pause.clicked.connect(self._pause)
        self.b_stop.clicked.connect(self._stop)
        self.b_render_all = QtWidgets.QPushButton("Render all")
        self.b_render_all.setToolTip("Render every render in the window that still has frames to render.\n"
                                     "Settings > Max renders at once decides how many run side by side.")
        self.b_render_all.clicked.connect(self._render_all)
        self.b_pause_all = QtWidgets.QPushButton("Pause all")
        self.b_pause_all.setToolTip("Pause or resume every render in the window")
        self.b_pause_all.clicked.connect(self._pause_all)
        for b in (self.b_start, self.b_render_all, self.b_pause, self.b_stop, self.b_pause_all):
            ctl.addWidget(b)
        ctl.addSpacing(14)

        def spin(label, lo, hi, value, suffix, tip):
            box = QtWidgets.QSpinBox()
            box.setRange(lo, hi)
            box.setValue(value)
            box.setSuffix(suffix)
            box.setToolTip(tip)
            lab = QtWidgets.QLabel(label)
            lab.setObjectName("dim")
            ctl.addWidget(lab)
            ctl.addWidget(box)
            return box

        cpu = os.cpu_count() or 8
        ram = int(psutil.virtual_memory().total / 1024 ** 3) if psutil else 64
        e, m = self.engine, self.manager
        self.s_workers = spin("Processes", 1, max(1, cpu // 2), m.total, "",
                              "Nuke processes for all renders together. One render gets them all;\n"
                              "renders side by side share them (8 -> 4 + 4 -> 3 + 3 + 2).")
        self.s_threads = spin("Threads", 1, cpu, m.threads, "",
                              "Threads per Nuke process (-m).")
        self.s_ram = spin("RAM", 1, ram, m.ram, " GB",
                          "Cache per Nuke process (-c). A process that grows past 1.3x this\n"
                          "is replaced by a fresh one after its chunk, so it never slows down.")
        self.s_chunk = spin("Chunk", 1, 10000, e.chunk_size, " fr",
                            "Frames per chunk. Applies to chunks that have not started yet.\n"
                            "With Settings > Auto chunk size this is the largest chunk; towards the end they get smaller.")
        self.range_edit = QtWidgets.QLineEdit(e.range_text)
        self.range_edit.setMaximumWidth(140)
        self.range_edit.setToolTip("Frames to render, e.g. 1001-1100 or 1001-1010,1050. Editable while not rendering.")
        lab = QtWidgets.QLabel("Frames")
        lab.setObjectName("dim")
        ctl.addWidget(lab)
        ctl.addWidget(self.range_edit)
        self.b_auto = QtWidgets.QPushButton("Auto")
        self.b_auto.setToolTip("Processes, threads, RAM and chunk size worked out for this PC")
        self.b_auto.clicked.connect(self._auto)
        ctl.addWidget(self.b_auto)
        ctl.addStretch(1)
        lay.addLayout(ctl)
        self.s_workers.valueChanged.connect(self._workers_changed)
        self.s_threads.valueChanged.connect(lambda v: self._budget_changed("threads", v))
        self.s_ram.valueChanged.connect(lambda v: self._budget_changed("ram", v))
        self.s_chunk.valueChanged.connect(self._chunk_changed)
        self.range_edit.editingFinished.connect(self._range_changed)

        self.banner = QtWidgets.QLabel("")
        self.banner.setWordWrap(True)
        self.banner.hide()
        lay.addWidget(self.banner)

        # Top half: Workers (process timeline) / Chunks / Log / Details. Bottom half: Output and System.
        self.tabs = QtWidgets.QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        for name in ("Workers", "Chunks", "Log", "Details"):
            self.tabs.addTab(name)
        lay.addWidget(self.tabs)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.gantt = Gantt(self.manager)
        self.gantt.show_all = bool(self.settings.get("show_all_workers", True))
        self.pages = QtWidgets.QStackedWidget()
        for page in (self.gantt, self._chunks_table(), self._log_tab(), self._details_tab()):
            self.pages.addWidget(page)
        split.addWidget(self.pages)
        self.sub_tabs = QtWidgets.QTabWidget()
        self.sub_tabs.addTab(self._output_tab(), "Output")
        self.sub_tabs.addTab(self._system_tab(), "System")
        self.sub_tabs.addTab(self._settings_tab(), "Settings")
        split.addWidget(self.sub_tabs)
        split.setSizes([360, 400])
        lay.addWidget(split, 1)
        self.tabs.currentChanged.connect(self._page_changed)

    def _page_changed(self, index):
        self.pages.setCurrentIndex(index)
        if index == 3:
            self._update_details()

    def _settings_tab(self):
        """Two columns: how renders are handled, and speed / safety."""
        w = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(w)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(40)

        def column(title):
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(7)
            head = QtWidgets.QLabel(title)
            head.setStyleSheet("color: %s; font-weight: 600; font-size: 10pt;" % C["accent"])
            col.addWidget(head)
            outer.addLayout(col)
            return col

        def check(col, text, key, default, tip, slot=None):
            box = QtWidgets.QCheckBox(text)
            box.setChecked(bool(self.settings.get(key, default)))
            box.setToolTip(tip)
            box.toggled.connect(slot or (lambda v, key=key: self._save_setting(key, v)))
            col.addWidget(box)
            return box

        def spin_row(col, label, widget, after=""):
            row = QtWidgets.QHBoxLayout()
            if label:
                row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(widget)
            if after:
                row.addWidget(QtWidgets.QLabel(after))
            row.addStretch(1)
            col.addLayout(row)

        # -- renders
        left = column("Renders")
        self.c_auto = check(left, "Start Render on open", "auto_start", False,
                            "Render as soon as the window opens, without clicking Render")
        self.s_max = QtWidgets.QSpinBox()
        self.s_max.setRange(1, MAX_RENDERS)
        self.s_max.setValue(self.manager.max_renders)
        self.s_max.setToolTip(
            "1: renders go one after another; the next one starts loading while the last chunks\n"
            "of the current one finish, so the PC never sits idle. That gets the first shot done soonest.\n"
            "2-3: renders run side by side and share the Nuke processes. Not faster in total,\n"
            "but you see frames of every shot early.")
        self.s_max.valueChanged.connect(lambda v: self._save_setting("max_renders", v))
        spin_row(left, "Max renders at once", self.s_max)
        slate_row = QtWidgets.QHBoxLayout()
        self.c_slate = QtWidgets.QCheckBox("Render slate frame")
        self.c_slate.setChecked(bool(self.settings.get("slate")))
        self.c_slate.setToolTip("The project range starts after the slate (e.g. 1001 with the slate on 1000).\n"
                                "With this on, every render also renders the slate frame.")
        self.s_slate = QtWidgets.QSpinBox()
        self.s_slate.setRange(-100000, 1000000)
        self.s_slate.setValue(int(self.settings.get("slate_frame", 1000)))
        self.s_slate.setToolTip("Frame number of the slate")
        self.c_slate.toggled.connect(lambda v: self._slate_changed("slate", v))
        self.s_slate.valueChanged.connect(lambda v: self._slate_changed("slate_frame", v))
        slate_row.addWidget(self.c_slate)
        slate_row.addWidget(QtWidgets.QLabel("on frame"))
        slate_row.addWidget(self.s_slate)
        slate_row.addStretch(1)
        left.addLayout(slate_row)
        self.c_all_workers = check(left, "Show processes of all renders", "show_all_workers", True,
                                   "On: Workers and System show the Nuke processes of every render in the window.\n"
                                   "Off: only those of the render you picked at the top.", self._all_workers_changed)
        left.addStretch(1)

        # -- speed and safety
        right = column("Speed and safety")
        self.c_tune = check(right, "Auto-tune processes", "auto_tune", True,
                            "On the first frames of a render with at least %d frames, try the processes as set\n"
                            "(e.g. 8 x 4 threads) and half as many with twice the threads (4 x 8), and keep the faster.\n"
                            "The winner is remembered for that script; later renders of it start with it." % TUNE_MIN_FRAMES)
        tuned = QtWidgets.QPushButton("Forget tuned scripts")
        tuned.setToolTip("Auto-tune measures every script again on its next render")
        tuned.clicked.connect(self._forget_tuned)
        spin_row(right, "", tuned)
        self.c_chunk_auto = check(right, "Auto chunk size", "auto_chunk", True,
                                  "Chunks get smaller towards the end of a render, down to single frames,\n"
                                  "so every Nuke process finishes at about the same time.\n"
                                  "Off: every chunk keeps the size set under Chunk.")
        self.c_guard = check(right, "RAM guard", "ram_guard", True,
                             "When RAM goes over the limit, fewer Nuke processes run (busy ones finish their chunk first)\n"
                             "until it is %d %% below the limit; then one more every 15 s. Close to full, one process is closed at once." % 8)
        self.s_limit = QtWidgets.QSpinBox()
        self.s_limit.setRange(50, 98)
        self.s_limit.setSuffix(" %")
        self.s_limit.setValue(int(self.settings.get("ram_limit", 90)))
        self.s_limit.valueChanged.connect(lambda v: self._save_setting("ram_limit", v))
        spin_row(right, "RAM limit", self.s_limit)
        self.c_test = check(right, "Render test frames first", "test_frames", True,
                            "First, middle and last frame render before the rest. If one is broken on disk the render\n"
                            "pauses; if they are fine you get an estimate of the whole render.")
        self.c_test_pause = check(right, "Pause after the test frames", "test_pause", False,
                                  "Wait for Resume after the test frames, so you can look at them first")
        right.addStretch(1)
        outer.addStretch(1)
        return w

    def _forget_tuned(self):
        self.settings["tuned"] = {}
        write_settings(self.settings)
        self.engine.note("Tuned splits forgotten; auto-tune measures each script again", "ok")

    def _all_workers_changed(self, on):
        self._save_setting("show_all_workers", on)
        self.gantt.show_all = on
        self.gantt.update()
        self._update_system()

    def _slate_changed(self, key, value):
        """Renders that are not running take the new range straight away; running ones keep theirs."""
        self._save_setting(key, value)
        for e in self.manager.engines:
            if e.state in ("ready", "stopped", "done", "failed") and e.set_range(e.default_range()):
                e.scan_disk()
        self.range_edit.setText(self.engine.range_text)
        self.frames_model.refilter()
        self._sync_controls()
        self._slow_tick()
        self.frame_map.update()

    def _chunks_table(self):
        self.chunk_table = QtWidgets.QTableWidget(0, 9)
        self.chunk_table.setHorizontalHeaderLabels(
            ["Chunk", "Frames", "Count", "State", "Process", "Done", "Time", "s / frame", "Note"])
        self.chunk_table.verticalHeader().hide()
        self.chunk_table.setAlternatingRowColors(True)
        self.chunk_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.chunk_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.chunk_table.horizontalHeader().setStretchLastSection(True)
        self.chunk_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.chunk_table.customContextMenuRequested.connect(self._chunk_menu)
        return self.chunk_table

    def _output_tab(self):
        """Frames list for image sequences; one movie file with its details for MOV / MP4 / MXF."""
        self.out_pages = QtWidgets.QStackedWidget()
        self.out_pages.addWidget(self._frames_page())
        self.out_pages.addWidget(self._movie_page())
        return self.out_pages

    def _movie_page(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        left = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        for text, fn in (("Check again", self._movie_check), ("Open folder", self._open_folder),
                         ("Delete movie", self._delete_all)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.b_delete_movie = b
        self.b_delete_movie.setToolTip("Delete the rendered movie file from disk")
        bar.addStretch(1)
        left.addLayout(bar)
        self.movie_view = QtWidgets.QTextBrowser()
        left.addWidget(self.movie_view, 1)
        lay.addLayout(left, 3)
        right = QtWidgets.QVBoxLayout()
        self.movie_preview = ZoomView()
        self.movie_preview.setToolTip("Mouse wheel zooms, drag moves, double-click fits the frame again")
        right.addWidget(self.movie_preview, 1)
        nav = QtWidgets.QHBoxLayout()
        self.b_play = QtWidgets.QToolButton()
        self.b_play.setFixedSize(34, 30)
        self.b_play.setIconSize(QtCore.QSize(22, 22))
        self.b_play.setIcon(play_icon(False))
        self.b_play.setToolTip("Play / Pause (loops)")
        self.b_play.setStyleSheet(
            "QToolButton { background: %s; border: 1px solid %s; border-radius: 3px; }"
            "QToolButton:hover { border-color: %s; } QToolButton:disabled { background: %s; }"
            % (C["panel2"], C["line"], C["accent"], C["bg"]))
        self.b_play.clicked.connect(self._movie_toggle)
        nav.addWidget(self.b_play)
        self.movie_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.movie_slider.setRange(0, 0)
        self.movie_slider.sliderPressed.connect(self._movie_stop)
        self.movie_slider.sliderReleased.connect(lambda: self._movie_still(self.movie_slider.value()))
        nav.addWidget(self.movie_slider, 1)
        self._player = None
        self._movie_index = 0
        self._play_timer = QtCore.QTimer(self)
        self._play_timer.timeout.connect(self._movie_next)
        right.addLayout(nav)
        self.movie_frame_label = QtWidgets.QLabel("")
        self.movie_frame_label.setObjectName("dim")
        right.addWidget(self.movie_frame_label)
        lay.addLayout(right, 2)
        self._movie_size_last = None
        return w

    def _frames_page(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        left = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        self.out_filter = QtWidgets.QComboBox()
        self.out_filter.addItems(["All", "Rendered", "Problems"])
        self.out_filter.currentTextChanged.connect(self._out_filter)
        bar.addWidget(self.out_filter)
        for text, fn in (("Check all again", self._verify), ("Render problem frames again", self._rerender_bad),
                         ("Open folder", self._open_folder), ("Delete all", self._delete_all)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.b_delete = b
        self.b_delete.setToolTip("Delete every rendered frame of this render from disk")
        bar.addStretch(1)
        left.addLayout(bar)
        self.out_summary = QtWidgets.QLabel("")
        self.out_summary.setObjectName("dim")
        left.addWidget(self.out_summary)
        self.frames_model = FramesModel(self.engine)
        self.frames_view = QtWidgets.QTableView()
        self.frames_view.setModel(self.frames_model)
        self.frames_view.verticalHeader().hide()
        self.frames_view.verticalHeader().setDefaultSectionSize(20)
        self.frames_view.setAlternatingRowColors(True)
        self.frames_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.frames_view.horizontalHeader().setStretchLastSection(True)
        self.frames_view.selectionModel().currentRowChanged.connect(
            lambda cur, prev: self._show_frame(self.frames_model.rows[cur.row()]) if cur.isValid() else None)
        self.frames_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.frames_view.customContextMenuRequested.connect(self._frames_menu)
        left.addWidget(self.frames_view, 1)
        lay.addLayout(left, 3)
        right = QtWidgets.QVBoxLayout()
        self.preview = QtWidgets.QLabel("Click a frame to see it")
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        # Small minimum and an ignored size hint: in a low panel the picture shrinks instead of
        # being drawn over the frame info under it.
        self.preview.setMinimumSize(320, 120)
        self.preview.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self.preview.setStyleSheet("background: #0d0e10; color: %s;" % C["dim"])
        self.preview_info = QtWidgets.QLabel("")
        self.preview_info.setObjectName("dim")
        self.preview_info.setWordWrap(True)
        self.preview_info.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        right.addWidget(self.preview, 1)
        right.addWidget(self.preview_info)
        lay.addLayout(right, 2)
        return w

    def _system_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        tiles = QtWidgets.QHBoxLayout()
        self.m_cpu = Tile("CPU")
        self.m_ram = Tile("RAM")
        self.m_nuke = Tile("Nuke RAM")
        self.m_dw = Tile("Disk write")
        self.m_dr = Tile("Disk read")
        self.m_net = Tile("Network")
        self.m_gpu = Tile("GPU")
        for t in (self.m_cpu, self.m_ram, self.m_nuke, self.m_dw, self.m_dr, self.m_net, self.m_gpu):
            tiles.addWidget(t, 1)
        lay.addLayout(tiles)
        graphs = QtWidgets.QHBoxLayout()
        self.g_load = Graph("Load", [("cpu", "CPU", C["accent"]), ("ram", "RAM", C["ram"]),
                                     ("gpu", "GPU", C["done"])], max_value=100)
        self.g_io = Graph("Disk / network", [("dw", "write", C["accent"]), ("dr", "read", C["ram"]),
                                             ("net", "net", C["done"])], unit="MB/s")
        graphs.addWidget(self.g_load, 1)
        graphs.addWidget(self.g_io, 1)
        lay.addLayout(graphs)
        self.cores = CoreGrid()
        lay.addWidget(self.cores)
        self.worker_table = QtWidgets.QTableWidget(0, 11)
        self.worker_table.setHorizontalHeaderLabels(
            ["Render", "Process", "PID", "State", "Chunk", "Frame", "CPU used", "RAM used", "Frames", "Avg frame",
             "Script load"])
        self.worker_table.verticalHeader().hide()
        self.worker_table.setAlternatingRowColors(True)
        self.worker_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.worker_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.worker_table, 1)
        return w

    def _log_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        bar = QtWidgets.QHBoxLayout()
        self.log_source = QtWidgets.QComboBox()
        self.log_source.addItems(["All", "JKrender only", "Errors and warnings"])
        self.log_source.currentTextChanged.connect(self._log_refilter)
        bar.addWidget(self.log_source)
        b = QtWidgets.QPushButton("Open log folder")
        b.clicked.connect(lambda: self._open_path(self.engine.log_dir))
        bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(30000)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 8.5pt;")
        lay.addWidget(self.log_view, 1)
        return w

    def _details_tab(self):
        self.details = QtWidgets.QTextBrowser()
        self.details.setOpenExternalLinks(False)
        return self.details

    # ---- controls -----------------------------------------------------------
    def _save_setting(self, key, value):
        self.settings[key] = value
        write_settings(self.settings)

    def _start(self):
        e = self.engine
        if e.state == "paused":
            e.resume()
        else:
            self._range_changed()
            self.manager.request_start(e, everything=e.state == "done" or e.nothing_to_render())
            self.frames_model.refilter()
        self._sync_controls()
        self._refresh_cards()

    def _pause(self):
        self.engine.pause()
        self._sync_controls()

    def _stop(self):
        e = self.engine
        if e.state == "waiting":
            self.manager.cancel(e)
        elif e.state in ("rendering", "paused", "finishing"):
            e.stop()
        self._sync_controls()
        self._refresh_cards()

    def _startable(self):
        return [x for x in self.manager.engines
                if x.state in ("ready", "stopped", "failed") and not x.nothing_to_render()]

    def _render_all(self):
        self._range_changed()
        for x in self._startable():
            self.manager.request_start(x)
        self.frames_model.refilter()
        self._sync_controls()
        self._refresh_cards()

    def _pause_all(self):
        if any(e.state == "rendering" for e in self.manager.engines):
            self.manager.pause_all()
        else:
            self.manager.resume_all()
        self._sync_controls()
        self._refresh_cards()

    def _workers_changed(self, v):
        self.manager.set_budget(total=v)
        self._save_setting("processes", v)

    def _budget_changed(self, key, v):
        self.manager.set_budget(**{key: v})
        self._save_setting(key, v)

    def _chunk_changed(self, v):
        self.engine.set_chunk_size(v)
        self.frame_map.update()

    def _range_changed(self):
        text = self.range_edit.text().strip()
        if text != self.engine.range_text:
            if self.engine.set_range(text):
                plan = auto_plan(len(self.engine.frames), self.engine.movie)
                self.s_chunk.setValue(plan["chunk"])
                self.engine.scan_disk()
                self.frames_model.refilter()
            else:
                self.range_edit.setText(self.engine.range_text)

    def _auto(self):
        plan = auto_plan(len(self.engine.frames), self.engine.movie)
        self.s_workers.setValue(plan["workers"])
        self.s_threads.setValue(plan["threads"])
        self.s_ram.setValue(plan["ram"])
        self.s_chunk.setValue(plan["chunk"])

    def _sync_controls(self):
        s = self.engine.state
        running = s in ("rendering", "paused", "finishing", "waiting")
        self.b_start.setEnabled(s not in ("rendering", "finishing", "waiting"))
        again = s == "done" or (s in ("ready", "stopped", "failed") and self.engine.nothing_to_render())
        self.b_start.setText("Resume" if s == "paused" else ("Render again" if again else "Render"))
        self.b_start.setToolTip("Render every frame again, also those already on disk" if again else "")
        self.b_pause.setEnabled(s == "rendering")
        self.b_stop.setEnabled(running)
        # Render settings are fixed while a render runs (paused too); Stop makes them editable again.
        # Processes, threads and RAM are shared by every render, so they wait until none runs.
        any_running = bool(self.manager.running())
        for widget in (self.range_edit, self.b_delete, self.b_delete_movie):
            widget.setEnabled(not running)
        self.s_chunk.setEnabled(not running and not self.engine.movie)
        for widget in (self.s_workers, self.s_threads, self.s_ram, self.b_auto):
            widget.setEnabled(not any_running)
        rendering = any(x.state == "rendering" for x in self.manager.engines)
        paused = any(x.state == "paused" for x in self.manager.engines)
        self.b_pause_all.setText("Pause all" if rendering or not paused else "Resume all")
        self.b_pause_all.setEnabled(rendering or paused)
        many = len(self.manager.engines) > 1  # the "all" buttons only mean something with several renders
        self.b_render_all.setVisible(many)
        self.b_pause_all.setVisible(many)
        self.b_render_all.setEnabled(bool(self._startable()))

    # ---- timers -------------------------------------------------------------
    def _tick(self):
        changed = self.manager.tick()
        for e in list(self.manager.engines):
            if e.new_log:
                if e is self.engine:
                    self._append_log(e.new_log)
                e.new_log = []
            prev = self._last_states.get(e)
            if e.state != prev:
                self._last_states[e] = e.state
                if e is self.engine:
                    self._state_changed()
                else:
                    self._sync_controls()  # shared controls follow every render
                self._refresh_cards()
                if prev is not None and e.state in ("done", "failed"):
                    QtWidgets.QApplication.alert(self)
        if self.exit_when_done and self.manager.engines and all(
                e.state in ("done", "failed") for e in self.manager.engines):
            self.exit_when_done = False
            self._slow_tick()
            QtCore.QTimer.singleShot(200, QtWidgets.QApplication.quit)
        if changed or self.engine.state in ("rendering", "paused"):
            self.frame_map.update()

    def _state_changed(self):
        e = self.engine
        self._sync_controls()
        colors = {"rendering": C["rendering"], "done": C["done"], "failed": C["failed"], "paused": C["accent"],
                  "stopped": C["accent"], "finishing": C["rendering"]}
        col = colors.get(e.state, C["queued"])
        self.state_chip.setText(STATE_LABELS.get(e.state, e.state))
        self.state_chip.setStyleSheet(
            "background: %s; color: #0e0f11; font-weight: 600; padding: 6px 12px; border-radius: 4px;" % col)
        if e.movie:
            self._update_movie()
            self._movie_show(0.5)  # while it renders this says the preview comes when the movie is finished
        if e.state == "failed" and e.error:
            self._banner(e.error, C["failed"])
        elif e.state == "done" and e.movie:
            checks = movie_checks(e.movie_info, e.movie_expected())
            bad = [t for _, st, t in checks if st == "bad"]
            warn = ["%s %s" % (what, t) for what, st, t in checks if st == "warn"]
            if bad:
                self._banner("Done in %s, but the movie is not usable: %s" % (fmt_secs(e.elapsed()), "; ".join(bad)),
                             C["failed"])
            else:
                extra = ("  Check: %s." % "; ".join(warn)) if warn else ""
                self._banner("Done in %s. %s is complete: %d frames.%s" % (
                    fmt_secs(e.elapsed()), os.path.basename(e.movie_path()), len(e.frames), extra),
                    C["small"] if warn else C["done"])
        elif e.state == "done":
            bad = sum(1 for fr in e.frames.values() if fr.state == "bad")
            small = sum(1 for fr in e.frames.values() if fr.small)
            if bad:
                self._banner("Done in %s, but %d frame(s) are broken on disk. Output tab → Render problem frames again."
                             % (fmt_secs(e.elapsed()), bad), C["failed"])
            else:
                extra = (" %d frame(s) are much smaller than the rest; worth a look." % small) if small else ""
                self._banner("Done in %s. All %d frames are on disk and pass the check.%s"
                             % (fmt_secs(e.elapsed()), len(e.frames), extra), C["done"])
        elif e.notice and e.state in ("rendering", "paused"):
            self._banner(e.notice[0], {"ok": C["done"], "warn": C["small"], "bad": C["failed"]}[e.notice[1]])
        elif e.state == "waiting":
            self._banner("Waiting: %d render(s) already use the PC. It starts as soon as one is on its last chunks "
                         "(Settings > Max renders at once)." % self.manager.max_renders, C["loading"])
        else:
            self.banner.hide()

    def _banner(self, text, color):
        self.banner.setText(text)
        self.banner.setStyleSheet("background: %s; color: #0e0f11; padding: 8px 10px; border-radius: 4px; font-weight: 600;" % color)
        self.banner.show()

    def _slow_tick(self):
        self._refresh_cards()
        e = self.engine
        if e.notice and e.state in ("rendering", "paused") and self.banner.text() != e.notice[0]:
            self._state_changed()
        c = e.counts()
        total = max(1, len(e.frames))
        finished = c["done"] + c["skipped"] + c["bad"]
        self.progress.setMaximum(total)
        self.progress.setValue(finished)
        self.t_frames.set("%d / %d" % (finished, total), "%.0f %%  ·  %d rendering" % (100.0 * finished / total, c["rendering"]))
        self.t_elapsed.set(fmt_secs(e.elapsed()) if e.started else "—",
                           "started %s" % datetime.datetime.fromtimestamp(e.started).strftime("%H:%M:%S") if e.started else "")
        eta = e.eta() if e.state in ("rendering", "paused") else None
        self.t_eta.set(fmt_secs(eta) if eta is not None else "—",
                       ("done at %s" % (datetime.datetime.now() + datetime.timedelta(seconds=eta)).strftime("%H:%M"))
                       if eta else "")
        rate = e.rate()
        self.t_speed.set("%.2f fr/s" % rate if rate else "—", "%.1f fr/min" % (rate * 60) if rate else "")
        secs = e.frame_secs()
        if secs:
            conc = sum(secs) / max(1.0, e.elapsed())
            self.t_avg.set("%.2f s" % (sum(secs) / len(secs)), "per frame in one process · ×%.1f at once" % conc)
        else:
            self.t_avg.set("—", "")
        live = [w for w in e.workers.values() if w.state != "gone"]
        busy = sum(1 for w in live if w.state == "busy")
        self.t_workers.set("%d / %d" % (busy, len(live)),
                           "rendering / running · %d of %d for all renders" % (self.manager.live_count(),
                                                                               self.manager.total))
        checked = [fr for fr in e.frames.values() if fr.checks]
        bad = sum(1 for fr in e.frames.values() if fr.state == "bad")
        small = sum(1 for fr in e.frames.values() if fr.small)
        if e.movie:
            verdict = e.movie_verdict() if e.state not in ("rendering", "paused", "finishing") else None
            words = {"ok": ("ok", C["done"]), "warn": ("check it", C["small"]), "bad": ("broken", C["failed"])}
            text, color = words.get(verdict, ("—", C["text"]))
            self.t_check.set(text, "one movie file")
            self.t_check.value.setStyleSheet("color: %s;" % color)
            self._update_movie()
        else:
            self.t_check.set("%d bad" % bad if bad else ("ok" if checked else "—"),
                             "%d checked · %d small" % (len(checked), small))
            self.t_check.value.setStyleSheet("color: %s;" % (C["failed"] if bad else (C["done"] if checked else C["text"])))
        self._update_chunks()
        self._update_system()
        self.frames_model.touch()
        total_size = sum(ch[1] or 0 for fr in e.frames.values() for ch in fr.checks.values())
        missing = sum(1 for fr in checked if any(ch[0] == "missing" for ch in fr.checks.values()))
        broken = sum(1 for fr in checked if any(ch[0] in ("empty", "truncated", "unreadable") for ch in fr.checks.values()))
        self.out_summary.setText("%d frames · %d checked · %d missing · %d broken · %d suspiciously small · %s on disk"
                                 % (len(e.frames), len(checked), missing, broken, small, fmt_size(total_size)))
        self.gantt.update()
        if self.tabs.currentIndex() == 3:
            self._update_details()

    def _update_chunks(self):
        e = self.engine
        t = self.chunk_table
        if t.rowCount() != len(e.chunks):
            t.setRowCount(len(e.chunks))
        now = time.time()
        for row, c in enumerate(e.chunks):
            done = sum(1 for f in c.all_frames if e.frames[f].state in ("done", "skipped", "bad"))
            secs = [e.frames[f].secs for f in c.all_frames if e.frames[f].secs is not None]
            dur = None
            if c.started and c.state in ("rendering", "done"):
                end = c.ended or (e.paused_at if e.state == "paused" and e.paused_at else now)
                dur = end - c.started - e.paused_between(c.started, end)
            note = c.error or ("try %d of %d" % (c.attempts + 1, MAX_ATTEMPTS) if c.attempts else "")
            values = [c.name, c.spec, str(len(c.all_frames)), c.state, c.worker or "", "%d / %d" % (done, len(c.all_frames)),
                      fmt_secs(dur) if dur is not None else "", "%.2f" % (sum(secs) / len(secs)) if secs else "", note]
            for col, v in enumerate(values):
                item = t.item(row, col)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    t.setItem(row, col, item)
                if item.text() != v:
                    item.setText(v)
                if col == 3:
                    item.setForeground(QtGui.QColor(C.get(c.state, C["text"]) if c.state != "queued" else C["dim"]))
        if not getattr(self, "_chunk_cols_sized", False) and e.chunks:
            t.resizeColumnsToContents()
            self._chunk_cols_sized = True

    def _update_system(self):
        m = self.manager
        if not psutil:
            self.m_cpu.set("psutil missing", "pip install psutil")
            return
        per = psutil.cpu_percent(percpu=True)
        cpu = sum(per) / max(1, len(per))
        vm = psutil.virtual_memory()
        shown = m.engines if self.settings.get("show_all_workers", True) else [self.engine]
        nuke_ram = sum(w.rss for x in shown for w in x.workers.values() if w.state != "gone")
        now = time.time()
        io = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        dw = dr = nmb = 0.0
        if self._io_last and io:
            dt = max(0.2, now - self._io_last[0])
            dw = (io.write_bytes - self._io_last[1].write_bytes) / dt / 1024 ** 2
            dr = (io.read_bytes - self._io_last[1].read_bytes) / dt / 1024 ** 2
        if self._net_last and net:
            dt = max(0.2, now - self._net_last[0])
            nmb = ((net.bytes_recv + net.bytes_sent) - (self._net_last[1].bytes_recv + self._net_last[1].bytes_sent)) / dt / 1024 ** 2
        self._io_last = (now, io)
        self._net_last = (now, net)
        self.m_cpu.set("%.0f %%" % cpu, "%d threads" % len(per))
        guard = ("  ·  guard: %d of %d processes" % (m.ram_cap, m.total)) if m.ram_cap else ""
        self.m_ram.set("%.0f %%" % vm.percent, "%.1f of %.0f GB%s" % (vm.used / 1024 ** 3, vm.total / 1024 ** 3, guard))
        per_render = ["%d: %.1f" % (x.number, sum(w.rss for w in x.workers.values() if w.state != "gone"))
                      for x in shown if any(w.state != "gone" for w in x.workers.values())]
        self.m_nuke.set("%.1f GB" % nuke_ram, "%d processes%s" % (
            sum(1 for x in shown for w in x.workers.values() if w.state != "gone"),
            ("  ·  " + "  ".join(per_render)) if len(per_render) > 1 else ""))
        self.m_dw.set("%.0f MB/s" % dw)
        self.m_dr.set("%.0f MB/s" % dr)
        self.m_net.set("%.1f MB/s" % nmb)
        gpu = self._gpu
        if gpu:
            self.m_gpu.set("%.0f %%" % gpu[0], "VRAM %.1f / %.0f GB" % (gpu[1] / 1024, gpu[2] / 1024))
        else:
            self.m_gpu.set("—", "no NVIDIA GPU" if not shutil.which("nvidia-smi") else "")
        self.g_load.add({"cpu": cpu, "ram": vm.percent, "gpu": gpu[0] if gpu else 0})
        self.g_io.add({"dw": dw, "dr": dr, "net": nmb})
        self.cores.set_values(per)
        t = self.worker_table
        rows = sorted(((x, w) for x in shown for w in x.workers.values()),
                      key=lambda xw: (xw[1].state == "gone", xw[0].number, xw[1].id))
        t.setRowCount(len(rows))
        for row, (x, w) in enumerate(rows):
            avg = (sum(w.frame_secs) / len(w.frame_secs)) if w.frame_secs else None
            gone = w.state == "gone"
            values = ["%d  %s" % (x.number, x.name), w.name, str(w.proc.pid), w.state if not gone else "closed (%s)" % w.exit_code,
                      w.chunk.name if w.chunk else "", str(w.frame) if w.frame is not None else "",
                      "" if gone else "%.1f of %d cores" % (w.cpu, w.threads), "" if gone else "%.1f / %d GB" % (w.rss, w.ram),
                      str(w.frames_done), "%.2f s" % avg if avg else "", "%.1f s" % w.load_secs if w.load_secs else ""]
            for col, v in enumerate(values):
                item = t.item(row, col)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    t.setItem(row, col, item)
                item.setText(v)
                item.setForeground(QtGui.QColor(C["dim"] if gone or x is not self.engine else C["text"]))

    def _gpu_loop(self):
        while True:
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, **hidden()).stdout
                vals = [float(x) for x in out.strip().splitlines()[0].split(",")]
                self._gpu = vals
            except Exception:
                self._gpu = None
            time.sleep(2)

    # ---- log ----------------------------------------------------------------
    def _keep(self, entry):
        mode = self.log_source.currentText()
        if mode == "JKrender only":
            return not entry[0]
        if mode == "Errors and warnings":
            return entry[2] in ("err", "warn")
        return True

    def _html(self, entry):
        src, text, level = entry
        color = {"err": C["failed"], "warn": C["small"], "ev": C["accent"], "ok": C["done"]}.get(level, C["text"])
        prefix = '<span style="color:%s">[%s]</span> ' % (C["dim"], src) if src else ""
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return '%s<span style="color:%s">%s</span>' % (prefix, color, safe)

    def _append_log(self, entries):
        lines = [self._html(x) for x in entries if self._keep(x)]
        if lines:
            bar = self.log_view.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 4
            self.log_view.appendHtml("<br>".join(lines))
            if at_end:
                bar.setValue(bar.maximum())

    def _log_refilter(self):
        self.log_view.clear()
        self._append_log(list(self.engine.log))

    # ---- chunks / frames ----------------------------------------------------
    def _chunk_menu(self, pos):
        rows = sorted({i.row() for i in self.chunk_table.selectedIndexes()})
        chunks = [self.engine.chunks[r] for r in rows if r < len(self.engine.chunks)]
        if not chunks:
            return
        menu = QtWidgets.QMenu(self)
        again = menu.addAction("Render again")
        log = menu.addAction("Show process log")
        act = menu.exec(self.chunk_table.viewport().mapToGlobal(pos))
        if act == again:
            for c in chunks:
                self.engine.requeue_chunk(c)
            self._after_requeue()
        elif act == log and chunks[0].worker:
            wid = int(chunks[0].worker[1:])
            w = self.engine.workers.get(wid)
            if w:
                self._open_path(w.log_path)

    def _frames_menu(self, pos):
        frames = sorted({self.frames_model.rows[i.row()] for i in self.frames_view.selectedIndexes()})
        if not frames:
            return
        menu = QtWidgets.QMenu(self)
        again = menu.addAction("Render %d frame(s) again" % len(frames))
        act = menu.exec(self.frames_view.viewport().mapToGlobal(pos))
        if act == again:
            self.engine.rerender(frames)
            self._after_requeue()

    def _after_requeue(self):
        self.frames_model.refilter()
        self._sync_controls()
        self._slow_tick()

    def _rerender_bad(self):
        frames = [f for f, fr in self.engine.frames.items() if fr.state in ("bad", "failed")]
        if not frames:
            QtWidgets.QMessageBox.information(self, "JKrender", "No broken or failed frames.")
            return
        self.engine.rerender(frames)
        self._after_requeue()

    def _verify(self):
        self.engine.verify_all()

    def _out_filter(self, text):
        self.frames_model.mode = text
        self.frames_model.refilter()

    def _show_frame(self, frame):
        e = self.engine
        fr = e.frames.get(frame)
        if fr is None:
            return
        self._preview_frame = frame
        files = e.output_files(frame)
        info = ["<b>Frame %d</b> — %s, chunk %s, %s" % (frame, fr.state, fr.chunk or "—", fr.worker or "no process yet")]
        if fr.secs is not None:
            info.append("Rendered in %.2f s" % fr.secs)
        for i, p in enumerate(files):
            chk = fr.checks.get(i)
            info.append("%s<br><span style='color:%s'>%s</span>" % (
                p, C["failed"] if chk and chk[0] != "ok" else C["dim"],
                ("%s · %s %s" % (chk[0], fmt_size(chk[1]), chk[2])) if chk else "not checked yet"))
        self.preview_info.setText("<br>".join(info))
        target = next((p for p in files if os.path.exists(p)), None)
        if fr.state == "rendering" or not target:
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText("Still rendering" if fr.state == "rendering" else "No file on disk yet")
            return
        try:
            img = preview_image(target, max(200, self.preview.width()), max(150, self.preview.height()))
            self.preview.setPixmap(QtGui.QPixmap.fromImage(img).scaled(
                self.preview.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        except Exception as exc:
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText("No preview: %s" % exc)
        if self.sub_tabs.currentIndex() != 0:
            self.sub_tabs.setCurrentIndex(0)

    # ---- movie output -------------------------------------------------------
    def _movie_check(self):
        self.engine.probe()
        self._update_movie()
        self._movie_show(0.5)

    def _movie_ready(self):
        e = self.engine
        info = e.movie_info or {}
        return (not info.get("error") and info.get("fps") and info.get("frames")
                and e.state not in ("rendering", "paused", "finishing"))

    def _movie_show(self, where):
        """Show the frame at 0..1 through the movie."""
        self._movie_stop()
        e = self.engine
        info = e.movie_info or {}
        if not self._movie_ready():
            self.movie_preview.set_text("Preview when the movie is finished" if e.state in (
                "rendering", "paused", "finishing") else ("No movie on disk" if not info or info.get("error") == "missing"
                                                          else "No preview"))
            self.movie_frame_label.setText("")
            self.movie_slider.setRange(0, 0)
            self.b_play.setEnabled(False)
            return
        frames = int(info["frames"])
        self.movie_slider.setRange(0, frames - 1)
        self.b_play.setEnabled(True)
        self._movie_still(int(round(where * (frames - 1))))

    def _movie_label(self, index):
        e = self.engine
        first = e.frame_list()[0] if e.frames else 0
        frames = int((e.movie_info or {}).get("frames") or 0)
        self.movie_frame_label.setText("Frame %d of %d  ·  script frame %d" % (index + 1, frames, first + index))
        self.movie_slider.blockSignals(True)
        self.movie_slider.setValue(index)
        self.movie_slider.blockSignals(False)

    def _movie_still(self, index):
        """Show one frame (0-based) and remember it as the place Play starts from."""
        if not self._movie_ready():
            return
        e = self.engine
        info = e.movie_info
        index = max(0, min(int(info["frames"]) - 1, index))
        self._movie_index = index
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            # A still is decoded at the movie's own size, so zooming in shows real pixels.
            image = movie_frame(e.movie_path(), movie_seek(info, index), int(info.get("width") or 1920) // 2 * 2)
            self._movie_paint(image)
            self._movie_label(index)
        except Exception as exc:
            self.movie_preview.set_text("No preview: %s" % exc)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _movie_paint(self, image):
        self.movie_preview.set_image(image)

    def _movie_toggle(self):
        if self._player:
            self._movie_stop()
            self._movie_still(self._movie_index)  # paused: the frame at full size, for zooming in
        else:
            self._movie_play()

    def _movie_play(self):
        """Play at the movie's own frame rate: ffmpeg decodes ahead into a small queue, a timer shows each frame."""
        if not self._movie_ready() or not find_tool("ffmpeg"):
            return
        e = self.engine
        info = e.movie_info
        frames = int(info["frames"])
        if self._movie_index >= frames - 1:
            self._movie_index = 0  # on the last frame: play from the start
        width = max(640, min(1920, self.movie_preview.width() * 2, int(info.get("width") or 1920))) // 2 * 2
        height = max(2, int(round(width * info["height"] / float(info["width"]) / 2)) * 2)
        cmd = [find_tool("ffmpeg"), "-v", "error", "-ss", "%.4f" % movie_seek(info, self._movie_index),
               "-i", e.movie_path(), "-vf", "scale=%d:%d" % (width, height), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden())
        except Exception as exc:
            self.movie_preview.set_text("Cannot play: %s" % exc)
            return
        frames_q = pyqueue.Queue(maxsize=12)
        stop = threading.Event()

        def reader():
            size = width * height * 3
            while not stop.is_set():
                data = proc.stdout.read(size)
                if len(data) < size:
                    break
                while not stop.is_set():
                    try:
                        frames_q.put(data, timeout=0.5)
                        break
                    except pyqueue.Full:
                        pass
            frames_q.put(None)

        threading.Thread(target=reader, daemon=True).start()
        self._player = {"proc": proc, "queue": frames_q, "stop": stop, "w": width, "h": height}
        self._play_timer.start(max(5, int(round(1000.0 / info["fps"]))))
        self.b_play.setIcon(play_icon(True))

    def _movie_next(self):
        player = self._player
        if not player:
            return
        try:
            data = player["queue"].get_nowait()
        except pyqueue.Empty:
            return  # ffmpeg is a little behind; show the frame on the next tick
        if data is None:
            # The end: start again from the first frame.
            self._movie_stop()
            self._movie_index = 0
            self._movie_play()
            return
        image = QtGui.QImage(data, player["w"], player["h"], 3 * player["w"], QtGui.QImage.Format_RGB888).copy()
        self._movie_paint(image)
        self._movie_label(self._movie_index)
        player["shown"] = self._movie_index
        frames = int((self.engine.movie_info or {}).get("frames") or 1)
        if self._movie_index < frames - 1:
            self._movie_index += 1

    def _movie_stop(self):
        self._play_timer.stop()
        player, self._player = self._player, None
        if player:
            player["stop"].set()
            try:
                player["proc"].kill()
            except Exception:
                pass
            if "shown" in player:
                self._movie_index = player["shown"]  # Play goes on from the frame on screen
        self.b_play.setIcon(play_icon(False))

    def _update_movie(self):
        e = self.engine
        info = e.movie_info

        def row(k, v, color=None):
            return "<tr><td style='color:%s;padding-right:18px'>%s</td><td style='color:%s'>%s</td></tr>" % (
                C["dim"], k, color or C["text"], v)

        def esc(v):
            return str(v).replace("&", "&amp;").replace("<", "&lt;")

        path = e.movie_path()
        parts = ["<h3>File</h3><table>", row("Path", esc(path))]
        if e.state in ("rendering", "paused", "finishing"):
            # The movie grows as Nuke writes it; finished details come when the render ends.
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            now = time.time()
            speed = None
            if self._movie_size_last and now > self._movie_size_last[0]:
                speed = max(0.0, (size - self._movie_size_last[1]) / (now - self._movie_size_last[0]))
            self._movie_size_last = (now, size)
            done = sum(1 for fr in e.frames.values() if fr.state == "done")
            parts += [row("Writing", "%s  ·  %d of %d frames" % (fmt_size(size), done, len(e.frames))),
                      row("Write speed", "%s/s" % fmt_size(speed) if speed is not None else "—"),
                      row("Size at the end", "~%s" % fmt_size(size / done * len(e.frames)) if done else "—")]
            parts.append("</table><p style='color:%s'>Codec, colour and checks appear when the movie is finished.</p>"
                         % C["dim"])
        elif not info or info.get("error") == "missing":
            parts.append("</table><p style='color:%s'>Not rendered yet.</p>" % C["dim"])
        else:
            parts += [row("Size", fmt_size(info.get("size"))),
                      row("Written", datetime.datetime.fromtimestamp(info["modified"]).strftime("%Y-%m-%d %H:%M:%S")
                          if info.get("modified") else "—")]
            if not info.get("error") and not info.get("no_probe"):
                dur = info.get("duration") or 0
                parts += [row("Container", esc(info.get("container"))),
                          row("Duration", "%s  ·  %d frames" % (fmt_secs(dur) if dur >= 1 else "%.2f s" % dur,
                                                                 info.get("frames") or 0)),
                          row("Start timecode", esc(info.get("timecode") or "none")),
                          row("Bit rate", "%.1f MB/s" % (info["bitrate"] / 8 / 1024 ** 2) if info.get("bitrate") else "—")]
                parts.append("</table><h3>Video</h3><table>")
                colour = " / ".join(esc(info.get(k) or "?") for k in ("primaries", "transfer", "matrix"))
                parts += [row("Codec", "%s%s" % (esc(info.get("codec_long") or info.get("codec")),
                                                  ("  ·  " + esc(info["profile"])) if info.get("profile") else "")),
                          row("Resolution", "%sx%s  ·  pixel aspect %s" % (info.get("width"), info.get("height"),
                                                                         esc(info.get("sar") or "1:1"))),
                          row("Frame rate", "%.3f fps" % (info.get("fps") or 0)),
                          row("Pixels", "%s%s%s" % (esc(info.get("pix_fmt")),
                                                    ("  ·  %s-bit" % info["bits"]) if info.get("bits") else "",
                                                    "  ·  alpha" if (info.get("pix_fmt") or "").startswith(
                                                        ("yuva", "rgba", "argb", "bgra", "gbrap", "ya")) else "")),
                          row("Colour tags", "%s  ·  %s range" % (colour, esc(info.get("range") or "?"))),
                          row("Field order", esc(info.get("field_order") or "—")),
                          row("Encoder", esc(info.get("encoder") or "—"))]
                parts.append("</table><h3>Audio</h3><table>")
                if info.get("audio"):
                    for codec, channels, rate in info["audio"]:
                        parts.append(row("Track", "%s  ·  %s ch  ·  %s Hz" % (esc(codec), channels, rate)))
                else:
                    parts.append(row("", "no audio"))
            parts.append("</table><h3>Check</h3><table>")
            icons = {"ok": ("ok", C["done"]), "warn": ("check", C["small"]), "bad": ("problem", C["failed"])}
            for what, state, text in movie_checks(info, e.movie_expected()):
                label, color = icons[state]
                parts.append(row(what, "%s  —  %s" % (label, esc(text)), color))
            parts.append("</table>")
        bar = self.movie_view.verticalScrollBar().value()
        self.movie_view.setHtml("".join(parts))
        self.movie_view.verticalScrollBar().setValue(bar)

    def _open_path(self, path):
        path = Path(path)
        target = path if path.exists() else next((p for p in path.parents if p.exists()), None)
        if target:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))

    def _open_folder(self):
        if self.engine.outputs:
            self._open_path(Path(frame_path(self.engine.outputs[0][1], 0)).parent)

    def _delete_all(self):
        """Delete every output file of this render's frame range, after asking."""
        e = self.engine
        if e.state in ("rendering", "paused", "finishing"):
            return
        files = sorted({p for f in e.frame_list() for p in e.output_files(f) if os.path.isfile(p)})
        if not files:
            QtWidgets.QMessageBox.information(self, "JKrender", "There are no rendered frames on disk.")
            return
        size = sum(os.path.getsize(p) for p in files)
        folders = sorted({os.path.dirname(p) for p in files})
        ask = QtWidgets.QMessageBox.warning(
            self, "Delete all rendered frames",
            "Delete %d file(s), %s, from disk?\n\n%s\n\nFrames %s. This cannot be undone."
            % (len(files), fmt_size(size), "\n".join(folders), e.range_text),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
        if ask != QtWidgets.QMessageBox.Yes:
            return
        failed = []
        for p in files:
            try:
                os.remove(p)
            except OSError as exc:
                failed.append("%s: %s" % (os.path.basename(p), exc))
        e.note("Deleted %d rendered file(s) (%s)" % (len(files) - len(failed), fmt_size(size)), "ok")
        for line in failed[:20]:
            e.note("!! could not delete " + line, "err")
        e.rebuild_plan()
        e.state = "ready"
        e.movie_info = None
        e.scan_disk()  # anything that could not be deleted still counts as rendered
        if e.movie:
            self._update_movie()
            self._movie_show(0.5)
        self._preview_frame = None
        self.preview.setPixmap(QtGui.QPixmap())
        self.preview.setText("Click a frame to see it")
        self.preview_info.setText("")
        self.frames_model.refilter()
        self._sync_controls()
        self._slow_tick()
        if failed:
            QtWidgets.QMessageBox.warning(self, "JKrender", "%d file(s) could not be deleted. See the Log." % len(failed))

    # ---- details ------------------------------------------------------------
    def _update_details(self):
        e = self.engine
        j = e.job
        secs = e.frame_secs()

        def row(k, v):
            return "<tr><td style='color:%s;padding-right:18px'>%s</td><td>%s</td></tr>" % (C["dim"], k, v)

        def esc(v):
            return str(v).replace("&", "&amp;").replace("<", "&lt;")

        parts = ["<h3>Render</h3><table>"]
        parts += [row("Script", esc(j.get("script"))), row("Snapshot rendered", esc(j.get("snapshot") or "(the script itself)")),
                  row("Nuke", esc(j.get("nuke_exe"))), row("Nuke version", esc(j.get("nuke_version", ""))),
                  row("Frames", "%s (%d)" % (esc(e.range_text), len(e.frames))),
                  row("Format", esc(j.get("format", ""))), row("fps", esc(j.get("fps", ""))),
                  row("Sent from Nuke", esc(j.get("created", ""))), row("Render folder", esc(e.dir))]
        parts.append("</table><h3>Write nodes</h3><table>")
        for w in j.get("writes", []):
            desc = ", ".join("%s %s" % (k, esc(w[k])) for k in ("file_type", "datatype", "compression", "channels", "colorspace") if w.get(k))
            parts.append(row(esc(w["name"]), esc(w.get("output", "")) + "<br><span style='color:%s'>%s</span>" % (C["dim"], desc)))
        parts.append("</table><h3>Split</h3><table>")
        parts += [row("Nuke processes", e.workers_target), row("Threads each (-m)", e.threads), row("RAM each (-c)", "%d GB" % e.ram),
                  row("Chunk size", "whole range (movie file)" if e.movie else "%d frames" % e.chunk_size),
                  row("Chunks", "%d (%d done, %d failed)" % (len(e.chunks), sum(1 for c in e.chunks if c.state == "done"),
                                                            sum(1 for c in e.chunks if c.state == "failed"))),
                  row("Retries per chunk", MAX_ATTEMPTS), row("Processes started", len(e.workers)),
                  row("Crashes", e.crashes)]
        parts.append("</table><h3>Times</h3><table>")
        if secs:
            loads = [w.load_secs for w in e.workers.values() if w.load_secs]
            fastest = min((fr.secs, f) for f, fr in e.frames.items() if fr.secs is not None)
            slowest = max((fr.secs, f) for f, fr in e.frames.items() if fr.secs is not None)
            parts += [row("Wall time", fmt_secs(e.elapsed())),
                      row("Frame time, average", "%.2f s" % (sum(secs) / len(secs))),
                      row("Frame time, median", "%.2f s" % statistics.median(secs)),
                      row("Fastest frame", "%d: %.2f s" % (fastest[1], fastest[0])),
                      row("Slowest frame", "%d: %.2f s" % (slowest[1], slowest[0])),
                      row("Sum of frame times", fmt_secs(sum(secs))),
                      row("Frames rendering at once", "×%.1f on average" % (sum(secs) / max(1.0, e.elapsed()))),
                      row("Script load per process", "%.1f s average" % (sum(loads) / len(loads)) if loads else "—")]
        else:
            parts.append(row("", "No frames rendered yet"))
        parts.append("</table><h3>Command of one process</h3><p style='font-family:Consolas'>%s</p>"
                     % esc(subprocess.list2cmdline(e._command())))
        env = j.get("env") or {}
        parts.append("<h3>Environment from Nuke</h3><table>")
        for k in sorted(env):
            parts.append(row(esc(k), esc(env[k]) or "<i>(cleared)</i>"))
        parts.append("</table>")
        bar = self.details.verticalScrollBar().value()
        self.details.setHtml("".join(parts))
        self.details.verticalScrollBar().setValue(bar)

    # ---- close --------------------------------------------------------------
    def closeEvent(self, event):
        running = self.manager.running()
        if running and not self.exit_when_done:
            ask = QtWidgets.QMessageBox.question(
                self, "JKrender", "%d render(s) still running.\n\nStop them and close?" % len(running),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if ask != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
        for e in running:
            if e.state == "waiting":
                self.manager.cancel(e)
            else:
                e.stop("Stopped: window closed")
        for e in self.manager.engines:
            e.write_report()
        self.manager.shutdown()
        event.accept()


def make_icon():
    """Grey tile, orange JK and a strip of chunks (orange, green, orange), drawn at every size Windows asks for."""
    icon = QtGui.QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
        img.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        p.scale(size / 256.0, size / 256.0)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#222222"))
        p.drawRoundedRect(QtCore.QRectF(8, 8, 240, 240), 52, 52)
        font = QtGui.QFont("Segoe UI", 1, QtGui.QFont.Black)
        font.setPixelSize(140)
        p.setFont(font)
        p.setPen(QtGui.QColor(C["accent"]))
        p.drawText(QtCore.QRectF(8, 8, 240, 200), QtCore.Qt.AlignCenter, "JK")
        p.setPen(QtCore.Qt.NoPen)
        x = 44.0
        for frac, color in ((0.28, C["accent"]), (0.28, C["done"]), (0.44, C["accent"])):  # three chunks
            p.setBrush(QtGui.QColor(color))
            p.drawRoundedRect(QtCore.QRectF(x, 190, 168 * frac - 10, 22), 5.5, 5.5)
            x += 168 * frac
        p.end()
        icon.addPixmap(QtGui.QPixmap.fromImage(img))
    return icon


def hand_to_running(job_path):
    """Give the render to the JKrender already open. False when none is."""
    try:
        with socket.create_connection(("127.0.0.1", APP_PORT), timeout=1.0) as sock:
            sock.sendall(json.dumps({"job": str(Path(job_path).resolve())}).encode("utf-8") + b"\n")
            sock.settimeout(10)
            return sock.recv(64).startswith(b"OK")
    except OSError:
        return False


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("Usage: JKrender.py <job.json> [<job.json> ...]   (the JKrender menu in Nuke creates them)")
    alone = "--exit-when-done" in sys.argv  # scripted runs keep to themselves
    if not alone:
        # One render per Write: hand each to the JKrender already open; whatever is left opens a window here.
        args = [job for job in args if not hand_to_running(job)]
        if not args:
            return
    if sys.platform.startswith("win"):
        try:
            import ctypes

            # Its own taskbar button and icon, instead of being grouped under python.exe.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JK.JKrender")
        except Exception:
            pass
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("JKrender")
    app.setWindowIcon(make_icon())
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    win = MainWindow(args[0], exit_when_done=alone)
    for job in args[1:]:
        win.add_job(job)
    win.select(win.manager.engines[0])
    if not alone:
        win.listen()
    win.show()
    code = app.exec()
    win.manager.shutdown()
    sys.exit(code)


if __name__ == "__main__":
    main()
