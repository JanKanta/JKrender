"""Movies: each a process of its own with every thread, beside the places of Processes (0.99.55); an image sequence keeps
all its places when a movie starts, with Reload after Frame and Chunk; System rows; the Write column in System, with the file each render writes."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "max_renders": 1, "chunk_auto": False, "chunk_size": 4})
slow = {"FAKE_SECS": "1"}
movies = [make_job("mov%d" % i, 1001, 1040, movie=True, env=slow) for i in (1, 2, 3)]
win = jk.MainWindow(movies[0])
for j in movies[1:]:
    win.add_job(j, auto=False)
m = win.manager
m.ram_percent = lambda: 40.0  # whatever this PC does: memory does not hold anything here
mov = [e for e in m.engines if e.movie]
check("three movies", len(mov) == 3, [e.name for e in m.engines])
m.set_budget(total=8)
win._update_system()
check("only movies open, Processes 8: System lists 3 places, one a movie, not 8", win.worker_table.rowCount() == 3,
      win.worker_table.rowCount())
m.set_budget(total=2)

say("Render all three: each at once in a process of its own, with every thread, beside Processes 2 (Max renders 1)")
for e in mov:
    m.request_start(e)
wait(lambda: all(any(w.state != "gone" for w in e.workers.values()) for e in mov), 60)
pump(1)
per = [sum(1 for w in e.workers.values() if w.state != "gone") for e in mov]
check("one process a movie, all three at once", per == [1, 1, 1], per)
check("none of them in the places of Processes", m.live_count() == 0 and m.movie_count() == 3, (m.live_count(), m.movie_count()))
cmd = mov[0]._command()
cpu = os.cpu_count()
check("with every thread: -m %d" % cpu, cmd[cmd.index("-m") + 1] == str(cpu), cmd[:6])
check("only movies: W01, W02, W03", sorted(w.id for e in mov for w in e.workers.values() if w.state != "gone") == [1, 2, 3],
      sorted(w.id for e in mov for w in e.workers.values()))

say("System: a Write column after Render, shown")
t = win.worker_table
head = t.horizontalHeader()
names = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
col = names.index("Write")
check("shown", not t.isColumnHidden(col))
check("right after Render", head.visualIndex(col) == head.visualIndex(names.index("Render")) + 1,
      (head.visualIndex(col), head.visualIndex(names.index("Render"))))
win._slow_tick()
pump(2)
writes = {t.item(r, col).text() for r in range(t.rowCount()) if t.item(r, col) and t.item(r, 1) and t.item(r, 1).text()}
check("it names the file each render writes, not the Write node (0.99.73)", writes and all(w.endswith(".mov") for w in writes) and "Write1" not in writes, writes)
check("columns CPU, GPU and VRAM (CPU used and GPU with VRAM in it before 0.99.51)",
      "CPU" in names and "GPU" in names and "VRAM" in names and "CPU used" not in names
      and head.visualIndex(names.index("VRAM")) == head.visualIndex(names.index("GPU")) + 1, names)
for e in m.engines:
    e.stop("test done")
wait(lambda: all(w.state == "gone" for e in m.engines for w in e.workers.values()), 60)
for e in list(m.engines):
    win._close_card(e)

say("An image sequence rendering, then a movie: the movie gets a process of its own, the sequence keeps all its places")
m.set_budget(total=4)
m.settings["max_renders"] = 3
for after in ("frame", "chunk"):
    win.c_reload_after.setCurrentIndex(win.c_reload_after.findData(after))
    seq = make_job("exr_" + after, 1001, 1060, env={"FAKE_SECS": "0.5"})
    movie = make_job("mov_" + after, 1001, 1020, movie=True, env={"FAKE_SECS": "0.3"})
    win.add_job(seq, auto=False)
    s_e = m.engines[-1]
    m.request_start(s_e)
    wait(lambda: sum(1 for w in s_e.workers.values() if w.state == "busy") == 4, 60)
    win.add_job(movie, auto=False)
    m_e = m.engines[-1]
    t0 = time.time()
    m.request_start(m_e)
    wait(lambda: any(w.state in ("loading", "idle", "busy") for w in m_e.workers.values()), 30)
    took = time.time() - t0
    pump(1.5)
    seq_live = sum(1 for w in s_e.workers.values() if w.state != "gone")
    check("%s: the movie starts at once (%.1f s), no place taken from the sequence" % (after, took), took < 2.0, took)
    check("%s: the sequence still has its 4 processes" % after, seq_live == 4 and not log_lines(s_e, "another render"),
          (seq_live, log_lines(s_e, "another render")[:1]))
    mw = [w for w in m_e.workers.values() if w.state != "gone"]
    check("%s: the movie's process is W05, after the places W01-W04" % after, [w.id for w in mw] == [5], [w.id for w in mw])
    win._update_system()
    check("%s: System lists the 4 places and the movie's row" % after, win.worker_table.rowCount() == 5, win.worker_table.rowCount())
    for x in (m_e, s_e):  # FakeNuke does not finish a movie: stop both
        x.stop("test")
    wait(lambda: all(w.state == "gone" for x in (m_e, s_e) for w in x.workers.values()), 60)
    for e in list(m.engines):
        win._close_card(e)
    pump(0.5)
finish()
