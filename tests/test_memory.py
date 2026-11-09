"""The RAM and VRAM guards with made-up readings: closing at the limit, coming back when there is room, the full card,
a card so full no frame finishes, Fresh Nuke when memory is short, and that State says what the decision is."""
import time
from common import *

jk, app = start(qt=False)
jk.RETURN_CHECK = 0.3


class P:
    def __init__(s, pid):
        s.pid = pid


class W:
    def __init__(s, i, base=4000.0):
        s.name, s.id, s.proc, s.gpu, s.state, s.pinned = "W%02d" % i, i, P(100 + i), True, "busy", False
        s.rss, s.frames_here, s.vram_base, s.spawned = 20.0, 10, base, time.time() - 900 + i
        s.retire = s.handover = s.leave = False
        s.chunk, s.closed_why, s.frame_secs, s.frame_ends = None, None, [10.0], [time.time()]


class E:
    def __init__(s, ws):
        s.workers, s.state, s.notes, s.killed, s.started, s.notice, s.settings = ws, "rendering", [], [], time.time(), None, {}
        s.size_seen, s.ram, s._vram_seen, s.last_frame_at = 26.0, 20, 0, time.time()

    def note(s, t, lvl="ev"):
        s.notes.append(t)

    def size_gb(s):
        return s.size_seen

    def _kill(s, w, requeue=False, note=""):
        s.killed.append(w.name)
        w.state = "gone"


def manager(n=4, **settings):
    base = {"processes": n, "threads": 8, "ram": 20, "ram_guard": True, "ram_limit": 95, "vram_guard": True, "vram_limit": 95,
            "use_gpu": True}
    base.update(settings)
    m = jk.RenderManager(base)
    ws = {i: W(i) for i in range(1, n + 1)}
    e = E(ws)
    m.engines, m.total = [e], n
    m.live_count = lambda: sum(1 for w in ws.values() if w.state != "gone")
    m.falls, m.vram_falls = [time.time() - 700], []
    m.vram, m.gpu_mem = [50, 0, 24576.0], {}
    return m, e, ws


def settle(m, fn, secs=1.0):
    t = time.time()
    while time.time() - t < secs:
        fn()
        time.sleep(0.05)
        m._ram_checked = m._vram_checked = 0


say("RAM over the limit, then free again")
m, e, ws = manager()
pct = {"v": 96.0}
m.ram_percent = lambda: pct["v"]
m.vram = [10, 24576 * 0.3, 24576.0]
m._ram_guard()
check("RAM 96 % over 95 %: one process fewer", m.ram_cap == 3, m.ram_cap)
ws[4].state = "gone"
check("the ones going are not counted as still to come", m._ram_pending() < 0.5, m._ram_pending())
for v, want in ((80.0, 3), (62.0, None)):
    pct["v"] = v
    settle(m, m._ram_guard)
    check("RAM %.0f %%: places %s" % (v, want or "all back"), m.ram_cap == want, m.ram_cap)
ws[4].state, ws[4].frames_here, ws[4].rss = "loading", 0, 2.0
check("one opening the script counts what it will take", abs(m._ram_pending() - 24.0) < 0.1, m._ram_pending())

say("VRAM over the limit, then free again")
m, e, ws = manager()
m.ram_percent = lambda: 40.0
m.gpu_mem = {w.proc.pid: (50, 4000 * 1024 ** 2) for w in ws.values()}
m.vram = [90, 24576 * 0.96, 24576.0]
m.vram_others = lambda: 1.0
m._vram_guard()
check("VRAM 96 %: one process fewer", m.vram_cap == 3, m.vram_cap)
ws[4].state = "gone"
for v, want in ((80.0, 3), (55.0, None)):
    m.vram = [60, 24576 * v / 100, 24576.0]
    settle(m, m._vram_guard)
    check("VRAM %.0f %%: places %s" % (v, want or "all back"), m.vram_cap == want, m.vram_cap)

say("A full card: the one started last closes at once; a card so full no frame finishes")
m, e, ws = manager()
m.ram_percent = lambda: 40.0
m.vram_others = lambda: 1.0
m.vram = [50, 24576 * 0.98, 24576.0]
m.gpu_mem = {w.proc.pid: (10, 2000 * 1024 ** 2) for w in ws.values()}
m.gpu_mem[ws[1].proc.pid] = (10, 9000 * 1024 ** 2)
m._last_vram_kill = m._last_kill = 0
m._vram_guard()
check("VRAM 98 %: W04 (started last) closed now, not W01 (the largest)", e.killed == ["W04"], e.killed)
ws[4].state = "busy"
e.killed.clear()
e.started, e.last_frame_at = time.time() - 600, time.time() - 300
m._last_vram_kill = m._last_kill = 0
m.vram_cap = None
m.vram = [50, 24576 * 0.96, 24576.0]
m._vram_checked = 0
m._vram_guard()
check("VRAM 96 %, no frame for 5 minutes: one closes", len(e.killed) == 1, e.killed)
e.killed.clear()
e.last_frame_at = time.time() - 20
m._last_vram_kill = 0
m._vram_checked = 0
m._vram_guard()
check("VRAM 96 %, a frame 20 s ago: none closes at once", e.killed == [], e.killed)

say("Fresh Nuke when memory is short: only after 30 s near the limit, never because a process waits")
jk.FRESH_HOLD = 0.5
m, e, ws = manager()
r = {"ram": 80.0}
m.ram_percent = lambda: r["ram"]


def short(ram, vram, wait_s=0.0):
    r["ram"] = ram
    m.vram = [0, vram * 245.76, 24576.0]
    m._short_update()
    if wait_s:
        time.sleep(wait_s)
        m._short_update()
    return m.memory_short()


check("RAM 80 %: nothing", short(80, 50) == "")
check("RAM 91 % once: nothing yet", short(91, 50) == "")
check("RAM back to 85 %: the count starts again", short(85, 50) == "")
check("RAM 91 % for 0.3 s: not yet", short(91, 50, 0.3) == "")
check("RAM 91 % held: RAM", short(91, 50, 0.3) == "RAM 91 %", m.memory_short())
check("VRAM 93 % held too: both", short(91, 93, 0.6) == "RAM 91 %, VRAM 93 %", m.memory_short())
check("RAM drops: VRAM only", short(70, 93) == "VRAM 93 %", m.memory_short())
check("both low: nothing", short(70, 50) == "")
m.ram_cap = m.vram_cap = 3
check("a process waiting to come back is no reason by itself", m.memory_short() == "", m.memory_short())

say("State says what the decision is: the target with the room kept for processes still starting")
GB = 1024 ** 3
for label, bases, now_gb, starting in (("3 just started", {1: 6144.0, 5: 5120.0}, {1: 6, 2: 1.5, 3: 1.5, 4: 2, 5: 5}, 3),
                                       ("none starting", {i: 4096.0 for i in range(1, 6)}, {1: 4, 2: 4, 3: 3, 4: 3, 5: 3}, 0)):
    m, e, ws = manager(6)
    for i, w in ws.items():
        w.vram_base = bases.get(i)
    ws[6].state = "gone"
    m.vram_cap = 5
    m.falls = []
    m.gpu_mem = {100 + i: (10, now_gb[i] * GB) for i in now_gb}
    used = sum(now_gb.values()) + 2
    m.vram = [20, used * 1024, 24576.0]
    why = jk.MainWindow._memory_why(type("X", (), {"manager": m})())
    shown = jk.MainWindow._short_why(why)
    fits = m._more_fit_vram(used / 24 * 100, 95, m.vram_one()) > 0
    target = float(shown.split("→")[1].split("%")[0])
    check("%s: '%s'" % (label, shown), ("(%d starting)" % starting in shown) if starting else "starting" not in shown, why[:120])
    check("%s: it comes back exactly when VRAM is under the target shown" % label, (used / 24 * 100 <= target) == fits,
          (used / 24 * 100, target, fits))
finish()
