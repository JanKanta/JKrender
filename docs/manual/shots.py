# Screenshots for the Czech manual: JKrender offscreen, own home, a stand-in Nuke (FakeNuke.exe) that draws PNG frames.
# Paths go through drive J: (subst onto ./proj), so they read like a project. No sound, nothing on the screen.
import json, os, shutil, sys, time, traceback, faulthandler
faulthandler.enable()

D = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
J = "J:"
OUT = D + "/shots"
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
HOME = D + "/home"
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME + "/.jkrender")
for sub in ("sh010", "sh020"):
    shutil.rmtree(J + "/Demo/" + sub, ignore_errors=True)
json.dump({"slate": True, "slate_frame": 1000, "processes": 8, "threads": 4, "ram": 8, "max_renders": 3,
           "auto_start": False, "use_gpu": True}, open(HOME + "/.jkrender/settings.json", "w"))
os.environ["USERPROFILE"] = HOME
os.environ.pop("NUKE_PATH", None)
os.environ["QT_SCALE_FACTOR"] = "1.5"
os.environ["JKRENDER_PORT"] = "54631"
LIB = r"C:\Users\Honza WorkStation\.nuke\JKrender\lib"
sys.path.insert(0, os.path.join(LIB, next(n for n in os.listdir(LIB) if os.path.isdir(os.path.join(LIB, n)))))
sys.path.insert(0, r"C:\Users\Honza WorkStation\JKrender")
FAKE = "J:/Nuke16.0v9/Nuke16.0.exe"
os.makedirs("J:/Nuke16.0v9", exist_ok=True)
shutil.copy(D + "/FakeNuke.exe", FAKE)
os.environ["FAKE_SECS"] = "2.4"


def make_job(shot, first, last, secs):
    d = J + "/Demo/%s/comp" % shot
    os.makedirs(d + "/render", exist_ok=True)
    script = d + "/%s_comp_v003.nk" % shot
    open(script, "w").write('Root {\n inputs 0\n first_frame %d\n last_frame %d\n}\n' % (first, last))
    out = d + "/render/%s_comp_v003.%%04d.png" % shot
    rd = J + "/Demo/%s/jkrender_%d" % (shot, int(time.time()))
    os.makedirs(rd)
    job = {"version": 1, "script": script, "first": first, "last": last, "nuke_exe": FAKE, "nuke_version": "16.0v9",
           "writes": [{"name": "Write_comp", "output": out, "file_type": "png", "views": ["main"]}], "fps": 24, "format": "1920x1080",
           "env": {}}
    json.dump(job, open(rd + "/job.json", "w"), indent=1)
    return rd + "/job.json"


import JKrender as jk
from PySide6 import QtCore, QtGui, QtWidgets

jk.play_sound = lambda name: None
errors = []
sys.excepthook = lambda t, v, tb: (errors.append(1), print("".join(traceback.format_exception(t, v, tb))[-1500:], flush=True))
T0 = time.time()


def say(text):
    print("%6.1f  %s" % (time.time() - T0, text), flush=True)


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
    pump(1.5)
    shot("01_ready")
    sub(1)
    shot("02_settings_ready")
    w.sub_tabs.widget(1).grab().save(OUT + "/02b_settings_panel.png")
    sub(0)
    w.add_job(make_job("sh020", 1001, 1048, 2.0), auto=False)
    e2 = w.engine
    pump(0.8)
    w.select(e1)
    m.request_start(e1)
    m.request_start(e2)
    w._sync_controls()
    wait(lambda: e1.counts()["done"] >= 30, 240, "30 frames")
    w.select(e1)
    f = next(f for f in e1.frame_list() if e1.frames[f].state == "done" and f > 1010)
    w._pick_frame(f)
    pump(1.0)
    shot("03_rendering_workers")
    page(1)
    shot("04_log")
    page(0)
    side(1)
    shot("05_chunks")
    side(2)
    shot("06_details")
    side(0)
    sub(1)
    shot("07_settings_rendering")
    sub(0)
    # menus: a process in System, an empty spot in System, the System header, a chunk, a frame
    busy = next(x for x in e1.workers.values() if x.state in ("busy", "idle"))
    popup("m1_process_menu", lambda: w._worker_menu(e1, busy, w.mapToGlobal(QtCore.QPoint(1200, 900))))
    popup("m2_columns_menu", lambda: w._columns_menu(w.mapToGlobal(QtCore.QPoint(1200, 800))))
    side(1)
    w.chunk_table.selectRow(0)
    popup("m3_chunk_menu", lambda: w._chunk_menu(QtCore.QPoint(30, 30)))
    side(0)
    w.frames_view.selectRow(5)
    popup("m4_frame_menu", lambda: w._frames_menu(QtCore.QPoint(30, 30)))
    w._pick_frame(f)
    w._toggle_big()
    shot("08_big_preview")
    w._toggle_big()
    wait(lambda: all(x.state == "done" for x in (e1, e2)) and not m.running(), 400, "both done")
    wait(lambda: not e1.pixels_pending and not e2.pixels_pending, 200, "pixels")
    pump(2.5)
    w.select(e1)
    w._pick_frame(1050)
    pump(0.8)
    shot("09_done")
    # a broken render: sh020 with one cut-off frame and one black frame
    p = e2.output_files(1030)[0]
    open(p, "r+b").truncate(900)
    img = QtGui.QImage(1920, 1080, QtGui.QImage.Format_RGB32)
    img.fill(0)
    img.save(e2.output_files(1031)[0])
    w.select(e2)
    pump(0.5)
    w._verify()
    pump(1.5)
    w._check_pixels()
    wait(lambda: not e2.pixels_pending, 120, "pixels sh020")
    pump(2.5)
    w.out_filter.setCurrentText("Problems")
    w._pick_frame(1031)
    pump(0.8)
    shot("10_problems")
    w.out_filter.setCurrentText("All")
    popup("d1_delete", w._delete_dialog)
    # Test this script and Analyze script
    w.select(e1)
    t = jk.ScriptTest(w, e1)
    t.resize(1100, 640)
    t.show()
    shot("11_test", t)
    t.close()
    a = jk.ScriptAnalysis(w, e1)
    a.resize(1100, 900)
    a.show()
    pump(1.0)
    shot("12_analyze_running", a)
    wait(lambda: a._analysis is not None and not a.timer.isActive(), 150, "analysis")
    pump(1.0)
    shot("13_analyze", a)
    a.close()
except Exception:
    traceback.print_exc()
for x in m.engines:
    if x.state in ("rendering", "paused", "finishing", "waiting"):
        x.stop("over")
say("exceptions: %d" % len(errors))
pump(2)
os._exit(0)
