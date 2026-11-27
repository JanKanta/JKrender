"""Processes held up together (0.99.90): when three or more Nuke processes finish a slow frame at the same moment, twice in
a render, the Log says something they share held them up, as a rule the disk, and that more processes will not help. A
light script writing large EXRs to a SATA drive did that: frames of 0.9 s took 5-6 s, all eight processes ending within
the same hundredth. Frame times are made up here; nothing renders."""
import types
from common import *

jk, app = start(qt=False)


WIN = sys.platform.startswith("win")
# Windows names the drive; macOS and Linux the mount point that holds the frames' folder (1.0.25: nothing before)
OUT = "E:/shots/sh010/sh010.%04d.exr" if WIN else RUN + "/shots/sh010/sh010.%04d.exr"


def mount_of(p):
    p = os.path.abspath(p)
    while not os.path.exists(p):
        p = os.path.dirname(p)
    while not os.path.ismount(p):
        p = os.path.dirname(p)
    return p


DISK = "E:" if WIN else mount_of(OUT)


def engine(n=8):
    e = types.SimpleNamespace(workers={}, outputs=[("Write1", OUT)], log=[])
    e.note = lambda text, level="ev": e.log.append((text, level))
    for i in range(1, n + 1):
        e.workers["W%02d" % i] = types.SimpleNamespace(name="W%02d" % i, state="busy", frames_here=5)
    return e


def frame(e, name, secs, t):
    jk.RenderEngine._watch_shared_stall(e, e.workers[name], secs, t)


def steady(e, t0, count=3, secs=0.9):
    """count rounds of every process finishing a usual frame, a little apart"""
    t = t0
    for _ in range(count):
        for i, name in enumerate(e.workers):
            frame(e, name, secs, t + i * 0.12)
        t += secs + 1.0
    return t


def together(e, t, secs=6.0, n=8):
    for i, name in enumerate(list(e.workers)[:n]):
        frame(e, name, secs, t + i * 0.01)
    return t + 1.0


say("All eight held up at once, twice: the Log says it, once")
e = engine()
t = steady(e, 1000.0)
t = together(e, t)
check("once is no pattern yet", not e.log, e.log)
t = steady(e, t + 3)
t = together(e, t)
notes = [x for x in e.log if "finished their frames together" in x[0]]
check("the second time: a warning", len(notes) == 1 and notes[0][1] == "warn", e.log)
check("it names the disk the frames go to and says more processes will not help",
      bool(notes) and "the disk the frames are written to (%s)" % DISK in notes[0][0] and "More processes will not" in notes[0][0],
      (DISK, notes))
check("disk_of: a drive on Windows, a mount point elsewhere, also for a folder not made yet",
      jk.disk_of("E:/a/b.exr") == "E:" if WIN else jk.disk_of("/no/such/folder/f.%04d.exr") == "/", jk.disk_of(OUT))
t = steady(e, t + 3)
together(e, t)
check("and only once a render", len([x for x in e.log if "finished their frames together" in x[0]]) == 1, e.log)

say("Slow frames that do not end together (a heavy part of the shot): nothing")
e = engine()
t = steady(e, 1000.0)
for k in range(12):
    frame(e, "W%02d" % (k % 8 + 1), 6.0, t + k * 1.5)
check("no warning", not e.log, e.log)

say("Two processes only: two frames ending together is too thin to tell")
e = engine(2)
t = steady(e, 1000.0, count=6)
for _ in range(3):
    t = together(e, t + 3, n=2)
check("no warning", not e.log, e.log)

say("The first frame of every Nuke is slow and they start together: not counted")
e = engine()
t = steady(e, 1000.0)
for w in e.workers.values():
    w.frames_here = 1
for _ in range(3):
    t = together(e, t + 3)
check("no warning", not e.log, e.log)
finish()
