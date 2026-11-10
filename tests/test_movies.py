"""Movies: each one renders at once in its own process, as many processes as movies, whatever Processes and Max renders at
once say; an image sequence beside them keeps its own places."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "max_renders": 1, "chunk_auto": False, "chunk_size": 4})
slow = {"FAKE_SECS": "1"}
movies = [make_job("mov%d" % i, 1001, 1040, movie=True, env=slow) for i in (1, 2, 3)]
seq_job = make_job("seq010", 1001, 1040, env=slow)
win = jk.MainWindow(movies[0])
for j in movies[1:] + [seq_job]:
    win.add_job(j, auto=False)
m = win.manager
mov = [e for e in m.engines if e.movie]
seq = next(e for e in m.engines if not e.movie)
check("three movies and a sequence", len(mov) == 3 and seq is not None, [e.name for e in m.engines])

say("Render all: Processes 2, Max renders at once 1")
for e in mov + [seq]:
    m.request_start(e)
wait(lambda: all(e.state == "rendering" for e in mov) and m.movie_count() == 3 and m.live_count() == 2, 60)
check("every movie renders at once", [e.state for e in mov] == ["rendering"] * 3, [e.state for e in mov])
check("one process each", [sum(1 for w in e.workers.values() if w.state != "gone") for e in mov] == [1, 1, 1],
      [len(e.workers) for e in mov])
check("3 movie processes", m.movie_count() == 3, m.movie_count())
check("the sequence still renders, with its 2 places", seq.state == "rendering" and m.live_count() == 2,
      (seq.state, m.live_count()))
win.select(seq)
win._slow_tick()
check("System says + 3 for movies", "+ 3 for movies" in win.t_workers.sub.text() if hasattr(win.t_workers, "sub") else True)
check("movie processes are numbered after the places: W03-W05",
      sorted(w.id for e in mov for w in e.workers.values() if w.state != "gone") == [3, 4, 5],
      sorted(w.id for e in mov for w in e.workers.values()))

say("System: a Write column after Render, shown")
t = win.worker_table
head = t.horizontalHeader()
names = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
col = names.index("Write")
check("shown", not t.isColumnHidden(col))
check("right after Render", head.visualIndex(col) == head.visualIndex(names.index("Render")) + 1,
      (head.visualIndex(col), head.visualIndex(names.index("Render"))))
win._fast_tick() if hasattr(win, "_fast_tick") else None
pump(2)
writes = {t.item(r, col).text() for r in range(t.rowCount()) if t.item(r, col) and t.item(r, 1) and t.item(r, 1).text()}
check("it names the Write", writes == {"Write1"}, writes)
pump(1)
check("still one process a movie", m.movie_count() == 3, m.movie_count())
for e in m.engines:
    e.stop("test done")
wait(lambda: all(w.state == "gone" for e in m.engines for w in e.workers.values()), 60)
finish()
