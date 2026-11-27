"""Delete rendered frames: the frame bar and the chunks show them as not rendered, whatever they showed before; also right
after a Stop, while the checks of what was rendered still run in the background."""
from common import *

jk, app = start({"processes": 3, "threads": 1, "chunk_auto": False, "chunk_size": 4, "check_pixels": True})


def summary(e):
    return sorted(collections.Counter((e.frames[f].state, jk.frame_color(e.frames[f]).name()) for f in e.frame_list()).items())


import collections
queued = None

say("1. Render, Stop, Render, Stop, then Delete at once")
job = make_job("sh020", 1001, 1060, env={"FAKE_SECS": "0.15"})
win = jk.MainWindow(job)
e = win.engine
queued = jk.QtGui.QColor(jk.C["queued"]).name()
for run in range(2):
    if run:
        answer("Only missing")  # the frames on disk: as you did, only what is missing
    win._start()
    wait(lambda: sum(fr.state == "done" for fr in e.frames.values()) >= 10 * (run + 1), 60)
    win._stop()
    pump(0.3)
say("  stopped: %s" % summary(e))
pump(3)  # the checks of what was rendered are in: nothing more to report
files = win._files_of(e)
repaints = []
win.frame_map.update = lambda *a: repaints.append(1)
win._after_delete(win._delete_files(e, files))
say("  right after Delete: %s" % summary(e))
pump(5)
say("  5 s later: %s" % summary(e))
say("  chunks: %s" % sorted(collections.Counter(c.state for c in e.chunks).items()))
check("every frame grey (queued)", all(jk.frame_color(e.frames[f]).name() == queued and e.frames[f].state == "queued"
                                       for f in e.frame_list()), summary(e))
check("the frame bar repaints: it showed the deleted frames as rendered", repaints)
check("every chunk queued", all(c.state == "queued" for c in e.chunks), sorted(collections.Counter(c.state for c in e.chunks).items()))
finish()
