"""Fresh Nuke after every chunk: Off, When memory is short (RAM low, RAM held near the limit), Always. A fresh Nuke takes
the same place (W01, W02), State says it ahead and why, the render ends whole."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "chunk_auto": False, "chunk_size": 4, "auto_chunk": False})
jk.FRESH_HOLD = 1.0

for mode, ram, want in (("off", None, "none"), ("short", 40.0, "none"), ("short", 91.0, "some"), ("always", None, "some")):
    say("Fresh Nuke %s%s" % (mode, ", RAM %.0f %%" % ram if ram else ""))
    job = make_job("r_%s_%s" % (mode, int(ram or 0)), 1001, 1024)
    win = jk.MainWindow(job)
    e = win.engine
    win.c_fresh.setCurrentIndex(win.c_fresh.findData(mode))
    if ram is not None:
        win.manager.ram_percent = lambda r=ram: r
    win._start()
    seen = set()

    def going():
        seen.update(e.worker_state(w) for w in e.workers.values())
        return e.state in ("done", "failed", "stopped")

    wait(going, 180)
    fresh = log_lines(e, "fresh Nuke after its chunk")
    loads = {seg[1] for w in e.workers.values() for seg in w.segments if seg[0] == "load"}
    check("done, 24 files", e.state == "done" and len(files(job)) == 24, (e.state, len(files(job))))
    check("the places stay W01 and W02", sorted(e.workers) == [1, 2], sorted(e.workers))
    check("setting saved", win.settings.get("fresh_after_chunk") == mode)
    if want == "none":
        check("no fresh Nuke", not fresh, fresh[:1])
        check("State never says fresh Nuke", not any("fresh" in s for s in seen), sorted(seen))
    else:
        check("fresh Nukes after chunks", len(fresh) >= 3, len(fresh))
        why = "busy → fresh Nuke" if mode == "always" else "busy → fresh Nuke (RAM 91 %)"
        check("State says it ahead: '%s'" % why, why in seen, sorted(seen))
        check("the timeline shows the reload", "again: fresh Nuke" in loads, loads)
        check("never for the last chunks", not any("no chunks" in x for x in fresh))
    win.close()
    win = None
finish()
