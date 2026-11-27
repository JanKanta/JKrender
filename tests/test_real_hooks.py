"""With the real Nuke (run_tests.py --real): a pipeline's render hooks, whatever the pipeline (0.99.81). A made-up studio
start-up script on NUKE_PATH adds callbacks as a pipeline does (nuke.addBeforeRender, addAfterRender, the frame ones, and an
afterRender its beforeRender adds), and the Write has all four hook knobs; every hook writes a line to a log. Rendered in two
processes: beforeRender once in each Nuke before its first frame, the frame hooks once a frame, afterRender once for the
whole render, after the last frame, with nuke.thisNode() the Write. Then an afterRender that raises: the frames are fine and
the card says it. Needs a Nuke licence; skipped while any Nuke is open."""
import shutil
from common import *

if nuke_is_open():
    say("skipped: a Nuke is open")
    finish()

nuke_dir = os.environ.get("NUKE_DIR", "")
exe = real_nuke_exes(nuke_dir)
if not exe:
    say("skipped: no Nuke in NUKE_DIR (%s)" % nuke_dir)
    finish()

jk, app = start({"processes": 2, "threads": 4, "ram": 2, "check_pixels": False})
use_real_home()  # JKrender read its own home at import; Nuke gets the real one

d = RUN + "/hooks"
site = d + "/site"  # the made-up studio's start-up folder, on NUKE_PATH
log = d + "/hooklog"  # a log for each process: two Nukes appending to one file on Windows lose lines
os.makedirs(site, exist_ok=True)
shutil.rmtree(log, ignore_errors=True)
os.makedirs(log)
open(site + "/jkhooks.py", "w", encoding="utf-8").write('''import os
import time

import nuke


def log(what):
    node = nuke.thisNode()
    with open(os.path.join(os.environ["JKHOOK_LOG"], "%d.log" % os.getpid()), "a") as f:
        f.write("%s %d %s %.3f\\n" % (what, os.getpid(), node.name() if node is not None else "-", time.time()))


added = []


def before():
    log("g_before")
    if not added:  # a pipeline that adds its afterRender from its beforeRender
        added.append(1)
        nuke.addAfterRender(log, ("g_added_after",), nodeClass="Write")


def register():
    nuke.addBeforeRender(before, nodeClass="Write")
    nuke.addAfterRender(log, ("g_after",), nodeClass="Write")
    nuke.addBeforeFrameRender(log, ("g_before_frame",), nodeClass="Write")
    nuke.addAfterFrameRender(log, ("g_after_frame",), nodeClass="Write")
''')
open(site + "/init.py", "w", encoding="utf-8").write("import jkhooks\njkhooks.register()\n")


def make(name, last, after, extra=""):
    out = d + "/" + name + "/" + name + ".%04d.png"
    os.makedirs(d + "/" + name, exist_ok=True)
    script = d + "/" + name + ".nk"
    open(script, "w", encoding="utf-8").write("""Root {
 inputs 0
 first_frame 1001
 last_frame %d
 format "640 360 0 0 640 360 1 jktest"
}
CheckerBoard2 {
 inputs 0
 name CheckerBoard1
}
Write {
 file "%s"
 file_type png
 beforeRender "import jkhooks; jkhooks.log('k_before')"
 beforeFrameRender "import jkhooks; jkhooks.log('k_before_frame')"
 afterFrameRender "import jkhooks; jkhooks.log('k_after_frame')"
 afterRender "%s"
 name Write1
}
%s""" % (last, out, after, extra))
    job = {"version": 1, "script": script, "first": 1001, "last": last, "nuke_exe": exe[0].replace("\\", "/"),
           "nuke_version": os.path.basename(nuke_dir), "fps": 24, "format": "640x360",
           "env": {"NUKE_PATH": site, "JKHOOK_LOG": log},
           "writes": [{"name": "Write1", "output": out, "file_type": "png", "views": ["main"]}]}
    path = d + "/" + name + ".json"
    json.dump(job, open(path, "w", encoding="utf-8"), indent=1)
    return path


def rows():
    found = []
    for name in sorted(os.listdir(log)):
        found += [r.split() for r in open(os.path.join(log, name), encoding="utf-8").read().splitlines()]
    return found


def of(what):
    return [r for r in rows() if r[0] == what]


say("12 frames in two Nukes, hooks on the Write and from the studio's start-up script")
job = make("hooks", 1012, "import jkhooks; jkhooks.log('k_after')")
win = jk.MainWindow(job)
e = win.engine
win._start()
wait(lambda: e.state in ("done", "failed", "stopped"), 600)
check("done", e.state == "done", (e.state, e.errors(), [x[1] for x in e.log][-6:]))
check("12 frames on disk", len(files(job)) == 12, files(job))
counts = {w: len(of(w)) for w in ("k_before_frame", "k_after_frame", "g_before_frame", "g_after_frame")}
check("the frame hooks once a frame", set(counts.values()) == {12}, counts)
pids = sorted({r[1] for r in of("k_after_frame")})
check("rendered in more than one Nuke", len(pids) >= 2, pids)
for what in ("k_before", "g_before"):
    check("%s once in each Nuke" % what, sorted(r[1] for r in of(what)) == pids, (of(what), pids))
    check("%s before that Nuke's first frame" % what,
          all(min(float(r[3]) for r in of(what) if r[1] == p) <= min(float(r[3]) for r in of("k_before_frame") if r[1] == p)
              for p in pids if any(r[1] == p for r in of(what))))
after = of("k_after") + of("g_after") + of("g_added_after")
check("afterRender once: the knob, the start-up script's, the one its beforeRender added",
      [len(of(w)) for w in ("k_after", "g_after", "g_added_after")] == [1, 1, 1], after)
check("after the last frame", bool(after) and min(float(r[3]) for r in after) >= max(float(r[3]) for r in of("k_after_frame")))
check("in one Nuke", len({r[1] for r in after}) == 1, after)
check("nuke.thisNode() is the Write", bool(after) and all(r[2] == "Write1" for r in after), after)
check("JKrender knew of the three", max(w.hooks for w in e.workers.values()) == 3, [(w.name, w.hooks) for w in e.workers.values()])
check("the Log says they ran", bool(log_lines(e, "ran the afterRender hooks (3)")), log_lines(e, "afterRender"))
check("no error", not e.errors(), e.errors())
befores = log_lines(e, "ran the beforeRender hooks (2) in")  # the knob and the start-up script's
check("JKrender shows beforeRender: a Log line for each Nuke", len(befores) == len(pids), (befores, pids))
check("and a block on its timeline", sum(1 for w in e.workers.values() for s in w.segments if s[0] == "before") == len(pids))
timed = [fr.hooks for fr in e.frames.values()]
check("the Write's per-frame hooks timed on every frame", all(fr and set(fr) == {"beforeFrameRender", "afterFrameRender"}
                                                             for fr in timed), timed[:3])
check("summed up in the Log", bool(log_lines(e, "beforeFrameRender on the Write: 12 frames")), log_lines(e, "on the Write"))

say("An afterRender that raises: the frames are fine, the card says it")
shutil.rmtree(log)
os.makedirs(log)
job = make("hookfail", 1004, "raise RuntimeError('publish is down')")
win.add_job(job, auto=False)
e = win.manager.engines[-1]
win.select(e)
win._start()
wait(lambda: e.state in ("done", "failed", "stopped"), 600)
check("done", e.state == "done", (e.state, e.errors()))
check("4 frames on disk", len(files(job)) == 4, files(job))
check("the error on the card", any("1 of 3 afterRender hook(s) failed: the afterRender knob of Write1: publish is down" in x
                                   for x in e.errors()), e.errors())
check("the other two ran", [len(of(w)) for w in ("g_after", "g_added_after")] == [1, 1], rows())

say("An afterRender that renders another Write (a movie, say): that render runs its own hooks, for its own node")
shutil.rmtree(log)
os.makedirs(log)
proxy = d + "/hooknest/proxy.%04d.jpg"
job = make("hooknest", 1004, "import jkhooks; jkhooks.log('k_after'); import nuke; nuke.execute(nuke.toNode('Write2'), 1001, 1001)",
           extra='Write {\n file "%s"\n file_type jpeg\n name Write2\n}\n' % proxy)
win.add_job(job, auto=False)
e = win.manager.engines[-1]
win.select(e)
win._start()
wait(lambda: e.state in ("done", "failed", "stopped"), 600)
check("done", e.state == "done", (e.state, e.errors()))
check("the other Write rendered", os.path.exists(proxy.replace("%04d", "1001")))
check("its afterRender callbacks ran for it, and once for Write1", sorted(r[2] for r in of("g_after")) == ["Write1", "Write2"],
      of("g_after"))
check("its beforeRender callbacks too, for it", [r[2] for r in of("g_before") if r[2] == "Write2"] == ["Write2"], of("g_before"))
check("no error", not e.errors(), e.errors())
finish()
