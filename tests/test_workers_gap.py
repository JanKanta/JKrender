"""Workers cuts out the stretches with no process at all (0.99.91): a render done, the next one sent later into the open
window, and the timeline no longer spends its width on the empty time between them; only a little space stands
there (1.0.10: no dashed lines any more; 1.0.9: no time axis under the rows). Between two renders even a short empty
stretch is cut (1.0.11); inside a render a pause shorter than GAP_CUT stays. The chunks' tooltips still find their frames. GAP_CUT is lowered so the test waits seconds."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "check_pixels": False})
jk.GAP_CUT = 2.0
job_a = make_job("first", 1001, 1006)
win = jk.MainWindow(job_a)
win.resize(1400, 900)
win.show()
a = win.engine
win._start()
check("first render done", wait(lambda: a.state == "done" and all(w.state == "gone" for w in a.workers.values()), 120), a.state)
pump(4.0)  # nothing runs for 4 s, over GAP_CUT
job_b = make_job("second", 1001, 1006)
win.add_job(job_b, auto=False)
b = win.manager.engines[-1]
win.select(b)
win.gantt.show_all = True
win._start()
check("second render done", wait(lambda: b.state == "done" and all(w.state == "gone" for w in b.workers.values()), 120), b.state)
pump(0.5)


def edges():
    win.gantt.grab()  # paints it
    blocks = win.gantt._blocks
    end_a = max(r.right() for r, e, *_ in blocks if e is a)
    start_b = min(r.left() for r, e, *_ in blocks if e is b)
    return start_b - end_a


cut = edges()
win.gantt.grab().save(RUN + "/gantt_gap.png")
check("the empty 4 s between the renders is cut out: %d px of space" % jk.GAP_PX, 0 < cut <= jk.GAP_PX + 6, cut)
img = win.gantt.grab().toImage()
end_a = max(r.right() for r, e, *_ in win.gantt._blocks if e is a)
start_b = min(r.left() for r, e, *_ in win.gantt._blocks if e is b)
rows_end = int(max(y1 for _, y1, *_ in win.gantt._rows))
between = {img.pixel(x, yy) for x in range(int(end_a) + 2, int(start_b) - 1) for yy in range(2, rows_end)}
check("nothing drawn in it: no dashed lines (1.0.10)", len(between) == 1, len(between))
img = win.gantt.grab().toImage()
below = int(max(y1 for _, y1, *_ in win.gantt._rows)) + 2
colours = {img.pixel(x, yy) for yy in range(below, img.height()) for x in range(0, img.width(), 2)}
check("no time axis under the rows (1.0.9): nothing drawn there", below < img.height() - 16 and len(colours) == 1,
      (below, img.height(), len(colours)))
jk.GAP_CUT = 60.0
kept = edges()
check("between two renders even a stretch shorter than GAP_CUT is cut (1.0.11): %.0f px" % kept, abs(kept - cut) <= 1, (kept, cut))
# inside one render: a chunk of the second render 4 s after its last one, a pause under GAP_CUT keeps its length
last_b = max(s[3] for w in b.workers.values() for s in w.segments if s[3])
w0 = list(b.workers.values())[0]
w0.segments.append(("chunk", "late", last_b + 4.0, last_b + 6.0, "done"))
pump(max(0.0, last_b + 6.5 - time.time()))  # Workers draws nothing that starts in the future
win.gantt.grab()
blocks = win.gantt._blocks
inner = (min(r.left() for r, e, w, k, label, *_ in blocks if label == "late")
         - max(r.right() for r, e, w, k, label, *_ in blocks if e is b and label != "late"))
check("inside a render a pause shorter than GAP_CUT stays (%.0f px here)" % inner, inner > cut + 20, (inner, cut))
w0.segments.pop()
win.gantt.grab()  # the blocks again without it
jk.GAP_CUT = 2.0
tips = [jk.Gantt._chunk_tip(e, w, label, x0, x1, state, w.name) for _, e, w, kind, label, x0, x1, state in win.gantt._blocks
        if kind == "chunk"]
check("the chunks' tooltips still find their frames, in both renders", len(tips) >= 2
      and all(t.splitlines()[1].endswith(")") for t in tips), tips[:2])
finish()
