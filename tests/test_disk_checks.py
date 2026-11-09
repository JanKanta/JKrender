"""Frames on disk and the checks: what a render shows when it opens, what Check frames says, and the checks when a render
ends (frames, then pixels) or is stopped."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "check_pixels": True, "chunk_auto": False, "chunk_size": 3})

say("A render opens with frames on disk: 1001-1003 whole, 1004 missing, 1005 cut off, 1006 empty")
job = make_job("sh010", 1001, 1006)
write_frames(job, [1001, 1002, 1003, 1005, 1006], cut=[1005], empty=[1006])
win = jk.MainWindow(job)
win.timer.stop()
win.slow.stop()
e, md = win.engine, win.frames_model


def states(mode="All"):
    win.out_filter.setCurrentText(mode)
    md.refilter()
    return {f: md.data(md.index(i, 1)) for i, f in enumerate(md.rows)}


s0 = states()
check("All lists every frame of the range", sorted(s0) == list(range(1001, 1007)), sorted(s0))
check("whole frames: skipped", all(s0[f] == "skipped" for f in (1001, 1002, 1003)), s0)
check("missing frame: not on disk", s0.get(1004) == "not on disk", s0.get(1004))
check("cut off and empty: broken on disk", s0.get(1005) == s0.get(1006) == "broken on disk", (s0.get(1005), s0.get(1006)))
check("broken ones counted as broken, the missing one not", sum(jk.frame_broken(fr) for fr in e.frames.values()) == 2)
check("the Log says what is on disk", bool(log_lines(e, "3 of 6 frames are already on disk")), [x[1] for x in e.log][:3])
check("the Log names the broken ones", bool(log_lines(e, "2 frame(s) on disk are broken")))
check("Problems: the two broken", sorted(states("Problems")) == [1005, 1006], sorted(states("Problems")))
check("Rendered: none yet", states("Rendered") == {})

say("Check frames says the same")
e.verify_all()
wait(lambda: (e.tick() or True) and e.pending_checks == 0, 10)
for _ in range(5):
    e.tick()
    pump(0.05)
s1 = states()
check("after Check frames nothing changed", s1 == s0, {f: (s0[f], s1[f]) for f in s0 if s0[f] != s1.get(f)})

say("A render: the frames are checked when it ends, not while it renders, then the pixels")
job2 = make_job("sh020", 1001, 1012)
win.add_job(job2, auto=False)
e2 = win.manager.engines[-1]
win.select(e2)
win.timer.start(200)
win._start()
seen = {"checked": 0}


def going():
    if e2.state == "rendering":
        seen["checked"] = max(seen["checked"], sum(1 for fr in e2.frames.values() if fr.checks))
    return e2.state in ("done", "failed", "stopped")


wait(going, 120)
wait(lambda: not e2.pixels_pending, 30)
check("render done", e2.state == "done", e2.state)
check("no frame checked while it rendered", seen["checked"] == 0, seen["checked"])
check("every frame checked when it ended", all(fr.checks for fr in e2.frames.values()))
order = [x[1] for x in e2.log if "Render finished" in x[1] or "Pixels checked" in x[1]]
check("the Log: render finished, then the pixels checked", len(order) == 2 and "Render finished" in order[0], order)
check("12 files written", len(files(job2)) == 12, len(files(job2)))

say("Stop: what it rendered is checked")
job3 = make_job("sh030", 1001, 1012, env={"FAKE_SECS": "0.6"})
win.add_job(job3, auto=False)
e3 = win.manager.engines[-1]
win.select(e3)
win._start()
wait(lambda: sum(1 for fr in e3.frames.values() if fr.state == "done") >= 3, 60)
win._stop()
wait(lambda: e3.pending_checks == 0, 10)
pump(0.3)
done = [f for f, fr in e3.frames.items() if fr.state == "done"]
check("stopped", e3.state == "stopped", e3.state)
check("the frames done before Stop are checked", bool(done) and all(e3.frames[f].checks for f in done), done)
finish()
