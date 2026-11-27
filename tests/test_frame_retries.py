"""Retries count per frame (0.99.82): a frame that fails three times, each in a fresh Nuke, is given up alone and the rest
of its chunk renders on; frames that fail once each in one chunk all get their tries. Before, the chunk counted: three
failures of any of its frames gave up the chunk, and every frame left in it was marked failed untried. A script where
nothing renders still stops after three frames are given up."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "chunk_auto": False, "chunk_size": 4, "auto_chunk": False,
                 "check_pixels": False})
win = None


def render(name, env):
    global win
    job = make_job(name, 1001, 1012, env=env)
    if win is None:
        win = jk.MainWindow(job)
        e = win.engine
    else:
        win.add_job(job, auto=False)
        e = win.manager.engines[-1]
        win.select(e)
    win._start()
    wait(lambda: e.state in ("done", "failed", "stopped", "ready"), 180)
    return job, e


say("Frame 1006 fails every time: only it is given up, 1007 and 1008 of its chunk render")
job, e = render("onebad", {"FAKE_FAIL_FRAMES": "1006"})
failed = sorted(f for f, fr in e.frames.items() if fr.state == "failed")
check("only 1006 failed", failed == [1006], failed)
check("after three tries", e.frames[1006].attempts == 3, e.frames[1006].attempts)
check("the other 11 on disk", len(files(job)) == 11, files(job))
check("the Log says it and that the rest went on", bool(log_lines(e, "frame 1006 failed 3 times, each in a fresh Nuke")) and
      bool(log_lines(e, "renders on as")), log_lines(e, "given up"))
check("the render ends failed, for one frame", e.state == "failed" and "1 frame(s) failed" in e.error, (e.state, e.error))

say("1006 and 1007, in one chunk, fail once each: both get their tries, nothing is given up")
job, e = render("flaky", {"FAKE_FLAKY_FRAMES": "1006,1007"})
check("done", e.state == "done", (e.state, e.error))
check("all 12 on disk", len(files(job)) == 12, files(job))
check("nothing given up", not log_lines(e, "given up"), log_lines(e, "given up"))

say("Nothing renders: it stops after three frames are given up")
job, e = render("allbad", {"FAKE_FAIL_FRAMES": ",".join(str(f) for f in range(1001, 1013))})
check("stopped as failed", e.state == "failed" and "frames failed and no frame rendered" in e.error, (e.state, e.error))
check("not every frame tried", len([f for f, fr in e.frames.items() if fr.state == "failed"]) < 12,
      sorted(f for f, fr in e.frames.items() if fr.state == "failed"))
finish()
