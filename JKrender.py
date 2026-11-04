#!/usr/bin/env python3
"""JKrender 0.70.7 alpha — one Nuke render, split into chunks across several Nuke processes.

Started by the Nuke integration with a job file:  python JKrender.py <render folder>/job.json

Every worker is one Nuke process that opens the script ONCE and then renders chunk after chunk,
so a chunk costs no script load. Chunks are handed out as workers free up, so no worker sits idle
while another still has a long list. Each finished frame is checked on disk (missing, empty,
truncated EXR / PNG / JPEG / DPX, suspiciously small).
"""

import collections
import concurrent.futures
import hashlib
import copy
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

APP_VERSION = "0.70.7 alpha"
HOME_DIR = Path.home() / ".jkrender"
SETTINGS_FILE = HOME_DIR / "settings.json"
TEST_DIR = HOME_DIR / "test"  # Settings > Test this script: its renders, deleted after each setup
TEST_RESULTS = HOME_DIR / "test_results"  # and its tables, kept
QUEUE_FILE = HOME_DIR / "queue.json"  # the cards while the window is open; still there on opening means it did not close properly
WORKER_SCRIPT = Path(__file__).resolve().with_name("jkrender_worker.py")

# Movie containers are one file for the whole range, so they render in one piece on one worker.
MOVIE_EXTS = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".webm", ".gif"}
MAX_ATTEMPTS = 3  # a chunk is rendered up to 3 times (each retry on a fresh Nuke) before it counts as failed
SPAWN_GAP = 0.3  # seconds between Nuke starts, so licence checkout and script loads do not all hit at once
BESIDE_CACHE = 4  # GB a Nuke process takes besides its cache (measured: 12 GB with a full 8 GB cache)
NUKE_ENV_KEYS = ("NUKE_PATH", "PYTHONPATH", "OCIO")
SECRET_ENV_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
JKR_LINE = re.compile(r"JKR (READY|START|FRAME|CHUNK|FAIL|ERROR|BYE|MEM)\b ?(.*)$")
PROBLEMS = ("missing", "empty", "truncated", "unreadable")

DEFAULT_SETTINGS = {"auto_start": False, "auto_next": False, "resume_after_crash": False, "check_frames": True, "check_pixels": True, "check_on_open": True, "max_renders": 3, "ram_guard": True, "ram_limit": 90,
                    "vram_guard": True, "vram_limit": 95,
                    "chunk_auto": True, "use_gpu": True, "log_cleanup": True, "log_days": 7,
                    "slate": True, "slate_frame": 1000,
                    "auto_chunk": True, "hide_old_done": True}
APP_PORT = int(os.environ.get("JKRENDER_PORT") or 54360)  # a second JKrender hands its render to the one already open, through this local port
NUKE_PORTS = range(54340, 54350)  # where open Nukes listen (LISTEN_PORTS in JKrender_NukeIntegration)
SYSTEM_HIDDEN = ["Uptime", "Peak RAM", "Chunks", "Last frame"]  # System columns off until ticked in its header
RETURN_CHECK = 5  # seconds after a fall until a process may come back, and that its memory has to stay free first
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
        # whole or not at all: a file cut off by a crash would silently bring every setting back to its default
        temp = SETTINGS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        os.replace(temp, SETTINGS_FILE)
    except Exception:
        pass


def read_queue():
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def end_leftovers(pids):
    """The Nuke processes of a JKrender that went down: they would keep the memory and write over the same files.
    Only a Nuke with the very start time noted, never another process that got the same number."""
    if not psutil:
        return 0
    ended = []
    for pid, created in pids:
        try:
            proc = psutil.Process(pid)
            if abs(proc.create_time() - created) < 1 and "nuke" in proc.name().lower():
                proc.kill()
                ended.append(proc)
        except Exception:
            pass
    psutil.wait_procs(ended, timeout=10)  # Windows keeps their files locked until they are gone
    return len(ended)


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


class GpuPerProcess:
    """GPU use and dedicated VRAM of every process, from the Windows counters Task Manager shows.

    nvidia-smi cannot tell processes apart on Windows (WDDM), so this reads the "GPU Engine" and
    "GPU Process Memory" counters through pdh.dll. sample() -> {pid: (percent, bytes)}."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.pdh = ctypes.WinDLL("pdh")

        class Fmt(ctypes.Structure):
            _fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_double)]

        class Item(ctypes.Structure):
            _fields_ = [("name", wintypes.LPWSTR), ("fmt", Fmt)]

        self.Item = Item
        self.query = wintypes.HANDLE()
        self.engine = wintypes.HANDLE()
        self.memory = wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) & 0xFFFFFFFF:
            raise OSError("PdhOpenQuery failed")
        for path, handle in ((r"\GPU Engine(*)\Utilization Percentage", self.engine),
                             (r"\GPU Process Memory(*)\Dedicated Usage", self.memory)):
            if self.pdh.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(handle)) & 0xFFFFFFFF:
                raise OSError("no GPU counters")
        self.pdh.PdhCollectQueryData(self.query)

    def _read(self, counter):
        ct, wt = self.ct, self.wt
        size, count = wt.DWORD(0), wt.DWORD(0)
        if self.pdh.PdhGetFormattedCounterArrayW(counter, 0x200, ct.byref(size), ct.byref(count), None) & 0xFFFFFFFF \
                != 0x800007D2:  # PDH_MORE_DATA: tells the size
            return []
        buf = (ct.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(counter, 0x200, ct.byref(size), ct.byref(count), buf) & 0xFFFFFFFF:
            return []
        items = ct.cast(buf, ct.POINTER(self.Item))
        return [(items[i].name, items[i].fmt.value) for i in range(count.value)]

    def sample(self):
        self.pdh.PdhCollectQueryData(self.query)
        engines, out = {}, {}
        for name, value in self._read(self.engine):  # pid_123_luid_..._eng_0_engtype_3D
            m = re.match(r"pid_(\d+)_.*engtype_(.*)$", name or "")
            if m:
                key = (int(m.group(1)), m.group(2))
                engines[key] = engines.get(key, 0.0) + value
        for (pid, _), value in engines.items():  # like Task Manager: the busiest engine type
            out[pid] = (max(out.get(pid, (0.0, 0))[0], min(100.0, value)), 0)
        for name, value in self._read(self.memory):
            m = re.match(r"pid_(\d+)_", name or "")
            if m:
                pid = int(m.group(1))
                pct, used = out.get(pid, (0.0, 0))
                out[pid] = (pct, used + value)
        return out


def clean_render_folders(root, days, keep=()):
    """Delete render folders (snapshot, job, logs) under root whose newest file is older than `days`.

    Only folders JKrender made (they hold job.json) and none of the renders open now."""
    cutoff = time.time() - days * 86400
    keep = {os.path.normcase(os.path.abspath(str(k))) for k in keep}
    deleted, freed = 0, 0
    try:
        folders = [d for d in Path(root).iterdir() if d.is_dir() and (d / "job.json").is_file()]
    except OSError:
        return 0, 0
    for d in folders:
        if os.path.normcase(os.path.abspath(str(d))) in keep:
            continue
        try:
            files = [f for f in d.rglob("*") if f.is_file()]
            newest = max([f.stat().st_mtime for f in files] + [d.stat().st_mtime])
            if newest >= cutoff:
                continue
            size = sum(f.stat().st_size for f in files)
            shutil.rmtree(d)
            deleted += 1
            freed += size
        except OSError:
            continue
    return deleted, freed


def open_nukes():
    """How many open Nukes listen. All ports at once, as the window waits for the answer and an empty port takes a moment."""
    found = []

    def knock(port):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                found.append(port)
        except OSError:
            pass

    threads = [threading.Thread(target=knock, args=(port,), daemon=True) for port in NUKE_PORTS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(0.4)
    return len(found)


def clear_open_nukes():
    """Every open Nuke frees its RAM cache and Blink cache, when you say yes to it on Render. In the background: a port
    nobody listens on takes a moment to answer. A Nuke started before this version just ignores it."""
    def send():
        for port in NUKE_PORTS:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3) as conn:
                    conn.sendall(b'{"command": "clear_cache"}')
                    conn.shutdown(socket.SHUT_WR)
                    conn.recv(64)
            except OSError:
                pass

    threading.Thread(target=send, daemon=True).start()


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
    _, ymin, _, ymax = struct.unpack("<4i", attrs[b"dataWindow"])
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


def _one_value(pixels):
    """The value of every channel if the whole image holds just one, else None. Every 8th pixel first: min and max of a whole
    4K frame take 0.14 s, and nearly every frame shows at once that it is not one colour."""
    import numpy as np

    for image in (pixels[::8, ::8], pixels):
        low, high = image.min(axis=(0, 1)), image.max(axis=(0, 1))
        if not np.all(low == high):
            return None
    return np.atleast_1d(low)


def _blank(channels):
    """"empty", "black" or "one colour" for a frame like that, else None. A frame without RGB channels is not judged."""
    rgb, alpha = [], None
    for name, channel in channels.items():
        pixels = channel.pixels
        if name in ("RGB", "RGBA") and pixels.ndim == 3:
            rgb.append(pixels[:, :, :3])
            alpha = pixels[:, :, 3] if name == "RGBA" else alpha
        elif name in ("R", "G", "B"):
            rgb.append(pixels)
        elif name == "A":
            alpha = pixels
    if not rgb:
        return None
    values = [_one_value(p) for p in rgb]
    if any(v is None for v in values):
        return None
    if any(v.any() for v in values):
        return "one colour"
    if alpha is None:
        return "black"
    one = _one_value(alpha)
    return "empty" if one is not None and not one.any() else "black"


def check_pixels(paths):
    """What only the pixels can tell, for one frame: {"nan", "inf", "hash", "blank", "props"}, or {"error"}. The hash is of the
    pixels of an EXR, not of the file: its header holds the time it was written, so two identical frames have different files."""
    digest = hashlib.blake2b(digest_size=16)
    nan = inf = 0
    blank, props = None, {}
    try:
        for path in paths:
            if path.lower().endswith(".exr"):
                import numpy as np
                import OpenEXR

                with OpenEXR.File(path) as f:
                    channels = f.channels()
                    header = f.header()
                    (x0, y0), (x1, y1) = header["displayWindow"]
                    props = {"size": "%dx%d" % (x1 - x0 + 1, y1 - y0 + 1), "channels": " ".join(sorted(channels)),
                             "bit depth": " ".join(sorted({str(c.pixels.dtype) for c in channels.values()})),
                             "compression": str(header.get("compression", "")).split(".")[-1].replace("_COMPRESSION", "")}
                    blank = blank or _blank(channels)
                    for name in sorted(channels):
                        pixels = channels[name].pixels
                        digest.update(pixels.tobytes())
                        if pixels.dtype.kind == "f":
                            broken = ~np.isfinite(pixels)
                            if broken.any():
                                n = int(np.isnan(pixels).sum())
                                nan += n
                                inf += int(broken.sum()) - n
            else:
                with open(path, "rb") as f:
                    digest.update(f.read())
    except ImportError:
        return {"error": "needs numpy and OpenEXR"}
    except Exception as exc:
        return {"error": str(exc)[:120]}
    return {"nan": nan, "inf": inf, "hash": digest.hexdigest(), "blank": blank, "props": props}


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
    __slots__ = ("state", "worker", "secs", "started", "checks", "small", "chunk", "pixels", "same_as")

    def __init__(self):
        self.state = "queued"  # queued / rendering / done / failed / bad / skipped
        self.worker = None
        self.secs = None
        self.started = None
        self.checks = {}  # output index -> (status, size, note)
        self.small = False
        self.chunk = None
        self.pixels = None  # check_pixels of the frame
        self.same_as = None  # the first frame with exactly the same pixels


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
        self.frames_here = 0  # by this very Nuke; frames_done and frame_secs go on from the one it replaced
        self.frame_secs = []
        self.segments = []  # [kind, label, t0, t1 or None, state]
        self.tail = collections.deque(maxlen=200)
        self.log = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)  # line by line: readable while it runs
        self.log_path = log_path
        self.cpu = 0.0
        self.rss = 0.0
        self.rss_peak = 0.0  # the most RAM it took
        self.rss_base = None  # GB after its first chunk: what this script needs, the yardstick for leaks
        self.vram_base = None  # MB of VRAM after its first chunk, the same yardstick for the graphics card
        self.user_paused = False  # frozen from its right-click menu
        self.cache = None  # (bytes Nuke's cache holds, its limit), reported after each frame
        self.pinned = False  # started again by hand: the RAM guard closes the others first
        self.gpu = False  # started with --gpu
        self.leave = False  # Work on another render: this one hands its place over first
        self.handover = False  # its place goes to another render: it still renders one chunk, then closes
        self.why_leaving = None  # "RAM limit" while it closes after its chunk because RAM is over the limit
        self.closed_why = None  # why it closed, shown in State: "RAM limit", "VRAM limit"
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


def queued_left_for(engine):
    return any(c.state == "queued" and engine.in_scope(c) for c in engine.chunks)


class RenderEngine:
    def __init__(self, job, job_dir, settings):
        self.job = job
        self.dir = Path(job_dir)
        self.log_dir = self.dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.events = pyqueue.Queue()
        self.checker = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.pixel_checker = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.pixel_futures = []
        self.pixels_run = 0  # results of a check that was stopped are not counted
        self.pixels_pending = 0
        self.pixels_total = 0
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
        self.held, self.restart_ids = set(), []  # processes closed from their menu; ones to start again
        self.filling = True  # all its processes still to start (see _may_spawn)
        self.size_seen = 0.0  # GB of the largest process of this run that has rendered a few frames
        self.preferred_wids = []  # names moved here with Work on, taken by the next processes started
        self.hand_wids = set()  # of those, the ones you started by hand: they go past the RAM and VRAM limits
        self.scope = None  # set of frames: only these render (Render this frame / batch); None = everything
        self.next_wid = 1
        self.next_cid = 1
        self.last_spawn = 0.0
        self.started = None
        self.rerun_at = None  # when the render was started again after an earlier run (Workers can hide that run's done chunks)
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
        self.name = Path(job.get("script") or "render").stem  # e.g. shot_010_comp_v004
        self.write_names = ", ".join(w["name"] for w in job.get("writes", []))
        self.everything = False
        self.resume_state = "ready"
        self.movie_info = None  # probe_movie() of the movie file, for movie output
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

    def preferred_chunk(self):
        """Chunk size from Settings: Auto = about 4 chunks per Nuke process (at most 10 frames), or the size set."""
        if self.movie:
            return max(1, len(self.frames))
        if self.settings.get("chunk_auto", True) or not self.settings.get("chunk_size"):
            procs = self.manager.total if self.manager else (self.settings.get("processes") or 8)
            return max(1, min(10, math.ceil(len(self.frames) / (max(1, procs) * 4))))
        return max(1, int(self.settings["chunk_size"]))

    def apply_chunk_setting(self):
        """Renders that are not running take the chunk size from Settings."""
        if self.movie or self.state not in ("ready", "stopped", "done", "failed"):
            return
        size = self.preferred_chunk()
        if size != self.chunk_size:
            self.set_chunk_size(size)

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

    def done_if_on_disk(self):
        """A render whose whole range is already on disk and passes the check shows as done (green card)
        without rendering anything; Render again renders it all anew."""
        if self.state == "ready" and self.frames and self.nothing_to_render():
            self.state = "done"
            self.note("The whole range is already rendered and passes the check: nothing to render", "ok")

    def start(self, everything=False):
        """everything=True renders the whole range again, including frames already on disk."""
        if self.state in ("rendering", "starting", "finishing"):
            return
        self._stop_pixels()
        self.filling = True  # all its processes still to start (see _may_spawn)
        if self.manager is not None and self.manager.live_count() == 0:
            self.manager.fresh_start()
        self.size_seen = 0.0
        self.crashes = self.load_failures = 0  # a new run: the last one's crashes must not stop it at its first
        self.ever_ready = False
        self.scope = None  # Render renders everything still to render
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
                fr.checks, fr.small, fr.pixels, fr.same_as = {}, False, None, None
        if not everything:
            self.scan_disk()  # frames already on disk and passing the check are never rendered twice
        if all(w.state == "gone" for w in self.workers.values()):
            # Stop closed every Nuke; the new run starts its own processes from W01 on a fresh timeline.
            self.workers = {}
            self.next_wid = 1
        self.held, self.restart_ids = set(), []
        self.error = ""
        self.state = "rendering"
        self.rerun_at = time.time() if self.started else None
        self.started = time.time()
        self.ended = None
        self.paused_secs = 0.0
        self.paused_at = None
        self.pauses = []
        self.done_times.clear()
        self.notice = None
        if self.manager:
            self.threads = self.manager.threads
        self.note("Render started: %d frames in %d chunks, %d threads and %d GB per Nuke process"
                  % (sum(len(c.frames) for c in self.chunks if c.state == "queued"),
                     sum(1 for c in self.chunks if c.state == "queued"), self.threads, self.ram))
        self._ram_seen = 0.0

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
            if w.state == "gone" or not w.ps or (not on and w.user_paused):  # one paused by hand stays paused
                continue
            self._suspend(w, on)
            n += 1
        return n

    @staticmethod
    def _suspend(w, on):
        try:
            procs = [w.ps] + w.ps.children(recursive=True)
        except Exception:
            procs = [w.ps]
        for proc in procs:
            try:
                proc.suspend() if on else proc.resume()
            except Exception:
                pass

    def worker_state(self, w):
        if w.state == "gone":
            return "closed (%s)" % w.closed_why if w.closed_why else "closed"
        state = "paused" if w.user_paused else w.state
        # where it goes after this chunk
        if w.leave and getattr(w, "leave_to", None) is not None:
            return "%s → %d %s" % (state, w.leave_to.number, w.leave_to.name)
        if getattr(w, "recycle_vram", False) and state == "busy":
            return "busy → fresh Nuke (VRAM)"
        if w.why_leaving and (w.handover or (w.retire and state == "busy")):
            return "%s → closes (%s)" % (state, w.why_leaving)
        if w.handover or (w.retire and state == "busy"):
            return "%s → another render" % state if self.manager and len(self.manager.engines) > 1 else "%s → closes" % state
        return state

    def user_close(self, w):
        """Close one process from its menu. Its chunk goes back to the queue; it stays closed until Start again."""
        if w.state == "gone":
            return
        self.held.add(w.id)
        if w.user_paused:
            self._suspend(w, False)
            w.user_paused = False
        self._kill(w, requeue=True, note="stopped")
        self.note("%s closed by you; its chunk goes back to the queue" % w.name)

    def user_start_here(self, wid, src=None):
        """Start again, in this render, a process that closed in render src (finished, stopped), under its name;
        without src, a place no process has taken yet."""
        if wid not in self.preferred_wids:
            self.preferred_wids.append(wid)
        self.hand_wids.add(wid)
        self.note(("W%02d of render %d (%s) starts again here%s" % (wid, src.number, src.name, self._make_room_by_hand()))
                  if src is not None else "W%02d starts here, started by you%s" % (wid, self._make_room_by_hand()),
                  "warn" if self.manager and self.manager.free_slots() <= 0 else "ev")

    def user_start(self, wid):
        """Start a closed process again, under its own name."""
        self.held.discard(wid)
        if wid not in self.restart_ids:
            self.restart_ids.append(wid)
        self.note("W%02d starts again%s" % (wid, self._make_room_by_hand()),
                  "warn" if self.manager and self.manager.free_slots() <= 0 else "ev")

    def _make_room_by_hand(self):
        """A process you start by hand goes past the RAM and VRAM limits: they let one more run, and do not close
        one again for 90 s. What it says about that, for the log."""
        m = self.manager
        cap = m.live_count() + 1 if m is not None else 0
        which = [n for n, c in (("RAM", m.ram_cap), ("VRAM", m.vram_cap)) if c is not None and c < cap] if m else []
        if m is not None and m.free_slots() <= 0 and which:
            m.started_by_hand = time.time() + 90
            if m.ram_cap is not None and m.ram_cap < cap:
                m.ram_cap = None if cap >= m.total else cap
            if m.vram_cap is not None and m.vram_cap < cap:
                m.vram_cap = None if cap >= m.total else cap
            return "; the %s limit lets one more process run for it" % " and ".join(which)
        if m is not None and m.free_slots() <= 0:
            return (" once a place is free: all %d Nuke processes run (Settings > Processes)" % m.total)
        if m is not None:
            m.started_by_hand = time.time() + 90
        return ""

    def user_pause(self, w, on):
        if not w.ps or w.state == "gone" or w.user_paused == on:
            return
        self._suspend(w, on)
        w.user_paused = on
        self.note("%s %s by you" % (w.name, "paused" if on else "resumed"))

    def paused_between(self, a, b):
        """Seconds of [a, b] the render spent paused."""
        spans = list(self.pauses) + ([(self.paused_at, time.time())] if self.paused_at else [])
        return sum(max(0.0, min(b, p1) - max(a, p0)) for p0, p1 in spans)

    def forget_workers(self):
        """Stop by hand: the stopped processes leave the Workers timeline and the System table.
        Their logs stay on disk."""
        if all(w.state == "gone" for w in self.workers.values()):
            self.workers = {}
            self.held, self.restart_ids = set(), []

    def stop(self, reason="Stopped by user"):
        self.scope = None
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

    def in_scope(self, chunk):
        return self.scope is None or any(f in self.scope for f in chunk.frames)

    def rerender(self, frames, only=False):
        """Render these frames again. only=True on a render that is not running renders just these frames
        (Render this frame / batch); the other frames still to render wait for Render."""
        frames = sorted(set(frames) & set(self.frames))
        if not frames:
            return
        idle = self.state in ("ready", "done", "failed", "stopped")
        if only and idle:
            self.scope = set(frames)
        for c in self.chunks:
            if c.state == "queued":
                c.frames = [f for f in c.frames if f not in frames]
        for f in frames:
            fr = self.frames[f]
            fr.state, fr.secs, fr.checks, fr.small, fr.worker, fr.pixels, fr.same_as = "queued", None, {}, False, None, None, None
        self.chunks = [c for c in self.chunks if c.state != "queued" or c.frames]
        made = self._chunk(frames)
        self.note("Queued %d frame(s) again in %d chunk(s)%s" % (len(frames), len(made),
                                                                 ": only these render" if self.scope else ""))
        if self.state in ("ready", "done", "failed", "stopped", "finishing"):
            if idle:  # a new run: its own clock
                self.crashes = self.load_failures = 0
                self.rerun_at = time.time() if self.started else None
                self.started, self.paused_secs, self.paused_at, self.pauses = time.time(), 0.0, None, []
                self.done_times.clear()
                self.notice = None
            self.state = "rendering"
            self.started = self.started or time.time()
            self.ended = None

    def requeue_chunk(self, chunk):
        if chunk.state == "rendering":
            return
        self.rerender(chunk.all_frames)

    # ---- workers ------------------------------------------------------------
    def _process_size(self):
        """Threads and Nuke cache of one Nuke process. A movie cannot be split, so its one process gets every
        thread; its cache is the Nuke cache setting like any other process, so several renders never ask for
        most of the RAM each."""
        if self.movie:
            return auto_plan(1, True)["threads"], self.ram
        return self.threads, self.ram

    def _worker_args(self):
        """What the worker opens: the script (its snapshot), the original's path, the Write names."""
        names = "|".join(w["name"] for w in self.job.get("writes", []))
        script = self.job.get("snapshot") or self.job.get("script")
        original = self.job.get("script") if self.job.get("snapshot") else ""
        return script, original, names

    def _command(self, gpu=False):
        script, original, names = self._worker_args()
        threads, ram = self._process_size()
        # Nuke in terminal mode renders GPU nodes (Kronos, ZDefocus, ...) on the CPU unless told --gpu
        gpu = ["--gpu", "0"] if gpu else []
        return [
            self.job.get("nuke_exe") or "Nuke",
            "-i", "-m", str(threads), "-c", "%dG" % ram] + gpu + [
            "-t", str(WORKER_SCRIPT), script, original, names,
        ]

    def _spawn(self):
        # A process that had to close (RAM guard, recycled) is replaced under its own number and on its
        # own timeline row, so the render keeps the same W01-W08 instead of W09, W10...
        running = {i for e in (self.manager.engines if self.manager else [self])
                   for i, x in e.workers.items() if x.state != "gone"}
        wanted = [i for i in self.preferred_wids if i not in running]  # Work on: the name the user moved here
        # never a number that runs in another render meanwhile (W03 closed here, W03 already started in render 1)
        restart = [i for i in getattr(self, "restart_ids", [])
                   if i in self.workers and self.workers[i].state == "gone" and i not in running]
        gone = restart or sorted(i for i, x in self.workers.items()
                                 if x.state == "gone" and i not in getattr(self, "held", ()) and i not in running)
        old = self.workers[gone[0]] if gone else None
        if wanted:
            self.preferred_wids.remove(wanted[0])
            old = self.workers.get(wanted[0]) if wanted[0] in self.workers else None
        by_hand = bool(old and old.id in getattr(self, "restart_ids", []))
        if by_hand:
            self.restart_ids.remove(old.id)
        if wanted and wanted[0] in self.hand_wids:
            self.hand_wids.discard(wanted[0])
            by_hand = True
        if old:
            wid = old.id
        elif wanted:
            wid = wanted[0]
        else:
            wid = self.manager.take_wid(self) if self.manager else self.next_wid
            self.next_wid = max(self.next_wid, wid) + 1
        use_gpu = bool(self.settings.get("use_gpu", True))
        cmd = self._command(gpu=use_gpu)
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        kwargs.update(hidden())
        kwargs["env"] = nuke_render_env(self.job.get("env"))
        # the same, as UTF-8: Nuke reads its command line in the Windows code page, so a path such as
        # "D:/Práce/Žluťoučký kůň/..." arrived broken and every Nuke stopped while opening the script
        kwargs["env"]["JKRENDER_ARGS"] = json.dumps(self._worker_args())
        if self.job.get("test_out"):
            kwargs["env"]["JKRENDER_TEST_OUT"] = self.job["test_out"]
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
        w.gpu = use_gpu
        if old:
            # its timeline goes on, with this script load on it too (a grey block, like the first one)
            load = w.segments[-1]
            load[1] = "again: %s" % old.closed_why if old.closed_why else "again"
            ended = old.segments[-1][3] if old.segments else None
            if ended and 0 <= load[2] - ended < 30:
                load[2] = ended  # from when the old one finished: closing it and starting this one is the reload too
            w.segments, w.frames_done, w.frame_secs = old.segments + [load], old.frames_done, old.frame_secs
        w.pinned = by_hand
        self.workers[wid] = w
        self.last_spawn = time.time()
        threading.Thread(target=self._reader, args=(w,), daemon=True).start()
        self.note("%s %s (pid %d, %d threads, %d GB)"
                  % (w.name, "started again" if old else "started", proc.pid, w.threads, w.ram))
        return w

    def _reader(self, w):
        try:
            for line in w.proc.stdout:
                self.events.put(("line", w, line))
        except Exception as exc:
            self.events.put(("line", w, "[output read error: %s]\n" % exc))
        finally:
            try:
                w.proc.wait(timeout=30)
            except Exception:
                pass
            self.events.put(("exit", w, w.proc.returncode))

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
        return [w for w in self.workers.values()
                if w.state in ("loading", "idle", "busy") and not w.retire and not w.handover]

    def need(self):
        """Nuke processes this render could use right now."""
        if self.state == "paused":  # frozen processes keep their place
            return sum(1 for w in self.workers.values() if w.state != "gone")
        if self.state != "rendering":
            return 0
        busy = sum(1 for w in self._live() if w.state == "busy")
        return busy + sum(1 for c in self.chunks if c.state == "queued" and self.in_scope(c))

    def is_tail(self):
        """Rendering its last chunks: nothing left to hand out, so the next render may start loading."""
        return self.state == "rendering" and not any(c.state == "queued" and self.in_scope(c) for c in self.chunks)

    def _may_spawn(self):
        m = self.manager
        if m is None:
            return True
        if m.free_slots() <= 0:
            return False
        if self.restart_ids or self.hand_wids:
            return True
        if self.filling:
            # Render starts all its processes straight away, also ones it has a place for only later (the renders share
            # the places). The rule for coming back starts when one falls: _fell, _on_exit.
            self.filling = len(self._live()) + 1 < m.total
            return True
        why = m.no_room(self)
        if why and time.time() - getattr(self, "_no_room_noted", 0) > 60:
            self._no_room_noted = time.time()
            self.note("One more Nuke process would not fit (%s): %d run, more start when there is room"
                      % (why, m.live_count()), "warn")
        return not why

    def _balance_workers(self):
        live = self._live()
        extra = len(live) - self.workers_target
        if extra > 0:
            # Hand the places over gently, so no work is thrown away: an idle one closes now; a busy one
            # finishes its chunk first; one still opening the script opens it, renders one chunk, then closes.
            # Busy ones before loading ones (they are free sooner), the last one started first.
            # Over the RAM or VRAM limit the last one started closes first whatever it does, and one still opening
            # the script closes now: it has the least work to lose and is about to take more memory.
            m = self.manager
            vram = m is not None and m.vram_bound()
            limit = vram or (m is not None and m.ram_cap is not None)
            for w in sorted(live, key=lambda w: (not w.leave, w.pinned, -w.spawned) if limit else
                            (not w.leave, {"idle": 0, "busy": 1, "loading": 2}[w.state], w.pinned, -w.spawned))[:extra]:
                why = None if w.leave or not limit else ("VRAM limit" if vram else "RAM limit")
                w.why_leaving = w.closed_why = why
                if w.state == "busy":
                    w.retire = True
                elif w.state == "loading" and not why:
                    w.handover = True
                else:
                    self._quit(w, "not needed")
        elif extra < 0:
            # Room again (another render done, the RAM limit up): ones still to close after their chunk stay.
            for w in [w for w in self.workers.values()
                      if w.state in ("loading", "idle", "busy") and (w.retire or w.handover)][:-extra]:
                w.retire = w.handover = False
                w.why_leaving = w.closed_why = None

    def _quit(self, w, why):
        w.retire = True
        if w.state == "loading":
            self._kill(w, requeue=False, note="retired")
            self.note("%s closed (%s)" % (w.name, why))
            return
        w.state = "quitting"
        w.quit_at = time.time()
        w.close_segment("done")
        w.send("QUIT")
        self.note("%s closing (%s)" % (w.name, why))

    # ---- event handling -----------------------------------------------------
    def note(self, text, level="ev"):
        entry = ("", "%s  %s" % (datetime.datetime.now().strftime("%H:%M:%S"), text), level)
        self.log.append(entry)
        self.new_log.append(entry)
        try:  # the Log tab's events, kept next to the workers' logs
            with open(self.dir / "jkrender.log", "a", encoding="utf-8") as f:
                f.write(entry[1] + "\n")
        except Exception:
            pass

    def _end_stuck(self):
        """A process told to quit that is still there after 8 s (Nuke can take long to let go of the GPU) is ended, so it
        does not hold a place another process is waiting for, or the memory once the render is through."""
        now = time.time()
        for w in self.workers.values():
            if w.state == "quitting" and now - getattr(w, "quit_at", now) > 8:
                try:
                    w.proc.kill()
                except Exception:
                    pass
                w.quit_at = now + 3600
                self.note("%s did not quit within 8 s and was ended" % w.name, "warn")

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
                self._on_line(ev[1], ev[2])
            elif kind == "exit":
                self._on_exit(ev[1], ev[2])
            elif kind == "check":
                self._on_check(*ev[1:])
            elif kind == "pixels":
                self._on_pixels(*ev[1:])
        self._end_stuck()
        if self._small_dirty:
            self._flag_small()
        if self.state in ("rendering", "paused"):
            self._schedule()
            changed = True
        elif self.state == "finishing" and self.pending_checks <= 0:
            self._finish()
            changed = True
        for w in self.workers.values():
            if w.state != "gone" and w.ps:
                try:
                    w.rss = w.ps.memory_info().rss / 1024 ** 3
                    w.rss_peak = max(w.rss_peak, w.rss)
                    if w.frames_here >= 2:
                        self.size_seen = max(self.size_seen, w.rss)
                except Exception:
                    pass
        return changed

    def _on_line(self, w, line):
        if w is None:
            return
        w.log.write(line)
        if w.state == "gone":  # last words of a closed process; its replacement may already use its name
            return
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
        elif kind == "MEM":
            try:
                w.cache = (int(rest[0]), int(rest[1]))
            except (IndexError, ValueError):
                pass
        elif kind == "FRAME":
            f, secs = int(rest[0]), float(rest[1])
            secs = max(0.0, secs - self.paused_between(now - secs, now))  # Nuke's clock ran on while frozen
            w.frame = None
            w.frames_done += 1
            w.frames_here += 1
            w.frame_secs.append(secs)
            if self.state == "rendering" or self.state == "paused":
                self.done_times.append(self.elapsed())  # render clock, so a pause does not drag the speed down
            fr = self.frames.get(f)
            if fr is not None:
                fr.state, fr.secs, fr.worker = "done", secs, w.name
                if self.settings.get("check_frames", True) or self.job.get("test_out"):  # Test this script counts its frames by this
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
            # Recycle a process whose RAM keeps growing (a leak), not one that a heavy script simply needs:
            # closing it after every chunk only costs a script load each time.
            if w.rss_base is None:
                w.rss_base = w.rss
                self._remember_ram(w.rss)
                if w.rss > w.ram * 1.3 and not getattr(self, "_ram_noted", False):
                    self._ram_noted = True
                    self.note("%s uses %.1f GB RAM after its first chunk, more than the %d GB Nuke cache set. "
                              "That is fine; Nuke takes more than its cache for heavy scripts. Use fewer processes if the PC runs short."
                              % (w.name, w.rss, w.ram), "warn")
            m = self.manager
            vram_now = m.gpu_mem.get(w.proc.pid, (0, 0))[1] / 1024 ** 2 if m is not None and w.gpu else 0.0
            if vram_now and w.vram_base is None:
                w.vram_base = vram_now
                self._vram_seen = max(getattr(self, "_vram_seen", 0.0), vram_now)
            # A process normally takes its cache and BESIDE_CACHE more, and the first chunk can be well under that.
            if w.rss > max(w.ram + 2 * BESIDE_CACHE, w.rss_base * 1.5):
                self._quit(w, "recycled:%.1f GB RAM, up from %.1f GB after its first chunk" % (w.rss, w.rss_base))
            elif getattr(w, "recycle_vram", False):
                # Picked only with the card close to the limit (_recycle_grown). Some scripts make Nuke keep VRAM it no
                # longer needs, frame after frame; with room on the card that costs nothing, and a restart costs a script load.
                w.closed_why = "recycled, VRAM"
                self._quit(w, "recycled: %.1f GB VRAM, up from %.1f GB after its first chunk"
                           % (vram_now / 1024, (w.vram_base or 0) / 1024))
            elif w.retire or w.handover:
                self._quit(w, "its place goes to another render" if self.manager and len(self.manager.engines) > 1
                           else "not needed")
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
            self._stop_if_nothing_renders()
        elif kind == "ERROR":
            w.error = " ".join(rest)
        elif kind == "BYE":
            pass

    def _stop_if_nothing_renders(self):
        """Chunk after chunk has failed and not one frame has come out of this run: a missing file or the like, which
        every chunk would meet again. Without this each chunk takes three fresh Nukes, hundreds on a long render."""
        failed = [c for c in self.chunks if c.state == "failed"]
        if len(failed) >= 3 and not self.done_times and self.state == "rendering":
            self.error = "%d chunks failed and no frame rendered: %s" % (len(failed), failed[0].error)
            self.stop("Render stopped: " + self.error)
            self.state = "failed"

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
            # the worker's own error first: Nuke's log may also name a missing file the script does not need
            reason = getattr(w, "error", None) or classify_error(tail)
            self.note("!! %s exited while opening the script (code %s): %s" % (w.name, code, reason), "err")
            if not self.ever_ready and self.load_failures >= min(2, max(1, self.workers_target)):
                self.error = "Nuke could not open the script: %s. See the %s log." % (reason, w.name)
                self.stop("Render stopped: " + self.error)
                self.state = "failed"
        else:
            self.crashes += 1
            self.filling = False
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

    def check_all_pixels(self):
        """Settings > Check pixels when a render ends, and the button: NaN and inf in every frame, and frames that are
        exactly the same anywhere in the sequence. In the background, whatever else goes on. Not for a movie."""
        if self.movie or self.pixels_pending:
            return
        frames = [f for f, fr in sorted(self.frames.items()) if fr.state in ("done", "skipped", "bad")]
        self.pixels_pending = self.pixels_total = len(frames)
        self.pixels_run += 1
        self.pixel_futures = []
        for f in frames:
            try:
                fut = self.pixel_checker.submit(check_pixels, self.output_files(f))
            except RuntimeError:  # closing
                self.pixels_pending = 0
                return
            self.pixel_futures.append(fut)
            fut.add_done_callback(lambda fu, f=f, run=self.pixels_run: fu.cancelled() or self.events.put(("pixels", run, f, fu.result())))

    def _stop_pixels(self):
        """A render starts: no reading frames next to it, some of them about to be written. When it ends the check runs on all of them."""
        if not self.pixels_pending:
            return
        for fut in self.pixel_futures:
            fut.cancel()
        self.pixels_pending = 0
        self.pixels_run += 1
        for fr in self.frames.values():
            fr.pixels, fr.same_as = None, None
        self.note("Pixel check stopped: a render started")

    def _on_pixels(self, run, frame, result):
        if run != self.pixels_run:
            return
        self.pixels_pending -= 1
        if frame in self.frames:
            self.frames[frame].pixels = result
        if self.pixels_pending > 0:
            return
        first, broken, same, errors, blank, differs = {}, [], [], {}, {}, {}
        # what most frames have: a frame with something else was rendered another way
        usual = {}
        for fr in self.frames.values():
            for key, value in ((fr.pixels or {}).get("props") or {}).items():
                usual.setdefault(key, collections.Counter())[value] += 1
        usual = {key: counts.most_common(1)[0][0] for key, counts in usual.items()}
        for f, fr in sorted(self.frames.items()):
            fr.same_as = None
            if not fr.pixels:
                continue
            if "error" in fr.pixels:
                errors.setdefault(fr.pixels["error"], []).append(f)
                continue
            if fr.pixels["nan"] or fr.pixels["inf"]:
                broken.append(f)
            fr.pixels["differs"] = ["%s %s, the rest %s" % (key, value, usual[key]) for key, value in fr.pixels["props"].items() if value != usual[key]]
            if fr.pixels["differs"]:
                differs[f] = fr.pixels["differs"]
            if fr.pixels["blank"]:
                blank.setdefault(fr.pixels["blank"], []).append(f)
            elif fr.pixels["hash"] in first:
                fr.same_as = first[fr.pixels["hash"]]
                same.append(f)
            else:
                first[fr.pixels["hash"]] = f
        if broken:
            self.note("!! NaN or inf pixels in %d frame(s): %s" % (len(broken), frames_to_spec(broken)), "err")
        if same:
            self.note("!! %d frame(s) exactly the same as an earlier one: %s. Fine if it is meant to hold." % (len(same), frames_to_spec(same)), "err")
        for kind, frames in blank.items():
            self.note("!! %d frame(s) %s: %s. Fine if it is meant to be." % (len(frames), kind, frames_to_spec(frames)), "err")
        for f, notes in sorted(differs.items()):
            self.note("!! frame %d differs from the sequence: %s" % (f, "; ".join(notes)), "err")
        for text, frames in errors.items():
            self.note("! Pixels not checked in %d frame(s): %s" % (len(frames), text), "warn")
        if not broken and not same and not errors and not blank and not differs:
            self.note("Pixels checked in %d frames: no NaN, no inf, no blank frame, none the same as another, all alike" % self.pixels_total, "ok")

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

    def _remember_ram(self, gb):
        """RAM one Nuke process of this script took after its first chunk (the most seen this run)."""
        if self.movie or gb <= getattr(self, "_ram_seen", 0.0):
            return
        self._ram_seen = gb
        need = dict(self.settings.get("script_ram") or {})
        need[self.name] = round(gb, 1)
        self.settings["script_ram"] = need
        if not self.settings.get("_test"):
            write_settings(self.settings)

    def size_gb(self):
        """GB one Nuke process of this render takes: the largest seen this run once one has rendered a few frames;
        before that its cache and what a process takes besides it. A process just loaded is far smaller than it will be."""
        return self.size_seen or self.ram + BESIDE_CACHE

    def ram_need(self):
        """GB per Nuke process this script took last time, or None."""
        return None if self.movie else (self.settings.get("script_ram") or {}).get(self.name)

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
            queued = sorted((c for c in self.chunks if c.state == "queued" and self.in_scope(c)),
                            key=lambda c: (c.attempts == 0, min(c.frames)))
            for w in [w for w in self.workers.values() if w.state == "idle" and not w.retire]:
                if not queued:
                    break
                self._dispatch(w, self._tail_split(queued.pop(0)))
            live = self._live()
            busy = sum(1 for w in live if w.state == "busy")
            loading = sum(1 for w in live if w.state in ("loading", "idle"))
            queued_left = sum(1 for c in self.chunks if c.state == "queued" and self.in_scope(c))
            want = min(self.workers_target - len(getattr(self, "held", ())), busy + queued_left)
            if (len(live) < want and now - self.last_spawn >= SPAWN_GAP and loading < queued_left + 1
                    and self._may_spawn()):
                self._spawn()
            self._balance_workers()
            # Nothing left to hand out: close idle workers right away so their RAM is free.
            if not queued_left:
                for w in [w for w in self.workers.values() if w.state == "idle"]:
                    self._quit(w, "no chunks left")
        if self.state == "rendering" and not any(c.state == "rendering" or (c.state == "queued" and self.in_scope(c))
                                                 for c in self.chunks):
            self.state = "finishing"
            for w in list(self.workers.values()):
                if w.state in ("idle", "loading"):
                    self._quit(w, "render finished")

    def _tail_split(self, chunk):
        """Hand out at most remaining frames / (2 x processes) at a time.

        Early on that is the whole chunk; towards the end chunks shrink down to single frames,
        so every process finishes at about the same time instead of one working through a long
        last chunk while the others wait. The rest of a split chunk goes back to the queue."""
        if self.movie or chunk.attempts:
            return chunk
        if not self.settings.get("auto_chunk", True):
            return chunk
        remaining = sum(len(c.frames) for c in self.chunks if c.state == "queued")
        procs = max(1, sum(1 for w in self.workers.values() if w.state in ("loading", "idle", "busy") and not w.retire))
        size = max(1, int(math.ceil(remaining / (2.0 * procs))))
        if len(chunk.frames) <= size:
            return chunk
        head, tail = chunk.frames[:size], chunk.frames[size:]
        return self._split_chunk(chunk, head, tail)

    def _split_chunk(self, chunk, head, tail):
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
        only = self.scope
        self.scope = None
        if failed:
            self.state = "failed"
            self.error = "%d chunk(s) failed: %s" % (len(failed), failed[0].error)
        elif only and any(fr.state == "queued" for fr in self.frames.values()):
            self.state = "ready"  # just the picked frames were rendered; Render does the rest
        else:
            self.state = "done"
        self.note("Render %s in %s. %d frames, %d bad on disk." % (
            "finished" if not failed else "ended with failures", fmt_secs(self.elapsed()), len(self.frames), len(bad)),
            "err" if failed or bad else "ok")
        self.write_report()
        if self.settings.get("check_pixels", True) and not self.job.get("test_out"):
            self.check_all_pixels()

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
        """Seconds left: the frames still to go at the current speed, but never less than the frames being rendered
        still need, so the last frames, when fewer processes work, do not show 0 or nothing."""
        c = self.counts()
        left = c["queued"] + c["rendering"]
        if not left:
            return 0.0
        r = self.rate()
        by_rate = left / r if r > 0 else None
        secs = self.frame_secs()[-40:]
        avg = sum(secs) / len(secs) if secs else None
        now = time.time()
        running = [fr for fr in self.frames.values() if fr.state == "rendering" and fr.started]
        in_flight = None
        if avg and running:
            in_flight = max(max(0.0, avg - (now - fr.started - self.paused_between(fr.started, now))) for fr in running)
        known = [x for x in (by_rate, in_flight) if x is not None]
        return max(1.0, max(known)) if known else None  # still rendering: never 0

    def frame_secs(self):
        return [fr.secs for fr in self.frames.values() if fr.secs is not None]

    def run_secs(self):
        """Frame times of this run. Frames from an earlier run keep theirs, which says nothing about its speed: against
        this run's clock they showed 8 processes rendering 17 frames at once."""
        return [fr.secs for fr in self.frames.values() if fr.secs is not None and fr.started and fr.started >= (self.started or 0)]

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
        self.pixel_checker.shutdown(wait=False, cancel_futures=True)


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
        self.vram_cap = None  # VRAM guard's lower process count while the graphics card is nearly full
        self._vram_others_noted = 0.0
        self.vram_idle = None  # MB of VRAM in use while none of our Nuke processes ran
        self._vram_lowered_at = None  # VRAM % when the VRAM limit last took a process away
        self.falls = collections.deque(maxlen=20)  # when a process last closed for memory (see fall_wait)
        self.vram_falls = collections.deque(maxlen=20)  # of those, for VRAM (see _fell_vram)
        self._running = set()  # renders running at the last tick, to see one end (see _start_next)
        self.started_by_hand = 0.0  # until then the RAM and VRAM limits leave a process started by hand alone
        self._vram_ok_since = None
        self.vram = None  # [GPU %, VRAM used MB, VRAM total MB] from the window's GPU monitor
        self.gpu_mem = {}  # pid -> (GPU %, VRAM bytes)
        self._vram_checked = 0.0
        self._last_vram_kill = 0.0

    @property
    def max_renders(self):
        return max(1, min(MAX_RENDERS, int(self.settings.get("max_renders") or MAX_RENDERS)))

    def add(self, job, job_dir):
        e = RenderEngine(job, job_dir, self.settings)
        e.manager = self
        if not e.movie:
            e.set_chunk_size(e.preferred_chunk())
        e.number = self.next_number
        self.next_number += 1
        e.threads, e.ram = self.threads, self.ram
        e.workers_target = 0
        e.scan_disk()  # frames the Write already rendered show as done straight away
        e.done_if_on_disk()
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
        """Processes the renders may use now: the total, unless the RAM or VRAM guard holds it lower."""
        return max(1, min(self.total, self.ram_cap or self.total, self.vram_cap or self.total))

    def vram_bound(self):
        """The VRAM limit, not the RAM limit, is what holds the number of processes down now."""
        return self.vram_cap is not None and (self.ram_cap is None or self.vram_cap <= self.ram_cap)

    def free_slots(self):
        return self.budget() - self.live_count()

    @staticmethod
    def ram_percent():
        return psutil.virtual_memory().percent if psutil else 0.0

    def _more_fit(self, pct, limit):
        """How many more Nuke processes fit: the RAM left under the limit, less a margin and what the processes running
        now are still going to take, split by the size of one process."""
        if not psutil:
            return 0
        total = psutil.virtual_memory().total / 1024 ** 3
        room = total * (limit - 2 - pct) / 100.0
        for e in self.engines:
            for w in e.workers.values():
                if w.state in ("loading", "idle", "busy"):
                    room -= max(0.0, e.size_gb() - w.rss)
        one = max([e.size_gb() for e in self.engines if e.state == "rendering"] or [total * 0.08])
        return max(0, int(room // max(0.5, one)))

    def fresh_start(self):
        """Nothing runs and a render starts: limits a render before ran into say nothing about this one."""
        self.ram_cap = self.vram_cap = self._vram_lowered_at = None
        self._ram_ok_since = self._vram_ok_since = None
        self.falls.clear()
        self.vram_falls.clear()

    def _fell(self):
        self.falls.append(time.time())
        for e in self.engines:
            e.filling = False

    def _fell_vram(self):
        """One closed for VRAM. Twice in 5 minutes: the renders say so, once a run. It changes nothing by itself."""
        self._fell()
        now = time.time()
        self.vram_falls.append(now)
        recent = sum(1 for t in self.vram_falls if now - t < 300)
        if recent < 2 or self.total < 2:
            return
        for e in self.engines:
            if e.state == "rendering" and not e.notice and not e.settings.get("_test"):
                e.notice = ("These processes keep filling the graphics card. Fewer processes with more threads may be much faster: "
                            "Test this script measures it.", "warn")
                e.note("!! %d processes closed for VRAM within 5 minutes. %s" % (recent, e.notice[0]), "warn")

    def fall_wait(self):
        """Seconds until a process may come back after one closed for memory: RETURN_CHECK, then 60, 120 and 300 as it happens
        again within 10 minutes, so a script that does not fit does not go round and round. Processes closing one
        after another within a minute are one overload, not several."""
        now = time.time()
        recent = [t for t in self.falls if now - t < 600]
        if not recent:
            return 0.0
        times = 1 + sum(1 for a, b in zip(recent, recent[1:]) if b - a >= 60)
        return max(0.0, recent[-1] + (RETURN_CHECK, 60, 120, 300)[min(times, 4) - 1] - now)

    def _vram_idle_update(self):
        """VRAM in use while no Nuke process of JKrender runs: Windows, other programs. The rest is ours, including
        what the per-process counters do not show (the CUDA context, the driver)."""
        if self.vram and self.live_count() == 0:
            self.vram_idle = float(self.vram[1])

    def vram_one(self, engine=None):
        """VRAM % one more Nuke process of this render will take: what its processes took after their first
        chunk (not what one grew to later), or else the largest one running now."""
        if not self.vram or not self.vram[2]:
            return None
        total = float(self.vram[2])
        for engines in ([engine] if engine is not None else [], self.engines):
            bases = [w.vram_base for e in engines for w in e.workers.values() if w.vram_base]
            bases += [e._vram_seen for e in engines if getattr(e, "_vram_seen", 0)]
            if bases:
                return max(bases) / total * 100
        sizes = [self.gpu_mem.get(w.proc.pid, (0, 0))[1] for e in self.engines for w in e.workers.values()
                 if w.gpu and w.state != "gone"]
        return max(sizes) / 1024 ** 2 / total * 100 if sizes and max(sizes) else None

    def vram_holder(self):
        """Who else holds a lot of VRAM, for the log: "; NukeX (pid 39760) holds 16.5 GB", or ""."""
        ours = {w.proc.pid for e in self.engines for w in e.workers.values() if w.state != "gone"}
        others = [(v[1], pid) for pid, v in (self.gpu_mem or {}).items() if pid not in ours and v[1] > 2 * 1024 ** 3]
        if not others:
            return ""
        size, pid = max(others)
        name = "a program"
        try:
            proc = psutil.Process(pid) if psutil else None
            if proc is not None:
                cmd = " ".join(proc.cmdline()).lower()
                name = "NukeX" if "--nukex" in cmd else ("Nuke" if "nuke" in proc.name().lower() else proc.name())
        except Exception:
            pass
        return "; %s (pid %d) holds %.1f GB of it" % (name, pid, size / 1024 ** 3)

    def vram_others(self):
        """VRAM % other programs hold, from when none of our processes ran; None until seen."""
        if self.vram_idle is None or not self.vram or not self.vram[2]:
            return None
        return self.vram_idle / float(self.vram[2]) * 100

    def no_room(self, engine=None):
        """Why a process that fell cannot come back yet, or "". It has to fit in the RAM (counting what the ones still
        opening the script will take) and in the graphics card's memory. Starting one that does not fit would
        only push the PC over a limit, and a process would be closed again. The first one always starts."""
        if self.live_count() == 0:
            return ""
        now = time.time()
        if self.settings.get("ram_guard", True) or self.settings.get("vram_guard", True):
            wait = self.fall_wait()
            if wait:
                return "one closed for memory, the next can try in %d s" % math.ceil(wait)
            # one at a time: how much one really takes shows only once it has rendered
            first = next((w for e in self.engines for w in e.workers.values() if w.state in ("loading", "idle", "busy")
                          and not w.frames_here and now - w.spawned < 300), None)
            if first is not None:
                return "%s has not rendered its first frame yet" % first.name
        if psutil and self.settings.get("ram_guard", True):
            limit = float(self.settings.get("ram_limit", 90))
            pct = self.ram_percent()
            if self._more_fit(pct, limit) < 1:
                return "RAM %.0f %%, limit %.0f %%" % (pct, limit)
        gpu_guard = self.vram and self.vram[2] and self.settings.get("use_gpu", True) and self.settings.get("vram_guard", True)
        one = self.vram_one(engine)
        if one and gpu_guard:
            total = float(self.vram[2])
            # processes that have not finished their first chunk take their VRAM yet: count them at full size,
            # or one closing frees a moment of VRAM and several start into it at once
            growing = 0.0
            for e in self.engines:
                size = self.vram_one(e)
                for w in e.workers.values():
                    if size and w.gpu and w.state != "gone" and w.vram_base is None:
                        now_pct = self.gpu_mem.get(w.proc.pid, (0, 0))[1] / 1024 ** 2 / total * 100
                        growing += max(0.0, size - now_pct)
            real = float(self.vram[1]) / total * 100
            pct = real + growing
            others = self.vram_others()
            limit = float(self.settings.get("vram_limit", 95))
            # over the limit only when JKrender's processes are what fills the card (see _vram_guard),
            # never into the last few % where Windows starts moving VRAM into the RAM
            if pct + one >= 97.0 or (pct + one >= limit and (others is None or others + one < limit)):
                return "VRAM %.0f %%%s, limit %.0f %%%s" % (
                    real, " and %d still starting" % sum(1 for e in self.engines for w in e.workers.values()
                                                       if w.gpu and w.state != "gone" and w.vram_base is None)
                    if growing else "", limit, self.vram_holder())
        return ""

    def _note_running(self, text, level="ev"):
        for e in self.engines:
            if e.state in ("rendering", "paused", "finishing"):
                e.note(text, level)

    @staticmethod
    def _close_first(running):
        """Of (engine, worker) pairs, the one to close over a limit: the last one started, so a process still opening
        the script or just started goes before one with work behind it. One you started by hand only when nothing else is left."""
        return max(running, key=lambda ew: (not ew[1].pinned, ew[1].spawned))

    @staticmethod
    def _chunk_back(w):
        return ", its chunk goes back to the queue" if w.chunk is not None else ""

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
            if (self.ram_cap is None or (live <= self.ram_cap and cap < self.ram_cap)) and now >= self.started_by_hand:
                self.ram_cap = cap
                self._fell()
                self._note_running("!! RAM %.0f %% (limit %.0f %%): down to %d Nuke processes until it drops"
                                   % (pct, limit, cap), "warn")
            self._ram_ok_since = None
            if pct >= min(98.0, limit + 6) and now - self._last_kill > 5 and now - self._last_vram_kill > 5:
                # Nearly out of memory: one process closes now instead of after its chunk.
                running = [(e, w) for e in self.engines for w in e.workers.values() if w.state in ("loading", "idle", "busy")]
                if running:
                    e, w = self._close_first(running)
                    e.note("!! RAM %.0f %%: %s closed now%s" % (pct, w.name, self._chunk_back(w)), "err")
                    w.closed_why = "RAM limit"
                    e._kill(w, requeue=True, note="stopped")
                    self._fell()
                    self._last_kill = now
        elif self.ram_cap is not None and self._more_fit(pct, limit) > 0:
            # Only processes that fit under the limit; one more would just push RAM over again and one
            # process would be closed and another started, round and round.
            if self._ram_ok_since is None:
                self._ram_ok_since = now
            elif now - self._ram_ok_since >= RETURN_CHECK and not self.fall_wait():
                self.ram_cap += self._more_fit(pct, limit)
                self._ram_ok_since = now
                if self.ram_cap >= self.total:
                    self.ram_cap = None
                    self._note_running("RAM is %.0f %% again: back to %d Nuke processes" % (pct, self.total), "ok")
                else:
                    self._note_running("RAM is %.0f %%: up to %d Nuke processes" % (pct, self.ram_cap))
        else:
            self._ram_ok_since = None

    def process_rows(self, engines=None):
        """One entry per process number (W01, W02 ...), across renders: [(wid, [(engine, worker), ...]), ...].

        A number moves from render to render (W01 of a finished render goes on in another), so a row
        holds every process that had that number, oldest first; the last one is the current one."""
        rows = {}
        for e in (self.engines if engines is None else engines):
            for wid, w in e.workers.items():
                rows.setdefault(wid, []).append((e, w))
        return [(wid, sorted(rows[wid], key=lambda ew: ew[1].spawned)) for wid in sorted(rows)]

    def _vram_guard(self):
        """Fewer Nuke processes while the graphics card's memory is over the limit, like the RAM limit: every
        process uses the GPU. Close to the limit the process that grew the most gets a fresh Nuke after its chunk;
        with room on the card nothing restarts. At the limit one process fewer: the last one started closes (after its chunk,
        or now if it is still opening the script), and the next only once it is gone. Places come back when one more fits.
        When other programs hold so much VRAM that closing JKrender's processes cannot get the card under the
        limit, nothing more closes (it would only slow the render down).
        About to spill into the RAM (97 %), the one taking the most closes at once."""
        now = time.time()
        if now - self._vram_checked < 2:
            return
        self._vram_checked = now
        if not (self.settings.get("use_gpu", True) and self.settings.get("vram_guard", True)) or not self.vram:
            self.vram_cap = self._vram_lowered_at = None
            return
        if not any(e.state == "rendering" for e in self.engines):
            return
        used, total = float(self.vram[1]), float(self.vram[2] or 1)
        pct = used / total * 100
        limit = float(self.settings.get("vram_limit", 95))
        live = self.live_count()

        def vram_of(w):
            return self.gpu_mem.get(w.proc.pid, (0, 0))[1] / 1024 ** 2

        one = self.vram_one() or 10.0
        # VRAM the other programs hold (Nuke GUIs, Frame Server...), from when none of our processes ran;
        # the per-process counters miss part of ours (CUDA context, driver), so used minus theirs is not it
        others = self.vram_others()
        # the last process that closed for the limit freed (almost) nothing: the VRAM is growing elsewhere
        # (only a guess while the VRAM of other programs is unknown: ours also grow, frame after frame)
        no_help = others is None and self._vram_lowered_at is not None and live <= (self.vram_cap or live) \
            and pct > self._vram_lowered_at - one / 2
        pending = any(getattr(w, "recycle_vram", False) for e in self.engines for w in e.workers.values()
                      if w.state != "gone")
        if limit - 5 <= pct < limit and not pending:
            # Close to the limit already: the process that grew the most gets a fresh Nuke after its chunk, so it
            # is done before the card fills (a grown process takes 1 GB more in one frame; 95 % to 97 % is 0.5 GB)
            self._recycle_grown(vram_of)
        if pct >= limit:
            self._vram_ok_since = None
            theirs = others is not None and others + one >= limit
            if theirs or no_help:
                if now - self._vram_others_noted > 120:
                    self._vram_others_noted = now
                    if theirs:
                        why = "other programs hold %.1f GB of it" % (self.vram_idle / 1024)
                    else:
                        why = "the last process closed for it freed almost none"
                    self._note_running("VRAM %.0f %% (limit %.0f %%), but %s: closing more of JKrender's processes "
                                       "would not help, so %d keep going" % (pct, limit, why, live), "warn")
            elif any(getattr(w, "recycle_vram", False) for e in self.engines for w in e.workers.values()
                     if w.state != "gone"):
                pass  # one is already to be replaced by a fresh Nuke after its chunk: see what that frees first
            elif self._recycle_grown(vram_of):
                pass
            elif live > 1:
                cap = max(1, min(self.budget(), live) - 1)
                if (self.vram_cap is None or (live <= self.vram_cap and cap < self.vram_cap)) and now >= self.started_by_hand:
                    self.vram_cap = cap
                    self._fell_vram()
                    self._vram_lowered_at = pct
                    self._note_running("!! VRAM %.0f %% (limit %.0f %%): down to %d Nuke processes until it drops"
                                       % (pct, limit, cap), "warn")
            if pct >= max(97.0, min(99.0, limit + 2)) and now - self._last_vram_kill > 8 and now - self._last_kill > 5:
                running = [(e, w) for e in self.engines for w in e.workers.values()
                           if w.gpu and w.state in ("loading", "idle", "busy")]
                # the only one left keeps going: closing it would only throw its chunk away, again and again,
                # while another program holds the card (NukeX with 16 GB); the card then lends it shared memory
                if len(running) > 1:
                    # the one taking the most: a new one frees little while an old one keeps 11-15 GB. By hand last.
                    e, w = max(running, key=lambda ew: (not ew[1].pinned, vram_of(ew[1])))
                    e.note("!! VRAM %.0f %%: %s (%.1f GB) closed now%s" % (pct, w.name, vram_of(w) / 1024, self._chunk_back(w)), "err")
                    w.closed_why = "VRAM limit"
                    e._kill(w, requeue=True, note="stopped")
                    self._fell_vram()
                    self._last_vram_kill = now
        elif self.vram_cap is not None:
            if pct + one < limit - 2:
                if self._vram_ok_since is None:
                    self._vram_ok_since = now
                elif now - self._vram_ok_since >= RETURN_CHECK and not self.fall_wait():
                    self.vram_cap += 1
                    self._vram_ok_since = now
                    self._vram_lowered_at = None
                    if self.vram_cap >= self.total:
                        self.vram_cap = None
                        self._note_running("VRAM is %.0f %% again: back to %d Nuke processes" % (pct, self.total), "ok")
                    else:
                        self._note_running("VRAM is %.0f %%: up to %d Nuke processes" % (pct, self.vram_cap))
            else:
                self._vram_ok_since = None

    def _recycle_grown(self, vram_of):
        """The GPU process that grew the most past what it took after its first chunk is replaced by a fresh Nuke
        after its chunk. Nuke keeps VRAM it no longer needs (one script: 5 GB after the first chunk, 14 GB later), so
        at the limit the card is full of that, not of too many processes. True when one was picked."""
        grown = [(vram_of(w) - w.vram_base, e, w) for e in self.engines for w in e.workers.values()
                 if w.gpu and w.state == "busy" and w.vram_base and not (w.retire or w.handover or w.leave)
                 and vram_of(w) > w.vram_base * 1.3]
        if not grown:
            return False
        _, e, w = max(grown, key=lambda g: g[0])
        w.recycle_vram = True
        e.note("VRAM close to the limit: %s has grown to %.1f GB (%.1f GB after its first chunk); after its chunk a fresh "
               "Nuke takes over" % (w.name, vram_of(w) / 1024, w.vram_base / 1024), "warn")
        return True

    def take_wid(self, engine=None):
        """The lowest process number no running process has. When a render is done, the process a render
        still running starts next takes over the free name (W01 goes on in render C), so the names stay
        W01-W08. A render never gets a number twice (its closed ones are started again under their own)."""
        running = {wid for e in self.engines for wid, w in e.workers.items() if w.state != "gone"}
        own = set(engine.workers) if engine is not None else set()
        wid = 1
        while wid in running or wid in own:
            wid += 1
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

    def paused_between(self, a, b):
        spans = list(self.pauses) + ([(self.paused_at, time.time())] if self.paused_at else [])
        return sum(max(0.0, min(b, p1) - max(a, p0)) for p0, p1 in spans)

    def tick(self):
        changed = False
        for e in list(self.engines):
            changed = e.tick() or changed
        self._vram_idle_update()
        self._ram_guard()
        self._vram_guard()
        changed = self._start_next() or changed
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

    def _start_next(self):
        """Settings > Start next render when one ends: for each render that finished just now, the next one on the cards
        that has not been started. One you stopped stays stopped and does not start another."""
        running = {e for e in self.engines if e.state in ("rendering", "paused", "finishing", "waiting")}
        ended = [e for e in self._running if e not in running and e.state in ("done", "failed")]
        self._running = running
        if not ended or not self.settings.get("auto_next", False):
            return False
        ready = sorted((e for e in self.engines if e.state == "ready" and not e.nothing_to_render()), key=lambda e: e.number)
        for e in ready[:len(ended)]:
            e.note("Started by itself: the render before it finished", "ok")
            self.request_start(e)
            self._running.add(e)
        return bool(ready)

    def _admit(self):
        busy = [e for e in self.engines if e.state in ("rendering", "paused") and not e.is_tail()]
        while self.waiting and len(busy) < self.max_renders:
            e = self.waiting.pop(0)
            e.state = e.resume_state
            e.start(everything=e.everything)
            if e.state == "rendering":
                busy.append(e)

    def _allocate(self):
        # a render with a process you started by hand first, so the place made for it is not dealt to another
        active = sorted((e for e in self.engines if e.state in ("rendering", "paused")),
                        key=lambda e: (not (e.restart_ids or e.hand_wids), e.started or 0, e.number))
        needs = {e: e.need() for e in active}
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
        # Work on: places moved by hand, as long as both renders run and the one moved to has work for it
        self.moves = [(a, b) for a, b in getattr(self, "moves", []) if a in share and b in share]
        for a, b in self.moves:
            if share[a] > 0 and share[b] < needs[b]:
                share[a] -= 1
                share[b] += 1
        for e in active:
            if e.workers_target != share[e]:
                e.set_workers(share[e])

    def move_worker(self, src, w, dst):
        """Work on: the process w of render src hands its place to render dst after its chunk; the
        process dst starts there gets w's name."""
        if src is dst:
            return
        if not hasattr(self, "moves"):
            self.moves = []
        if w.state != "gone" and src.state in ("rendering", "paused"):
            w.leave = True
            w.leave_to = dst
            self.moves.append((src, dst))
        dst.preferred_wids.append(w.id)
        src.note("%s goes on to render %d (%s) after its chunk" % (w.name, dst.number, dst.name))
        dst.note("%s comes over from render %d (%s)" % (w.name, src.number, src.name))
        self._allocate()

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
    "vram": "#5aa9e6", "refresh": "#4e7a37", "queued": "#4a4a4a", "rendering": "#f7931e", "done": "#7fae58", "failed": "#d4503f",
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
QRadioButton { color: %(text)s; }
QCheckBox:disabled, QLabel:disabled, QRadioButton:disabled { color: #666666; }
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


def pixels_broken(fr):
    return bool(fr.pixels and (fr.pixels.get("nan") or fr.pixels.get("inf") or fr.pixels.get("blank") or fr.pixels.get("differs")))


def pixels_note(fr):
    """Words for what the pixel check found in a frame, or ""."""
    notes = []
    if pixels_broken(fr):
        notes += ["%d NaN" % fr.pixels["nan"]] if fr.pixels.get("nan") else []
        notes += ["%d inf" % fr.pixels["inf"]] if fr.pixels.get("inf") else []
        notes += [fr.pixels["blank"]] if fr.pixels.get("blank") else []
        notes += fr.pixels.get("differs") or []
    if fr.same_as is not None:
        notes.append("same as %d" % fr.same_as)
    return ", ".join(notes)


def frame_color(fr):
    if pixels_broken(fr) or fr.same_as is not None:
        return QtGui.QColor(C["failed"])
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
        if pixels_note(fr):
            lines.append(pixels_note(fr))
        QtWidgets.QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)

    def mousePressEvent(self, event):
        f = self._frame_at(event.position())
        if f is not None:
            self.frameClicked.emit(f)


class Gantt(QtWidgets.QWidget):
    """One row per Nuke process (W01, W02 ...). A process that moves to another render keeps its row;
    with several renders each chunk says which render it was for, and the picked render is bright.

    Script load in grey, each chunk as a block. Right-click a row for that process's menu."""

    workerMenu = QtCore.Signal(object, object, object)  # engine, worker, global position

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.selected = None
        self.show_all = True  # False: only the picked render's processes
        self.dim_others = True  # False: every render's chunks equally bright
        self.hide_old_done = True  # a render started again: the done chunks of its earlier run are not drawn
        self.setMouseTracking(True)
        self.setMinimumHeight(120)
        self._blocks = []

    def paintEvent(self, event):
        m = self.manager
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(C["panel"]))
        engines = m.engines if self.show_all or self.selected is None else [self.selected]
        rows = m.process_rows(engines)  # W01, W02 ...: a process keeps its row when it moves to another render
        self._blocks = []
        self._rows = []
        if not rows:
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

        def shown(e, w):  # the segments drawn: without the done chunks of an earlier run, when hidden
            cut = e.rerun_at if self.hide_old_done else None
            return [s for s in w.segments
                    if not (cut and s[3] and s[3] <= cut and (s[0] == "load" or s[4] == "done"))]  # its loading too: no empty start

        pairs = [ew for _, ews in rows for ew in ews]
        running = any(e.state in ("rendering", "paused", "finishing") for e, _ in pairs)
        starts = [s[2] for e, w in pairs for s in shown(e, w)]
        t0 = clock(min(starts) if starts else min(w.spawned for _, w in pairs))
        t1 = max([clock(now) if running else 0] + [clock(end_of(e, s[3])) for e, w in pairs for s in shown(e, w)])
        span = max(1.0, t1 - t0)
        many = len(m.engines) > 1
        left, top, right = 46, 6, 8
        avail = self.height() - top - 22
        row_h = max(5.0, min(22.0, avail / float(len(rows))))
        width = self.width() - left - right
        font = p.font()
        font.setPointSizeF(7.5)
        p.setFont(font)
        y = top
        for wid, ews in rows:
            e_now, w_now = ews[-1]
            p.setOpacity(1.0)
            p.setPen(QtGui.QColor(C["text"] if w_now.state != "gone" else C["dim"]))
            if row_h >= 9:
                p.drawText(QtCore.QRectF(0, y, left - 6, row_h), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                           w_now.name)
            for e, w in ews:
                p.setOpacity(0.35 if many and self.dim_others and self.selected is not None and e is not self.selected
                             else 1.0)
                for kind, label, a, b, state in shown(e, w):
                    if a > now:
                        continue
                    a, b = clock(a), clock(end_of(e, b))
                    x = left + (a - t0) / span * width
                    bw = max(1.5, (b - a) / span * width)
                    rect = QtCore.QRectF(x, y + 1.5, bw - 1, row_h - 3)
                    if kind == "load":
                        # a fresh Nuke for the VRAM is part of rendering: darker green; other script loads grey
                        color = QtGui.QColor(C["refresh"] if "VRAM" in label else C["loading"])
                    elif state in ("rendering", "done", "failed"):
                        color = QtGui.QColor(C[state])
                    else:  # stopped, closed, retired: the chunk went back to the queue
                        color = QtGui.QColor(C["stopped"])
                    if state == "crashed":
                        color = QtGui.QColor(C["failed"])
                    p.setPen(QtCore.Qt.NoPen)
                    p.setBrush(color)
                    p.drawRoundedRect(rect, 2, 2)
                    if kind == "chunk" and row_h >= 12:
                        text = "%d · %s" % (e.number, label) if many else label
                        if p.fontMetrics().horizontalAdvance(text) + 6 > bw:
                            text = label if p.fontMetrics().horizontalAdvance(label) + 6 <= bw else ""
                        if text:
                            p.setPen(QtGui.QColor("#0e0f11"))
                            p.drawText(rect, QtCore.Qt.AlignCenter, text)
                    self._blocks.append((rect, e, w, kind, label, a, b, state))
            p.setOpacity(1.0)
            self._rows.append((y, y + row_h, e_now, w_now))
            if e_now.state == "paused" and w_now.state != "gone":
                # this process is frozen with its render
                band = QtCore.QRectF(left, y, width, row_h)
                p.fillRect(band, QtGui.QColor(0, 0, 0, 120))
                if row_h >= 12:
                    p.setPen(QtGui.QColor(C["accent"]))
                    p.drawText(band, QtCore.Qt.AlignCenter, "PAUSED")
            y += row_h
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

    def contextMenuEvent(self, event):
        y = event.pos().y()
        for y0, y1, e, w in getattr(self, "_rows", []):  # the row's current process
            if y0 <= y < y1:
                self.workerMenu.emit(e, w, event.globalPos())
                return

    def mouseMoveEvent(self, event):
        pos = event.position()
        for rect, e, w, kind, label, a, b, state in self._blocks:
            if rect.contains(pos):
                who = "%s (%s)" % (w.name, e.name)
                if kind == "load":
                    why = label[len("again: "):] if label.startswith("again: ") else ""
                    text = ("%s opening the script again%s: %.1f s" % (who, " (%s)" % why if why else "", b - a)
                            if label.startswith("again") else "%s opening the script: %.1f s" % (who, b - a))
                else:
                    c = next((c for c in e.chunks if c.name == label), None)
                    text = "%s on %s — %s\nframes %s\n%s" % (label, who, state, c.spec if c else "?", fmt_secs(b - a))
                QtWidgets.QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QtWidgets.QToolTip.hideText()


class ScriptAnalysis(QtWidgets.QDialog):
    """Settings > Analyze script: a Nuke opens the script in this render's environment, reports what feeds the Write and
    closes. Then one Nuke with all threads renders a few frames to a test folder, about a minute at most: how many threads it
    really uses, what RAM and VRAM it takes. It recommends processes, threads and Nuke cache from that; Test this script measures them."""

    PROBE_SECONDS = 45  # no new frame after this; a frame already going may finish, up to PROBE_MOST
    PROBE_MOST = 120
    PROBE_LIGHT = 6  # a light script keeps rendering for this long, so its numbers are not from one instant
    PROBE_CACHE = 16  # GB: the same for every script, so its RAM says what it wants, not what the settings allow
    ENOUGH_CACHE = 13  # GB a process needs at most, for the recommendation

    def __init__(self, window, engine):
        super().__init__(window)
        self.main, self.src = window, engine
        self.setWindowTitle("Analyze script: %s" % engine.name)
        self.resize(1060, 640)
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setSpacing(10)
        self.waiting = QtWidgets.QWidget()
        wait = QtWidgets.QVBoxLayout(self.waiting)
        wait.addStretch(1)
        text = QtWidgets.QLabel("Opening the script in Nuke…")
        self.wait_text = text
        text.setObjectName("dim")
        text.setAlignment(QtCore.Qt.AlignCenter)
        wait.addWidget(text)
        busy = QtWidgets.QProgressBar()
        busy.setRange(0, 0)
        busy.setTextVisible(False)
        busy.setMaximumHeight(6)
        wait.addWidget(busy)
        wait.addStretch(1)
        self.lay.addWidget(self.waiting, 1)
        self.b_use = QtWidgets.QPushButton("Use these settings")
        self.b_use.setObjectName("primary")
        self.b_use.setEnabled(False)
        self.b_use.clicked.connect(self.use)
        self._analysis = self._analysis_proc = None
        self.stage = ""
        # with a render going, or for a movie, the numbers would say nothing: the nodes only
        self.probing = not engine.movie and not window.manager.running()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._analysis_tick)
        threading.Thread(target=self._analysis_run, daemon=True).start()
        self.timer.start(200)

    def _analysis_run(self):
        e = self.src
        env = nuke_render_env(e.job.get("env"))
        env["JKRENDER_ARGS"] = json.dumps(e._worker_args())
        env["JKRENDER_ANALYZE"] = "1"
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                      errors="replace", env=env)
        kwargs.update(hidden())
        script_dir = Path(e.job.get("script") or "").parent
        if script_dir.is_dir():
            kwargs["cwd"] = str(script_dir)
        try:
            self._analysis_proc = subprocess.Popen([e.job.get("nuke_exe") or "Nuke", "-i", "-m", "2", "-c", "2G", "-t", str(WORKER_SCRIPT)],
                                                   **kwargs)
            out = self._analysis_proc.communicate(timeout=300)[0]
        except Exception as exc:
            self._analysis = "Could not analyze the script: %s" % exc
            return
        found = re.search(r"JKR INFO (\{.*\})", out)
        if not found:
            self._analysis = "Nuke did not report on the script:\n" + out[-600:]
            return
        info = dict(json.loads(found.group(1)), format=e.job.get("format", ""))
        if self.probing:
            self.stage = "Rendering a few frames to a test folder: about a minute at most, less on a light script…"
            try:
                info["probe"] = self._probe()
            except Exception as exc:
                info["probe"] = {"failed": str(exc)}
        self._analysis = info

    def _probe(self):
        """A few frames in one Nuke with all threads, stopped when time is up: {"threads", "rss", "vram", "growth", "secs"}."""
        e = self.src
        cpu = os.cpu_count() or 8
        out = TEST_DIR / ("analyze_%s" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        out.mkdir(parents=True, exist_ok=True)
        env = nuke_render_env(e.job.get("env"))
        env["JKRENDER_ARGS"] = json.dumps(e._worker_args())
        env["JKRENDER_TEST_OUT"] = str(out).replace("\\", "/")
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
        kwargs.update(hidden())
        script_dir = Path(e.job.get("script") or "").parent
        if script_dir.is_dir():
            kwargs["cwd"] = str(script_dir)
        gpu = ["--gpu", "0"] if self.main.settings.get("use_gpu", True) else []
        self._analysis_proc = proc = subprocess.Popen([e.job.get("nuke_exe") or "Nuke", "-i", "-m", str(cpu), "-c", "%dG" % self.PROBE_CACHE] + gpu + ["-t", str(WORKER_SCRIPT)], **kwargs)
        lines = pyqueue.Queue()

        def read():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read, daemon=True).start()
        vram = lambda: (self.main._gpu_procs or {}).get(proc.pid, (0, 0))[1] / 1024.0 ** 3
        frames = e.frame_list()
        frames = frames[max(0, (len(frames) - 12) // 2):][:12]
        secs, vrams, cpus, rss, started, first = [], [], [], 0.0, time.time(), None
        ps = psutil.Process(proc.pid) if psutil else None
        failed = ""
        try:
            for frame in frames:
                busy = time.time() - (first or time.time())
                if secs and (time.time() - started >= self.PROBE_SECONDS or (len(secs) >= 3 and busy >= self.PROBE_LIGHT)):
                    break
                proc.stdin.write("RENDER P %d\n" % frame)
                proc.stdin.flush()
                done = False
                while not done:
                    if time.time() - started > self.PROBE_MOST:
                        failed = failed or ("no frame in %d s" % self.PROBE_MOST if not secs else "")
                        done = True
                        break
                    try:
                        line = lines.get(timeout=0.5)
                    except pyqueue.Empty:
                        line = ""
                    if line is None:
                        failed = "Nuke closed"
                        done = True
                        break
                    if "JKR START" in line and first is None:
                        first = time.time()
                        if ps:
                            ps.cpu_percent(None)
                    if "JKR FRAME" in line:
                        secs.append(float(line.split("JKR FRAME", 1)[1].split()[1]))
                    if "JKR FAIL" in line or "JKR ERROR" in line:
                        failed = line.split("JKR", 1)[1].strip()[:160]
                        done = True
                    if "JKR CHUNK" in line:
                        done = True
                    if ps and first is not None and not line:
                        cpus.append(ps.cpu_percent(None) / 100.0)
                        rss = max(rss, ps.memory_info().rss / 1024.0 ** 3)
                if failed or time.time() - started > self.PROBE_MOST:
                    break
                if ps:
                    cpus.append(ps.cpu_percent(None) / 100.0)
                    rss = max(rss, ps.memory_info().rss / 1024.0 ** 3)
                if sum(secs) >= 2.5:  # the counters read every 2 s
                    vrams.append(vram())
            time.sleep(2.2)
            vrams.append(vram())
        finally:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass
            shutil.rmtree(out, ignore_errors=True)
        # all threads busy at their best, not while it reads files between frames
        cpus = sorted(cpus)[len(cpus) // 2:] or [0.0]
        steps = max(1, len(vrams) - 2)  # the last reading is the same frame's, later
        return {"failed": failed if not secs else "", "frames": len(secs), "secs": sum(secs) / max(1, len(secs)), "threads": sum(cpus) / len(cpus),
                "rss": rss, "vram": max(vrams or [0.0]), "growth": max(0.0, (vrams[-1] - vrams[0]) / steps) if len(vrams) >= 3 else 0.0}

    def _analysis_tick(self):
        result = self._analysis
        if self.stage and self.wait_text.text() != self.stage:
            self.wait_text.setText(self.stage)
        if result is None:
            return
        self.timer.stop()
        self.waiting.hide()
        if isinstance(result, str):
            log = QtWidgets.QPlainTextEdit(result)
            log.setReadOnly(True)
            self.lay.addWidget(log, 1)
            return
        # for this script on this PC, not for this moment: what runs now (renders, a Nuke's viewer cache) says nothing about it.
        # The RAM limit, less what Windows and other programs usually hold.
        total = psutil.virtual_memory().total / 1024 ** 3 if psutil else float(self.main.manager.ram * self.main.manager.total)
        free = total * float(self.main.settings.get("ram_limit", 90)) / 100 - min(16.0, total * 0.2)
        gpu = self.main._gpu or [0, 0, 0]
        card = (float(gpu[2]) * float(self.main.settings.get("vram_limit", 95)) / 100 - float(gpu[1])) / 1024.0 if self.main.settings.get("use_gpu", True) and gpu[2] else None
        n, t, c, why = self.advise(result, os.cpu_count() or 8, free, card)
        self.best = (n, t, c)
        m = self.main.manager
        self.b_use.setEnabled((n, t, c) != (m.total, m.threads, m.ram) and self.main.s_workers.isEnabled())
        self._show_result(result, why)

    def use(self):
        n, t, c = self.best
        self.main.s_workers.setValue(n)
        self.main.s_threads.setValue(t)
        self.main.s_ram.setValue(c)
        self.b_use.setEnabled(False)

    def closeEvent(self, event):
        self.timer.stop()
        if self._analysis_proc is not None and self._analysis_proc.poll() is None:
            self._analysis_proc.kill()
        super().closeEvent(event)

    @staticmethod
    def advise(info, cpu, free_gb, card_gb=None):
        """Processes, threads, Nuke cache and why. As many processes of 4 threads as fit in the graphics card and the RAM, from
        what one Nuke did in the short render. Measured, several small processes beat one big one as long as they fit, also
        when one process uses all its threads: what stops them is memory. Without the render, from the number of nodes."""
        probe = info.get("probe") or {}
        floor = 4 if info["above"] < 60 else 8  # GB of cache: a heavy script slows down a lot under about that
        n = max(1, cpu // 4)
        n = min(n, 8)
        why = []
        if probe.get("frames") and card_gb and probe["vram"] > 0.3:
            # a process that keeps growing is a lot bigger after a few more frames
            grown = probe["vram"] + probe["growth"] * 10
            fit = max(1, int(card_gb // grown))
            if fit < n:
                n = fit
                why.append("Its VRAM %s, %.1f GB%s: the card holds %d such process%s." % (
                    "keeps growing" if probe["growth"] >= 0.3 else "is high", probe["vram"],
                    " and %.1f GB more per frame" % probe["growth"] if probe["growth"] >= 0.3 else "", n, "" if n == 1 else "es"))
        # less than a script wants slows it down far more than one process fewer: measured 5 x 6 with 10 GB 12 frames / min,
        # 4 x 8 with 14 GB 16. So what the short render took, less a little, and only numbers of processes measured.
        # A script that fills the short render's cache only shows it takes all it gets, so past ENOUGH_CACHE more says nothing:
        # a heavy comp, 2 x 16 with 30 GB 17.7 frames / min, 4 x 8 with 13 GB 23.6.
        size = max(floor, min(ScriptAnalysis.ENOUGH_CACHE, probe.get("rss", 0.0) - 2))
        fit = n
        while fit > 1 and free_gb / fit - BESIDE_CACHE < size:
            fit -= 1
        fit = max(x for x in (1, 2, 4, 6, 8) if x <= fit)
        if fit < n:
            why.append("The RAM holds %d with at least %.0f GB of cache each, and a script with too little cache slows down a lot." % (fit, size))
            n = fit
        if not why:
            why.append("It fits in the RAM and the graphics card, so as many small processes as the CPU takes." if probe.get("frames") else
                       ("Few nodes: several smaller processes usually make the best use of the CPU." if info["above"] < 60 else
                        "Many nodes: a script like this needs a big cache, and with too little it can slow down a lot."))
        return n, max(1, cpu // n), max(1, int(free_gb / n - BESIDE_CACHE)), " ".join(why)

    def _card(self, title, hint=""):
        frame = QtWidgets.QFrame()
        frame.setObjectName("card")
        frame.setStyleSheet("#card { background: %s; border: 1px solid %s; border-radius: 6px; }" % (C["panel"], C["line"]))
        box = QtWidgets.QVBoxLayout(frame)
        box.setContentsMargins(14, 10, 14, 12)
        box.setSpacing(6)
        head = QtWidgets.QLabel(title)
        head.setStyleSheet("color: %s; font-weight: 600; font-size: 10pt;" % C["accent"])
        box.addWidget(head)
        if hint:
            box.addWidget(self._text(hint, dim=True))
        return frame, box

    @staticmethod
    def _text(text, dim=False):
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        if dim:
            label.setObjectName("dim")
        return label

    def _node_list(self, title, hint, counts):
        """A card with one line per kind of node, the most first."""
        frame, box = self._card(title, hint)
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setVerticalSpacing(3)
        for i, (name, count) in enumerate(sorted(counts, key=lambda kv: -kv[1])):
            # the class without its version: Merge2 is the Merge you know
            grid.addWidget(QtWidgets.QLabel(re.sub(r"\d+$", "", name)), i, 0)
            number = QtWidgets.QLabel(str(count))
            number.setAlignment(QtCore.Qt.AlignRight)
            grid.addWidget(number, i, 1)
        if not counts:
            grid.addWidget(self._text("None", dim=True), 0, 0)
        box.addLayout(grid)
        box.addStretch(1)
        return frame

    def _show_result(self, info, why):
        """First what to set and why, then what the script is made of."""
        n, t, c = self.best
        m = self.main.manager
        writes = ", ".join(w.get("name", "") for w in self.src.job.get("writes", []))
        title = QtWidgets.QLabel("%s  ·  %s" % (self.src.name, writes))
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        title.setToolTip("The render picked on the cards, as it was saved when it was sent to JKrender")
        self.lay.addWidget(title)
        frame, box = self._card("Recommended to start from")
        row = QtWidgets.QHBoxLayout()
        for name, value, now, meaning in (
                ("Processes", str(n), str(m.total), "Nuke processes rendering at once."),
                ("Threads", str(t), str(m.threads), "CPU threads each process may use."),
                ("Nuke cache", "%d GB" % c, "%d GB" % m.ram, "RAM cache of each process. A process takes about %d GB more." % BESIDE_CACHE)):
            tile = Tile(name)
            tile.set(value, "now %s" % now)
            tile.value.setStyleSheet("color: %s; font-size: 20pt; font-weight: 600;" % C["accent"])
            tile.layout().addWidget(self._text(meaning, dim=True))
            row.addWidget(tile, 1)
        row.addWidget(self.b_use, 0, QtCore.Qt.AlignVCenter)
        box.addLayout(row)
        box.addWidget(self._text(why))
        probe = info.get("probe") or {}
        box.addWidget(self._text("From %d frames in one Nuke, a quick estimate. Test this script measures what is really fastest for this script." % probe["frames"] if probe.get("frames")
                                 else "From the number of nodes only%s. Test this script measures what is really fastest for this script."
                                 % (", because a render is going" if not self.probing and not self.src.movie else (": the short render failed, %s" % probe["failed"] if probe.get("failed") else "")), dim=True))
        self.lay.addWidget(frame)
        if info["gpu"]:
            warn = self._text("This script runs nodes on the GPU. If the VRAM fills up or keeps growing while it renders, "
                              "fewer processes with more threads may be faster: try that in Test this script.")
            warn.setStyleSheet("color: %s;" % C["small"])
            self.lay.addWidget(warn)
        tiles = QtWidgets.QHBoxLayout()
        for name, value, sub, meaning in (
                ("Nodes doing the work", str(info["above"]), "of %d in the script" % info["all"],
                 "Only nodes that reach this Write. Disabled branches do not count."),
                ("Format", info.get("format") or "unknown", "", "More pixels take more RAM and time."),
                ("Reads", str(info["reads"]), "largest %.1f MP" % (info["largest_read"] / 1e6), "Files read to make a frame."),
                ("Blurs of 100 px or more", str(info["big_blurs"]), "", "Big Blurs can take a lot of CPU time."))[:None if not probe.get("frames") else 2] + (() if not probe.get("frames") else (
                ("Threads it uses", "%.0f of %d" % (probe["threads"], os.cpu_count() or 8), "%.1f s a frame" % probe["secs"], "One process with all threads."),
                ("Memory of one process", "%.1f GB RAM" % probe["rss"], "%.1f GB VRAM%s" % (probe["vram"], ", +%.1f per frame" % probe["growth"] if probe["growth"] >= 0.3 else ""), "After these frames."))):
            tile = Tile(name)
            tile.set(value, sub)
            tile.layout().addWidget(self._text(meaning, dim=True))
            tiles.addWidget(tile, 1)
        self.lay.addLayout(tiles)
        lists = QtWidgets.QHBoxLayout()
        lists.addWidget(self._node_list("On the GPU", "Use the graphics card and its memory (VRAM). All processes share one card.",
                                        list(info["gpu"].items())), 1)
        lists.addWidget(self._node_list("Look at other frames", "Need neighbouring frames too, so usually more RAM and reading.",
                                        list(info["temporal"].items())), 1)
        lists.addWidget(self._node_list("Most used", "The most common nodes feeding this Write. The count does not say how heavy they are.",
                                        [tuple(kv) for kv in info["top"]]), 1)
        self.lay.addLayout(lists, 1)


class NodeMeasure(QtWidgets.QDialog):
    """Settings > Measure nodes: a few frames from the middle of the render in one Nuke, to a test folder. Nuke's timers give
    each node's time; the counters the GPU, VRAM and RAM of that Nuke, for the whole test: Nuke counts them for no node.
    Nothing is saved, and the frames are deleted."""

    FRAMES = 6
    LIMIT = 300  # seconds for one run

    _card = ScriptAnalysis._card
    _text = staticmethod(ScriptAnalysis._text)

    def __init__(self, window, engine):
        super().__init__(window)
        self.main, self.src = window, engine
        self.setWindowTitle("Measure nodes: %s" % engine.name)
        self.resize(1060, 680)
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setSpacing(10)
        writes = ", ".join(w.get("name", "") for w in engine.job.get("writes", []))
        title = QtWidgets.QLabel("%s  ·  %s" % (engine.name, writes))
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        self.lay.addWidget(title)
        self.status = self._text("", dim=True)
        self.lay.addWidget(self.status)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(6)
        self.lay.addWidget(self.bar)
        self.lay.addStretch(1)
        self.message = "Rendering %d frames in one Nuke, with the timers on…" % self.FRAMES
        self.outcome = None
        self.stopping = False
        self.proc = None
        frames = engine.frame_list()
        middle = max(0, (len(frames) - self.FRAMES) // 2)
        self.frames = frames[middle:middle + self.FRAMES]
        self.out = TEST_DIR / ("measure_%s" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(200)
        threading.Thread(target=self._run, daemon=True).start()

    def _nuke(self):
        e = self.src
        env = nuke_render_env(e.job.get("env"))
        env["JKRENDER_ARGS"] = json.dumps(e._worker_args())
        env["JKRENDER_TEST_OUT"] = str(self.out).replace("\\", "/")
        env["JKRENDER_TIMERS"] = "1"
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                      bufsize=1, env=env)
        kwargs.update(hidden())
        script_dir = Path(e.job.get("script") or "").parent
        if script_dir.is_dir():
            kwargs["cwd"] = str(script_dir)
        gpu = ["--gpu", "0"] if self.main.settings.get("use_gpu", True) else []
        return subprocess.Popen([e.job.get("nuke_exe") or "Nuke", "-i", "-m", str(os.cpu_count() or 8), "-c", "%dG" % self.main.manager.ram]
                                + gpu + ["-t", str(WORKER_SCRIPT)], **kwargs)

    def _watch(self, proc, got):
        """While this Nuke lives: its RAM and VRAM at their highest, and its GPU use while it renders."""
        ps = psutil.Process(proc.pid) if psutil else None
        while proc.poll() is None:
            try:
                got["ram"] = max(got["ram"], ps.memory_info().rss / 1024.0 ** 3) if ps else 0.0
            except Exception:
                pass
            use, vram = (self.main._gpu_procs or {}).get(proc.pid, (0, 0))
            got["vram"] = max(got["vram"], vram / 1024.0 ** 3)
            if got["rendering"]:
                got["gpu"].append(use)
            time.sleep(0.5)

    def _once(self):
        """One Nuke and the frames: {"secs": [...], "nodes": ..., "gpu": [...], "vram": GB, "ram": GB}."""
        self.proc = proc = self._nuke()
        if self.stopping:  # closed in the moment it started
            proc.kill()
        killer = threading.Timer(self.LIMIT, proc.kill)
        killer.start()
        got = {"secs": [], "failed": "", "nodes": [], "gpu": [], "vram": 0.0, "ram": 0.0, "rendering": False}
        threading.Thread(target=self._watch, args=(proc, got), daemon=True).start()
        def until(*kinds):
            for line in proc.stdout:
                m = re.search(r"JKR (START|FRAME|CHUNK|FAIL|ERROR|INFO) ?(.*)$", line.rstrip())
                if m and m.group(1) == "START":
                    got["rendering"] = True
                if m and m.group(1) in ("FAIL", "ERROR"):
                    got["failed"] = m.group(2)[:200]
                    return None
                if m and m.group(1) == "FRAME":
                    got["secs"].append(float(m.group(2).split()[1]))
                if m and m.group(1) in kinds:
                    return m.group(2)
            return None

        try:
            proc.stdin.write("RENDER M %s\n" % frames_to_spec(self.frames))
            proc.stdin.flush()
            if until("CHUNK") is not None:
                got["rendering"] = False
                proc.stdin.write("PROFILE\n")
                proc.stdin.flush()
                info = until("INFO")
                got["nodes"] = json.loads(info)["nodes"] if info else []
                time.sleep(2.2)  # the counters read every 2 s: the VRAM of the last frame
            proc.stdin.write("QUIT\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            got["failed"] = got["failed"] or str(exc)
        finally:
            killer.cancel()
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass
            shutil.rmtree(self.out, ignore_errors=True)
            self.out.mkdir(parents=True, exist_ok=True)
        if not got["failed"] and len(got["secs"]) < len(self.frames):
            got["failed"] = "stopped after %d of %d frames" % (len(got["secs"]), len(self.frames))
        return got

    def _run(self):
        try:
            self.out.mkdir(parents=True, exist_ok=True)
            run = self._once()
            self.outcome = "The script did not render: %s" % run["failed"] if run["failed"] else run
        except Exception as exc:
            self.outcome = "Could not measure: %s" % exc
        finally:
            shutil.rmtree(self.out, ignore_errors=True)

    def _tick(self):
        self.status.setText(self.message)
        if self.outcome is None:
            return
        self.timer.stop()
        self.bar.hide()
        if isinstance(self.outcome, str):
            self.status.setText(self.outcome)
            return
        self._show(self.outcome)

    def _table(self, heads, rows):
        table = QtWidgets.QTableWidget(len(rows), len(heads))
        table.setHorizontalHeaderLabels(heads)
        table.verticalHeader().hide()
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QtWidgets.QTableWidgetItem(str(value)))
        return table

    def _show(self, run):
        verdict = "Frame time %.2f s, %d frames in one Nuke." % (sum(run["secs"]) / len(run["secs"]), len(self.frames))
        self.status.setText(verdict)
        self.status.setStyleSheet("font-weight: 600;")
        gpu_on = self.main.settings.get("use_gpu", True)
        gpu = "off in Settings" if not gpu_on else "%.0f %% on average" % (sum(run["gpu"]) / len(run["gpu"])) if run["gpu"] else "not read"
        use = "GPU %s  ·  VRAM peak %s  ·  RAM peak %s" % (gpu, "%.1f GB" % run["vram"] if gpu_on and run["vram"] else "—", "%.1f GB" % run["ram"] if run["ram"] else "—")
        whole, box = self._card("The whole test", "One Nuke with all threads and the Nuke cache from Settings. Nuke counts these for no node, so they are for the test as a whole.")
        box.addWidget(self._text(use))
        self.lay.insertWidget(self.lay.count() - 1, whole)
        total = sum(n[2] for n in run["nodes"]) or 1
        slow = [[n[0], re.sub(r"\d+$", "", n[1]), "on" if gpu_on and n[4] else "", "%.1f s" % (n[2] / 1e6 / len(self.frames)), "%.0f %%" % (n[2] * 100.0 / total), ""] for n in run["nodes"][:20]]
        frame, box = self._card("Slowest nodes", "Nuke's timers, %d frames in one Nuke with all threads: CPU time on each node per frame, over all threads, and its share. "
                                "Rough on Windows. Work on the GPU is not in this time: a node with GPU on can take longer than it shows." % len(self.frames))
        box.addWidget(self._table(["Node", "Class", "GPU", "CPU per frame", "Share", ""], slow), 1)
        self.lay.insertWidget(self.lay.count() - 1, frame, 1)
        try:
            TEST_RESULTS.mkdir(parents=True, exist_ok=True)
            path = TEST_RESULTS / ("%s_nodes_%s.txt" % (self.src.name, datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
            path.write_text("\n".join([verdict, use, ""] + [" | ".join(r) for r in slow]) + "\n", encoding="utf-8")
        except OSError:
            pass

    def closeEvent(self, event):
        self.stopping = True
        self.timer.stop()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        super().closeEvent(event)


class ScriptTest(QtWidgets.QDialog):
    """Settings > Test this script: frames from the middle of the render, rendered with each Processes x Threads x
    Nuke cache setup: a few recommended ones (the RAM split between the processes) and your own, added here. Frames
    per minute from when the script is open. It recommends the fastest; Use these settings puts it into Settings.
    Nothing changes unless you press it. The frames go to a test folder, deleted after each setup; the render's own
    files stay as they are."""

    LIMIT = 360  # seconds a setup may take; a slow one is measured on the frames it got done
    # Once a setup has rendered the frames, one that takes this much longer cannot win and stops. After two like that
    # in a row the setups with still more processes are left out: each has less cache and fewer threads
    # (a heavy comp: 4 GB of cache 69 s a frame, 2 GB and 1 GB not one frame in 6 minutes). Your own settings always run.
    SLOWER = 1.5
    GIVE_UP = 2
    # The recommended ones, on 32 threads 1x32, 2x16, 4x8, 6x6, 8x4. (n, None): n processes share the CPU's
    # threads; (None, t): processes of t threads each fill it; 6 x 6 keeps its share (6/32).
    # 10x3, 16x2 and 32x1 are not among them: on a heavy comp, with 1-2 GB of cache, not one frame in 6 minutes. They can be added.
    SETUPS = ((1, None), (2, None), (4, None), (None, 4), (6, 6 / 32.0))
    FRAMES = 20  # per setup by default (Frames per setup), and at least 2 per process, or a setup with many has them all on its first frame

    @classmethod
    def plan(cls, cpu, free_gb):
        """The setups for this PC: (processes, threads, Nuke cache GB). The RAM free for Nuke, split by the number
        of processes, less what a process takes besides its cache. A setup that cannot fit gets 1 GB of cache
        and starts what the RAM limit lets it (the Ran column shows how many)."""
        setups = []
        for n, t in cls.SETUPS:
            if n is None:
                procs, threads = max(1, cpu // t), t
            else:
                procs, threads = n, max(1, round(cpu * t) if t else cpu // n)
            if procs <= cpu and (procs, threads) not in [x[:2] for x in setups]:
                setups.append((procs, threads, max(1, int(free_gb / procs - BESIDE_CACHE))))
        return sorted(setups)

    def _free_gb(self):
        """RAM Nuke processes may take: up to the RAM limit, less what the PC uses now (nothing should render)."""
        if not psutil:
            return float(self.main.manager.ram * self.main.manager.total)
        vm = psutil.virtual_memory()
        limit = float(self.main.settings.get("ram_limit", 90)) / 100
        return max(1.0, (vm.total * limit - vm.used) / 1024 ** 3)

    def __init__(self, window, engine):
        super().__init__(window)
        self.main, self.src = window, engine
        self.setWindowTitle("Test this script: %s" % engine.name)
        self.resize(1400, 580)
        cpu = os.cpu_count() or 8
        m = window.manager
        cur = (m.total, m.threads, m.ram)
        self.current = cur
        self.added = {tuple(x) for x in window.settings.get("test_setups", [])}  # your own, kept for next time
        # your own settings stand in for a planned setup with the same processes and threads and about the same cache
        self.setups = sorted({x for x in self.plan(cpu, self._free_gb())
                              if not (x[:2] == cur[:2] and abs(x[2] - cur[2]) <= 2)} | {cur} | self.added)
        self.all_frames = engine.frame_list()
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Renders frames from the middle of this render with each setup below, at most %d minutes each, and measures\n"
            "frames per minute. The frames go to a test folder and are deleted; your render's files stay as they are.\n"
            "Nothing else should render meanwhile." % (self.LIMIT // 60))
        intro.setObjectName("dim")
        lay.addWidget(intro)
        self.table = QtWidgets.QTableWidget(len(self.setups), 11)
        self.table.setHorizontalHeaderLabels(["Processes x threads", "Ran", "Took", "Frames / min", "Frame", "CPU avg", "RAM avg", "GPU avg",
                                              "VRAM avg", "Fell", "State"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.verdict = QtWidgets.QLabel("")
        self.verdict.setWordWrap(True)
        lay.addWidget(self.verdict)
        ram = max(1, int(psutil.virtual_memory().total / 1024 ** 3)) if psutil else 256

        def box(lo, hi, value, suffix=""):
            spin = QtWidgets.QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(value)
            spin.setSuffix(suffix)
            return spin

        def label(text):
            lab = QtWidgets.QLabel(text)
            lab.setObjectName("dim")
            return lab

        edit = QtWidgets.QHBoxLayout()
        total = max(1, len(self.all_frames))
        self.s_frames = box(1, total, min(total, int(window.settings.get("test_frames", self.FRAMES))))
        self.s_frames.setToolTip("Frames each setup renders; at least 2 per process, so a setup with many gets more.\n"
                                 "Few frames are quick but less exact: run the two best again with more to be sure.")
        self.s_frames.valueChanged.connect(lambda v: window._save_setting("test_frames", v))
        self.s_add_p, self.s_add_t, self.s_add_c = box(1, cpu, m.total), box(1, cpu, m.threads), box(1, ram, m.ram, " GB")
        self.b_add = QtWidgets.QPushButton("Add setup")
        self.b_add.clicked.connect(self._add_setup)
        self.b_remove = QtWidgets.QPushButton("Remove selected")
        self.b_remove.setToolTip("A recommended setup comes back the next time this window opens; one you added is gone.")
        self.b_remove.clicked.connect(self._remove_setups)
        for widget in (label("Frames per setup"), self.s_frames):
            edit.addWidget(widget)
        edit.addSpacing(24)
        for widget in (label("Processes"), self.s_add_p, label("Threads"), self.s_add_t, label("Nuke cache"), self.s_add_c,
                       self.b_add, self.b_remove):
            edit.addWidget(widget)
        edit.addStretch(1)
        lay.addLayout(edit)
        buttons = QtWidgets.QHBoxLayout()
        self.b_start = QtWidgets.QPushButton("Start")
        self.b_start.setObjectName("primary")
        self.b_stop = QtWidgets.QPushButton("Stop")
        self.b_use = QtWidgets.QPushButton("Use these settings")
        self.b_use.setEnabled(False)
        self.b_stop.setEnabled(False)
        for b in (self.b_start, self.b_stop, self.b_use):
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.b_start.clicked.connect(self.start)
        self.b_stop.clicked.connect(self.stop)
        self.b_use.clicked.connect(self.use)
        self.results = {}
        self.index = -1
        self.run = None
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self._fill_table()

    def _fill_table(self):
        self.table.clearContents()
        self.table.setRowCount(len(self.setups))
        for i, (n, t, c) in enumerate(self.setups):
            mark = "  (yours)" if (n, t, c) == self.current else ("  (added)" if (n, t, c) in self.added else "")
            self._cell(i, 0, "%d x %d, cache %d GB%s" % (n, t, c, mark))
            self._cell(i, 10, "waiting")
        self._fit_columns()

    def _fit_columns(self):
        """The window grows to show every column whole, as far as the screen allows."""
        wanted = sum(max(self.table.horizontalHeader().sectionSizeHint(c), self.table.sizeHintForColumn(c))
                     for c in range(self.table.columnCount())) + 60
        room = self.screen().availableGeometry().width() - 40
        if self.width() < min(wanted, room):
            self.resize(min(wanted, room), self.height())

    def _add_setup(self):
        setup = (self.s_add_p.value(), self.s_add_t.value(), self.s_add_c.value())
        self.added.add(setup)
        self.main._save_setting("test_setups", sorted(self.added))
        if setup not in self.setups:
            self.setups = sorted(self.setups + [setup])
        self._fill_table()

    def _remove_setups(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        gone = {self.setups[r] for r in rows}
        self.setups = [x for x in self.setups if x not in gone]
        self.added -= gone
        self.main._save_setting("test_setups", sorted(self.added))
        self._fill_table()

    def _idle(self, on):
        for widget in (self.b_start, self.b_add, self.b_remove, self.s_frames, self.s_add_p, self.s_add_t, self.s_add_c):
            widget.setEnabled(on)
        self.b_stop.setEnabled(not on)

    def _frames_for(self, processes):
        """Frames from the middle of the render, the same for every setup but for those that need more."""
        count = min(len(self.all_frames), max(self.s_frames.value(), 2 * processes))
        first = (len(self.all_frames) - count) // 2
        return self.all_frames[first:first + count]

    def _cell(self, row, col, text):
        item = self.table.item(row, col)
        if item is None:
            item = QtWidgets.QTableWidgetItem()
            self.table.setItem(row, col, item)
        item.setText(text)

    def start(self):
        if self.src.movie:
            QtWidgets.QMessageBox.information(self, "JKrender", "A movie renders in one process from start to end: "
                                              "there is nothing to compare.")
            return
        if any(e.state in ("rendering", "paused", "finishing", "waiting") for e in self.main.manager.engines):
            QtWidgets.QMessageBox.information(self, "JKrender", "A render is going: the test would only measure the two "
                                              "fighting over the PC. Start it when nothing renders.")
            return
        if not self.setups:
            return
        self._idle(False)
        self.b_use.setEnabled(False)
        self.verdict.setText("")
        self._fill_table()
        self.results = {}
        self.index = -1
        self.slow_in_a_row = 0
        self._next()
        self.timer.start(1000)

    def _next(self):
        self.index += 1
        while (self.index < len(self.setups) and self.slow_in_a_row >= self.GIVE_UP
               and self.setups[self.index] != self.current and self.setups[self.index] not in self.added):
            self._cell(self.index, 10, "left out: slower")
            self.index += 1
        if self.index >= len(self.setups):
            self._finish()
            return
        n, t, c = self.setups[self.index]
        settings = dict(self.main.settings)
        settings.update(processes=n, threads=t, ram=c, max_renders=1, _test=True)
        settings.pop("script_ram", None)  # what the script took with another cache would keep processes out
        root = TEST_DIR / ("%s_%dx%d_%d" % (datetime.datetime.now().strftime("%Y%m%d_%H%M%S"), n, t, c))
        out = root / "out"
        out.mkdir(parents=True, exist_ok=True)
        job = copy.deepcopy(self.src.job)
        for w in job.get("writes", []):
            w["output"] = str(out / os.path.basename(w.get("output") or "test_####.exr")).replace("\\", "/")
        job["test_out"] = str(out).replace("\\", "/")
        manager = RenderManager(settings)
        e = manager.add(job, root)
        frames = self._frames_for(n)
        e.set_range(frames_to_spec(frames))
        manager.request_start(e)
        self.run = {"manager": manager, "engine": e, "root": root, "t0": time.time(), "open": None, "frames": frames,
                    "cpu": [], "ram": [], "ram_gb": [], "gpu": [], "vram": [], "vram_gb": [], "busy": [], "stopping": False}
        if psutil:
            psutil.cpu_percent(None)  # from here on
        self._cell(self.index, 10, "rendering")

    def tick(self):
        r = self.run
        if r is None:
            return
        m, e = r["manager"], r["engine"]
        m.vram = self.main._gpu
        m.gpu_mem = self.main._gpu_procs or {}
        m.tick()
        now = time.time()
        done = e.counts()["done"]
        if any(": missing " in x[1] for x in e.log):
            # Nuke says it wrote a frame that is not in the test folder: it went somewhere else, so stop at once
            self.stop()
            self.b_use.setEnabled(False)
            self.verdict.setText("Test stopped: a frame did not arrive in the test folder, so it may have gone to the "
                                 "render's own folder. Check that folder before you render.")
            return
        if r["open"] is None and any(w.state == "busy" for w in e.workers.values()):
            r["open"] = now  # the first process has opened the script: a short test should not be mostly script loads
        if psutil:
            r["cpu"].append(psutil.cpu_percent(None))
            vm = psutil.virtual_memory()
            r["ram"].append(vm.percent)
            r["ram_gb"].append(vm.used / 1024.0 ** 3)
        if m.vram and m.vram[2]:
            r["gpu"].append(m.vram[0])
            r["vram"].append(m.vram[1] / m.vram[2] * 100)
            r["vram_gb"].append(m.vram[1] / 1024.0)
        r["busy"].append(sum(1 for w in e.workers.values() if w.state == "busy"))
        rate = done / ((now - (r["open"] or r["t0"])) / 60.0) if done else None
        self._show(self.index, r, rate, done)
        # time per frame: a setup with many processes renders more of them
        paces = [x["took"] / len(x["frames"]) for x in self.results.values() if x["complete"]]
        slower = bool(paces) and now - r["t0"] >= min(paces) * len(r["frames"]) * self.SLOWER and e.state == "rendering"
        if not r["stopping"] and (slower or now - r["t0"] >= self.LIMIT or e.state in ("done", "failed", "stopped")):
            r["stopping"] = True
            r["slower"] = slower
            r["complete"] = done >= len(r["frames"])
            r["took"] = now - r["t0"]
            r["rate"] = rate or 0.0  # no rate means no frame done yet
            r["frame"] = statistics.median([sec for w in e.workers.values() for sec in w.frame_secs] or [0])
            r["fell"] = sum("closed now" in x[1] for x in e.log) + e.crashes
            if e.state in ("rendering", "paused", "finishing", "waiting"):
                e.stop("Test setup done")
        if r["stopping"] and all(w.state == "gone" for w in e.workers.values()):
            self.results[self.setups[self.index]] = r
            self.slow_in_a_row = self.slow_in_a_row + 1 if r["slower"] or not done else 0
            self._show(self.index, r, r["rate"], done, final=True)
            self._drop(r)
            self.run = None
            self._next()

    @staticmethod
    def _drop(r):
        """The setup's processes and its folder: frames, and logs once JKrender has let go of them."""
        r["manager"].shutdown()
        for w in r["engine"].workers.values():
            try:
                w.log.close()
            except Exception:
                pass
        shutil.rmtree(r["root"], ignore_errors=True)

    def _show(self, i, r, rate, done, final=False):
        busy = r["busy"][len(r["busy"]) // 3:] or r["busy"]  # once it has settled
        self._cell(i, 1, "%.1f" % (sum(busy) / float(len(busy))) if busy else "")
        # the whole render of these frames, from the start with the script loads; when the limit ended it, how far it got
        took = fmt_secs(r.get("took", time.time() - r["t0"]))
        if final:
            took += "  (%d of %d)" % (done, len(r["frames"]))
        self._cell(i, 2, took)
        self._cell(i, 3, "%.1f" % rate if rate is not None else "")
        if final:
            self._cell(i, 4, "%.1f s" % r["frame"] if r.get("frame") else "")
        average = lambda v: sum(v) / float(len(v))
        cpus = os.cpu_count() or 1
        self._cell(i, 5, "%.0f %% · %.1f threads" % (average(r["cpu"]), average(r["cpu"]) * cpus / 100.0) if r["cpu"] else "")
        self._cell(i, 6, "%.0f %% · %.1f GB" % (average(r["ram"]), average(r["ram_gb"])) if r["ram"] else "")
        self._cell(i, 7, "%.0f %%" % average(r["gpu"]) if r["gpu"] else "")
        self._cell(i, 8, "%.0f %% · %.1f GB" % (average(r["vram"]), average(r["vram_gb"])) if r["vram"] else "")
        self._cell(i, 9, str(r.get("fell", "")) if final else "")
        self._cell(i, 10, ("stopped: slower" if r.get("slower") else "done") if final
                   else "rendering, %d frames" % done)
        self._fit_columns()

    def stop(self):
        self.timer.stop()
        r = self.run
        if r is not None:
            if r["engine"].state in ("rendering", "paused", "finishing", "waiting"):
                r["engine"].stop("Test stopped")
            self._drop(r)
            self._cell(self.index, 10, "stopped")
            self.run = None
        self._idle(True)
        if self.results:
            self._finish(partial=True)

    def _finish(self, partial=False):
        self.timer.stop()
        shutil.rmtree(TEST_DIR, ignore_errors=True)  # its test renders; a folder Windows still held is gone now
        self._idle(True)
        if not self.results:
            return
        # a setup that rendered all the frames; the rate of one stopped early says less
        whole = [k for k in self.results if self.results[k]["complete"]] or [k for k in self.results if self.results[k]["rate"]]
        if not whole:
            text = "No setup finished a frame, so there is nothing to recommend."
            self.verdict.setText(text)
            self._save_results(text)
            return
        best = max(whole, key=lambda k: self.results[k]["rate"])
        self.best = best
        rate = self.results[best]["rate"]
        mine = self.results.get(self.current)
        if best == self.current:
            text = "Your settings (Processes %d, Threads %d, Nuke cache %d GB) are the fastest of these for this script: " \
                   "%.1f frames / min." % (best[0], best[1], best[2], rate)
        else:
            gain = " — %.0f %% faster than yours (%.1f)" % ((rate / mine["rate"] - 1) * 100, mine["rate"]) if mine and mine["rate"] else ""
            text = "Fastest for this script on this PC: Processes %d, Threads %d, Nuke cache %d GB, %.1f frames / min%s." % (
                best[0], best[1], best[2], rate, gain)
            self.b_use.setEnabled(True)
        if partial:
            text = "(Stopped before all setups were tested.) " + text
        self.verdict.setText(text)
        self._save_results(text)

    def _save_results(self, verdict):
        """The table as text, to look at or compare later: ~/.jkrender/test_results."""
        try:
            TEST_RESULTS.mkdir(parents=True, exist_ok=True)
            head = [self.table.horizontalHeaderItem(c).text() for c in range(self.table.columnCount())]
            rows = [[(self.table.item(r, c).text() if self.table.item(r, c) else "") for c in range(self.table.columnCount())]
                    for r in range(self.table.rowCount())]
            path = TEST_RESULTS / ("%s_%s.txt" % (self.src.name, datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
            lines = ["%s, %d frames per setup or more, %d threads" % (self.src.name, self.s_frames.value(), os.cpu_count() or 0),
                     " | ".join(head)] + [" | ".join(r) for r in rows] + [verdict]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def use(self):
        n, t, c = self.best
        self.main.s_workers.setValue(n)
        self.main.s_threads.setValue(t)
        self.main.s_ram.setValue(c)
        self.verdict.setText(self.verdict.text() + "  Set: Processes %d, Threads %d, Nuke cache %d GB." % (n, t, c))
        self.b_use.setEnabled(False)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


class TotalCard(QtWidgets.QFrame):
    """Right of the render cards: when every render still to go should be done, at the speed the PC renders now,
    and how long they have taken so far. Once they are all through, how long it took altogether."""

    def __init__(self):
        super().__init__()
        self.setFixedWidth(260)
        self.setStyleSheet("TotalCard { background: %s; border: 1px solid %s; border-radius: 5px; }" % (C["panel"], C["line"]))
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(10, 6, 10, 7)
        lay.setVerticalSpacing(2)
        self.t0 = None  # when the renders now counted started
        self.members = []  # the renders counted since then, finished ones too
        self.through = False  # all of them are through: it shows how long they took
        self.head = head = QtWidgets.QLabel("ALL RENDERS DONE IN")
        head.setStyleSheet("color: %s; font-size: 7.5pt; font-weight: 600; letter-spacing: 1px; background: transparent;"
                           % C["dim"])
        self.when = QtWidgets.QLabel("")
        self.when.setStyleSheet("font-weight: 600; font-size: 11pt; background: transparent;")
        self.left = QtWidgets.QLabel("")
        self.left.setStyleSheet("color: %s; background: transparent;" % C["dim"])
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.pct = QtWidgets.QLabel("")
        self.pct.setStyleSheet("color: %s; background: transparent;" % C["dim"])
        self.pct.setMinimumWidth(36)
        self.pct.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(head, 0, 0, 1, 2)
        lay.addWidget(self.when, 1, 0, 1, 2)
        lay.addWidget(self.left, 2, 0, 1, 2)
        lay.addWidget(self.bar, 3, 0)
        lay.addWidget(self.pct, 3, 1)
        lay.setColumnStretch(0, 1)
        self.setToolTip("Every render still rendering or waiting: the frames they have left, at the speed all\n"
                        "Nuke processes together render now. Waiting renders are counted at that speed too.")

    def refresh(self, engines):
        active = [e for e in engines if e.state in ("rendering", "paused", "finishing", "waiting")]
        now = time.time()
        if active:
            if self.t0 is None or self.through:  # renders start again after all were through: count anew
                self.members, self.through = [], False
                self.t0 = min([e.started for e in active if e.started] or [now])
            self.members += [e for e in active if e not in self.members]
        self.members = [e for e in self.members if e in engines]  # a closed render leaves the count
        if not active:
            self._finished()
            return
        self.show()
        self.head.setText("ALL RENDERS DONE IN")
        self.when.setStyleSheet("font-weight: 600; font-size: 11pt; background: transparent;")
        frames = sum(len(e.frames) for e in active)
        left = sum(e.counts()["queued"] + e.counts()["rendering"] for e in active)
        done = frames - left
        self.bar.setMaximum(max(1, frames))
        self.bar.setValue(done)
        self.pct.setText("%.0f %%" % (100.0 * done / max(1, frames)))
        running = [e for e in active if e.state in ("rendering", "finishing")]
        rate = sum(e.rate() for e in running)
        eta = left / rate if rate > 0 else None
        alone = [x for x in (e.eta() for e in running) if x is not None]
        if eta is not None and alone:
            eta = max(eta, max(alone))  # never sooner than one render on its own
        renders = "%d render%s" % (len(active), "" if len(active) == 1 else "s")
        so_far = "%s so far" % fmt_secs(now - self.t0)
        if not running:
            self.when.setText("Paused" if any(e.state == "paused" for e in active) else "Waiting")
            self.left.setText("%s  ·  %d frames  ·  %s" % (so_far, left, renders))
        elif eta is None:
            self.when.setText("Estimating…")
            self.left.setText("%s  ·  %d frames  ·  %s" % (so_far, left, renders))
        else:
            end = datetime.datetime.now() + datetime.timedelta(seconds=eta)
            day = "" if end.date() == datetime.date.today() else (
                "tomorrow " if end.date() == datetime.date.today() + datetime.timedelta(days=1) else end.strftime("%d.%m. "))
            self.when.setText(fmt_secs(eta))
            self.left.setText("finishes at %s%s  ·  %s  ·  %d frames" % (day, end.strftime("%H:%M"), so_far, left))
        self.setToolTip("Every render still rendering or waiting: the frames they have left, at the speed all\n"
                        "Nuke processes together render now. Waiting renders are counted at that speed too.\n"
                        "So far: since the first of them started (%s)." % renders)

    def _finished(self):
        """No render left to go: how long the renders took altogether, until the next one starts."""
        ended = [e for e in self.members if e.ended]
        if self.t0 is None or not ended:
            self.t0 = None
            self.hide()
            return
        self.show()
        self.through = True
        end = max(e.ended for e in ended)
        ok = all(e.state == "done" for e in self.members)
        self.head.setText("ALL RENDERS TOOK" if ok else "RENDERS ENDED AFTER")
        self.when.setStyleSheet("font-weight: 600; font-size: 11pt; background: transparent; color: %s;"
                                % (C["done"] if ok else C["text"]))
        self.when.setText(fmt_secs(end - self.t0))
        frames = sum(len(e.frames) for e in self.members)
        n = len(self.members)
        self.left.setText("finished at %s  ·  %d frames  ·  %d render%s" % (
            datetime.datetime.fromtimestamp(end).strftime("%H:%M"), frames, n, "" if n == 1 else "s"))
        self.bar.setMaximum(1)
        self.bar.setValue(1 if ok else 0)
        self.pct.setText("100 %" if ok else "")
        self.setToolTip("From the first of these renders starting to the last one ending.")


class RenderCard(QtWidgets.QFrame):
    """One render at the top of the window: name, progress and state. Click to look at it."""

    clicked = QtCore.Signal(object, bool)  # engine, Shift held
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
        eta = e.eta() if e.state in ("rendering", "paused") else None
        left = ("&nbsp;&nbsp;·&nbsp;&nbsp;%s left" % fmt_secs(eta)) if eta else ""
        self.status.setText('<span style="color:%s">%s</span>&nbsp;&nbsp;<span style="color:%s">%d / %d%s</span>'
                            % (color, STATE_LABELS.get(e.state, e.state), C["dim"], finished, total, left))
        self.badge.setStyleSheet("background: %s; color: %s; border-radius: 3px; font-weight: 600;"
                                 % (C["accent"] if selected else C["line"], "#111" if selected else C["text"]))
        if e.state == "done":  # finished: the whole card goes green
            self.setStyleSheet("RenderCard { background: #3b4a2f; border: %dpx solid %s; border-radius: 5px; }"
                               % (2 if selected else 1, C["done"]))
        else:
            self.setStyleSheet("RenderCard { background: %s; border: 1px solid %s; border-radius: 5px; }"
                               % (C["panel2"] if selected else C["panel"], C["accent"] if selected else C["line"]))
        self.close_btn.setVisible(closable)

    def mousePressEvent(self, event):
        self.clicked.emit(self.engine, bool(event.modifiers() & QtCore.Qt.ShiftModifier))


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


class Toggle(QtWidgets.QCheckBox):
    """On / off switch in Nuke orange; a QCheckBox underneath, so isChecked / toggled work as usual."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 20)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        on, enabled = self.isChecked(), self.isEnabled()
        track = QtGui.QColor(C["accent"] if on else "#555555")
        if not enabled:
            track = QtGui.QColor("#6b5434" if on else "#444444")
        r = QtCore.QRectF(1, 3, self.width() - 2, self.height() - 6)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = self.height() - 4
        x = self.width() - d - 2 if on else 2
        p.setBrush(QtGui.QColor("#f2f2f2" if enabled else "#9a9a9a"))
        p.drawEllipse(QtCore.QRectF(x, 2, d, d))
        p.end()


class Tile(QtWidgets.QFrame):
    def __init__(self, title):
        super().__init__()
        self.setStyleSheet("QFrame { background: %s; border-radius: 4px; }" % C["panel2"])
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        self.title = t = QtWidgets.QLabel(title)
        t.setObjectName("dim")
        self.value = QtWidgets.QLabel("—")
        self.value.setObjectName("tileValue")
        self.sub = QtWidgets.QLabel("")
        self.sub.setObjectName("dim")
        self.avg = QtWidgets.QLabel("")  # the average while rendering (CPU, RAM, GPU)
        self.avg.setObjectName("dim")
        self.avg.setToolTip("Average while rendering, from when the renders started until the last one ended")
        self.avg.hide()
        lay.addWidget(t)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        lay.addWidget(self.avg)

    def set(self, value, sub=""):
        self.value.setText(value)
        self.sub.setText(sub)

    def set_avg(self, text):
        self.avg.setText(text)
        self.avg.setVisible(bool(text))

    def show_state(self, value, sub, color):
        self.set(value, sub)
        self.value.setStyleSheet("color: %s;" % color)


class TwoTiles(QtWidgets.QFrame):
    """Two tiles in the place of one, with a line between them. The right one can go, for a movie."""

    def __init__(self, left, right):
        super().__init__()
        self.setStyleSheet("QFrame { background: %s; border-radius: 4px; }" % C["panel2"])
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.right = right
        self.line = line = QtWidgets.QFrame()
        line.setFixedWidth(1)
        line.setStyleSheet("QFrame { background: %s; border-radius: 0; }" % C["dim"])
        lay.addWidget(left, 1)
        lay.addWidget(line)
        lay.addWidget(right, 1)

    def show_right(self, on):
        self.line.setVisible(on)
        self.right.setVisible(on)


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


def nav_icon(kind):
    """Small orange icons for the frame bar: "prev", "next", "big" (enlarge) and "small" (back)."""
    pix = QtGui.QPixmap(32, 32)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    col = QtGui.QColor(C["accent"])
    if kind in ("prev", "next"):
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        if kind == "prev":
            p.drawRect(QtCore.QRectF(8, 8, 3, 16))
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(24, 8), QtCore.QPointF(12, 16), QtCore.QPointF(24, 24)]))
        else:
            p.drawRect(QtCore.QRectF(21, 8, 3, 16))
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(8, 8), QtCore.QPointF(20, 16), QtCore.QPointF(8, 24)]))
    else:
        pen = QtGui.QPen(col, 3)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        out = kind == "big"  # corners pointing out to enlarge, in to go back
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            cx, cy = (16 - sx * 10, 16 - sy * 10) if out else (16 - sx * 4, 16 - sy * 4)
            d = 7 if out else -7
            p.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(cx + sx * d, cy))
            p.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(cx, cy + sy * d))
    p.end()
    return QtGui.QIcon(pix)


class FramesModel(QtCore.QAbstractTableModel):
    COLS = ["Frame", "State", "Chunk", "Worker", "Time", "Size", "Check frames", "Check pixels"]

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
        return fr.state in ("bad", "failed") or fr.small or pixels_broken(fr) or fr.same_as is not None

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
            if col == 7:
                if not fr.pixels:
                    return ""
                return "not read" if "error" in fr.pixels else pixels_note(fr) or "ok"
        if role == QtCore.Qt.ForegroundRole and col == 6 and not fr.checks:
            return None
        if role == QtCore.Qt.ForegroundRole and col == 7:
            if not fr.pixels:
                return None
            if "error" in fr.pixels:
                return QtGui.QColor(C["small"])
            return QtGui.QColor(C["failed"] if pixels_broken(fr) or fr.same_as is not None else C["done"])
        if role == QtCore.Qt.ForegroundRole and col in (1, 6):
            if fr.state in ("bad", "failed") or (col == 1 and (pixels_broken(fr) or fr.same_as is not None)):
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
    def __init__(self, job_path, exit_when_done=False, restoring=False):
        super().__init__()
        self._queue_saved = None
        self.settings = read_settings()
        self.manager = RenderManager(self.settings)
        self.exit_when_done = exit_when_done
        self.cards = {}
        self._last_states = {}
        self._preview_frame = None
        self._gpu = None
        self._gpu_procs = {}  # pid -> (GPU %, VRAM bytes)
        self._io_last = None
        self._net_last = None
        self.server = None
        self.setWindowTitle("JKrender %s" % APP_VERSION)
        self.resize(1320, 900)
        job_path = Path(job_path)
        # Before the window shows: frames the Write already rendered appear as done.
        self.engine = self.manager.add(json.loads(job_path.read_text(encoding="utf-8")), job_path.parent)
        if self.settings.get("log_cleanup", True):
            days = int(self.settings.get("log_days", 7))
            deleted, freed = clean_render_folders(job_path.parent.parent, days, keep=[job_path.parent])
            if deleted:
                self.engine.note("Deleted %d render log folder(s) older than %d days (%s)" % (deleted, days, fmt_size(freed)))
        self._build()
        self._restore_headers()
        self._add_card(self.engine)
        self.select(self.engine)
        if psutil:
            psutil.cpu_percent(percpu=True)
        if shutil.which("nvidia-smi") or sys.platform.startswith("win"):
            threading.Thread(target=self._gpu_loop, daemon=True).start()
        QtWidgets.QApplication.instance().installEventFilter(self)  # clicks off the cards drop the Shift picks
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)
        self.slow = QtCore.QTimer(self)
        self.slow.timeout.connect(self._slow_tick)
        self.slow.start(1000)
        if self.settings.get("auto_start", False) and not self.engine.nothing_to_render() and not restoring:
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

    def add_job(self, job_path, auto=True):
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
        if auto and self.settings.get("auto_start", False) and not e.nothing_to_render():
            self.manager.request_start(e)
            self._sync_controls()

    def _save_queue(self):
        """The cards and the Nuke processes, whole or not at all, whenever they change."""
        if self.exit_when_done:
            return
        renders = [{"job": str(e.dir / "job.json"), "state": e.state, "range": e.range_text, "started": e.started} for e in self.manager.engines]
        pids = []
        for e in self.manager.engines:
            for w in e.workers.values():
                if w.state != "gone" and w.ps:
                    try:
                        pids.append([w.proc.pid, w.ps.create_time()])
                    except Exception:
                        pass
        data = {"renders": renders, "pids": sorted(pids)}
        if data == self._queue_saved:
            return
        try:
            HOME_DIR.mkdir(parents=True, exist_ok=True)
            temp = QUEUE_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(temp, QUEUE_FILE)
            self._queue_saved = data
        except OSError:
            pass

    def restore(self, e, saved):
        """A card as it was when JKrender went down. What the disk has counts; a render that was going is left
        stopped, for Render to go on from the rest. True if it was going."""
        if saved.get("range") and saved["range"] != e.range_text:
            e.set_range(saved["range"])
            e.scan_disk()
            e.done_if_on_disk()
        if saved.get("started"):
            e.started = saved["started"]  # Nuke's temp files of that run go by this
            e._clean_temp()
            e.started = None
        going = saved.get("state") in ("rendering", "paused", "finishing", "waiting")
        if e.state != "done" and (going or saved.get("state") in ("stopped", "failed")):
            e.state = "stopped"
        if going:
            e.note("!! Interrupted: JKrender went down while this rendered. What is on disk counts; Render goes on from the rest.", "err")
        self._last_states.pop(e, None)
        return going and e.state != "done"

    def _bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _add_card(self, e):
        card = RenderCard(e)
        card.clicked.connect(self._card_clicked)
        card.closeRequested.connect(self._close_card)
        self.cards_row.insertWidget(self.cards_row.count() - 2, card)  # before the stretch and All renders
        self.cards[e] = card
        self._refresh_cards()
        QtCore.QTimer.singleShot(300, lambda: self._check_on_open(e))  # after a card from before a crash got its range back

    def _check_on_open(self, e):
        """Settings > Check pixels when a render opens. The frame check already ran: it is how the card knows what is on disk."""
        if (self.settings.get("check_on_open", True) and e in self.manager.engines and not e.job.get("test_out")
                and e.state in ("ready", "stopped", "done", "failed")):
            e.check_all_pixels()

    def _card_clicked(self, e, shift):
        """A click picks one render; Shift+click adds it to (or takes it from) the renders picked, and Render,
        Pause and Stop then act on all of them."""
        picked = getattr(self, "picked", set())
        if shift:
            if not picked:
                picked = {self.engine}
            if e in picked and len(picked) > 1:
                picked.discard(e)
                self.picked = picked
                if e is self.engine:
                    self.select(next(x for x in self.manager.engines if x in picked))
                else:
                    self._refresh_cards()
                    self._sync_controls()
                return
            picked.add(e)
        else:
            picked = set()
        self.picked = picked
        self.select(e)

    def _targets(self):
        """The renders Render / Pause / Stop act on: the picked ones, or the one shown."""
        picked = [x for x in self.manager.engines if x in getattr(self, "picked", set())]
        return picked if len(picked) > 1 else [self.engine]

    def _refresh_cards(self):
        picked = getattr(self, "picked", set())
        for e, card in self.cards.items():
            card.refresh(e is self.engine or e in picked, len(self.cards) > 1)
        self.total_card.refresh(self.manager.engines)

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
        getattr(self, "picked", set()).discard(e)
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
        if hasattr(self, "l_ram_need"):
            self._show_chunk()
        self.range_edit.setText(e.range_text)
        self.chunk_table.setRowCount(0)  # columns keep their widths: fitted once, or as you left them
        self._preview_frame = None
        self.preview.setPixmap(QtGui.QPixmap())
        self.preview.setText("Click a frame to see it")
        self.preview_info.setText("")
        self._frame_play_stop()
        self._sync_frame_bar(None)  # the slider covers this render's frames before any frame is shown
        # frames already rendered: the first one shows in the preview straight away
        first = None if e.movie else next((f for f in e.frame_list() if e.frames[f].state in ("done", "skipped")), None)
        if first is not None:
            QtCore.QTimer.singleShot(0, lambda e=e, f=first: self._show_first(e, f))
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
        self.total_card = TotalCard()  # stays last, after the stretch
        self.cards_row.addWidget(self.total_card)
        lay.addLayout(self.cards_row)

        # controls (Render, Pause, Stop, frame range, and the render's result right after them) go here
        head = QtWidgets.QHBoxLayout()
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
        self.t_check_frames = Tile("Check frames")
        self.t_check_pixels = Tile("Check pixels")
        for t in (self.t_frames, self.t_elapsed, self.t_eta, self.t_speed, self.t_avg, self.t_workers):
            stats.addWidget(t, 1)
        self.check_tiles = TwoTiles(self.t_check_frames, self.t_check_pixels)
        stats.addWidget(self.check_tiles, 2)
        lay.addLayout(stats)

        self.frame_map = FrameMap(self.engine)
        self.frame_map.frameClicked.connect(self._pick_frame)
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
        self.b_start.setToolTip("Shift+click render cards to pick several: Render, Pause and Stop act on all of them")
        for b in (self.b_start, self.b_pause, self.b_stop):
            ctl.addWidget(b)
        ctl.addSpacing(14)

        def spin(label, lo, hi, value, suffix, tip):
            # placed in the Settings tab (see _settings_tab)
            box = QtWidgets.QSpinBox()
            box.setRange(lo, hi)
            box.setValue(value)
            box.setSuffix(suffix)
            box.setToolTip(tip)
            return box

        cpu = os.cpu_count() or 8
        ram = int(psutil.virtual_memory().total / 1024 ** 3) if psutil else 64
        e, m = self.engine, self.manager
        self.s_workers = spin("Processes", 1, cpu, m.total, "",
                              "Nuke processes for all renders together. One render gets them all;\n"
                              "renders side by side share them (8 -> 4 + 4 -> 3 + 3 + 2).")
        self.s_threads = spin("Threads", 1, cpu, m.threads, "",
                              "Threads per Nuke process (-m).")
        self.s_ram = spin("RAM", 1, ram, m.ram, " GB",
                          "Nuke's cache limit per process (-c). A heavy script takes more than this;\n"
                          "System > RAM shows what each process really takes.")
        self.s_chunk = spin("Chunk", 1, 10000, e.chunk_size, " fr",
                            "Frames per chunk. Applies to chunks that have not started yet.\n"
                            "With Settings > Auto chunk size this is the largest chunk; towards the end they get smaller.")
        self.range_edit = QtWidgets.QLabel(e.range_text)  # plain text: the range comes from Nuke
        self.range_edit.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.range_edit.setToolTip("Frames this render covers: the Write's range from Nuke,\n"
                                   "plus the slate frame when Settings > Render slate frame is on.")
        lab = QtWidgets.QLabel("FrameRanges")
        lab.setObjectName("dim")
        ctl.addWidget(lab)
        ctl.addWidget(self.range_edit)
        ctl.addSpacing(14)
        self.banner = QtWidgets.QLabel("")  # "Done in 4:39. All 92 frames ...": right after the controls
        self.banner.hide()
        ctl.addWidget(self.banner, 1)  # runs to the right edge of the window
        ctl.addStretch(0)  # keeps the buttons on the left while there is no result to show
        head.addLayout(ctl)
        self.s_workers.valueChanged.connect(self._workers_changed)
        self.s_threads.valueChanged.connect(lambda v: self._budget_changed("threads", v))
        self.s_ram.valueChanged.connect(lambda v: self._budget_changed("ram", v))
        self.s_chunk.valueChanged.connect(self._chunk_changed)

        # Three panes: on the left, full height, Output / Chunks / Details; on the right, Workers (process
        # timeline) / Log on top and System / Settings below.
        main = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.side_tabs = QtWidgets.QTabWidget()
        self.output_page = self._output_tab()
        self.side_tabs.addTab(self.output_page, "Output")
        self.side_tabs.addTab(self._chunks_table(), "Chunks")
        self.side_tabs.addTab(self._details_tab(), "Details")
        self.side_tabs.currentChanged.connect(lambda i: self._details_shown() and self._update_details())
        main.addWidget(self.side_tabs)
        right = QtWidgets.QWidget()
        rlay = QtWidgets.QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        self.tabs = QtWidgets.QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        for name in ("Workers", "Log"):
            self.tabs.addTab(name)
        rlay.addWidget(self.tabs)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.gantt = Gantt(self.manager)
        self.gantt.workerMenu.connect(self._worker_menu)
        self.gantt.show_all = bool(self.settings.get("show_all_workers", True))
        self.gantt.dim_others = bool(self.settings.get("dim_others", True))
        self.gantt.hide_old_done = bool(self.settings.get("hide_old_done", True))
        self.pages = QtWidgets.QStackedWidget()
        for page in (self.gantt, self._log_tab()):
            self.pages.addWidget(page)
        split.addWidget(self.pages)
        self.sub_tabs = QtWidgets.QTabWidget()
        self.sub_tabs.addTab(self._system_tab(), "System")
        self.sub_tabs.addTab(self._settings_tab(), "Settings")
        split.addWidget(self.sub_tabs)
        split.setSizes([360, 400])
        rlay.addWidget(split, 1)
        main.addWidget(right)
        main.setStretchFactor(0, 2)
        main.setStretchFactor(1, 3)
        main.setSizes([560, 840])
        lay.addWidget(main, 1)
        self.main_split = main
        self.big_box = QtWidgets.QWidget()  # the big preview, in place of the three panes
        self.big_lay = QtWidgets.QVBoxLayout(self.big_box)
        self.big_lay.setContentsMargins(0, 0, 0, 0)
        self.big_box.hide()
        lay.addWidget(self.big_box, 1)
        self.tabs.currentChanged.connect(self._page_changed)

    def _details_shown(self):
        return self.side_tabs.currentWidget() is self.details

    def _page_changed(self, index):
        self.pages.setCurrentIndex(index)
        if self._details_shown():
            self._update_details()

    def _settings_tab(self):
        """Three cards: General, Performance and Optimization, Processes and Chunks.

        Every card is a grid: the name of a setting on the left, its switch or value on the right,
        so everything lines up; a short grey note sits under the row it explains."""
        w = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        FIELD = 96  # every value box the same width

        def card(title):
            frame = QtWidgets.QFrame()
            frame.setObjectName("card")
            frame.setStyleSheet("#card { background: %s; border: 1px solid %s; border-radius: 6px; }"
                                % (C["panel"], C["line"]))
            v = QtWidgets.QVBoxLayout(frame)
            v.setContentsMargins(16, 12, 16, 14)
            v.setSpacing(12)
            head = QtWidgets.QLabel(title)
            head.setStyleSheet("color: %s; font-weight: 600; font-size: 10pt; background: transparent;" % C["accent"])
            v.addWidget(head)
            grid = QtWidgets.QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(10)
            grid.setColumnMinimumWidth(0, 150)
            grid.setColumnStretch(2, 1)
            v.addLayout(grid)
            v.addStretch(1)
            outer.addWidget(frame, 1)
            frame.grid, frame.box = grid, v
            return frame

        def row(c, label, widgets, tip=""):
            r = c.grid.rowCount()
            lab = QtWidgets.QLabel(label)
            lab.setStyleSheet("background: transparent;")
            lab.setToolTip(tip)
            c.grid.addWidget(lab, r, 0, QtCore.Qt.AlignVCenter)
            h = QtWidgets.QHBoxLayout()
            h.setSpacing(10)
            for x in widgets:
                if isinstance(x, QtWidgets.QAbstractSpinBox):
                    x.setFixedWidth(FIELD)
                if tip and not x.toolTip():
                    x.setToolTip(tip)
                h.addWidget(x)
            h.addStretch(1)
            c.grid.addLayout(h, r, 1, 1, 2)
            return lab

        def note(c, widget_or_text):
            lab = widget_or_text if isinstance(widget_or_text, QtWidgets.QLabel) else QtWidgets.QLabel(widget_or_text)
            lab.setStyleSheet("color: %s; font-size: 8pt; background: transparent;" % C["dim"])
            lab.setWordWrap(True)
            r = c.grid.rowCount()
            c.grid.addWidget(lab, r, 1, 1, 2)
            c.grid.setRowMinimumHeight(r, 0)
            return lab

        def toggle(key, default, slot=None):
            t = Toggle()
            t.setChecked(bool(self.settings.get(key, default)))
            t.toggled.connect(slot or (lambda v, key=key: self._save_setting(key, v)))
            return t

        # -- General
        gen = card("General")
        self.c_auto = toggle("auto_start", False)
        row(gen, "Start render on open", [self.c_auto], "Render as soon as the window opens, without clicking Render")
        self.c_next = toggle("auto_next", False)
        row(gen, "Start next render when one ends", [self.c_next],
            "When a render finishes, the next render still waiting to be started starts by itself, in the order of the\n"
            "cards, and so on. A render you stopped stays stopped and does not start the next one.")
        self.c_resume = toggle("resume_after_crash", False)
        row(gen, "Go on after a crash", [self.c_resume],
            "If JKrender went down while rendering, its cards come back the next time it opens, with what is on disk counted.\n"
            "On: the renders that were going start again by themselves. Off: they wait for Render.")
        self.c_frames = toggle("check_frames", True)
        row(gen, "Check frames as they render", [self.c_frames],
            "Each frame right after it renders: is the file there, not empty, whole and readable, and is it far smaller than the rest.\n"
            "Off: a frame that came out broken counts as done until you press Check frames. Render still checks what is on disk before it starts.")
        self.c_pixels = toggle("check_pixels", True)
        row(gen, "Check pixels when a render ends", [self.c_pixels],
            "NaN and inf, black, empty or one-colour frames, frames exactly the same as another, and a size, channels, bit depth\n"
            "or compression unlike the rest of the sequence, in the background.\n"
            "About 0.1 s a 4K frame. It needs numpy and OpenEXR for EXR files, and only reports: nothing renders again by itself.")
        self.c_open = toggle("check_on_open", True)
        row(gen, "Check pixels when a render opens", [self.c_open],
            "The same check on the frames already on disk, in the background, as soon as a render opens here.\n"
            "Render stops it, and a render that ends checks them all anyway. Frames on disk are always checked for missing, empty or cut-off files when a render opens.")
        self.s_max = QtWidgets.QSpinBox()
        self.s_max.setRange(1, MAX_RENDERS)
        self.s_max.setValue(self.manager.max_renders)
        self.s_max.valueChanged.connect(lambda v: self._save_setting("max_renders", v))
        row(gen, "Max renders at once", [self.s_max],
            "1: renders go one after another; the next one starts loading while the last chunks\n"
            "of the current one finish, so the PC never sits idle. That gets the first shot done soonest.\n"
            "2-3: renders run side by side and share the Nuke processes. Not faster in total,\n"
            "but you see frames of every shot early.")
        self.c_slate = Toggle()
        self.c_slate.setChecked(bool(self.settings.get("slate", True)))
        self.s_slate = QtWidgets.QSpinBox()
        self.s_slate.setRange(-100000, 1000000)
        self.s_slate.setValue(int(self.settings.get("slate_frame", 1000)))
        self.s_slate.setEnabled(self.c_slate.isChecked())
        self.c_slate.toggled.connect(lambda v: (self._slate_changed("slate", v), self.s_slate.setEnabled(v)))
        self.s_slate.valueChanged.connect(lambda v: self._slate_changed("slate_frame", v))
        row(gen, "Render slate frame", [self.c_slate, self.s_slate],
            "The project range starts after the slate (e.g. 1001 with the slate on 1000).\n"
            "With this on, every render also renders the slate frame on this frame.")
        self.c_all_workers = toggle("show_all_workers", True, self._all_workers_changed)
        row(gen, "Show processes of all renders", [self.c_all_workers],
            "On: Workers and System show the Nuke processes of every render in the window.\n"
            "Off: only those of the render you picked at the top.")
        self.c_dim = toggle("dim_others", True, self._dim_changed)
        row(gen, "Dim other renders in Workers", [self.c_dim],
            "On: in the Workers timeline the chunks of the render you picked are bright, the others dimmed.\n"
            "Off: every render's chunks are equally bright.")
        self.c_hide_done = toggle("hide_old_done", True, self._hide_done_changed)
        row(gen, "Hide done chunks when rendering again", [self.c_hide_done],
            "On: when you render a render again (Render again, Render this frame / batch, Render after Done),\n"
            "the Workers timeline shows only the new run, without the chunks that were done before.\n"
            "Off: the earlier run's done chunks stay on the timeline.")
        self.c_clean = toggle("log_cleanup", True, lambda v: (self._save_setting("log_cleanup", v),
                                                               self.s_days.setEnabled(v)))
        self.s_days = QtWidgets.QSpinBox()
        self.s_days.setRange(1, 3650)
        self.s_days.setSuffix(" days")
        self.s_days.setValue(int(self.settings.get("log_days", 7)))
        self.s_days.setEnabled(self.c_clean.isChecked())
        self.s_days.valueChanged.connect(lambda v: self._save_setting("log_days", v))
        row(gen, "Delete old render logs", [self.c_clean, self.s_days],
            "When JKrender opens, render folders in the log folder (script snapshot, job and the\n"
            "processes' logs) whose newest file is older than this are deleted. Rendered frames are not touched.")
        note(gen, "Snapshots and logs in the log folder; rendered frames are never touched.")
        self.b_reset = QtWidgets.QPushButton("Reset to defaults")
        self.b_reset.setToolTip("Every setting back to how JKrender comes. What it learned about scripts is kept.")
        self.b_reset.clicked.connect(self._reset_settings)
        gen.box.addWidget(self.b_reset, 0, QtCore.Qt.AlignLeft)

        # -- Performance and Optimization
        perf = card("Performance and Optimization")
        self.c_gpu = toggle("use_gpu", True)
        row(perf, "Use GPU", [self.c_gpu],
            "Start the Nuke processes with --gpu, so GPU nodes (Kronos, ZDefocus, Denoise, ...)\n"
            "render on the graphics card instead of the CPU. Every process uses the same GPU and\n"
            "its own VRAM: System > GPU shows how much each takes. Applies to processes started from now on.")
        note(perf, "Kronos, ZDefocus, Denoise ... on the graphics card. Each process takes its own VRAM.")
        self.c_vram = toggle("vram_guard", True, lambda v: (self._save_setting("vram_guard", v),
                                                             self.s_vram.setEnabled(v and self.c_gpu.isChecked())))
        self.s_vram = QtWidgets.QSpinBox()
        self.s_vram.setRange(50, 98)
        self.s_vram.setSuffix(" %")
        self.s_vram.setValue(int(self.settings.get("vram_limit", 95)))
        self.s_vram.valueChanged.connect(lambda v: self._save_setting("vram_limit", v))
        self.s_vram.setEnabled(self.c_vram.isChecked() and self.c_gpu.isChecked())
        self.c_vram.setEnabled(self.c_gpu.isChecked())
        self.c_gpu.toggled.connect(lambda v: (self.c_vram.setEnabled(v), self.s_vram.setEnabled(v and self.c_vram.isChecked())))
        row(perf, "VRAM limit", [self.c_vram, self.s_vram],
            "Every Nuke process uses the GPU. With room on the card nothing restarts. Close to the limit\n"
            "the process that grew the most gets a fresh Nuke after its chunk.\n"
            "Over the limit, fewer processes, like the RAM limit: the last one started closes first (after its chunk,\n"
            "or now if it is still opening the script), one at a time; places come back when one more fits.\n"
            "About to spill into the RAM (97 %), the one taking the most VRAM closes at once.\n"
            "Off: every process uses the GPU; a full card makes them all wait (GPU at 100 %, CPU idle).")
        note(perf, "Over the limit the process started last closes first, and comes back when one more fits.")
        self.c_guard = toggle("ram_guard", True, self._guard_changed)
        self.s_limit = QtWidgets.QSpinBox()
        self.s_limit.setRange(50, 98)
        self.s_limit.setSuffix(" %")
        self.s_limit.setValue(int(self.settings.get("ram_limit", 90)))
        self.s_limit.valueChanged.connect(lambda v: self._save_setting("ram_limit", v))
        self.s_limit.setEnabled(self.c_guard.isChecked())
        row(perf, "RAM limit", [self.c_guard, self.s_limit],
            "When RAM goes over the limit, fewer Nuke processes run:\n"
            "the one started last closes first, so the least work is lost (close to full at once,\n"
            "otherwise after its chunk). They come back when they fit again.\n"
            "Off: nothing watches RAM; Windows starts swapping when it runs out.")
        note(perf, "Over the limit the process started last closes first, and comes back when RAM allows.")

        # -- Processes and Chunks (Processes, Threads and Nuke cache are shared by every render; locked while rendering)
        procs = card("Processes and Chunks")
        row(procs, "Processes", [self.s_workers])
        row(procs, "Threads", [self.s_threads])
        row(procs, "Nuke cache", [self.s_ram])
        self.b_test = QtWidgets.QPushButton("Test this script…")
        self.b_test.setToolTip("Renders a piece of the selected render's script with a few Processes x Threads x Nuke cache setups\n"
                               "and recommends the fastest for it on this PC. Nothing changes unless you take it.")
        self.b_test.clicked.connect(lambda: ScriptTest(self, self.engine).show())
        row(procs, "Test", [self.b_test])
        self.b_analyze = QtWidgets.QPushButton("Analyze script…")
        self.b_analyze.setToolTip("Opens the selected render's script without rendering, goes through the nodes that feed its Write\n"
                                  "and recommends Processes, Threads and Nuke cache to start from. Test this script measures them.")
        self.b_analyze.clicked.connect(lambda: ScriptAnalysis(self, self.engine).show())
        row(procs, "Analyze", [self.b_analyze])
        self.b_measure = QtWidgets.QPushButton("Measure nodes…")
        self.b_measure.setToolTip("Renders a few frames in one Nuke to a test folder and shows how long each node takes, which ones have GPU on,\n"
                                  "and the GPU use, VRAM peak and RAM peak of the whole test.\n"
                                  "Nothing else should render. Nothing is saved or changed.")
        self.b_measure.clicked.connect(self._measure_nodes)
        row(procs, "Measure", [self.b_measure])
        self.l_ram_need = note(procs, QtWidgets.QLabel(""))
        # Auto | Fixed as two joined buttons; checkable, so they read like radio buttons in the code
        self.r_chunk_auto = QtWidgets.QPushButton("Auto")
        self.r_chunk_auto.setToolTip("About 4 chunks per Nuke process, at most 10 frames each:\n"
                                     "46 frames with 8 processes -> 2, 96 -> 3, 320 and more -> 10.")
        self.r_chunk_set = QtWidgets.QPushButton("Fixed")
        self.r_chunk_set.setToolTip("The same chunk size for every render, set on the right")
        group = QtWidgets.QButtonGroup(w)
        group.setExclusive(True)
        for i, b in enumerate((self.r_chunk_auto, self.r_chunk_set)):
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            group.addButton(b)
            near, far = ("left", "right") if i == 0 else ("right", "left")
            b.setStyleSheet(
                "QPushButton { background: %s; border: 1px solid %s; padding: 3px 12px; min-width: 34px;"
                " border-top-%s-radius: 3px; border-bottom-%s-radius: 3px;"
                " border-top-%s-radius: 0px; border-bottom-%s-radius: 0px; }"
                "QPushButton:checked { background: %s; color: #111111; }"
                "QPushButton:disabled { color: #666666; }"
                % (C["panel2"], C["line"], near, near, far, far, C["accent"]))
        self._chunk_group = group
        auto = bool(self.settings.get("chunk_auto", True))
        self.r_chunk_auto.setChecked(auto)
        self.r_chunk_set.setChecked(not auto)
        self.s_chunk.setToolTip("Frames per chunk for every render. Applies to renders that are not running.")
        seg_w = QtWidgets.QWidget()
        seg_w.setStyleSheet("background: transparent;")
        seg = QtWidgets.QHBoxLayout(seg_w)
        seg.setContentsMargins(0, 0, 0, 0)
        seg.setSpacing(0)
        seg.addWidget(self.r_chunk_auto)
        seg.addWidget(self.r_chunk_set)
        row(procs, "Chunk", [seg_w, self.s_chunk])
        self.r_chunk_auto.toggled.connect(self._chunk_mode_changed)
        self.c_chunk_auto = toggle("auto_chunk", True)
        row(procs, "Smaller chunks at the end", [self.c_chunk_auto],
            "Chunks get smaller towards the end of a render, down to single frames,\n"
            "so every Nuke process finishes at about the same time.\n"
            "Off: every chunk keeps the size set under Chunk.")
        note(procs, "Every process finishes at about the same time.")
        self._chunk_mode_changed(self.r_chunk_auto.isChecked(), save=False)
        return w

    def _chunk_mode_changed(self, auto, save=True):
        if save:
            self._save_setting("chunk_auto", auto)
            if not auto:
                self._save_setting("chunk_size", self.s_chunk.value())
        self._apply_chunks()

    def _apply_chunks(self):
        for e in self.manager.engines:
            e.apply_chunk_setting()
        self._show_chunk()
        self.frame_map.update()

    def _show_chunk(self):
        """The chunk box shows the size of the picked render; it can only be typed into with the size set by hand."""
        self.s_chunk.blockSignals(True)
        self.s_chunk.setValue(self.engine.chunk_size)
        self.s_chunk.blockSignals(False)
        need = self.engine.ram_need()
        self.l_ram_need.setText("this script took %.1f GB per process last time" % need if need else "")
        self._sync_controls()

    def _tables(self):
        # A new name when a table gets a column: restoreState takes an older layout whole, with its widths on the wrong columns
        return {"system": self.worker_table, "chunks": self.chunk_table, "frames_checks": self.frames_view}

    def _restore_headers(self):
        """Column widths and order of every table as they were when the window last closed."""
        for name, table in self._tables().items():
            head = table.horizontalHeader()
            self._header_defaults.setdefault(name, head.saveState())
            saved = self.settings.get("headers", {}).get(name)
            if saved:
                built = head.saveState()
                head.restoreState(QtCore.QByteArray.fromBase64(saved.encode("ascii")))
                if head.count() != table.model().columnCount():  # saved before a column was added or removed
                    head.restoreState(built)
                    continue
                if name == "chunks":
                    self._chunk_cols_sized = True  # keep the saved widths instead of fitting them to the first chunks

    def _save_headers(self):
        self._save_setting("headers", {name: bytes(t.horizontalHeader().saveState().toBase64()).decode("ascii")
                                       for name, t in self._tables().items()})

    def _reset_headers(self):
        for name, table in self._tables().items():
            table.horizontalHeader().restoreState(self._header_defaults[name])
        self._chunk_cols_sized = False
        for col in range(1, self.worker_table.columnCount()):
            self.worker_table.setColumnHidden(col, self.worker_table.horizontalHeaderItem(col).text() in SYSTEM_HIDDEN)

    def _reset_settings(self):
        if (QtWidgets.QMessageBox.question(self, "Reset to defaults",
                                          "Put every setting back to how JKrender comes?\n"
                                          "The columns of every table go back to their widths and order too.")
                != QtWidgets.QMessageBox.Yes):
            return
        plan = auto_plan(10 ** 6, False)
        learned = {k: self.settings[k] for k in ("script_ram",) if k in self.settings}
        self.settings.clear()
        self.settings.update(DEFAULT_SETTINGS)
        self.settings.update(learned)
        write_settings(self.settings)
        for box, value in ((self.s_workers, plan["workers"]), (self.s_threads, plan["threads"]), (self.s_ram, plan["ram"]),
                           (self.s_max, MAX_RENDERS), (self.s_slate, 1000), (self.s_limit, 90), (self.s_days, 7),
                           (self.s_vram, 95)):
            box.setValue(value)
        for box, on in ((self.c_auto, False), (self.c_next, False), (self.c_resume, False), (self.c_frames, True), (self.c_pixels, True), (self.c_open, True), (self.c_slate, True), (self.c_all_workers, True), (self.c_dim, True), (self.c_hide_done, True), (self.c_clean, True),
                        (self.c_gpu, True), (self.c_guard, True), (self.c_vram, True), (self.c_chunk_auto, True),
                        (self.r_chunk_auto, True)):
            box.setChecked(on)
        self._apply_chunks()
        self._reset_headers()
        self.settings.pop("system_hidden", None)
        self.settings.pop("system_order", None)
        write_settings(self.settings)
        self.engine.note("Settings reset to defaults", "ok")

    def _guard_changed(self, on):
        self._save_setting("ram_guard", on)
        self.s_limit.setEnabled(on)

    def _dim_changed(self, on):
        self._save_setting("dim_others", on)
        self.gantt.dim_others = on
        self.gantt.update()

    def _hide_done_changed(self, on):
        self._save_setting("hide_old_done", on)
        self.gantt.hide_old_done = on
        self.gantt.update()

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
                e.done_if_on_disk()
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
        lay = QtWidgets.QVBoxLayout(w)  # list above, preview below
        lay.setContentsMargins(8, 8, 8, 8)
        left = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        for text, fn in (("Check video", self._movie_check), ("Open folder", self._open_folder),
                         ("Delete video", lambda: self._delete_all())):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, fn=fn: fn())  # clicked(checked) must not reach fn
            bar.addWidget(b)
        self.b_delete_movie = b
        self.b_delete_movie.setToolTip("Delete the rendered video file from disk")
        bar.addStretch(1)
        left.addLayout(bar)
        self.movie_view = QtWidgets.QTextBrowser()
        left.addWidget(self.movie_view, 1)
        lay.addLayout(left, 3)
        right = QtWidgets.QVBoxLayout()
        self.movie_box = QtWidgets.QWidget()  # preview, bar and frame number: moves to the big view and back
        mbox = QtWidgets.QVBoxLayout(self.movie_box)
        mbox.setContentsMargins(0, 0, 0, 0)
        mbox.setSpacing(6)
        right.addWidget(self.movie_box, 1)
        self._movie_home = right
        self.movie_preview = ZoomView()
        self.movie_preview.setToolTip("Mouse wheel zooms, drag moves, double-click fits the frame again")
        mbox.addWidget(self.movie_preview, 1)
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
        self.b_big_movie = QtWidgets.QToolButton()
        self.b_big_movie.setFixedSize(34, 30)
        self.b_big_movie.setIconSize(QtCore.QSize(20, 20))
        self.b_big_movie.setIcon(nav_icon("big"))
        self.b_big_movie.setToolTip("Player as big as the three panes (Esc goes back)")
        self.b_big_movie.setStyleSheet(self.b_play.styleSheet())
        self.b_big_movie.clicked.connect(self._toggle_big)
        nav.addWidget(self.b_big_movie)
        mbox.addLayout(nav)
        self.movie_frame_label = QtWidgets.QLabel("")
        self.movie_frame_label.setObjectName("dim")
        mbox.addWidget(self.movie_frame_label)
        lay.insertLayout(0, right, 2)  # preview on top, the list under it
        self._movie_size_last = None
        return w

    def _frames_page(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)  # list above, preview below
        lay.setContentsMargins(8, 8, 8, 8)
        left = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        self.out_filter = QtWidgets.QComboBox()
        self.out_filter.addItems(["All", "Rendered", "Problems"])
        self.out_filter.currentTextChanged.connect(self._out_filter)
        bar.addWidget(self.out_filter)
        for text, fn in (("Check frames", self._verify), ("Check pixels", self._check_pixels), ("Open folder", self._open_folder),
                         ("Delete", self._delete_dialog)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
            if text == "Check frames":
                b.setToolTip("Looks at the files of all frames again: there, not empty, whole and readable, and not far smaller than the rest.")
            if text == "Check pixels":
                b.setToolTip("Looks at every pixel of every rendered frame, in the background: NaN and inf, black, empty or one-colour frames,\n"
                             "frames exactly the same as another anywhere in the sequence, and a size, channels, bit depth or compression unlike the rest.")
        self.b_delete = b
        self.b_delete.setToolTip("Delete rendered frames of this render from disk: a frame range, or all of them")
        bar.addStretch(1)
        left.addLayout(bar)
        self.out_summary = QtWidgets.QLabel("")
        self.out_summary.setObjectName("dim")
        left.addWidget(self.out_summary)
        self.pixels_bar = QtWidgets.QProgressBar()
        self.pixels_bar.setTextVisible(False)
        self.pixels_bar.setMaximumHeight(6)
        self.pixels_bar.hide()
        left.addWidget(self.pixels_bar)
        self.frames_model = FramesModel(self.engine)
        self.frames_view = QtWidgets.QTableView()
        self.frames_view.setModel(self.frames_model)
        self.frames_view.verticalHeader().hide()
        self.frames_view.verticalHeader().setDefaultSectionSize(20)
        self.frames_view.setAlternatingRowColors(True)
        self.frames_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.frames_view.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((60, 66, 54, 64, 64, 70, 110)):  # narrow, so the checks (last) fit in the pane
            self.frames_view.setColumnWidth(col, width)
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
        self.preview.installEventFilter(self)  # double-click: big preview
        self.preview.setToolTip("Double-click to make the preview as big as the three panes (Esc goes back)")
        # the preview, its frame bar and the frame info in one box, so the box can move to the big view and back
        self.preview_box = QtWidgets.QWidget()
        box = QtWidgets.QVBoxLayout(self.preview_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        box.addWidget(self.preview, 1)
        box.addLayout(self._frame_bar())
        box.addWidget(self.preview_info)
        right.addWidget(self.preview_box, 1)
        self._preview_home = right
        lay.insertLayout(0, right, 2)  # preview on top, the list under it
        return w

    def _frame_bar(self):
        """Previous / Play / next, a slider over every frame of the render, the frame number, and Enlarge."""
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(4)

        def tool(icon, tip, slot):
            b = QtWidgets.QToolButton()
            b.setFixedSize(30, 26)
            b.setIconSize(QtCore.QSize(18, 18))
            b.setIcon(icon)
            b.setToolTip(tip)
            b.setStyleSheet("QToolButton { background: %s; border: 1px solid %s; border-radius: 3px; }"
                            "QToolButton:hover { border-color: %s; }" % (C["panel2"], C["line"], C["accent"]))
            b.clicked.connect(slot)
            bar.addWidget(b)
            return b

        tool(nav_icon("prev"), "Previous frame", lambda: self._step_frame(-1))
        self.b_frame_play = tool(play_icon(False), "Play / Pause the rendered frames (loops)", self._frame_play_toggle)
        tool(nav_icon("next"), "Next frame", lambda: self._step_frame(1))
        self.frame_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.valueChanged.connect(self._frame_slider_moved)
        self.frame_slider.sliderPressed.connect(self._frame_play_stop)
        bar.addWidget(self.frame_slider, 1)
        self.frame_label = QtWidgets.QLabel("")
        self.frame_label.setMinimumWidth(56)
        self.frame_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        bar.addWidget(self.frame_label)
        self.b_big = tool(nav_icon("big"), "Preview as big as the three panes (Esc goes back)", self._toggle_big)
        self._frame_timer = QtCore.QTimer(self)
        self._frame_timer.timeout.connect(self._frame_play_step)
        self._pix_cache = collections.OrderedDict()  # (frame, size, mtime) -> QPixmap: a second loop plays smoothly
        self._big = False
        esc = QtGui.QShortcut(QtGui.QKeySequence("Esc"), self)
        esc.activated.connect(lambda: self._big and self._toggle_big())
        return bar

    def _frame_order(self):
        return self.engine.frame_list()

    def _sync_frame_bar(self, frame):
        frames = self._frame_order()
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, max(0, len(frames) - 1))
        if frame in frames:
            self.frame_slider.setValue(frames.index(frame))
        self.frame_slider.blockSignals(False)
        self.frame_label.setText(str(frame) if frame is not None else "")

    def _frame_slider_moved(self, i):
        frames = self._frame_order()
        if 0 <= i < len(frames):
            self._pick_frame(frames[i])

    def _step_frame(self, step):
        self._frame_play_stop()
        frames = self._frame_order()
        if not frames:
            return
        cur = frames.index(self._preview_frame) if self._preview_frame in frames else -step
        self._pick_frame(frames[max(0, min(len(frames) - 1, cur + step))])

    def _frame_playable(self, f):
        fr = self.engine.frames.get(f)
        return fr is not None and fr.state in ("done", "skipped", "bad")

    def _frame_play_toggle(self):
        if self._frame_timer.isActive():
            self._frame_play_stop()
            return
        fps = float(self.engine.job.get("fps") or 24) or 24.0
        self._frame_timer.start(max(15, int(1000 / fps)))
        self.b_frame_play.setIcon(play_icon(True))

    def _frame_play_stop(self):
        if self._frame_timer.isActive():
            self._frame_timer.stop()
            self.b_frame_play.setIcon(play_icon(False))

    def _frame_play_step(self):
        """Next rendered frame, looping; frames not on disk yet are skipped."""
        frames = [f for f in self._frame_order() if self._frame_playable(f)]
        if not frames:
            self._frame_play_stop()
            return
        later = [f for f in frames if self._preview_frame is None or f > self._preview_frame]
        self._pick_frame(later[0] if later else frames[0])

    def _toggle_big(self):
        """The preview (or the movie player), with its bar, as big as the three panes; again, Esc or a
        double-click on a frame preview goes back."""
        self._big = not self._big
        if self._big:
            movie = bool(self.engine.movie)
            self._big_parts = (self.movie_box, self._movie_home) if movie else (self.preview_box, self._preview_home)
            self.big_lay.addWidget(self._big_parts[0], 1)
            self.main_split.hide()
            self.big_box.show()
        else:
            box, home = self._big_parts
            home.insertWidget(0, box, 1)
            self.big_box.hide()
            self.main_split.show()
        for b in (self.b_big, self.b_big_movie):
            b.setIcon(nav_icon("small" if self._big else "big"))
        if self._preview_frame is not None and not self.engine.movie:
            QtCore.QTimer.singleShot(0, lambda: self._show_frame(self._preview_frame))

    def eventFilter(self, obj, event):
        if obj is getattr(self, "preview", None) and event.type() == QtCore.QEvent.MouseButtonDblClick:
            self._toggle_big()
            return True
        if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease) and event.key() == QtCore.Qt.Key_Space \
                and isinstance(obj, QtWidgets.QWidget):
            # Space plays and stops the preview. It never presses a button: after a click on Stop, Qt would press Stop again
            if obj.window() is self and not isinstance(obj, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox)):
                if event.type() == QtCore.QEvent.KeyPress and not event.isAutoRepeat():
                    button = self.b_play if self.engine.movie else self.b_frame_play
                    if button.isVisible():
                        button.click()
                return True
            if isinstance(obj, QtWidgets.QAbstractButton):
                return True
        if event.type() == QtCore.QEvent.MouseButtonPress and len(getattr(self, "picked", ())) > 1 \
                and isinstance(obj, QtWidgets.QWidget) and obj.window() is self:
            # a click anywhere but on a render card (or Render / Pause / Stop) drops the cards picked with Shift
            w = obj
            while w is not None:
                if isinstance(w, RenderCard) or w in (self.b_start, self.b_pause, self.b_stop):
                    break
                w = w.parentWidget()
            else:
                self.picked = set()
                self._refresh_cards()
                self._sync_controls()
        return super().eventFilter(obj, event)

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
                                     ("gpu", "GPU", C["done"]), ("vram", "VRAM", C["vram"])], max_value=100)
        self.g_load.setToolTip("CPU, RAM, GPU and VRAM use in %.")
        self.g_io = Graph("Disk / network", [("dw", "write", C["accent"]), ("dr", "read", C["ram"]),
                                             ("net", "net", C["done"])], unit="MB/s")
        graphs.addWidget(self.g_load, 1)
        graphs.addWidget(self.g_io, 1)
        lay.addLayout(graphs)
        self.cores = CoreGrid()
        lay.addWidget(self.cores)
        self.worker_table = QtWidgets.QTableWidget(0, 17)
        self.worker_table.setHorizontalHeaderLabels(
            ["Process", "Render", "PID", "State", "Chunk", "Frame", "CPU used", "RAM", "Cache", "GPU", "Frames", "Avg frame",
             "Script load", "Uptime", "Peak RAM", "Chunks", "Last frame"])
        self.worker_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.worker_table.customContextMenuRequested.connect(self._worker_table_menu)
        self._worker_rows = []
        self.worker_table.verticalHeader().hide()
        self.worker_table.setAlternatingRowColors(True)
        self.worker_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.worker_table.horizontalHeader().setStretchLastSection(True)
        self.worker_table.setColumnWidth(1, 240)  # Render: wide enough for the script name
        head = self.worker_table.horizontalHeader()
        head.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        head.customContextMenuRequested.connect(lambda pos: self._columns_menu(head.mapToGlobal(pos)))
        head.sectionClicked.connect(lambda _: self._columns_menu(QtGui.QCursor.pos()))
        self._header_defaults = {"system": head.saveState()}
        hidden = set(self.settings.get("system_hidden", SYSTEM_HIDDEN))
        for col in range(1, self.worker_table.columnCount()):
            self.worker_table.setColumnHidden(col, self.worker_table.horizontalHeaderItem(col).text() in hidden)
        names = [self.worker_table.horizontalHeaderItem(c).text() for c in range(self.worker_table.columnCount())]
        for i, name in enumerate(n for n in self.settings.get("system_order", []) if n in names):
            head.moveSection(head.visualIndex(names.index(name)), i)
        head.setSectionsMovable(True)  # drag a column by its header, or use the arrows in the header menu
        head.sectionMoved.connect(lambda *_: self._save_column_order())
        lay.addWidget(self.worker_table, 1)
        return w

    def _save_column_order(self):
        t = self.worker_table
        head = t.horizontalHeader()
        self._save_setting("system_order", [t.horizontalHeaderItem(head.logicalIndex(v)).text()
                                            for v in range(t.columnCount())])

    def _columns_menu(self, global_pos):
        """A click on the System table's header: tick the columns to show, arrows to change their order.
        Process always shows."""
        t = self.worker_table
        head = t.horizontalHeader()
        menu = QtWidgets.QMenu(self)
        menu.addSection("Columns")
        panel = QtWidgets.QWidget()  # one widget for every row, so the menu stays open while ticking and moving
        rows = QtWidgets.QVBoxLayout(panel)
        rows.setContentsMargins(6, 2, 6, 2)
        rows.setSpacing(0)
        order = [c for c in sorted(range(t.columnCount()), key=head.visualIndex) if c != 0]

        def move(col, step):
            i = order.index(col)
            j = i + step
            if not 0 <= j < len(order):
                return
            head.moveSection(head.visualIndex(col), head.visualIndex(order[j]))  # saves via sectionMoved
            order[i], order[j] = order[j], order[i]
            row = rows.itemAt(i).widget()
            rows.removeWidget(row)
            rows.insertWidget(j, row)

        for col in order:
            row = QtWidgets.QWidget()
            line = QtWidgets.QHBoxLayout(row)
            line.setContentsMargins(8, 0, 0, 0)
            box = QtWidgets.QCheckBox(t.horizontalHeaderItem(col).text())
            box.setChecked(not t.isColumnHidden(col))
            box.setStyleSheet("QCheckBox { padding: 4px 6px; }")
            box.toggled.connect(lambda on, c=col: self._column_toggled(c, on))
            line.addWidget(box, 1)
            for arrow, step, tip in (("▲", -1, "Move left"), ("▼", 1, "Move right")):
                b = QtWidgets.QToolButton()
                b.setText(arrow)
                b.setToolTip(tip)
                b.setAutoRaise(True)
                b.setFixedSize(22, 22)
                b.clicked.connect(lambda _=False, c=col, s=step: move(c, s))
                line.addWidget(b)
            rows.addWidget(row)
        act = QtWidgets.QWidgetAction(menu)
        act.setDefaultWidget(panel)
        menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Show all columns", lambda: [self._column_toggled(c, True) for c in range(1, t.columnCount())])
        menu.exec(global_pos)

    def _column_toggled(self, col, on):
        t = self.worker_table
        t.setColumnHidden(col, not on)
        self._save_setting("system_hidden", [t.horizontalHeaderItem(c).text() for c in range(1, t.columnCount())
                                             if t.isColumnHidden(c)])

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
    def _measure_nodes(self):
        if self.engine.movie:
            QtWidgets.QMessageBox.information(self, "JKrender", "A movie renders in one piece: Measure nodes works on image sequences.")
        elif self.manager.running():
            QtWidgets.QMessageBox.information(self, "JKrender", "A render is going: the numbers would mostly measure the two fighting "
                                              "over the PC. Start it when nothing renders.")
        else:
            NodeMeasure(self, self.engine).show()

    def _save_setting(self, key, value):
        self.settings[key] = value
        write_settings(self.settings)

    def _ask_clear_cache(self):
        """Render: an open Nuke's cache is someone's work in progress, so it goes only on a yes. The render starts either way."""
        count = open_nukes()
        if not count:
            return
        what = "the open Nuke" if count == 1 else "the %d open Nukes" % count
        ask = QtWidgets.QMessageBox.question(
            self, "JKrender", "Clear the cache of %s?\n\nRAM cache and Blink cache, so the render has the memory. "
            "The disk cache stays. The render starts either way." % what,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if ask == QtWidgets.QMessageBox.Yes:
            clear_open_nukes()

    def _start(self):
        targets = self._targets()
        if len(targets) == 1:
            starts = targets[0].state != "paused"
        else:
            starts = any(e.state in ("ready", "stopped", "failed") and not e.nothing_to_render() for e in targets)
        if starts:
            self._ask_clear_cache()
        for e in targets:
            if e.state == "paused":
                e.resume()
            elif len(targets) == 1:
                self.manager.request_start(e, everything=e.state == "done" or e.nothing_to_render())
            elif e.state in ("ready", "stopped", "failed") and not e.nothing_to_render():
                self.manager.request_start(e)  # several picked: a finished render is left as it is
        self.frames_model.refilter()
        self._sync_controls()
        self._refresh_cards()

    def _pause(self):
        for e in self._targets():
            if e.state == "rendering":
                e.pause()
        self._sync_controls()
        self._refresh_cards()

    def _stop(self):
        for e in self._targets():
            if e.state == "waiting":
                self.manager.cancel(e)
            elif e.state in ("rendering", "paused", "finishing"):
                e.stop()
                e.forget_workers()  # its processes leave the timeline and the System table
        self.gantt.update()
        self._update_system()
        self._sync_controls()
        self._refresh_cards()

    def _workers_changed(self, v):
        self.manager.set_budget(total=v)
        self._save_setting("processes", v)
        if self.settings.get("chunk_auto", True):
            self._apply_chunks()  # Auto chunk follows the number of processes

    def _budget_changed(self, key, v):
        self.manager.set_budget(**{key: v})
        self._save_setting(key, v)

    def _chunk_changed(self, v):
        if self.r_chunk_auto.isChecked():
            return
        self._save_setting("chunk_size", v)
        self._apply_chunks()

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
        for widget in (self.b_delete, self.b_delete_movie):
            widget.setEnabled(not running)
        manual = hasattr(self, "r_chunk_auto") and not self.r_chunk_auto.isChecked()
        self.s_chunk.setEnabled(manual and not running and not self.engine.movie)
        for widget in (self.s_workers, self.s_threads, self.s_ram, self.c_gpu):
            widget.setEnabled(not any_running)
        targets = self._targets()
        if len(targets) > 1:  # several renders picked with Shift: the buttons act on all of them
            startable = any(x.state in ("ready", "stopped", "failed") and not x.nothing_to_render() for x in targets)
            paused = any(x.state == "paused" for x in targets)
            self.b_start.setEnabled(startable or paused)
            self.b_start.setText("Resume" if paused and not startable else "Render")
            self.b_start.setToolTip("%d renders picked; finished ones are left as they are" % len(targets))
            self.b_pause.setEnabled(any(x.state == "rendering" for x in targets))
            self.b_stop.setEnabled(any(x.state in ("rendering", "paused", "finishing", "waiting") for x in targets))

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
                self._save_queue()
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
        done_in = ("Done in %s" % fmt_secs(e.elapsed())) if e.started else "Already rendered"
        self._sync_controls()
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
                self._banner("%s, but the movie is not usable: %s" % (done_in, "; ".join(bad)),
                             C["failed"])
            else:
                extra = ("  Check: %s." % "; ".join(warn)) if warn else ""
                self._banner("%s. %s is complete: %d frames.%s" % (
                    done_in, os.path.basename(e.movie_path()), len(e.frames), extra),
                    C["small"] if warn else C["done"])
        elif e.state == "done":
            bad = sum(1 for fr in e.frames.values() if fr.state == "bad")
            small = sum(1 for fr in e.frames.values() if fr.small)
            if bad:
                self._banner("%s, but %d frame(s) are broken on disk. Output: Problems, select them, right-click → Render again."
                             % (done_in, bad), C["failed"])
            else:
                extra = (" %d frame(s) are much smaller than the rest; worth a look." % small) if small else ""
                self._banner("%s. All %d frames are on disk and pass the check.%s"
                             % (done_in, len(e.frames), extra), C["done"])
        elif e.notice and e.state in ("rendering", "paused"):
            self._banner(e.notice[0], {"ok": C["done"], "warn": C["small"], "bad": C["failed"]}[e.notice[1]])
        elif e.state == "waiting":
            self._banner("Waiting: %d render(s) already use the PC. It starts as soon as one is on its last chunks "
                         "(Settings > Max renders at once)." % self.manager.max_renders, C["loading"])
        else:
            self.banner.hide()

    def _banner(self, text, color):
        self.banner.setText(text)
        self.banner.setStyleSheet("background: %s; color: #0e0f11; padding: 3px 10px; border-radius: 3px; font-weight: 600;" % color)
        self.banner.show()

    def _feed_vram(self):
        self.manager.vram = self._gpu
        self.manager.gpu_mem = self._gpu_procs or {}

    def _slow_tick(self):
        self._feed_vram()
        self._save_queue()
        self._refresh_cards()
        e = self.engine
        if self._preview_frame is None and not e.movie and self.side_tabs.currentWidget() is self.output_page:
            # nothing picked yet: the first frame rendered shows in the preview straight away
            first = next((f for f in e.frame_list() if e.frames[f].state == "done"
                          and e.frames[f].worker != "on disk"), None)
            if first is not None:
                self._pick_frame(first)
        if e.notice and e.state in ("rendering", "paused") and self.banner.text() != e.notice[0]:
            self._state_changed()
        c = e.counts()
        total = max(1, len(e.frames))
        finished = c["done"] + c["skipped"] + c["bad"]
        self.t_frames.set("%d / %d" % (finished, total), "%.0f %%  ·  %d rendering" % (100.0 * finished / total, c["rendering"]))
        self.t_elapsed.set(fmt_secs(e.elapsed()) if e.started else "—",
                           "started %s" % datetime.datetime.fromtimestamp(e.started).strftime("%H:%M:%S") if e.started else "")
        eta = e.eta() if e.state in ("rendering", "paused") else None
        self.t_eta.set(fmt_secs(eta) if eta is not None else "—",
                       ("done at %s" % (datetime.datetime.now() + datetime.timedelta(seconds=eta)).strftime("%H:%M"))
                       if eta else "")
        rate = e.rate()
        self.t_speed.set("%.2f fr/s" % rate if rate else "—", "%.1f fr/min" % (rate * 60) if rate else "")
        secs = e.run_secs()
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
        self.t_check_frames.title.setText("Check video" if e.movie else "Check frames")
        self.check_tiles.show_right(not e.movie)
        if e.movie:
            verdict = e.movie_verdict() if e.state not in ("rendering", "paused", "finishing") else None
            words = {"ok": ("ok", C["done"]), "warn": ("check it", C["small"]), "bad": ("broken", C["failed"])}
            text, color = words.get(verdict, ("—", C["text"]))
            self.t_check_frames.show_state(text, "one movie file", color)
            self._update_movie()
        else:
            looking = e.pending_checks > 0 and e.state not in ("rendering", "paused", "finishing")
            sub = "%d checked%s" % (len(checked), " · %d small" % small if small else "")
            if not checked and not self.settings.get("check_frames", True):
                sub = "off in Settings"
            if bad:
                self.t_check_frames.show_state("%d bad" % bad, sub, C["failed"])
            elif looking:
                self.t_check_frames.show_state("checking", sub, C["text"])
            else:
                self.t_check_frames.show_state("ok" if checked else "—", sub, C["done"] if checked else C["text"])
            read = [fr for fr in e.frames.values() if fr.pixels and "error" not in fr.pixels]
            not_read = sum(1 for fr in e.frames.values() if fr.pixels and "error" in fr.pixels)
            broken_pixels = sum(pixels_broken(fr) or fr.same_as is not None for fr in e.frames.values())
            sub = "%d checked" % len(read)
            if not read and not not_read and not self.settings.get("check_pixels", True):
                sub = "off in Settings"
            if e.pixels_pending:
                self.t_check_pixels.show_state("checking", "%d of %d" % (e.pixels_total - e.pixels_pending, e.pixels_total), C["text"])
            elif broken_pixels:
                self.t_check_pixels.show_state("%d bad" % broken_pixels, sub, C["failed"])
            elif not_read:
                self.t_check_pixels.show_state("not read", "%d frames, see the Log" % not_read, C["small"])
            else:
                self.t_check_pixels.show_state("ok" if read else "—", sub, C["done"] if read else C["text"])
        self._update_chunks()
        self._update_system()
        self.frames_model.touch()
        total_size = sum(ch[1] or 0 for fr in e.frames.values() for ch in fr.checks.values())
        missing = sum(1 for fr in checked if any(ch[0] == "missing" for ch in fr.checks.values()))
        broken = sum(1 for fr in checked if any(ch[0] in ("empty", "truncated", "unreadable") for ch in fr.checks.values()))
        pixels = ""
        if e.pixels_pending:
            pixels = " · checking pixels %d of %d" % (e.pixels_total - e.pixels_pending, e.pixels_total)
        elif any(fr.pixels and "error" in fr.pixels for fr in e.frames.values()):
            pixels = " · pixels not checked, see the Log"
        elif any(fr.pixels for fr in e.frames.values()):
            pixels = " · %d with faults in the pixels · %d same as another" % (sum(pixels_broken(fr) for fr in e.frames.values()),
                                                                              sum(fr.same_as is not None for fr in e.frames.values()))
        self.pixels_bar.setVisible(bool(e.pixels_pending))
        self.pixels_bar.setRange(0, max(1, e.pixels_total))
        self.pixels_bar.setValue(e.pixels_total - e.pixels_pending)
        self.out_summary.setText("%d frames · %d checked · %d missing · %d broken · %d suspiciously small%s · %s on disk"
                                 % (len(e.frames), len(checked), missing, broken, small, pixels, fmt_size(total_size)))
        self.gantt.update()
        if self._details_shown():
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
        # sum of every thread's load = how many threads' worth of work runs right now
        self.m_cpu.set("%.0f %%" % cpu, "%d of %d threads in use" % (round(sum(per) / 100), len(per)))
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
        vram = gpu[1] / gpu[2] * 100 if gpu and gpu[2] else None
        self.g_load.add({"cpu": cpu, "ram": vm.percent, "gpu": gpu[0] if gpu else 0, "vram": vram or 0})
        self._update_averages(now, cpu, vm.percent, gpu[0] if gpu else None, vram)
        self.g_io.add({"dw": dw, "dr": dr, "net": nmb})
        self.cores.set_values(per)
        t = self.worker_table
        # one row per process number; a process that moved on to another render shows that render now
        rows = [(wid, ews[-1]) for wid, ews in m.process_rows(shown)]
        # every place of Settings > Processes, also the ones no process has taken yet, with why not
        taken = {wid for wid, _ in rows}
        empty = [(wid, (None, wid)) for wid in range(1, m.total + 1) if wid not in taken]
        rows = [r for _, r in sorted(rows + empty, key=lambda r: r[0])]
        t.setRowCount(len(rows))
        self._worker_rows = rows
        gpus = self._gpu_procs or {}
        idle_why = self._empty_place_state() if empty else ""
        for row, (x, w) in enumerate(rows):
            if x is None:
                values = ["W%02d" % w, "", "", idle_why] + [""] * (t.columnCount() - 4)
                for col, v in enumerate(values):
                    item = t.item(row, col)
                    if item is None:
                        item = QtWidgets.QTableWidgetItem()
                        t.setItem(row, col, item)
                    item.setText(v)
                    item.setForeground(QtGui.QColor(C["dim"]))
                    item.setToolTip(v if col == 3 else "")
                continue
            avg = (sum(w.frame_secs) / len(w.frame_secs)) if w.frame_secs else None
            gone = w.state == "gone"
            gpu = gpus.get(w.proc.pid)
            values = [w.name, "%d  %s" % (x.number, x.name), str(w.proc.pid), x.worker_state(w),
                      w.chunk.name if w.chunk else "", str(w.frame) if w.frame is not None else "",
                      "" if gone else "%.1f of %d threads" % (w.cpu, w.threads), "" if gone else "%.1f GB" % w.rss,
                      "" if gone or not w.cache else "%.1f / %.0f GB" % (w.cache[0] / 1024 ** 3, w.cache[1] / 1024 ** 3),
                      "" if gone or gpu is None else "%.0f %%  ·  %.1f GB" % (gpu[0], gpu[1] / 1024 ** 3),
                      str(w.frames_done), "%.2f s" % avg if avg else "", "%.1f s" % w.load_secs if w.load_secs else "",
                      fmt_secs(self._worker_uptime(w)), "%.1f GB" % w.rss_peak if w.rss_peak else "",
                      self._worker_chunks(w), "%.2f s" % w.frame_secs[-1] if w.frame_secs else ""]
            for col, v in enumerate(values):
                item = t.item(row, col)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    t.setItem(row, col, item)
                item.setText(v)
                item.setForeground(QtGui.QColor(C["dim"] if gone or x is not self.engine else C["text"]))
                item.setToolTip("")

    def _update_averages(self, now, cpu, ram, gpu, vram):
        """Average CPU, RAM, GPU and VRAM use while rendering, in the tiles: from when renders start (a pause does
        not count) until the next ones start after all have ended."""
        states = [e.state for e in self.manager.engines]
        if not any(x in ("rendering", "paused", "finishing", "waiting") for x in states):
            self._avg_going = False
        elif "rendering" in states:
            if not getattr(self, "_avg_going", False):
                self._avg_going = True
                self._avg = {}
            if now - getattr(self, "_avg_last", 0.0) >= 1.0:  # this also runs on clicks; samples stay even
                self._avg_last = now
                for key, value in (("cpu", cpu), ("ram", ram), ("gpu", gpu), ("vram", vram)):
                    if value is not None:
                        total, n = self._avg.get(key, (0.0, 0))
                        self._avg[key] = (total + value, n + 1)
        values = {k: t / n for k, (t, n) in getattr(self, "_avg", {}).items()}
        self.m_cpu.set_avg("average %.0f %%  ·  %.1f threads" % (values["cpu"], values["cpu"] * (os.cpu_count() or 1) / 100.0)
                           if "cpu" in values else "")
        self.m_ram.set_avg("average %.0f %%" % values["ram"] if "ram" in values else "")
        self.m_gpu.set_avg("average %.0f %%  ·  VRAM %.0f %%" % (values["gpu"], values.get("vram", 0)) if "gpu" in values else "")

    def _empty_place_state(self):
        """State of a place no process has: why nothing runs there now."""
        m = self.manager
        if not any(e.state in ("rendering", "paused", "finishing") for e in m.engines):
            return "not running"
        if m.live_count() >= m.budget():
            if m.vram_cap is not None and m.vram_cap <= (m.ram_cap or m.total):
                return "not running (VRAM limit)"
            if m.ram_cap is not None:
                return "not running (RAM limit)"
            return "not running"
        why = m.no_room()
        if why:
            return "not running: %s" % why.replace("; ", " · ")
        if not any(e.state == "rendering" and queued_left_for(e) for e in m.engines):
            return "not needed (no chunks left)"
        return "starting"

    @staticmethod
    def _worker_uptime(w):
        if w.state != "gone":
            return time.time() - w.spawned
        ends = [seg[3] for seg in w.segments if seg[3]]
        return (max(ends) if ends else w.spawned) - w.spawned

    @staticmethod
    def _worker_chunks(w):
        """Chunks done, and how many it did not finish (failed, stopped, handed back)."""
        ended = [seg[4] for seg in w.segments if seg[0] == "chunk" and seg[3]]
        done = ended.count("done")
        return "%d" % done + ("  ·  %d back" % (len(ended) - done) if len(ended) > done else "")

    # ---- one process: right-click in System or on the Workers timeline -------------------
    def _worker_table_menu(self, pos):
        row = self.worker_table.rowAt(pos.y())
        if 0 <= row < len(self._worker_rows):
            e, w = self._worker_rows[row]
            if e is None:
                self._empty_place_menu(w, self.worker_table.viewport().mapToGlobal(pos))
            else:
                self._worker_menu(e, w, self.worker_table.viewport().mapToGlobal(pos))

    def _empty_place_menu(self, wid, global_pos):
        """A place no process has taken: start one there by hand, in a render still going."""
        menu = QtWidgets.QMenu(self)
        menu.addSection("W%02d" % wid)
        running = [x for x in self.manager.engines if x.state in ("rendering", "paused")]
        actions = {}
        sub = menu.addMenu("Start in")
        sub.setEnabled(bool(running))
        for x in running:
            actions[sub.addAction("%d  %s" % (x.number, x.name))] = (lambda x=x: x.user_start_here(wid))
        act = menu.exec(global_pos)
        if act in actions:
            actions[act]()
            self._update_system()

    def _worker_menu(self, e, w, global_pos):
        menu = QtWidgets.QMenu(self)
        menu.addSection("%s  (%s)" % (w.name, e.name))
        actions = {}
        running = [x for x in self.manager.engines if x.state in ("rendering", "paused")]
        busy_ids = {i for x in self.manager.engines for i, y in x.workers.items() if y.state != "gone"}
        if w.state == "gone" and e.state in ("rendering", "paused"):
            act = menu.addAction("Start again")
            act.setEnabled(w.id not in busy_ids)
            actions[act] = lambda: e.user_start(w.id)
        elif w.state == "gone":
            # its render is through: it starts again in one still going
            if len(running) == 1:
                act = menu.addAction("Start again in %d  %s" % (running[0].number, running[0].name))
                act.setEnabled(w.id not in busy_ids)
                actions[act] = lambda x=running[0]: x.user_start_here(w.id, e)
            else:
                sub = menu.addMenu("Start again in")
                sub.setEnabled(bool(running) and w.id not in busy_ids)
                for x in running:
                    actions[sub.addAction("%d  %s" % (x.number, x.name))] = (lambda x=x: x.user_start_here(w.id, e))
        else:
            if w.user_paused:
                actions[menu.addAction("Resume")] = lambda: e.user_pause(w, False)
            else:
                act = menu.addAction("Pause")
                act.setEnabled(w.state in ("loading", "idle", "busy") and e.state == "rendering")
                actions[act] = lambda: e.user_pause(w, True)
            actions[menu.addAction("Close")] = lambda: e.user_close(w)
        others = [x for x in self.manager.engines if x is not e and x.state in ("rendering", "paused")]
        if others and w.state != "gone":
            sub = menu.addMenu("Work on")
            for x in others:
                act = sub.addAction("%d  %s" % (x.number, x.name))
                actions[act] = (lambda x=x: self.manager.move_worker(e, w, x))
        menu.addSeparator()
        actions[menu.addAction("Show process log")] = lambda: self._open_path(w.log_path)
        act = menu.exec(global_pos)
        if act in actions:
            actions[act]()
            self._update_system()
            self.gantt.update()

    def _gpu_loop(self):
        per_process = None
        if sys.platform.startswith("win"):
            try:
                per_process = GpuPerProcess()
            except Exception:
                per_process = None
        while True:
            if shutil.which("nvidia-smi"):
                try:
                    out = subprocess.run(
                        ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=5, **hidden()).stdout
                    vals = [float(x) for x in out.strip().splitlines()[0].split(",")]
                    self._gpu = vals
                except Exception:
                    self._gpu = None
            if per_process:
                try:
                    self._gpu_procs = per_process.sample()
                except Exception:
                    self._gpu_procs = {}
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
        menu.addSeparator()
        delete = menu.addAction("Delete frames from chunk" if len(chunks) == 1 else
                                "Delete frames from %d chunks" % len(chunks))
        delete.setEnabled(self.engine.state not in ("rendering", "paused", "finishing", "waiting"))
        act = menu.exec(self.chunk_table.viewport().mapToGlobal(pos))
        if act == again:
            for c in chunks:
                self.engine.requeue_chunk(c)
            self._after_requeue()
        elif act == delete:
            self._delete_all(sorted({f for c in chunks for f in c.all_frames}))  # asks first
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
        one = len(frames) == 1
        again = menu.addAction("Render this frame" if one else "Render this batch (%d frames)" % len(frames))
        delete = menu.addAction("Delete this frame" if one else "Delete this batch (%d frames)" % len(frames))
        e = self.engine
        again.setEnabled(e.state not in ("finishing", "waiting"))
        delete.setEnabled(e.state not in ("rendering", "paused", "finishing", "waiting"))
        act = menu.exec(self.frames_view.viewport().mapToGlobal(pos))
        if act == again:
            # only these frames, unless the whole render is running anyway: then they just join it
            e.rerender(frames, only=True)
            self._after_requeue()
        elif act == delete:
            self._delete_all(frames)  # asks first

    def _after_requeue(self):
        self.frames_model.refilter()
        self._sync_controls()
        self._slow_tick()

    def _verify(self):
        self.engine.verify_all()

    def _check_pixels(self):
        self.engine.check_all_pixels()

    def _out_filter(self, text):
        self.frames_model.mode = text
        self.frames_model.refilter()

    def _show_first(self, e, frame):
        """Preview a render's first rendered frame without switching away from the tab that is open."""
        if self.engine is not e or self._preview_frame is not None:
            return
        self._quiet_pick = True
        try:
            self._pick_frame(frame)
        finally:
            self._quiet_pick = False

    def _pick_frame(self, frame):
        """A click on the chunk map: the frame is selected in the Output list, which scrolls to it, and previewed."""
        rows = self.frames_model.rows
        if frame in rows:
            idx = self.frames_model.index(rows.index(frame), 0)
            self.frames_view.selectionModel().setCurrentIndex(
                idx, QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
            self.frames_view.scrollTo(idx, QtWidgets.QAbstractItemView.PositionAtCenter)
            if self._preview_frame != frame:  # setCurrentIndex shows it, unless it was the current row already
                self._show_frame(frame)
        else:  # not in the list with the filter set now
            self._show_frame(frame)

    def _show_frame(self, frame):
        e = self.engine
        fr = e.frames.get(frame)
        if fr is None:
            return
        self._preview_frame = frame
        self._sync_frame_bar(frame)
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
            size = (max(200, self.preview.width()), max(150, self.preview.height()))
            key = (frame, size, os.path.getmtime(target))
            pix = self._pix_cache.get(key)
            if pix is None:
                img = preview_image(target, *size)
                pix = QtGui.QPixmap.fromImage(img).scaled(
                    self.preview.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                self._pix_cache[key] = pix
                # about 300 MB of preview pictures at most
                while len(self._pix_cache) > max(20, int(300e6 / max(1, size[0] * size[1] * 4))):
                    self._pix_cache.popitem(last=False)
            self.preview.setPixmap(pix)
        except Exception as exc:
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText("No preview: %s" % exc)
        if self.side_tabs.currentWidget() is not self.output_page and not getattr(self, "_quiet_pick", False):
            self.side_tabs.setCurrentWidget(self.output_page)

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

    def _delete_dialog(self):
        """Delete: all frames to start with, or a frame range (the frames selected in the list filled in)."""
        e = self.engine
        if e.state in ("rendering", "paused", "finishing"):
            return
        picked = sorted({self.frames_model.rows[i.row()] for i in self.frames_view.selectedIndexes()})
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Delete rendered frames")
        lay = QtWidgets.QVBoxLayout(d)
        r_range = QtWidgets.QRadioButton("Frames")
        r_all = QtWidgets.QRadioButton("All frames  (%s)" % e.range_text)
        edit = QtWidgets.QLineEdit(frames_to_spec(picked) if picked else "")
        edit.setPlaceholderText("e.g. 1001-1010, 1050")
        edit.textEdited.connect(lambda _: r_range.setChecked(True))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(r_range)
        row.addWidget(edit, 1)
        lay.addLayout(row)
        lay.addWidget(r_all)
        r_all.setChecked(True)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Delete…")
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        lay.addWidget(buttons)
        d.resize(380, d.sizeHint().height())
        if d.exec() != QtWidgets.QDialog.Accepted:
            return
        if r_all.isChecked():
            self._delete_all()
            return
        frames = sorted(set(parse_frames(edit.text())) & set(e.frames))
        if not frames:
            QtWidgets.QMessageBox.information(self, "JKrender", "None of those frames is in this render (%s)." % e.range_text)
            return
        self._delete_all(frames)

    def _delete_all(self, frames=None):
        """Delete the output files of these frames (all of the render's when None), after asking."""
        e = self.engine
        if e.state in ("rendering", "paused", "finishing"):
            return
        frames = e.frame_list() if frames is None else frames
        files = sorted({p for f in frames for p in e.output_files(f) if os.path.isfile(p)})
        if not files:
            QtWidgets.QMessageBox.information(self, "JKrender", "There are no rendered frames on disk.")
            return
        size = sum(os.path.getsize(p) for p in files)
        folders = sorted({os.path.dirname(p) for p in files})
        ask = QtWidgets.QMessageBox.warning(
            self, "Delete rendered frames",
            "Delete %d file(s), %s, from disk?\n\n%s\n\nFrames %s. This cannot be undone."
            % (len(files), fmt_size(size), "\n".join(folders), frames_to_spec(frames)),
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
        e.done_if_on_disk()
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
                      row("Frames rendering at once", "×%.1f on average" % (sum(e.run_secs()) / max(1.0, e.elapsed()))),
                      row("Script load per process", "%.1f s average" % (sum(loads) / len(loads)) if loads else "—")]
        else:
            parts.append(row("", "No frames rendered yet"))
        parts.append("</table><h3>Command of one process</h3><p style='font-family:Consolas'>%s</p>"
                     % esc(subprocess.list2cmdline(e._command(gpu=bool(self.settings.get("use_gpu", True))))))
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
        for dialog in self.findChildren(QtWidgets.QDialog):  # a test still running has Nukes and frames of its own
            if isinstance(dialog, (ScriptTest, ScriptAnalysis, NodeMeasure)):
                dialog.close()
        for e in running:
            if e.state == "waiting":
                self.manager.cancel(e)
            else:
                e.stop("Stopped: window closed")
        for e in self.manager.engines:
            e.write_report()
        self._save_headers()  # every table's column widths and order, for the next window
        self.manager.shutdown()
        try:
            QUEUE_FILE.unlink()  # closed properly: nothing to bring back
        except OSError:
            pass
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
    alone = "--exit-when-done" in sys.argv  # scripted runs keep to themselves
    if not alone:
        # One render per Write: hand each to the JKrender already open; whatever is left opens a window here.
        if not args:  # Reopen Renders: nothing to do if a window is open
            try:
                socket.create_connection(("127.0.0.1", APP_PORT), timeout=1.0).close()
                return
            except OSError:
                pass
        else:
            sent = len(args)
            args = [job for job in args if not hand_to_running(job)]
            if sent and not args:
                return
    saved = read_queue() if not alone else None
    restored = []
    if saved:
        end_leftovers(saved.get("pids") or [])
        restored = [r for r in saved.get("renders") or [] if Path(r.get("job") or "").is_file()]
    if not args and not restored:
        raise SystemExit("Usage: JKrender.py <job.json> [<job.json> ...]   (the JKrender menu in Nuke creates them)")
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
    win = MainWindow(restored[0]["job"] if restored else args[0], exit_when_done=alone, restoring=bool(restored))
    going = []
    for i, saved_card in enumerate(restored):
        if i:
            win.add_job(saved_card["job"], auto=False)
        if win.restore(win.engine, saved_card):
            going.append(win.engine)
    for job in args[0 if restored else 1:]:
        win.add_job(job)
    missing = len(saved.get("renders") or []) - len(restored) if saved else 0
    if missing:
        win.engine.note("!! %d render(s) from before the crash could not come back: their folders are gone" % missing, "err")
    win.select(win.manager.engines[0])
    going = [e for e in going if e in win.manager.engines]
    if going and win.settings.get("resume_after_crash", False):
        for e in going:
            win.manager.request_start(e)
    if not alone:
        win.listen()
    win.show()
    code = app.exec()
    win.manager.shutdown()
    sys.exit(code)


if __name__ == "__main__":
    main()
