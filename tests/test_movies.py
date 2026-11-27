"""Movies: each a process of its own with every thread, beside the places of Processes (0.99.55); an image sequence keeps
all its places when a movie starts, with Reload after Frame and Chunk; System rows; the File column in System, with the file each render writes."""
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
win._slow_tick()
check("Nuke processes: only movies, so '3 for movies', not '0 of 8 for all renders' (1.0.4)",
      win.t_workers.sub.text() == "rendering · 3 for movies", win.t_workers.sub.text())

say("System: a File column after Script, shown (Write and Render before 0.99.74)")
t = win.worker_table
head = t.horizontalHeader()
names = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
col = names.index("File")
check("shown", not t.isColumnHidden(col))
old = jk.SETTINGS_FILE.read_text(encoding="utf-8") if jk.SETTINGS_FILE.exists() else None
jk.SETTINGS_FILE.write_text(json.dumps({"system_order": ["Process", "Render", "Write", "State"], "system_hidden": ["Write", "PID"]}), encoding="utf-8")
got = jk.read_settings()
check("saved columns from before 0.99.74 come back as Script and File", got["system_order"] == ["Process", "Script", "File", "State"]
      and got["system_hidden"] == ["File", "PID"], (got["system_order"], got["system_hidden"]))
if old is None:
    jk.SETTINGS_FILE.unlink()
else:
    jk.SETTINGS_FILE.write_text(old, encoding="utf-8")
check("right after Script", head.visualIndex(col) == head.visualIndex(names.index("Script")) + 1,
      (head.visualIndex(col), head.visualIndex(names.index("Script"))))
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
    win._slow_tick()
    check("%s: Nuke processes counts both: '4 of 4 for all renders + 1 for movies'" % after,
          win.t_workers.sub.text().endswith("4 of 4 for all renders + 1 for movies"), win.t_workers.sub.text())
    for x in (m_e, s_e):  # FakeNuke does not finish a movie: stop both
        x.stop("test")
    wait(lambda: all(w.state == "gone" for x in (m_e, s_e) for w in x.workers.values()), 60)
    for e in list(m.engines):
        win._close_card(e)
    pump(0.5)

say("An image sequence done, its card still there, then a movie: the movie takes W01 (0.99.84; it took W05)")
seq = make_job("exr_done", 1001, 1008)
win.add_job(seq, auto=False)
s_e = m.engines[-1]
m.request_start(s_e)
wait(lambda: s_e.state == "done" and all(w.state == "gone" for w in s_e.workers.values()), 120)
movie = make_job("mov_after", 1001, 1020, movie=True, env={"FAKE_SECS": "0.3"})
win.add_job(movie, auto=False)
m_e = m.engines[-1]
m.request_start(m_e)
wait(lambda: any(w.state in ("loading", "idle", "busy") for w in m_e.workers.values()), 30)
mw = [w.id for w in m_e.workers.values() if w.state != "gone"]
check("the movie's process is W01", mw == [1], mw)
m_e.stop("test")
wait(lambda: all(w.state == "gone" for w in m_e.workers.values()), 60)

# Start next render when one ends: a finished movie's process may still be closing when the next starts
say("A process closing after its render is done gives its name up (1.0.11: the next movie took W02)")
for e in list(m.engines):
    win._close_card(e)
pump(0.5)
for name in ("mov_next1", "mov_next2"):
    win.add_job(make_job(name, 1001, 1004, movie=True, env={"FAKE_SECS": "0.2"}), auto=False)
n1, n2 = m.engines[-2], m.engines[-1]
m.request_start(n1)
check("a movie renders in FakeNuke too (RANGE)", wait(lambda: n1.state == "done" and all(w.state == "gone" for w in n1.workers.values()), 120), n1.state)
w1 = list(n1.workers.values())[0]
w1.state = "quitting"
check("its process closing after the render is done: the next takes W01", m.take_wid(n2) == 1, m.take_wid(n2))
n1.state = "rendering"
check("closing while its render still runs: the next takes W02", m.take_wid(n2) == 2, m.take_wid(n2))
n1.state, w1.state = "done", "gone"
finish()
