"""Near the end of a render a chunk is split so that every process finishes at about the same time. Every idle process gets
a piece in the same tick (0.99.70): before, the rest of a split chunk waited for the next tick, so the last processes in
the list sat idle for a few tenths of a second before their last chunk, a gap in the Workers tab."""
import types
from common import *

jk, app = start({"processes": 3, "chunk_auto": True})
job = make_job("sh010", 1001, 1012)
win = jk.MainWindow(job)
e = win.engine

say("Three idle processes, one chunk of 7 frames left")
given = []


def dispatch(w, chunk):
    chunk.state = "rendering"
    w.state = "busy"
    given.append((w.name, list(chunk.frames)))


e._dispatch = dispatch
e._spawn = e._balance_workers = lambda *a: None
e._may_spawn = lambda: False
e.in_scope = lambda c: True
e.workers = {i: types.SimpleNamespace(name="W%02d" % i, state="idle", retire=False, ps=None, cpu=0.0) for i in (1, 2, 3)}
e._live = lambda: list(e.workers.values())
e.chunks = [jk.Chunk(1, range(1006, 1013))]
e.next_cid = 2
e.state = "rendering"
e._schedule()
check("every idle process gets a piece in the same tick", [g[0] for g in given] == ["W01", "W02", "W03"], given)
check("in frame order, none twice", [f for g in given for f in g[1]] == sorted(set(f for g in given for f in g[1])), given)
left = sorted(f for c in e.chunks if c.state == "queued" for f in c.frames)
check("the rest still queued, nothing lost", sorted(left + [f for g in given for f in g[1]]) == list(range(1006, 1013)), left)

finish()
