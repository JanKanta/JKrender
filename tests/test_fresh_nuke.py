"""Fresh Nuke after every chunk: Off, When memory is short (RAM low, RAM held near the limit), Always. A fresh Nuke takes
the same place (W01, W02), State says it ahead and why, the render ends whole. When memory is short, one at a time."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "chunk_auto": False, "chunk_size": 4, "auto_chunk": False})
jk.FRESH_HOLD = 1.0

def in_refresh(e):
    """Processes getting a fresh Nuke right now: closing for it, waiting to start, or the new one before its first frame."""
    n = 0
    for w in e.workers.values():
        if w.state == "quitting" and w.closed_why == "reload":
            n += 1
        elif w.state in ("loading", "idle", "busy") and not w.frames_here and                 [seg[1] for seg in w.segments if seg[0] == "load"][-1:] == ["again: reload"]:
            n += 1  # the new one, before its first frame (known by its timeline, so this also tells on older versions)
        elif w.state == "gone" and w.id in e.refresh_ids:
            n += 1
    return n


for mode, ram, want, procs in (("off", None, "none", 2), ("short", 40.0, "none", 2), ("short", 91.0, "some", 4),
                               ("always", None, "some", 2)):
    say("Fresh Nuke %s%s, %d processes" % (mode, ", RAM %.0f %%" % ram if ram else "", procs))
    job = make_job("r_%s_%s" % (mode, int(ram or 0)), 1001, 1024 if procs == 2 else 1048,
                   env={"FAKE_LOAD_SECS": "2", "FAKE_SECS": "0.8"} if procs > 2 else None)  # a longer script load: chunks ending together show
    win = jk.MainWindow(job)
    win.s_workers.setValue(procs)
    e = win.engine
    win.c_fresh.setCurrentIndex(win.c_fresh.findData(mode))
    win.s_reload_ram.setValue(90)
    if ram is not None:
        # RAM drops while a process reloads (the old Nuke gone, the new one still small), as it does
        win.manager.ram_percent = lambda r=ram, e=e: 80.0 if r > 90 and any(
            w.state in ("quitting", "gone", "loading") for w in e.workers.values()) else r
    win._start()
    seen = set()
    most = {"n": 0, "shown": 0}

    def going():
        states = [e.worker_state(w) for w in e.workers.values()]
        seen.update(states)
        most["n"] = max(most["n"], in_refresh(e))
        most["shown"] = max(most["shown"], sum(1 for x in states if "→ reload" in x))
        return e.state in ("done", "failed", "stopped")

    wait(going, 180)
    fresh = log_lines(e, "reloads after its chunk")
    loads = {seg[1] for w in e.workers.values() for seg in w.segments if seg[0] == "load"}
    frames = 24 if procs == 2 else 48
    check("done, %d files" % frames, e.state == "done" and len(files(job)) == frames, (e.state, len(files(job))))
    check("the places stay W01-W%02d" % procs, sorted(e.workers) == list(range(1, procs + 1)), sorted(e.workers))
    check("setting saved", win.settings.get("fresh_after_chunk") == mode)
    if want == "none":
        check("no fresh Nuke", not fresh, fresh[:1])
        check("State never says reload", not any("reload" in s for s in seen), sorted(seen))
    else:
        check("fresh Nukes after chunks", len(fresh) >= (3 if mode == "always" else 1), len(fresh))
        why = "busy → reload" if mode == "always" else "busy → reload (RAM 91 %)"
        check("State says it ahead: '%s'" % why, why in seen, sorted(seen))
        check("the timeline shows the reload", "again: reload" in loads, loads)
        check("never for the last chunks", not any("no chunks" in x for x in fresh))
        if mode == "short":
            check("one at a time: never two fresh Nukes at once", most["n"] <= 1, most["n"])
            check("State shows 'busy → reload' on one process at a time", most["shown"] == 1, most["shown"])
    win.close()
    win = None
say("RAM holding 2 of 4 places: one marked 'closes (RAM full)' at a time")
job = make_job("limit_one", 1001, 1040, env={"FAKE_SECS": "1.0"})
win = jk.MainWindow(job)
win.s_workers.setValue(4)
e = win.engine
win.c_fresh.setCurrentIndex(win.c_fresh.findData("off"))
win._start()
wait(lambda: sum(1 for w in e.workers.values() if w.state == "busy") == 4, 60)
win.manager.ram_cap = 2
e.workers_target = 2
e._balance_workers()
e._balance_workers()
marked = [e.worker_state(w) for w in e.workers.values() if "closes (RAM full)" in e.worker_state(w)]
check("only one shows 'busy → closes (RAM full)', no 'RAM limit'", len(marked) == 1, [e.worker_state(w) for w in e.workers.values()])
win._stop()
wait(lambda: e.state == "stopped", 30)
win.close()
say("The RAM stays over Reload Nuke at after a reload: the fresh Nuke waits for room, the render ends whole")
job = make_job("reload_waits", 1001, 1080, env={"FAKE_SECS": "0.5"})
win = jk.MainWindow(job)
win.s_workers.setValue(4)
e = win.engine
win.c_fresh.setCurrentIndex(win.c_fresh.findData("short"))
win.s_reload_ram.setValue(90)
win.manager.ram_percent = lambda: 92.0
win._start()
waits = []


def watch():
    waits.extend(x[1] for x in e.log if "waits to start again" in x[1] and x[1] not in waits)
    return e.state in ("done", "failed", "stopped")


wait(watch, 180)
check("a reloaded process waits to start again (RAM over Reload Nuke at)", bool(waits), [x[1] for x in e.log][-8:])
check("its place is held down", win.manager.ram_cap is not None or e.state == "done", win.manager.ram_cap)
check("done, all 80 frames", e.state == "done" and len(files(job)) == 80, (e.state, len(files(job))))
win.close()
finish()
