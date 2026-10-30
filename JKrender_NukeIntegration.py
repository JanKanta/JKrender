"""Nuke-side companion for JKrender.

JKrender > Render Selected Write(s) saves a snapshot of the script, writes a job file and opens a
JKrender window for that render. The window and its Nuke processes are separate from this Nuke,
so you keep working (and saving) while it renders.

menu.py:
    import JKrender_NukeIntegration as _jk
    _jk.install_menu()
"""

import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import nuke

NUKE_DIR = Path.home() / ".nuke"
RENDERS_DIR = Path.home() / ".jkrender" / "renders"
CONFIG_PATH = NUKE_DIR / "jkrender_path.txt"  # location of JKrender.py
PYTHON_CACHE = NUKE_DIR / "jkrender_python.txt"  # external Python that has PySide6
DEFAULT_APP = Path.home() / "JKrender" / "JKrender.py"
WRITE_CLASSES = ("Write", "Write2", "DeepWrite")
LISTEN_PORTS = range(54340, 54350)
HOST = "127.0.0.1"


# ----------------------------------------------------------------------------
# What to render
# ----------------------------------------------------------------------------
def _is_disabled(node):
    try:
        return bool(node["disable"].value())
    except Exception:
        return False


def _write_nodes():
    nodes = nuke.selectedNodes()
    # A selected Group stands for the Writes inside it.
    for group in [n for n in nodes if isinstance(n, nuke.Group)]:
        try:
            nodes += nuke.allNodes(group=group, recurseGroups=True)
        except Exception:
            pass
    seen = set()
    writes = []
    for n in nodes:
        if n.Class() in WRITE_CLASSES and n.fullName() not in seen:
            seen.add(n.fullName())
            writes.append(n)
    disabled = [n for n in writes if _is_disabled(n)]
    writes = [n for n in writes if not _is_disabled(n)]
    if not writes:
        if disabled:
            nuke.message("The Write node(s) are disabled:\n\n" + "\n".join(n.fullName() for n in disabled))
        else:
            nuke.message("Select a Write node first.")
    return writes


def _script_path():
    path = nuke.root().name()
    if not path or path == "Root" or not path.lower().endswith(".nk"):
        nuke.message("Save the Nuke script before rendering it with JKrender.")
        return None
    try:
        modified = bool(nuke.root().modified())
    except Exception:
        modified = False
    if modified:
        if not nuke.ask("The script has unsaved changes.\n\nSave it and render?\n\n(JKrender renders a copy of the saved script.)"):
            return None
        try:
            nuke.scriptSave()
        except Exception as exc:
            nuke.message("Could not save the script:\n%s" % exc)
            return None
    return nuke.root().name()


def _knob(node, name):
    try:
        k = node.knob(name)
        return k.value() if k is not None else ""
    except Exception:
        return ""


def _write_info(node):
    try:
        output = nuke.filename(node) or node["file"].value()
    except Exception:
        output = node["file"].value()
    return {
        "name": node.fullName(),
        "output": output,
        "file_type": str(_knob(node, "file_type")),
        "datatype": str(_knob(node, "datatype")),
        "compression": str(_knob(node, "compression")),
        "channels": str(_knob(node, "channels")),
        "colorspace": str(_knob(node, "colorspace") or _knob(node, "ocioColorspace")),
        "views": list(nuke.views()) if hasattr(nuke, "views") else ["main"],
        "limit": [int(_knob(node, "first")), int(_knob(node, "last"))] if _knob(node, "use_limit") else None,
        "width": _size(node, "width"),
        "height": _size(node, "height"),
        "movie": _movie_settings(node),
    }


def _size(node, which):
    try:
        return int(node.width() if which == "width" else node.height())
    except Exception:
        return None


def _movie_settings(node):
    """What the movie writer should produce, so JKrender can check the finished file against it."""
    codec = str(_knob(node, "mov64_codec") or "")
    if not codec:
        return None
    code = codec.split("\t")[0].strip()
    if code in ("AVdn",):
        profile = _knob(node, "mov64_dnxhd_codec_profile")
    elif code in ("avc1", "h264"):
        profile = _knob(node, "mov_h264_codec_profile")
    else:
        profile = _knob(node, "mov_prores_codec_profile")
    return {
        "codec": code,
        "profile": str(profile or ""),
        "fps": _knob(node, "mov64_fps"),
        "write_timecode": bool(_knob(node, "mov64_write_timecode")),
        "container": str(_knob(node, "mov64_format") or ""),
    }


def _session_env():
    """Search paths and AYON context, so the render starts like this session. No keys or tokens."""
    env = {key: os.environ.get(key, "") for key in ("NUKE_PATH", "PYTHONPATH", "OCIO")}
    for key, value in os.environ.items():
        if key.startswith("AYON_") and not any(w in key.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            env[key] = value
    return env


def _nuke_executable():
    exe = os.path.abspath(os.sys.executable)
    return exe if "nuke" in Path(exe).name.lower() else ""


def _make_job(writes, current_frame_only=False):
    script = _script_path()
    if not script:
        return None
    root = nuke.root()
    infos = [_write_info(n) for n in writes]
    if current_frame_only:
        first = last = int(nuke.frame())
    elif all(i["limit"] for i in infos):
        first = min(i["limit"][0] for i in infos)
        last = max(i["limit"][1] for i in infos)
    else:
        first, last = int(root["first_frame"].value()), int(root["last_frame"].value())
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", "%s_%s" % (Path(script).stem, writes[0].fullName()))
    folder = RENDERS_DIR / ("%s_%s" % (stamp, label))
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / ("snapshot_" + Path(script).name)
    shutil.copy2(script, snapshot)  # you can keep working and saving; the render uses this copy
    try:
        fmt = root.format()
        format_text = "%dx%d %s" % (fmt.width(), fmt.height(), fmt.name() or "")
    except Exception:
        format_text = ""
    job = {
        "version": 1,
        "script": script,
        "snapshot": str(snapshot).replace("\\", "/"),
        "writes": infos,
        "first": first,
        "last": last,
        "nuke_exe": _nuke_executable(),
        "nuke_version": getattr(nuke, "NUKE_VERSION_STRING", ""),
        "fps": root["fps"].value(),
        "format": format_text,
        "width": infos[0].get("width"),
        "height": infos[0].get("height"),
        "env": _session_env(),
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    job_path = folder / "job.json"
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return job_path


# ----------------------------------------------------------------------------
# Launching the window (external Python with PySide6, never Nuke's own)
# ----------------------------------------------------------------------------
def _app_path():
    for value in (os.environ.get("JKRENDER", ""), _read(CONFIG_PATH), str(DEFAULT_APP)):
        if value and Path(value).is_file():
            return value
    path = nuke.getFilename("Locate JKrender.py", "*.py")
    if path and Path(path).is_file():
        try:
            CONFIG_PATH.write_text(path, encoding="utf-8")
        except Exception:
            pass
        return path
    return ""


def _read(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _python_candidates():
    candidates = []

    def add(value):
        if not value:
            return
        value = os.path.abspath(os.path.expandvars(os.path.expanduser(str(value))))
        if os.path.isfile(value) and value not in candidates and "nuke" not in Path(value).name.lower():
            candidates.append(value)

    add(os.environ.get("JKRENDER_PYTHON", "").strip())
    add(_read(PYTHON_CACHE))
    add(_read(NUKE_DIR / "batch_gartner_python.txt"))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            for exe in sorted((Path(local) / "Programs" / "Python").glob("Python*/python.exe"), reverse=True):
                add(exe)
        for exe in sorted(Path(os.environ.get("ProgramFiles", r"C:\Program Files")).glob("Python*/python.exe"), reverse=True):
            add(exe)
        names = ("python.exe", "python3.exe")
    else:
        for exe in ("/usr/local/bin/python3", "/opt/homebrew/bin/python3", "/usr/bin/python3"):
            add(exe)
        names = ("python3", "python")
    for name in names:
        exe = shutil.which(name)
        if exe and "windowsapps" not in exe.lower():
            add(exe)
    return candidates


def _clean_env():
    """Environment for the external Python without Nuke's own Python / Qt settings."""
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                "QML2_IMPORT_PATH", "PYSIDE_DESIGNER_PLUGINS"):
        env.pop(key, None)
    nuke_dir = os.path.dirname(os.path.abspath(os.sys.executable)).lower()
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep) if p and not os.path.abspath(p).lower().startswith(nuke_dir))
    return env


def _has_pyside6(python_exe, env):
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        return subprocess.run([python_exe, "-c", "import PySide6.QtWidgets, psutil"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=20, creationflags=flags, env=env).returncode == 0
    except Exception:
        return False


def _launch(app, job_paths):
    """Background thread, so Nuke never waits for the Python check. One launch carries every render."""
    env = _clean_env()
    python_exe = next((exe for exe in _python_candidates() if _has_pyside6(exe, env)), None)
    if not python_exe:
        nuke.executeInMainThread(nuke.message, args=(
            "JKrender needs an external Python with PySide6 and psutil.\n\n"
            "Install them with:  python -m pip install PySide6 psutil\n"
            "or set JKRENDER_PYTHON to that python executable.",))
        return
    try:
        PYTHON_CACHE.write_text(python_exe, encoding="utf-8")
    except Exception:
        pass
    kwargs = {"cwd": str(Path(app).parent), "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([python_exe, app] + [str(p) for p in job_paths], **kwargs)
    except Exception as exc:
        nuke.executeInMainThread(nuke.message, args=("Could not start JKrender:\n%s" % exc,))


def _render(current_frame_only=False):
    writes = _write_nodes()
    if not writes:
        return
    app = _app_path()
    if not app:
        return
    # Every Write is its own render: its own card, range, chunks and output check in JKrender.
    job_paths = []
    for node in writes:
        try:
            job_path = _make_job([node], current_frame_only)
        except Exception as exc:
            nuke.message("JKrender could not prepare %s:\n%s" % (node.fullName(), exc))
            continue
        if not job_path:
            return  # the script was not saved
        job_paths.append(job_path)
    if job_paths:
        threading.Thread(target=_launch, args=(app, job_paths), daemon=True).start()


def render_selected():
    _render(False)


def open_renders_folder():
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(RENDERS_DIR))
    else:
        subprocess.Popen(["open" if os.sys.platform == "darwin" else "xdg-open", str(RENDERS_DIR)])


# ----------------------------------------------------------------------------
# JKrender -> Nuke: "Create Read in Nuke"
# ----------------------------------------------------------------------------
_listener = None


def _norm(path):
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path or "")))
    except Exception:
        return path or ""


def _create_read(msg):
    same_script = _norm(nuke.root().name()) == _norm(msg.get("script", ""))
    if not msg.get("force") and not same_script:
        return "SKIP"
    file_path = msg.get("file") or ""
    if not file_path:
        return "ERROR: no output path"
    write = nuke.toNode(msg.get("write") or "") if same_script else None
    nuke.Undo.begin("JKrender: Create Read")
    try:
        for n in nuke.selectedNodes():
            n.setSelected(False)
        read = nuke.nodes.Read()
        read["file"].fromUserText("%s %d-%d" % (file_path, int(msg["first"]), int(msg["last"])))
        try:
            read["label"].setValue("JKrender")
        except Exception:
            pass
        if write is not None:
            read.setXYpos(write.xpos() + 150, write.ypos())
        read.setSelected(True)
    finally:
        nuke.Undo.end()
    return "OK %s" % read.name()


def _serve(server):
    while True:
        try:
            conn, _ = server.accept()
        except Exception:
            time.sleep(0.2)
            continue
        try:
            conn.settimeout(2.0)
            data = b""
            while True:
                part = conn.recv(65536)
                if not part:
                    break
                data += part
                if len(data) > 1000000:
                    raise ValueError("message too large")
            msg = json.loads(data.decode("utf-8"))
            if msg.get("command") == "create_read":
                reply = nuke.executeInMainThreadWithResult(_create_read, (msg,))
            else:
                reply = "OK " + _norm(nuke.root().name())
        except Exception as exc:
            reply = "ERROR: %s" % exc
        try:
            conn.sendall(str(reply).encode("utf-8", "replace"))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


def start_listener():
    global _listener
    if _listener is not None:
        return
    for port in LISTEN_PORTS:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            server.bind((HOST, port))
            server.listen(4)
        except OSError:
            server.close()
            continue
        _listener = server
        threading.Thread(target=_serve, args=(server,), daemon=True).start()
        return


# ----------------------------------------------------------------------------
# Menu
# ----------------------------------------------------------------------------
def install_menu():
    menu = nuke.menu("Nuke").addMenu("JKrender")
    menu.addCommand("Render Selected Write(s)", render_selected, "Ctrl+Alt+R")
    menu.addCommand("Open Renders Folder", open_renders_folder)
    if nuke.GUI:
        try:
            start_listener()
        except Exception as exc:
            print("JKrender: Create Read listener not started:", exc)
