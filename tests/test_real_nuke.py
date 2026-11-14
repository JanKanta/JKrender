"""With the real Nuke (run_tests.py --real): a small made-up script (CheckerBoard, Blur, Write) rendered in two processes,
the frames checked, then Test this script with two setups. Needs a Nuke licence; skipped while any Nuke is open, so it never
competes with your work. The worker gets your real home folder, as a studio's start-up scripts may need it."""
import glob
import re
import subprocess
from common import *

running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Nuke*"], capture_output=True, text=True).stdout
if "Nuke" in running.replace("INFO: No tasks", ""):
    say("skipped: a Nuke is open")
    finish()

nuke_dir = os.environ.get("NUKE_DIR", "")
exe = sorted(p for p in glob.glob(nuke_dir + "/Nuke*.exe") if re.match(r"Nuke\d+\.\d+\.exe$", os.path.basename(p)))
if not exe:
    say("skipped: no Nuke*.exe in NUKE_DIR (%s)" % nuke_dir)
    finish()

jk, app = start({"processes": 2, "threads": 4, "ram": 2, "check_pixels": True})
os.environ["USERPROFILE"] = REAL_HOME  # JKrender read its own home at import; Nuke gets the real one

d = RUN + "/real"
os.makedirs(d + "/render", exist_ok=True)
out = d + "/render/real.%04d.png"
script = d + "/real.nk"
open(script, "w", encoding="utf-8").write("""Root {
 inputs 0
 first_frame 1001
 last_frame 1010
 format "640 360 0 0 640 360 1 jktest"
}
CheckerBoard2 {
 inputs 0
 name CheckerBoard1
}
Blur {
 size {{frame-1000}}
 name Blur1
}
Write {
 file "%s"
 file_type png
 name Write1
}
""" % out)
job = {"version": 1, "script": script, "first": 1001, "last": 1010, "nuke_exe": exe[-1].replace("\\", "/"),
       "nuke_version": os.path.basename(nuke_dir), "fps": 24, "format": "640x360", "env": {},
       "writes": [{"name": "Write1", "output": out, "file_type": "png", "views": ["main"]}]}
json.dump(job, open(d + "/job.json", "w", encoding="utf-8"), indent=1)

say("A render with the real Nuke")
win = jk.MainWindow(d + "/job.json")
e = win.engine
win._start()
wait(lambda: e.state in ("done", "failed", "stopped"), 600)
wait(lambda: not e.pixels_pending, 60)
check("done", e.state == "done", (e.state, [x[1] for x in e.log][-6:]))
check("10 frames on disk", len(files(d + "/job.json")) == 10, files(d + "/job.json"))
check("the frames pass the check", not any(jk.frame_broken(fr) for fr in e.frames.values()))
check("the pixels too", bool(log_lines(e, "Pixels checked")) and not any(jk.pixels_broken(fr) for fr in e.frames.values()))

say("Test this script with the real Nuke: 1 x 4 against 2 x 4")
t = jk.ScriptTest(win, e)
t.setups, t.added = [(1, 4, 2, "short"), (2, 4, 2, "short")], set()
t._fill_table()
t.start()
wait(lambda: not t.timer.isActive() and t.run is None, 900)
check("a winner", getattr(t, "best", None) in t.setups, getattr(t, "best", None))
check("its test folder is gone", not os.path.exists(str(jk.TEST_DIR)))
check("the render's frames untouched", len(files(d + "/job.json")) == 10)
finish()
