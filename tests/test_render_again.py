"""Render again: on one render and on several picked at once (Shift+click), finished or stopped, with Only missing,
All frames again and Cancel. Frames on disk from before a Stop show as on disk, skipped."""
from common import *

jk, app = start({"processes": 3, "threads": 2, "ram": 2, "max_renders": 3, "chunk_auto": False, "chunk_size": 3})

jobs = [make_job(n, 1001, 1009) for n in ("sh010", "sh020", "sh030")]
win = jk.MainWindow(jobs[0])
for j in jobs[1:]:
    win.add_job(j, auto=False)
es = list(win.manager.engines)
check("three renders open", len(es) == 3)


def pick(engines):
    win._card_clicked(engines[0], False)
    for e in engines[1:]:
        win._card_clicked(e, True)


def all_done(engines, secs=150):
    """Done, and the checks at the end through: until then JKrender is reading the frames (Check frames, Check pixels),
    and Windows will not let a frame being read be deleted (that made this test fail now and then)."""
    return wait(lambda: all(e.state == "done" and e.pending_checks <= 0 and not e.pixels_pending for e in engines), secs)


asked = []
say("First render of all three: nothing on disk, no question")
pick(es)
win._start()
check("all three rendered", all_done(es), [e.state for e in es])
check("no question asked", asked == [])
check("27 files", sum(len(files(j)) for j in jobs) == 27)

say("Render again on the three, finished: one question for all, All frames again")
pick(es)
answer("All frames again", asked)
win._start()
check("asked once, about all three", len(asked) == 1 and "3 renders" in asked[0], asked)
check("all three render", all(e.state in ("rendering", "waiting") for e in es), [e.state for e in es])
check("all three done again", all_done(es))
check("every frame rendered anew", all(sum(1 for fr in e.frames.values() if fr.state == "done" and fr.worker != "on disk") == 9
                                        for e in es))

say("Two frames of sh020 deleted: Only missing renders just those")
os.remove(frame_path(jobs[1], 1003))
os.remove(frame_path(jobs[1], 1007))
for e in es:
    e.scan_disk()
asked.clear()
pick(es)
answer("Only missing", asked)
win._start()
all_done(es)
rendered = sorted(f for f, fr in es[1].frames.items() if fr.state == "done")
check("sh020 rendered only 1003 and 1007", rendered == [1003, 1007], rendered)
check("the others rendered nothing", all(not any(fr.state == "done" for fr in e.frames.values()) for e in (es[0], es[2])))
check("27 files again", sum(len(files(j)) for j in jobs) == 27)

say("Cancel: nothing starts")
asked.clear()
pick(es)
answer("Cancel", asked)
win._start()
pump(0.5)
check("all three still done", all(e.state == "done" for e in es), [e.state for e in es])

say("One render stopped half way, Render: it asks, Only missing goes on from the rest")
job4 = make_job("sh040", 1001, 1012, env={"FAKE_SECS": "0.5"})
win.add_job(job4, auto=False)
e4 = win.manager.engines[-1]
pick([e4])
win._start()
wait(lambda: sum(1 for fr in e4.frames.values() if fr.state == "done") >= 4, 60)
win._stop()
wait(lambda: e4.pending_checks == 0, 10)
before = sorted(f for f, fr in e4.frames.items() if fr.state == "done")
asked.clear()
pick([e4])
answer("Only missing", asked)
win._start()
check("it asked", len(asked) == 1, asked)
wait(lambda: e4.state == "done", 90)
skipped = sorted(f for f, fr in e4.frames.items() if fr.state == "skipped")
check("the frames from before Stop show as on disk, skipped", skipped == before, (skipped, before))
check("all 12 on disk", len(files(job4)) == 12, len(files(job4)))

say("Card numbers by place: a card closed leaves no gap")


def numbers():
    return [(e.number, win.cards[e].badge.text()) for e in win.manager.engines]


check("four cards: 1 2 3 4", numbers() == [(1, "1"), (2, "2"), (3, "3"), (4, "4")], numbers())
win._close_card(es[1])
win._close_card(es[2])
check("two closed: 1 2, not 1 4", numbers() == [(1, "1"), (2, "2")], numbers())
win.add_job(make_job("sh050", 1001, 1003), auto=False)
check("one added: 3, not 5", numbers()[-1] == (3, "3"), numbers())
finish()
