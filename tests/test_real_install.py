"""The install as a new user does it, with the real Nuke (run_tests.py --real): the JKrender folder copied into ~/.nuke, one line
in ~/.nuke/init.py, nothing else. Then Nuke (-t, in a home of its own) opens a small script, and what the JKrender menu does for
Render Selected Write(s) runs: find JKrender.py, the log folder, make the job and snapshot, pick Nuke's own Python with PySide6.
JKrender then renders it with the real Nuke (offscreen) and ends. numpy and OpenEXR are copied from your install, not
downloaded. Skipped while any Nuke is open."""
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

shutil.rmtree(RUN, ignore_errors=True)
home = RUN + "/home"
plugin = home + "/.nuke/JKrender"

say("1. Copy the JKrender folder into ~/.nuke, as it is in git")
tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.split()
check("git lists the files", len(tracked) > 5, tracked[:5])
for f in tracked:
    if f.startswith("tests/") or f.startswith("docs/"):
        continue
    os.makedirs(os.path.dirname(plugin + "/" + f), exist_ok=True)
    shutil.copy2(REPO + "/" + f, plugin + "/" + f)
for f in ("JKrender.py", "jkrender_worker.py", "JKrender_NukeIntegration.py", "menu.py", "jkrender_deps.py", "INSTALL.txt"):
    check("%s is in the folder" % f, os.path.isfile(plugin + "/" + f))
real_lib = REAL_HOME + "/.nuke/JKrender/lib"
if os.path.isdir(real_lib):
    shutil.copytree(real_lib, plugin + "/lib")  # what the first start would download

say("2. One line in ~/.nuke/init.py")
open(home + "/.nuke/init.py", "w").write('nuke.pluginAddPath("./JKrender")\n')
os.makedirs(home + "/.nuke/JKrender/settings", exist_ok=True)
json.dump({"auto_start": True, "processes": 2, "threads": 4, "ram": 2, "slate": False, "check_on_open": False},
          open(home + "/.nuke/JKrender/settings/settings.json", "w"))

say("3. Nuke opens a script; JKrender > Render Selected Write(s) does its part")
proj = RUN + "/project"
os.makedirs(proj + "/render", exist_ok=True)
script = proj + "/shot.nk"
open(script, "w").write("""Root {
 inputs 0
 first_frame 1001
 last_frame 1006
 format "640 360 0 0 640 360 1 jktest"
}
CheckerBoard2 {
 inputs 0
 name CheckerBoard1
}
Write {
 file "%s/render/shot.%%04d.png"
 file_type png
 name Write1
}
""" % proj)
driver = RUN + "/driver.py"
open(driver, "w").write(r'''
import json, os, subprocess, sys
import nuke
out = {}
try:
    import JKrender_NukeIntegration as jk
    out["integration"] = jk.__file__.replace("\\", "/")
except Exception as exc:
    out["integration_error"] = repr(exc)
    print("RESULT " + json.dumps(out)); sys.exit(0)
nuke.scriptOpen(sys.argv[1])
w = nuke.toNode("Write1")
w.setSelected(True)
out["writes"] = [n.fullName() for n in jk._write_nodes()]
app = jk._known_app()
out["app"] = app.replace("\\", "/")
renders = jk._renders_dir(app)
out["renders"] = str(renders).replace("\\", "/")
job = jk._make_job([w], renders)
out["job"] = str(job).replace("\\", "/")
own = jk._nuke_python()
out["python"] = own[0].replace("\\", "/") if own else None
out["pyside6"] = bool(own and jk._has_pyside6(*own))
if own and out["pyside6"]:
    # what _launch starts, offscreen and ending when done, so nothing shows and it does not stay open
    env = dict(own[1])
    env.update(QT_QPA_PLATFORM="offscreen", JKRENDER_PORT=os.environ["JK_INSTALL_PORT"])
    r = subprocess.run([own[0], app, str(job), "--exit-when-done"], env=env, cwd=os.path.dirname(app),
                       capture_output=True, text=True, timeout=600)
    out["jkrender_exit"] = r.returncode
    out["jkrender_err"] = (r.stderr or "")[-600:]
print("RESULT " + json.dumps(out))
''')
env = dict(os.environ)
env.update(USERPROFILE=home.replace("/", "\\"), HOME=home, JK_INSTALL_PORT=str(free_port()))  # a number on Nuke's command line is a frame range
for k in ("NUKE_PATH", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM", "JKRENDER_TEST_RUN"):
    env.pop(k, None)
r = subprocess.run([exe[-1], "-i", "-t", driver, script], env=env, cwd=RUN, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=900)
line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
check("Nuke ran the steps", line is not None, (r.stdout + r.stderr)[-800:])
res = json.loads(line[7:]) if line else {}
norm = lambda p: (p or "").replace("\\", "/").lower()
check("the plugin path from init.py works: the menu's module loads from ~/.nuke/JKrender",
      norm(res.get("integration")).startswith(norm(plugin)), res.get("integration") or res.get("integration_error"))
check("the selected Write is found", res.get("writes") == ["Write1"], res.get("writes"))
check("JKrender.py found next to it", norm(res.get("app")) == norm(plugin + "/JKrender.py"), res.get("app"))
check("render folders go to its log folder", norm(res.get("renders")) == norm(plugin + "/log"), res.get("renders"))
job = res.get("job") or ""
check("a job and a snapshot of the script", os.path.isfile(job) and glob.glob(os.path.dirname(job) + "/snapshot_*.nk"), job)
check("Nuke's own Python with PySide6 runs the window", res.get("pyside6") is True, res.get("python"))

say("4. JKrender renders it with the real Nuke")
check("JKrender ended by itself when done", res.get("jkrender_exit") == 0, res.get("jkrender_err"))
frames = sorted(glob.glob(proj + "/render/shot.*.png"))
check("6 frames rendered into the project", len(frames) == 6, frames)
rep = os.path.dirname(job) + "/report.json"
report = json.load(open(rep, encoding="utf-8")) if os.path.isfile(rep) else {}
check("the render's report says done", report.get("result", {}).get("rendered") == 6, report.get("result"))
check("settings stayed in the home of the test", os.path.isfile(home + "/.nuke/JKrender/settings/settings.json"))
finish()
