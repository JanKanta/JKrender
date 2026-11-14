"""All frames again really writes every frame anew: each file's write time is newer than the click, whether every frame was
on disk, only some, the render was stopped half way, or it was a new render opened over frames from an earlier one."""
from common import *

jk, app = start({"processes": 3, "threads": 2, "ram": 2, "max_renders": 3, "chunk_auto": False, "chunk_size": 3})


def mtimes(job, frames):
    return {f: os.path.getmtime(frame_path(job, f)) for f in frames if os.path.exists(frame_path(job, f))}


def done(e, secs=120):
    return wait(lambda: e.state in ("done", "failed", "stopped") and e.pending_checks <= 0 and not e.pixels_pending, secs)


def again(win, e, how):
    asked = []
    answer(how, asked)
    win.select(e)
    win.picked = set()
    t = time.time()
    time.sleep(1.1)  # file times have a resolution; frames written from here on are newer
    win._start()
    return t, asked


def stale(job, frames, t):
    m = mtimes(job, frames)
    return sorted(f for f in frames if m.get(f, 0) <= t)


FR = list(range(1001, 1013))

say("1. Every frame on disk, rendered by this window: All frames again")
job = make_job("sh010", 1001, 1012)
win = jk.MainWindow(job)
e = win.engine
win._start()
check("first render done", done(e), e.state)
t, asked = again(win, e, "All frames again")
check("asked", len(asked) == 1, asked)
check("done again", done(e), e.state)
check("every file written anew", stale(job, FR, t) == [], stale(job, FR, t))

say("2. Some frames deleted: All frames again")
for f in (1002, 1005, 1011):
    os.remove(frame_path(job, f))
e.scan_disk()
t, asked = again(win, e, "All frames again")
check("done again", done(e), e.state)
check("every file written anew", stale(job, FR, t) == [], stale(job, FR, t))

say("3. Stopped half way: All frames again")
job3 = make_job("sh030", 1001, 1012, env={"FAKE_SECS": "0.4"})
win.add_job(job3, auto=False)
e3 = win.manager.engines[-1]
win.select(e3)
win.picked = set()
win._start()
wait(lambda: sum(1 for fr in e3.frames.values() if fr.state == "done") >= 4, 60)
win._stop()
wait(lambda: e3.state == "stopped" and e3.pending_checks <= 0, 30)
t, asked = again(win, e3, "All frames again")
check("asked", len(asked) == 1, asked)
check("done again", done(e3), e3.state)
check("every file written anew", stale(job3, FR, t) == [], stale(job3, FR, t))

say("4. A new render opened over frames from an earlier one (a new send from Nuke), all on disk")
job4 = make_job("sh010", 1001, 1012, folder=RUN + "/second")
with open(job4, encoding="utf-8") as f:
    j4 = json.load(f)
j4["writes"][0]["output"] = json.load(open(job, encoding="utf-8"))["writes"][0]["output"]
j4["env"]["FAKE_OUT"] = j4["writes"][0]["output"]
json.dump(j4, open(job4, "w", encoding="utf-8"))
win.add_job(job4, auto=False)
e4 = win.manager.engines[-1]
pump(1.0)
check("it sees the frames on disk", sum(1 for fr in e4.frames.values() if fr.state in ("done", "skipped")) == 12,
      [fr.state for fr in e4.frames.values()])
t, asked = again(win, e4, "All frames again")
check("asked", len(asked) == 1, asked)
check("done again", done(e4), e4.state)
check("every file written anew", stale(job, FR, t) == [], stale(job, FR, t))

say("5. Same, with part of the frames on disk")
for f in range(1001, 1007):
    os.remove(frame_path(job, f))
win.add_job(job4, auto=False)
e5 = win.manager.engines[-1]
pump(1.0)
t, asked = again(win, e5, "All frames again")
check("asked", len(asked) == 1, asked)
check("done again", done(e5), e5.state)
check("every file written anew", stale(job, FR, t) == [], stale(job, FR, t))
finish()
