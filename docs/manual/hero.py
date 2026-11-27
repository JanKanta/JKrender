# Screenshots for the Czech manual: JKrender offscreen, own home, a stand-in Nuke (FakeNuke.exe) that draws PNG frames.
# Paths go through drive J: (subst onto a scratch folder), so they read like a project: the renders, this home and the
# program itself (a copy of this repo's). No sound, nothing on the screen.
import json, os, re, shutil, sys, time, traceback, faulthandler
from pathlib import Path
faulthandler.enable()

D = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
REPO = os.path.dirname(os.path.dirname(D))
J = "J:"
LIB = os.environ.get("JK_SHOTS_LIB") or os.path.expanduser("~/.nuke/JKrender/lib")  # PySide6 as JKrender installed it
OUT = D + "/hero"
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
APP = J + "/JKrender"
shutil.rmtree(APP, ignore_errors=True)
os.makedirs(APP)
for name in ("JKrender.py", "jkrender_worker.py", "jkrender_deps.py", "JKrender_NukeIntegration.py", "donation.json"):
    shutil.copy(REPO + "/" + name, APP)
HOME = J + "/home"
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME + "/.nuke/JKrender/settings")
for sub in ("sh010", "sh020"):
    shutil.rmtree(J + "/Demo/" + sub, ignore_errors=True)
json.dump({"slate": True, "slate_frame": 1000, "processes": 8, "threads": 4, "ram": 8, "max_renders": 3,
           "auto_start": False, "use_gpu": True}, open(HOME + "/.nuke/JKrender/settings/settings.json", "w"))
os.environ["USERPROFILE"] = HOME
os.environ.pop("NUKE_PATH", None)
os.environ["QT_SCALE_FACTOR"] = "1.5"
os.environ["JKRENDER_PORT"] = "54631"
sys.path.insert(0, os.path.join(LIB, next(n for n in os.listdir(LIB) if os.path.isdir(os.path.join(LIB, n)))))
sys.path.insert(0, APP)
FAKE = "J:/Nuke16.0v9/Nuke16.0.exe"
os.makedirs("J:/Nuke16.0v9", exist_ok=True)
shutil.copy(D + "/FakeNuke.exe", FAKE)
os.environ["FAKE_SECS"] = "2.4"
# the frames in the pictures: a real script's, rendered once (JK_SHOTS_FRAMES, a pattern with %04d); else FakeNuke
# draws its own
if os.environ.get("JK_SHOTS_FRAMES"):
    os.environ["FAKE_FRAMES"] = os.environ["JK_SHOTS_FRAMES"]


def make_job(shot, first, last, secs):
    d = J + "/Demo/%s/comp" % shot
    os.makedirs(d + "/render", exist_ok=True)
    script = d + "/%s_comp_v003.nk" % shot
    open(script, "w").write('Root {\n inputs 0\n first_frame %d\n last_frame %d\n}\n' % (first, last))
    out = d + "/render/%s_comp_v003.%%04d.png" % shot
    rd = J + "/Demo/%s/jkrender_%d" % (shot, int(time.time()))
    os.makedirs(rd)
    job = {"version": 1, "script": script, "first": first, "last": last, "nuke_exe": FAKE, "nuke_version": "16.0v9",
           "writes": [{"name": "Write_comp", "output": out, "file_type": "png", "views": ["main"]}], "fps": 24, "format": "1920x1012",
           "env": {}}
    json.dump(job, open(rd + "/job.json", "w"), indent=1)
    return rd + "/job.json"


import JKrender as jk
from PySide6 import QtCore, QtGui, QtWidgets

# resolve() follows subst back to the real folder: the command of a process (Details) shows these
jk.WORKER_SCRIPT = Path(APP + "/jkrender_worker.py")
jk.DONATION_FILE = Path(APP + "/donation.json")

jk.play_sound = lambda name: None
jk.open_nukes = lambda: 0  # a Nuke open on this PC: never asked about, its cache never cleared
jk.clear_open_nukes = lambda: 0
errors = []
sys.excepthook = lambda t, v, tb: (errors.append(1), print("".join(traceback.format_exception(t, v, tb))[-1500:], flush=True))
T0 = time.time()


def say(text):
    print("%6.1f  %s" % (time.time() - T0, text), flush=True)


OUTSIDE = re.compile(r"(?<![A-Za-z0-9])(?![Jj]:)[A-Za-z]:[\\/]|\\\\[A-Za-z0-9]")  # a drive other than J:, or \\server


def texts(widget):
    """What a widget and the ones in it show as text: labels, buttons, fields, menus, the cells of tables and trees."""
    out = []
    for x in [widget] + widget.findChildren(QtWidgets.QWidget):
        if not x.isVisible():
            continue
        for get in ("text", "toPlainText", "currentText", "windowTitle"):
            fn = getattr(x, get, None)
            if callable(fn):
                try:
                    out.append(str(fn()))
                except Exception:
                    pass
        if isinstance(x, QtWidgets.QMenu):
            out += [a.text() for a in x.actions()]
        model = x.model() if isinstance(x, QtWidgets.QAbstractItemView) else None
        stack = [QtCore.QModelIndex()] if model is not None else []
        while stack:
            parent = stack.pop()
            for r in range(model.rowCount(parent)):
                out += [str(model.data(model.index(r, c, parent)) or "") for c in range(model.columnCount(parent))]
                stack.append(model.index(r, 0, parent))
    return out


def paths_off_j(name, widget):
    """A picture shows J: paths only: one with a folder of whoever made it is a picture to make again."""
    for t in texts(widget):
        hit = OUTSIDE.search(t)
        if hit:
            say("!! a path off J: in %s: %s" % (name, t[max(0, hit.start() - 30):hit.start() + 70].replace("\n", " ")))


app = QtWidgets.QApplication([])
app.setStyle("Fusion")
pal = QtGui.QPalette()
P = QtGui.QPalette
for role, col in ((P.Window, "#282828"), (P.WindowText, "#cdcdcd"), (P.Base, "#323232"), (P.AlternateBase, "#2d2d2d"), (P.Text, "#cdcdcd"),
                  (P.Button, "#3b3b3b"), (P.ButtonText, "#cdcdcd"), (P.Highlight, "#6b4a22"), (P.HighlightedText, "#ffffff"),
                  (P.ToolTipBase, "#3b3b3b"), (P.ToolTipText, "#cdcdcd"), (P.PlaceholderText, "#8c8c8c")):
    pal.setColor(role, QtGui.QColor(col))
for role in (P.WindowText, P.Text, P.ButtonText):
    pal.setColor(P.Disabled, role, QtGui.QColor("#666666"))
app.setPalette(pal)
app.setStyleSheet(jk.STYLE)
w = jk.MainWindow(make_job("sh010", 1001, 1096, 2.6))
w.resize(2200, 1240)
w.show()
m = w.manager


def pump(secs=0.3):
    t = time.time()
    while time.time() - t < secs:
        app.processEvents()
        time.sleep(0.02)


def shot(name, widget=None):
    pump(0.4)
    (widget or w).grab().save(OUT + "/%s.png" % name)
    say("shot " + name)
    paths_off_j(name, widget or w)


def wait(cond, secs, label):
    t = time.time()
    while not cond():
        pump(0.1)
        if time.time() - t > secs:
            say("!! timed out: " + label)
            return False
    return True


# a menu or dialog: shown, photographed, closed without doing anything
NEXT = {"name": None}


def grab_popup():
    """While a menu or a dialog waits modally: photograph it and close it, so nothing is picked."""
    pw = app.activePopupWidget() or app.activeModalWidget()
    if pw is None:
        pw = next((x for x in app.topLevelWidgets() if x.isVisible() and isinstance(x, (QtWidgets.QMenu, QtWidgets.QDialog))), None)
    if pw is None:
        QtCore.QTimer.singleShot(200, grab_popup)
        return
    if NEXT["name"]:
        pw.grab().save(OUT + "/%s.png" % NEXT["name"])
        say("popup " + NEXT["name"])
        paths_off_j(NEXT["name"], pw)
    if isinstance(pw, QtWidgets.QDialog):
        pw.reject()
    else:
        pw.close()


def popup(name, fn):
    NEXT["name"] = name
    QtCore.QTimer.singleShot(700, grab_popup)
    fn()
    NEXT["name"] = None
    pump(0.2)


def side(i):
    w.side_tabs.setCurrentIndex(i)


def page(i):
    w.tabs.setCurrentIndex(i)


def sub(i):
    w.sub_tabs.setCurrentIndex(i)


try:
    e1 = w.engine
    w.add_job(make_job("sh040", 1001, 1024, 0.8), auto=False)
    e4 = w.engine
    w.add_job(make_job("sh020", 1001, 1060, 2.0), auto=False)
    e2 = w.engine
    w.add_job(make_job("sh030", 1001, 1120, 2.2), auto=False)
    e3 = w.engine
    pump(0.8)
    for e in (e4, e1, e2, e3):
        m.request_start(e)
    w._sync_controls()
    wait(lambda: e4.state == "done" and e3.state == "rendering" and e1.counts()["done"] >= 45, 400, "busy enough")
    pump(6)
    w.select(e1)
    f = max(f for f in e1.frame_list() if e1.frames[f].state == "done")
    w._pick_frame(f)
    pump(1.0)
    shot("hero")
except Exception:
    traceback.print_exc()
    errors.append(1)
say("exceptions: %d" % len(errors))
for e in m.engines:
    e.stop("done")
pump(3)
m.shutdown()
os._exit(0)
