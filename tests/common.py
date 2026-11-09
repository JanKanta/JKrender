"""What every test of JKrender shares. A test is a script run by run_tests.py with Nuke's Python (it has PySide6), offscreen,
no sound, in a home of its own under tests/_runs/<test>, so nothing touches your settings, renders or open Nukes.

    from common import *
    jk, app = start({"processes": 2})          # settings for this test's JKrender, then import it
    job = make_job("sh010", 1001, 1012)       # a render of PNG frames that FakeNuke writes
    win = jk.MainWindow(job)
    check("it opened", win.engine.state == "ready")
    finish()                                  # prints "FAILED: n | exceptions: m", the line run_tests.py reads
"""
import glob
import json
import os
import shutil
import socket
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
REPO = os.environ.get("JKRENDER_SOURCE") or os.path.dirname(HERE)  # another copy of JKrender to test, e.g. an older version
NAME = os.path.splitext(os.path.basename(sys.argv[0]))[0]
RUN = os.environ.get("JKRENDER_TEST_RUN") or (HERE + "/_runs/" + NAME)
HOME = RUN + "/home"
SETTINGS_DIR = HOME + "/.nuke/JKrender/settings"
FAKE = os.environ.get("JKRENDER_FAKE_NUKE") or (HERE + "/fakenuke/build/Nuke16.0v9/Nuke16.0.exe")
REAL_HOME = os.environ.get("JKRENDER_REAL_HOME") or os.path.expanduser("~")

failed = 0
errors = []
jk = None
app = None


def say(text):
    print(text, flush=True)


def check(label, ok, detail=""):
    """One expectation: printed as ok or FAIL, counted for the end."""
    global failed
    if not ok:
        failed += 1
    say("  %-4s %s%s" % ("ok" if ok else "FAIL", label, ("  (%s)" % (detail,)) if detail != "" and not ok else ""))
    return ok


def finish():
    say("FAILED: %d | exceptions: %d" % (failed, len(errors)))
    sys.stdout.flush()
    os._exit(0)  # Qt and the checker threads would only slow the way out


def _lib_dirs():
    """numpy / OpenEXR / psutil for Nuke's Python: the lib folder next to JKrender.py, or of the installed copy."""
    out = []
    for root in (REPO + "/lib", REAL_HOME + "/.nuke/JKrender/lib"):
        out += sorted(d for d in glob.glob(root + "/*") if os.path.isdir(d))
    return out


def start(settings=None, clean=True, qt=True):
    """This test's home with these settings, then JKrender imported (it reads its home when imported). Returns (jk, app)."""
    global jk, app
    if clean:
        shutil.rmtree(RUN, ignore_errors=True)
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    base = {"slate": False, "check_on_open": False, "use_gpu": False, "auto_start": False}
    base.update(settings or {})
    with open(SETTINGS_DIR + "/settings.json", "w", encoding="utf-8") as f:
        json.dump(base, f)
    os.environ["USERPROFILE"] = HOME
    os.environ["HOME"] = HOME
    os.environ.pop("NUKE_PATH", None)
    for d in _lib_dirs():
        if d not in sys.path:
            sys.path.append(d)
    sys.path.insert(0, REPO)
    import JKrender
    jk = JKrender
    jk.play_sound = lambda name: None
    jk.open_nukes = lambda: 0  # never asks to clear the cache of an open Nuke, never touches one
    sys.excepthook = lambda t, v, tb: (errors.append(1), say("EXCEPTION " + "".join(traceback.format_exception(t, v, tb))[-1500:]))
    if qt:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return jk, app


def make_job(name, first, last, folder=None, movie=False, env=None, slate=False):
    """A render of one Write writing PNG frames, as the JKrender menu in Nuke would send it. FakeNuke writes to the
    Write's path (FAKE_OUT). Returns the path of its job.json."""
    d = (folder or RUN) + "/" + name
    os.makedirs(d + "/render", exist_ok=True)
    script = d + "/" + name + ".nk"
    with open(script, "w", encoding="utf-8") as f:
        f.write("Root {\n first_frame %d\n last_frame %d\n}\n" % (first, last))
    out = d + "/render/" + name + (".mov" if movie else ".%04d.png")
    job_env = {"FAKE_OUT": out}
    job_env.update(env or {})
    job = {"version": 1, "script": script, "first": first, "last": last, "nuke_exe": FAKE, "nuke_version": "16.0v9",
           "writes": [{"name": "Write1", "output": out, "file_type": "mov" if movie else "png", "views": ["main"]}],
           "fps": 24, "format": "640x360", "env": job_env}
    with open(d + "/job.json", "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1)
    return d + "/job.json"


def frame_path(job, frame):
    with open(job, encoding="utf-8") as f:
        out = json.load(f)["writes"][0]["output"]
    return out.replace("%04d", "%04d" % frame)


def files(job):
    d = os.path.dirname(frame_path(job, 0))
    return sorted(n for n in os.listdir(d) if n.endswith(".png")) if os.path.isdir(d) else []


def write_frames(job, frames, cut=(), empty=()):
    """Frames on disk before a render opens: whole PNGs, or cut off / empty ones."""
    from PySide6 import QtGui
    for f in frames:
        p = frame_path(job, f)
        img = QtGui.QImage(64, 36, QtGui.QImage.Format_RGB888)
        img.fill(QtGui.QColor(f % 255, 80, 120))
        img.save(p)
        if f in cut:
            data = open(p, "rb").read()
            open(p, "wb").write(data[:len(data) // 2])
        if f in empty:
            open(p, "wb").close()


def pump(secs=0.1):
    t = time.time()
    while time.time() - t < secs:
        app.processEvents()
        time.sleep(0.02)


def wait(cond, secs=120, step=0.05):
    """Let the window run until cond() or the time is up. True if cond() came."""
    t = time.time()
    while time.time() - t < secs:
        app.processEvents()
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def answer(button_text, also=None):
    """Click a button of the next modal window (a question) when it shows. The texts it showed go into `also`."""
    from PySide6 import QtCore, QtWidgets

    def click():
        box = QtWidgets.QApplication.activeModalWidget()
        if box is None:
            QtCore.QTimer.singleShot(30, click)
            return
        if also is not None:
            also.append(box.text() if hasattr(box, "text") else box.windowTitle())
        for b in (box.buttons() if hasattr(box, "buttons") else []):
            if b.text().replace("&", "") == button_text:
                b.click()
                return
        box.reject()
    QtCore.QTimer.singleShot(30, click)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def log_lines(engine, text):
    return [x[1] for x in engine.log if text in x[1]]
