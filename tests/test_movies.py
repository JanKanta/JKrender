"""Movies: a render like any other, within the places of Processes, one process with every thread; the Write column in System."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "max_renders": 1, "chunk_auto": False, "chunk_size": 4})
slow = {"FAKE_SECS": "1"}
movies = [make_job("mov%d" % i, 1001, 1040, movie=True, env=slow) for i in (1, 2, 3)]
win = jk.MainWindow(movies[0])
for j in movies[1:]:
    win.add_job(j, auto=False)
m = win.manager
mov = [e for e in m.engines if e.movie]
check("three movies", len(mov) == 3, [e.name for e in m.engines])

say("Render all: one process a movie with every thread, within Processes 2")
for e in mov:
    m.request_start(e)
wait(lambda: any(w.state != "gone" for e in mov for w in e.workers.values()), 60)
pump(2)
per = [sum(1 for w in e.workers.values() if w.state != "gone") for e in mov]
check("at most one process a movie", max(per) == 1, per)
check("no more processes than Processes (2): no movie on top of them", m.live_count() == 2 and sum(per) == 2, (m.live_count(), per))
cmd = mov[0]._command()
cpu = os.cpu_count()
check("with every thread: -m %d" % cpu, cmd[cmd.index("-m") + 1] == str(cpu), cmd[:6])
check("its process is W01", sorted(w.id for w in mov[0].workers.values() if w.state != "gone") == [1],
      sorted(mov[0].workers))

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
check("it names the Write", writes == {"Write1"}, writes)
for e in m.engines:
    e.stop("test done")
wait(lambda: all(w.state == "gone" for e in m.engines for w in e.workers.values()), 60)
finish()
