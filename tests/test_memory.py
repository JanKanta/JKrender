"""Since 0.99.30 there is no Memory Limit: over Reload Nuke at (95 %) nothing closes at once, Reload Nuke takes the
process holding the most. The RAM and VRAM guards with made-up readings: closing at the limit, coming back when there is room, the full card,
a card so full no frame finishes, Fresh Nuke when memory is short, and that State says what the decision is."""
import time
import types
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
        s.rss_base = None
        s.retire = s.handover = s.leave = False
        s.chunk, s.closed_why, s.frame_secs, s.frame_ends = None, None, [10.0], [time.time()]


class E:
    def __init__(s, ws):
        s.workers, s.state, s.notes, s.killed, s.started, s.notice, s.settings = ws, "rendering", [], [], time.time(), None, {}
        s.size_seen, s.ram, s._vram_seen, s.last_frame_at = 26.0, 20, 0, time.time()
        s.movie = False

    def note(s, t, lvl="ev"):
        s.notes.append(t)

    def size_gb(s):
        return s.size_seen

    def _kill(s, w, requeue=False, note=""):
        s.killed.append(w.name)
        w.state = "gone"


def manager(n=4, **settings):
    base = {"processes": n, "threads": 8, "ram": 20, "ram_guard": True, "vram_guard": True, "use_gpu": True}
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


say("RAM over Reload Nuke at: nothing closes at once; a place held down comes back when it fits")
check("Reload Nuke at is 95 % by default, and it is the limit places come back under",
      (jk.pct_limit({}, "RAM"), jk.pct_limit({}, "VRAM")) == (95.0, 95.0))
m, e, ws = manager()
pct = {"v": 96.0}
m.ram_percent = lambda: pct["v"]
m.vram = [10, 24576 * 0.3, 24576.0]
m._ram_guard()
check("RAM 96 %: no 'one process fewer' any more, none closes", m.ram_cap is None and e.killed == [], (m.ram_cap, e.killed))
m._hold_down("RAM", 96.0, live=3)  # as when a reload's fresh Nuke does not fit
check("a place that does not fit waits: 3 places", m.ram_cap == 3, m.ram_cap)
ws[4].state = "gone"
check("the ones going are not counted as still to come", m._ram_pending() < 0.5, m._ram_pending())
for v, want in ((96.0, 3), (80.0, None)):  # under Reload Nuke at (95 %) it comes back, as after a reload
    pct["v"] = v
    settle(m, m._ram_guard)
    check("RAM %.0f %%: places %s" % (v, want or "all back"), m.ram_cap == want, m.ram_cap)
ws[4].state, ws[4].frames_here, ws[4].rss = "loading", 0, 2.0
check("one opening the script counts what it will take", abs(m._ram_pending() - 24.0) < 0.1, m._ram_pending())

say("VRAM over Reload Nuke at: nothing closes at once; a place held down comes back when it fits")
m, e, ws = manager()
m.ram_percent = lambda: 40.0
m.gpu_mem = {w.proc.pid: (50, 4000 * 1024 ** 2) for w in ws.values()}
m.vram = [90, 24576 * 0.96, 24576.0]
m.vram_others = lambda: 1.0
m._vram_guard()
check("VRAM 96 %: none closes", m.vram_cap is None and e.killed == [], (m.vram_cap, e.killed))
m._hold_down("VRAM", 96.0, live=3)
check("a place that does not fit waits: 3 places", m.vram_cap == 3, m.vram_cap)
ws[4].state = "gone"
for v, want in ((96.0, 3), (80.0, None)):
    m.vram = [60, 24576 * v / 100, 24576.0]
    settle(m, m._vram_guard)
    check("VRAM %.0f %%: places %s" % (v, want or "all back"), m.vram_cap == want, m.vram_cap)
say("Places come back one at a time: the next only once the last one has rendered a frame")
m, e, ws = manager()
m.ram_percent = lambda: 60.0
m.vram = [10, 24576 * 0.3, 24576.0]
m._hold_down("RAM", 96.0, live=2)
ws[3].state = ws[4].state = "gone"
ws[2].frames_here, ws[2].spawned = 0, time.time()  # W02 has not rendered a frame yet
settle(m, m._ram_guard)
check("RAM 60 %, W02 before its first frame: no place back yet", m.ram_cap == 2, m.ram_cap)
check("State says why: W02 1st frame", "W02 has not rendered its first frame" in (m.no_room() or ""), m.no_room())
ws[2].frames_here = 1
m._ram_ok_since = None
settle(m, m._ram_guard, 0.5)
check("W02 has a frame: one place back, not both", m.ram_cap == 3, m.ram_cap)

say("A full card: the one started last closes at once; a card so full no frame finishes")
m, e, ws = manager()
m.ram_percent = lambda: 40.0
m.vram_others = lambda: 1.0
m.vram = [50, 24576 * 0.98, 24576.0]
m.gpu_mem = {w.proc.pid: (10, 2000 * 1024 ** 2) for w in ws.values()}
m.gpu_mem[ws[1].proc.pid] = (10, 9000 * 1024 ** 2)
m._last_vram_kill = m._last_kill = 0
m._vram_guard()
check("VRAM 98 %, all rendering: W01 (the largest, 9 GB) closed now", e.killed == ["W01"], e.killed)
check("State says closed (VRAM 98 %), no 'VRAM limit'", ws[1].closed_why == "VRAM 98 %", ws[1].closed_why)
check("and its place waits: 3 places", m.vram_cap == 3, m.vram_cap)
ws[1].state = "busy"
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

say("Kill at: from this % the one started last closes at once; never under Reload Nuke at")


def nearly_full(ram=None, vram=None, **settings):
    m, e, ws = manager(**settings)
    m.vram_others = lambda: 1.0
    m.ram_percent = lambda: ram if ram is not None else 40.0
    m.vram = [50, 24576 * (vram if vram is not None else 30) / 100, 24576.0]
    m.gpu_mem = {w.proc.pid: (10, 2000 * 1024 ** 2) for w in ws.values()}
    m._last_vram_kill = m._last_kill = 0
    m._ram_checked = m._vram_checked = 0
    m._ram_guard()
    m._vram_guard()
    return m, e, ws


m, e, ws = nearly_full(ram=96.5)
check("RAM 96.5 %, Kill at 98 % (default): none closes at once", e.killed == [], e.killed)
m, e, ws = nearly_full(ram=96.5, kill_ram=96)
check("RAM 96.5 %, Kill at 96 %: W04 closes at once", e.killed == ["W04"], e.killed)
check("State says closed (RAM 96 %)", ws[4].closed_why == "RAM 96 %", ws[4].closed_why)
m, e, ws = nearly_full(ram=93.0, kill_ram=80)
check("Kill at 80 % is never under the limit (95 %): RAM 93 %, none", e.killed == [], e.killed)
m, e, ws = nearly_full(vram=98.5)
check("VRAM 98.5 %, Kill at 98 % (default): W04 closes at once", e.killed == ["W04"], e.killed)
m, e, ws = nearly_full(vram=98.5, kill_vram=99)
check("VRAM 98.5 %, Kill at 99 %: none closes at once", e.killed == [], e.killed)
m, e, ws = nearly_full(vram=97.5, kill_vram=97)
check("VRAM 97.5 %, Kill at 97 %: W04 closes at once", e.killed == ["W04"], e.killed)
check("pct_kill: RAM 98, VRAM 98 by default", (jk.pct_kill({}, "RAM"), jk.pct_kill({}, "VRAM")) == (98.0, 98.0))

say("Kill at with the card held by other programs: none closes when closing one would not get it under Kill at")
m, e, ws = manager()
m.vram_others = lambda: 70.0
m.ram_percent = lambda: 40.0
m.vram = [50, 24576 * 0.995, 24576.0]  # 99.5 %: our four hold 0.2 GB each, the rest is NukeX
m.gpu_mem = {w.proc.pid: (10, 0.2 * 1024 ** 3) for w in ws.values()}
m._last_vram_kill = m._last_kill = 0
m._vram_checked = 0
m._vram_guard()
check("99.5 %, one of ours holds 0.2 GB (0.8 %): none closes, it would stay over 98 %", e.killed == [], e.killed)
check("the Log says why", any("other programs hold the card" in t for t in e.notes), e.notes[-2:])
m.gpu_mem[ws[2].proc.pid] = (10, 6 * 1024 ** 3)  # W02 holds 6 GB (24 %): closing it gets the card to 75 %
e.notes.clear()
m._vram_checked = 0
m._vram_guard()
check("W02 holds 6 GB: closing it helps, so it closes", e.killed == ["W02"], e.killed)

say("Kill at while processes start: the least loaded of the starting ones; once all render: the one holding the most")
m, e, ws = manager()
m.vram_others = lambda: 1.0
m.ram_percent = lambda: 40.0
m.vram = [50, 24576 * 0.985, 24576.0]
m.gpu_mem = {ws[1].proc.pid: (10, 9 * 1024 ** 3), ws[2].proc.pid: (10, 6 * 1024 ** 3),
             ws[3].proc.pid: (10, 1 * 1024 ** 3), ws[4].proc.pid: (10, 3 * 1024 ** 3)}
ws[3].frames_here = ws[4].frames_here = 0  # W03 and W04 have not rendered a frame yet
m._last_vram_kill = m._last_kill = 0
m._vram_checked = 0
m._vram_guard()
check("VRAM 98.5 %, W03 and W04 starting: W03 (the least loaded starting one) closes, not W01", e.killed == ["W03"], e.killed)
m, e, ws = manager()
m.vram_others = lambda: 1.0
m.vram = [10, 24576 * 0.3, 24576.0]
m.ram_percent = lambda: 98.5
for w, gb in zip(ws.values(), (12.0, 30.0, 14.0, 13.0)):
    w.rss = gb
m._last_vram_kill = m._last_kill = 0
m._ram_checked = 0
m._ram_guard()
check("RAM 98.5 %, all rendering: W02 (30 GB, the most) closes", e.killed == ["W02"], e.killed)
ws[2].state = "busy"
e.killed.clear()
ws[1].frames_here = ws[4].frames_here = 0
m._last_vram_kill = m._last_kill = 0
m._ram_checked = 0
m._ram_guard()
check("RAM 98.5 %, W01 and W04 starting: W01 (12 GB, the least loaded starting) closes", e.killed == ["W01"], e.killed)

say("Stalled just under the limit: the card full enough that the processes wait, VRAM 94.8 % with the limit at 95 %")


def stalled(vram_pct, cpu=(1.0, 1.0, 1.0, 1.0), idle_for=None, last_frame=5, frame_secs=10.0):
    m, e, ws = manager()
    m.ram_percent = lambda: 40.0
    m.vram_others = lambda: 1.0
    m.gpu_mem = {w.proc.pid: (10, 5000 * 1024 ** 2) for w in ws.values()}
    m.vram = [100, 24576 * vram_pct / 100, 24576.0]
    m._last_vram_kill = m._last_kill = 0
    e.started, e.last_frame_at = time.time() - 900, time.time() - last_frame
    for w, c in zip(ws.values(), cpu):
        w.cpu, w.frame_secs = c, [frame_secs]
    if idle_for is not None:
        m._cpu_idle_since = time.time() - idle_for
    m._vram_checked = 0
    m._vram_guard()
    return m, e, ws


m, e, ws = stalled(94.8, last_frame=300)
check("no frame for 5 minutes at 94.8 %: the one started last closes", e.killed == ["W04"], e.killed)
check("and the places go one down until the card has room", m.vram_cap == 3, m.vram_cap)
check("the note says why", any("no frame finished" in t and "W04 closed now" in t for t in e.notes), e.notes)
check("State says closed (GPU frozen)", ws[4].closed_why == "GPU frozen", ws[4].closed_why)
for v, want in ((96.0, 3), (60.0, None)):
    m.vram = [60, 24576 * v / 100, 24576.0]
    settle(m, m._vram_guard)
    check("VRAM %.0f %%: places %s" % (v, want or "all back"), m.vram_cap == want, m.vram_cap)
m, e, ws = stalled(94.8, cpu=(0.0, 0.02, 0.0, 0.01), idle_for=40)
check("every process 40 s without CPU at 94.8 %, a frame 5 s ago: one closes", e.killed == ["W04"], e.killed)
check("the note says they waited for the card", any("without using the CPU" in t for t in e.notes), e.notes)
m, e, ws = stalled(94.8, cpu=(0.0, 0.02, 0.0, 0.01), idle_for=10)
check("only 10 s without CPU: none closes", e.killed == [], e.killed)
m, e, ws = stalled(94.8, cpu=(0.0, 3.5, 0.0, 0.0), idle_for=40)
check("one process still uses the CPU: none closes", e.killed == [], e.killed)
m, e, ws = stalled(94.8, cpu=(0.0, 0.0, 0.0, 0.0), idle_for=40, frame_secs=60.0)
check("frames take a minute (a long GPU frame): 40 s without CPU is no stall", e.killed == [], e.killed)
m, e, ws = stalled(80.0, cpu=(0.0, 0.0, 0.0, 0.0), idle_for=400, last_frame=600)
check("VRAM 80 %: never a stall, whatever the CPU and the frames", e.killed == [] and m.vram_cap is None, (e.killed, m.vram_cap))
m, e, ws = stalled(94.8, cpu=(0.0, 0.0, 0.0, 0.0), idle_for=40)
e.killed.clear()
m._vram_checked = 0
m._cpu_idle_since = time.time() - 40
m._vram_guard()
check("not a second one within 15 s", e.killed == [], e.killed)
check("15 s without CPU, 15 s between two", (jk.STALL_CPU_SECS, jk.STALL_GAP) == (15, 15), (jk.STALL_CPU_SECS, jk.STALL_GAP))
m, e, ws = stalled(94.8, cpu=(0.0, 0.0, 0.0, 0.0), idle_for=17, frame_secs=5.0)
check("17 s without CPU, frames 5 s: one closes (15 s is enough)", e.killed == ["W04"], e.killed)
m, e, ws = stalled(94.8, cpu=(0.0, 0.0, 0.0, 0.0), idle_for=17, frame_secs=10.0)
check("17 s without CPU, frames 10 s: not yet (2 x the frame time, 20 s)", e.killed == [], e.killed)

say("The 1.3 rule: a place waits 5 s for memory, the process holding the most of it reloads after its chunk, if it grew 1.3 x")
check("5 s", jk.WAIT_RELOAD == 5, jk.WAIT_RELOAD)
GB = 1024 ** 3


def waiting(name, secs, rss=(20.0, 30.0, 22.0, 21.0), vram=(4.0, 5.0, 7.0, 4.5)):
    m, e, ws = manager()
    e.chunks = [types.SimpleNamespace(state="queued")]
    for w, r, v in zip(ws.values(), rss, vram):
        w.rss, w.rss_base, w.vram_base, w.state = r, 20.0, 4000.0, "busy"
    m.gpu_mem = {w.proc.pid: (10, v * GB) for w, v in zip(ws.values(), vram)}
    m.refreshing = lambda: ""
    if name:
        m._wait_since[name] = time.time() - secs
    return m, e, ws


m, e, ws = waiting("RAM", 20)
check("RAM: W01 does not reload, W02 holds the most", m.wait_reload(ws[1]) == "", m.wait_reload(ws[1]))
why = m.wait_reload(ws[2])
check("RAM, 20 s waiting: W02 reloads, 30 GB up from 20", "RAM" in why and "W02" in why and "30.0 GB" in why, why)
check("the Log calls it the 1.3 rule", why.startswith("1.3 rule:"), why)
check("then not again at once", m.wait_reload(ws[2]) == "")
m, e, ws = waiting("RAM", 3)
check("RAM, only 3 s waiting: none", m.wait_reload(ws[2]) == "")
m, e, ws = waiting("RAM", 6)
check("RAM, 6 s waiting: W02 reloads", "W02" in m.wait_reload(ws[2], peek=True))
check("asking for State (peek) does not start the count again", "W02" in m.wait_reload(ws[2], peek=True))
m, e, ws = waiting("RAM", 20, rss=(20.0, 23.0, 22.0, 21.0))
check("RAM: the biggest grew only from 20 to 23 GB (no leak): none", m.wait_reload(ws[2]) == "")
m, e, ws = waiting("VRAM", 20)
check("VRAM: not W02 (the most RAM)", m.wait_reload(ws[2]) == "")
why = m.wait_reload(ws[3])
check("VRAM: W03 holds the most VRAM, 7 GB up from 3.9: it reloads", "VRAM" in why and "W03" in why, why)
m, e, ws = waiting("VRAM", 20)
m.refreshing = lambda: "W01"
check("one reloading already: none (one at a time)", m.wait_reload(ws[3]) == "")
m, e, ws = waiting(None, 0)
m.total, m.ram_cap = 4, 3
ws[4].state = "gone"
m._wait_update()
check("a place held down by the RAM limit: the wait starts", "RAM" in m._wait_since, m._wait_since)
m.ram_cap = None
m._wait_update()
check("all places back: the wait is over", "RAM" not in m._wait_since, m._wait_since)

say("After a close for memory the wait is RETURN_CHECK every time, it does not grow")
m, e, ws = manager()
t = time.time()
m.falls.clear()
for back in (500, 300, 120, 0.1):
    m.falls.append(t - back)
w = m.fall_wait()
check("the fourth close in 10 minutes: still RETURN_CHECK, not 150 s", 0 < w <= jk.RETURN_CHECK, w)
m.falls.clear()
m.falls.append(t - jk.RETURN_CHECK - 1)
check("and none once it is over", m.fall_wait() == 0.0, m.fall_wait())

say("Reload Nuke when memory is full: only once held over Reload Nuke at (here set to 90 %), never because a process waits")
check("held 10 s by default", jk.FRESH_HOLD == 10, jk.FRESH_HOLD)
jk.FRESH_HOLD = 0.5
m, e, ws = manager(reload_ram=90, reload_vram=90)
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
check("RAM 91 % held: 'RAM over 90 %' (the threshold, not the reading)", short(91, 50, 0.3) == "RAM over 90 %", m.memory_short())
check("VRAM 93 % held too: both", short(91, 93, 0.6) == "RAM over 90 %, VRAM over 90 %", m.memory_short())
check("RAM drops: VRAM only", short(70, 93) == "VRAM over 90 %", m.memory_short())
check("both low: nothing", short(70, 50) == "")
m.ram_cap = m.vram_cap = 3
check("a process waiting to come back is no reason by itself", m.memory_short() == "", m.memory_short())
m.ram_cap = m.vram_cap = None
say("Reload Nuke at set lower (Settings): RAM 80 %, VRAM 85 %")
m.settings.update({"reload_ram": 80, "reload_vram": 85})
short(70, 50)
check("RAM 79 %: nothing", short(79, 50, 0.6) == "")
check("RAM 82 % held: RAM, under the old 90 %", short(82, 50, 0.6) == "RAM over 80 %", m.memory_short())
check("VRAM 86 % held too: both", short(82, 86, 0.6) == "RAM over 80 %, VRAM over 85 %", m.memory_short())
m.settings.update({"reload_ram": 90, "reload_vram": 90})
check("back to 90 %: VRAM 86 % is no reason", short(70, 86, 0.6) == "", m.memory_short())

say("State says what a place waits for: Reload Nuke at, or a process before its first frame")
m, e, ws = manager(6)
ws[6].state = "gone"
m.vram_cap = 5
m.falls = []
m.vram = [20, 24576 * 0.96, 24576.0]
why = jk.MainWindow._memory_why(type("X", (), {"manager": m})())
check("VRAM 96 %: 'wait: Reload at 95 % of VRAM'", jk.MainWindow._short_why(why) == "wait: Reload at 95 % of VRAM", (why, jk.MainWindow._short_why(why)))
ws[3].frames_here, ws[3].spawned = 0, time.time()
why = jk.MainWindow._memory_why(type("X", (), {"manager": m})())
check("W03 before its first frame: 'wait: W03 1st frame'", jk.MainWindow._short_why(why) == "wait: W03 1st frame", why)
ws[3].frames_here = 1
m.vram = [20, 24576 * 0.80, 24576.0]
check("VRAM 80 %, none starting: one more fits", m._more_fit_vram(80.0, 95, 10.0) == 1)
finish()
