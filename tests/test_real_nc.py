"""With the real Nuke Non-commercial (run_tests.py --real real_nc): a .nknc made in NC terminal mode, rendered by JKrender
with NC workers (--nc): an EXR sequence with Reload Nuke After every chunk, then a PNG sequence and a ProRes movie side by
side, every check, and Test this script. Needs a Nuke whose NC sign-in works from the terminal (JKRENDER_NC_EXE, or the
newest C:/Program Files/Nuke*). The worker gets your real home, where the NC sign-in lives. HD only, as NC renders."""
import glob
import re
import shutil
import subprocess
from common import *

exe = os.environ.get("JKRENDER_NC_EXE") or next(iter(sorted(
    (p for p in glob.glob("C:/Program Files/Nuke*/Nuke*.exe") if re.match(r"Nuke\d+\.\d+\.exe$", os.path.basename(p))),
    key=lambda p: [int(x) for x in re.findall(r"\d+", os.path.basename(os.path.dirname(p)))], reverse=True)), "")
if not exe:
    say("skipped: no Nuke")
    finish()
exe = exe.replace("\\", "/")
# start() empties this test's folder: first, then the script
jk, app = start({"processes": 2, "threads": 4, "ram": 2, "check_pixels": True, "chunk_auto": False, "chunk_size": 4,
                 "fresh_after_chunk": "always", "slate": False, "use_gpu": False})
os.environ["USERPROFILE"] = REAL_HOME  # JKrender read its own home at import; Nuke gets the real one (the NC sign-in)
d = RUN + "/nc"
shutil.rmtree(d, ignore_errors=True)
os.makedirs(d + "/render")
mk = d + "/mk.py"
open(mk, "w", encoding="utf-8").write('''import nuke
D = %r
r = nuke.root()
r["first_frame"].setValue(1001); r["last_frame"].setValue(1024); r["format"].setValue("HD_1080"); r["fps"].setValue(25)
cb = nuke.nodes.CheckerBoard2(); bl = nuke.nodes.Blur(); bl.setInput(0, cb); bl["size"].setExpression("frame-1000")
tx = nuke.nodes.Text2(message="NC [frame]"); tx.setInput(0, bl)
for name, f, t in (("Write_exr", D + "/render/nc.####.exr", "exr"), ("Write_png", D + "/render/nc.####.png", "png"),
                   ("Write_mov", D + "/render/nc.mov", "mov")):
    w = nuke.nodes.Write(name=name, file=f, file_type=t); w.setInput(0, tx)
nuke.scriptSaveAs(D + "/nc_v001.nknc", 1)
print("SAVED", nuke.env.get("nc"))
''' % d)
r = subprocess.run([exe, "--nc", "-i", "-t", mk], capture_output=True, text=True, timeout=300,
                   env=dict(os.environ, USERPROFILE=REAL_HOME))
if "SAVED True" not in r.stdout:
    say("skipped: Nuke Non-commercial does not start from the terminal here (%s)" % os.path.basename(exe))
    finish()
script = d + "/nc_v001.nknc"
check("the script is an encrypted .nknc", open(script, "rb").read(14) == b"Nuke NC mode e", open(script, "rb").read(20))



def nc_job(write, out, ftype):
    jd = d + "/" + write
    os.makedirs(jd, exist_ok=True)
    snap = jd + "/snapshot_nc_v001.nknc"
    shutil.copy2(script, snap)
    job = {"version": 1, "script": script, "snapshot": snap, "first": 1001, "last": 1024, "nuke_exe": exe, "nuke_mode": "nc",
           "nuke_version": os.path.basename(os.path.dirname(exe)), "fps": 25, "format": "1920x1080 HD_1080", "env": {},
           "writes": [{"name": write, "output": out, "file_type": ftype, "views": ["main"]}]}
    json.dump(job, open(jd + "/job.json", "w", encoding="utf-8"), indent=1)
    return jd + "/job.json"


say("EXR, Reload Nuke After every chunk: 6 chunks in 2 NC processes")
win = jk.MainWindow(nc_job("Write_exr", d + "/render/nc.####.exr", "exr"))
e = win.engine
check("the worker Nuke starts with --nc", e._command()[1] == "--nc", e._command()[:3])
m = win.manager
m.request_start(e)
wait(lambda: e.state in ("done", "failed", "stopped"), 900)
wait(lambda: not e.pixels_pending, 120)
exrs = glob.glob(d + "/render/nc.*.exr")
check("done", e.state == "done", (e.state, [x[1] for x in e.log][-6:]))
check("24 EXR on disk", len(exrs) == 24, len(exrs))
check("the frames pass the check", not any(jk.frame_broken(fr) for fr in e.frames.values()))
check("the pixels too", bool(log_lines(e, "Pixels checked")) and not any(jk.pixels_broken(fr) for fr in e.frames.values()),
      log_lines(e, "Pixels")[-1:])
check("Reload Nuke: more Nuke starts than processes", len(e.workers) > 2 or len(log_lines(e, "reload")) >= 2,
      (len(e.workers), len(log_lines(e, "reload"))))

say("PNG and the ProRes movie side by side (the movie in a process of its own)")
m.settings["fresh_after_chunk"] = "short"
win.add_job(nc_job("Write_png", d + "/render/nc.####.png", "png"), auto=False)
ep = m.engines[-1]
win.add_job(nc_job("Write_mov", d + "/render/nc.mov", "mov"), auto=False)
em = m.engines[-1]
check("the movie is a movie", em.movie)
m.request_start(ep)
m.request_start(em)
wait(lambda: all(x.state in ("done", "failed", "stopped") for x in (ep, em)), 900)
wait(lambda: not ep.pixels_pending, 120)
pngs = glob.glob(d + "/render/nc.*.png")
check("PNG done, 24 on disk", ep.state == "done" and len(pngs) == 24, (ep.state, len(pngs)))
check("PNG frames pass the check", not any(jk.frame_broken(fr) for fr in ep.frames.values()))
check("movie done", em.state == "done", (em.state, [x[1] for x in em.log][-6:]))
info = em.probe() or {}
check("the movie: 24 frames, 25 fps, 1920 x 1080", info.get("frames") == 24 and abs((info.get("fps") or 0) - 25) < .01
      and info.get("width") == 1920, {k: info.get(k) for k in ("frames", "fps", "width", "height", "codec")})
check("the movie check is not bad", em.movie_verdict() != "bad", em.movie_verdict())

say("Test this script with NC: 1 x 4 against 2 x 4")
t = jk.ScriptTest(win, ep)
t.setups, t.added = [(1, 4, 2, "short", "frame"), (2, 4, 2, "short", "frame")], set()
t._fill_table()
t.start()
wait(lambda: not t.timer.isActive() and t.run is None, 900)
check("a winner", getattr(t, "best", None) in t.setups, getattr(t, "best", None))
check("its test folder is gone", not os.path.exists(str(jk.TEST_DIR)))
check("the render's PNGs untouched", len(glob.glob(d + "/render/nc.*.png")) == 24)
finish()
