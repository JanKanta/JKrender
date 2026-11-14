"""Delete all: shown only when more than one render is picked and more than one of them has frames on disk; one question
for all, then every picked render's frames are gone and it is ready to render again; a render still running is left out."""
from common import *

jk, app = start({"processes": 3, "threads": 2, "ram": 2, "max_renders": 3, "chunk_auto": False, "chunk_size": 3})
jobs = [make_job(n, 1001, 1006) for n in ("sh010", "sh020", "sh030")]
win = jk.MainWindow(jobs[0])
for j in jobs[1:]:
    win.add_job(j, auto=False)
es = list(win.manager.engines)


def pick(engines):
    win._card_clicked(engines[0], False)
    for e in engines[1:]:
        win._card_clicked(e, True)
    win._sync_controls()


def shown():
    return not win.b_delete_all.isHidden()


def settled(engines, secs=120):
    return wait(lambda: all(e.state in ("done", "stopped", "failed") and e.pending_checks <= 0 and not e.pixels_pending
                            for e in engines), secs)


say("sh010 and sh020 rendered, sh030 not")
pick(es[:2])
win._start()
check("two rendered", settled(es[:2]), [e.state for e in es])
check("12 files", sum(len(files(j)) for j in jobs) == 12)

pick([es[0]])
check("one render picked: no Delete all", not shown())
pick([es[0], es[2]])
check("two picked, only one with frames: no Delete all", not shown())
pick(es)
check("three picked, two with frames: Delete all", shown())
check("and it can be clicked", win.b_delete_all.isEnabled())

say("Cancel keeps everything")
asked = []
answer("Cancel", asked)
win._delete_picked()
check("asked once, about the two renders with frames", len(asked) == 1 and "2 renders" in asked[0] and "12 file(s)" in asked[0], asked)
check("12 files still there", sum(len(files(j)) for j in jobs) == 12)

say("Yes deletes the frames of all of them")
asked = []
answer("Yes", asked)
win._delete_picked()
check("asked once", len(asked) == 1, asked)
check("no files left", sum(len(files(j)) for j in jobs) == 0, [files(j) for j in jobs])
check("both ready to render again", all(e.state == "ready" and not e.nothing_to_render() for e in es[:2]), [e.state for e in es])
check("the note says so", any("Deleted 6 rendered file(s)" in x[1] for x in es[0].log), [x[1] for x in es[0].log][-3:])
win._sync_controls()
check("nothing on disk: no Delete all", not shown())

say("A render still running is left out")
slow = make_job("sh040", 1001, 1012, env={"FAKE_SECS": "0.6"})
win.add_job(slow, auto=False)
e4 = win.manager.engines[-1]
pick([es[0]])
win._start()
settled([es[0]])
pick([e4])
win._start()
wait(lambda: sum(1 for fr in e4.frames.values() if fr.state == "done") >= 3, 60)  # reported by Nuke, not only on disk
pick([es[0], e4])
check("two picked with frames, one running: Delete all", shown() and win.b_delete_all.isEnabled())
asked = []
answer("Yes", asked)
win._delete_picked()
check("asked about the one that is not running", len(asked) == 1 and "1 renders" in asked[0], asked)
check("its frames gone", files(jobs[0]) == [])
check("the running one keeps rendering, frames and all", e4.state == "rendering" and len(files(slow)) >= 3, (e4.state, len(files(slow))))
win._stop()
settled([e4])
finish()
