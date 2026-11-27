"""A pipeline's render hooks run as when one Nuke renders the whole range (0.99.81): afterRender once, when every frame is
on disk, in the process that rendered the last chunk, which closes after it; the render ends after them. A hook that fails,
a Nuke that ends while they run, Stop: the card says so and nothing waits. Before, a Write's afterRender ran after every
frame in every process. beforeRender once in each Nuke is the worker's part: test_real_hooks, with the real Nuke."""
from common import *

jk, app = start({"processes": 3, "threads": 2, "ram": 2, "check_pixels": False})


def after_log(job):
    p = os.path.join(os.path.dirname(frame_path(job, 0)), ".fake_after.log")
    return open(p, encoding="utf-8").read().splitlines() if os.path.exists(p) else []


def ended(e):
    return e.state in ("done", "failed", "stopped", "ready")


def render(name, env, first=True):
    job = make_job(name, 1001, 1024, env=env)
    if first:
        w = jk.MainWindow(job)
        e = w.engine
    else:
        w = win
        w.add_job(job, auto=False)
        e = w.manager.engines[-1]
        w.select(e)
    w._start()
    return w, job, e


say("A Write with afterRender hooks: they run once, when all 24 frames are on disk")
win, job, e = render("hooks", {"FAKE_HOOKS": "2", "FAKE_AFTER_SECS": "1.5"})
seen = []


def watch():
    if e.after_state == "sent" and e.after_worker is not None:
        win._refresh_cards()
        seen.append((e.state, e.worker_state(e.after_worker), sum(1 for x in e.workers.values() if x.state != "gone"),
                     win.cards[e].status.text()))
    return ended(e)


wait(watch, 120)
lines = after_log(job)
check("done", e.state == "done", (e.state, e.error, e.errors()))
check("afterRender ran once", len(lines) == 1, lines)
check("with every frame on disk", bool(lines) and lines[0].split()[2] == "24", lines)
check("in the process that rendered the last chunk", bool(lines) and e.after_worker is not None
      and lines[0].split()[1] == str(e.after_worker.proc.pid), (lines, e.after_worker and e.after_worker.proc.pid))
check("the render waited for them", bool(seen) and all(s[0] == "finishing" for s in seen), seen[:2])
check("System and the card say what goes on", bool(seen) and seen[-1][1] == "running afterRender hooks"
      and "afterRender hooks" in seen[-1][3], seen[-1:])
check("the other processes closed meanwhile", bool(seen) and seen[-1][2] == 1, seen[-1:])
check("its process closes after them", wait(lambda: all(w.state == "gone" for w in e.workers.values()), 15),
      [(w.name, w.state) for w in e.workers.values()])
check("on its timeline", any(s[0] == "after" and s[4] == "done" for w in e.workers.values() for s in w.segments))
check("the Log says it", bool(log_lines(e, "ran the afterRender hooks (2)")), log_lines(e, "afterRender"))
check("no error on the card", not e.errors(), e.errors())

say("No hooks: no AFTER, the processes close as before")
win, job, e = render("nohooks", {}, first=False)
wait(lambda: ended(e), 120)
check("done", e.state == "done", (e.state, e.errors()))
check("no AFTER", not after_log(job) and e.after_state is None, (after_log(job), e.after_state))
check("no afterRender in the Log", not log_lines(e, "afterRender"), log_lines(e, "afterRender"))

say("One hook fails: the frames are fine, the card says it")
win, job, e = render("hookfail", {"FAKE_HOOKS": "2", "FAKE_AFTER_FAIL": "1"}, first=False)
wait(lambda: ended(e), 120)
check("done", e.state == "done", (e.state, e.errors()))
check("the error on the card", any("1 of 2 afterRender hook(s) failed: publish: stand-in hook failed" in x for x in e.errors()),
      e.errors())
win._refresh_cards()
check("which goes red", jk.C["failed"] in win.cards[e].styleSheet(), win.cards[e].styleSheet())
check("red on the timeline", any(s[0] == "after" and s[4] == "failed" for w in e.workers.values() for s in w.segments))

say("Nuke ends while they run: the render ends all the same")
win, job, e = render("hookcrash", {"FAKE_HOOKS": "1", "FAKE_AFTER_CRASH": "1"}, first=False)
wait(lambda: ended(e), 120)
check("done", e.state == "done", (e.state, e.errors()))
check("the card says they did not finish", any("afterRender hooks did not finish: Nuke exited (code 3)" in x for x in e.errors()),
      e.errors())
check("not a crash of the render", e.crashes == 0, e.crashes)

say("Stop while they run: they end with the render")
win, job, e = render("hookstop", {"FAKE_HOOKS": "1", "FAKE_AFTER_SECS": "60"}, first=False)
wait(lambda: e.after_state == "sent" or ended(e), 120)
check("they run", e.after_state == "sent" and e.state == "finishing", (e.after_state, e.state))
e.stop()
pump(0.5)
check("stopped at once", e.state == "stopped" and all(w.state == "gone" for w in e.workers.values()),
      (e.state, [(w.name, w.state) for w in e.workers.values()]))
check("the card says they did not finish", any("did not finish: closed (stopped)" in x for x in e.errors()), e.errors())
check("once", len(after_log(job)) == 1, after_log(job))

say("beforeRender and the Write's per-frame hooks are shown (0.99.85): the Log, the timeline, each frame")
win, job, e = render("hooktimes", {"FAKE_BEFORE_SECS": "0.5", "FAKE_FRAME_HOOKS": "1"}, first=False)
wait(lambda: ended(e), 120)
check("done", e.state == "done", (e.state, e.errors()))
rendered = {fr.worker for fr in e.frames.values()}
notes = log_lines(e, "ran the beforeRender hooks (1) in 0.50 s")
check("one Log line for each Nuke that rendered", len(notes) == len(rendered) >= 2, (notes, rendered))
befores = [(w.name, s) for w in e.workers.values() for s in w.segments if s[0] == "before"]
check("a beforeRender block on each one's timeline, 0.5 s", sorted(n for n, _ in befores) == sorted(rendered)
      and all(s[1] == "beforeRender 0.50 s" for _, s in befores), befores)
nxt = [w.segments[w.segments.index(s) + 1] for w in e.workers.values() for s in w.segments if s[0] == "before"]
check("between the script load and the chunk: the chunk's block starts where it ends",
      all(n[0] == "chunk" and n[2] == s[3] for n, (_, s) in zip(nxt, [(w, s) for w in e.workers.values() for s in w.segments
                                                                     if s[0] == "before"])), nxt)
tips = [jk.Gantt._chunk_tip(e, w, s[1], s[2], s[3], s[4], w.name) for w in e.workers.values() for s in w.segments
        if s[0] == "chunk"]
check("the chunks' tooltips still find their frames", all(t.splitlines()[1].endswith(")") for t in tips),
      tips[:2])
check("their chunks still close as rendered", all(s[4] == "done" for w in e.workers.values() for s in w.segments
                                                  if s[0] == "chunk"), [(w.name, s[1], s[4]) for w in e.workers.values() for s in w.segments])
check("every frame knows its per-frame hooks", all(fr.hooks == {"beforeFrameRender": 0.01, "afterFrameRender": 0.02}
                                                   for fr in e.frames.values()), [fr.hooks for fr in e.frames.values()][:2])
check("the Log sums them up at the end", bool(log_lines(e, "beforeFrameRender on the Write: 24 frames, 0.01 s"))
      and bool(log_lines(e, "afterFrameRender on the Write: 24 frames, 0.02 s")), log_lines(e, "on the Write"))

say("A beforeRender of a moment still shows on the timeline: a block at least HOOK_BLOCK px wide")
for x in list(win.manager.engines):
    win._close_card(x)
win, job, e = render("hooktiny", {"FAKE_BEFORE_SECS": "0.01", "FAKE_SECS": "0.6"}, first=False)
wait(lambda: ended(e), 120)
win.resize(1400, 900)
win.show()
pump(0.5)
from PySide6 import QtGui
img = win.gantt.grab().toImage()
img.save(RUN + "/gantt_hooks.png")
want = QtGui.QColor(jk.C["after"]).rgb()
runs = []
for y in range(img.height()):
    run = 0
    for x in range(img.width()):
        if img.pixel(x, y) == want:
            run += 1
        elif run:
            runs.append(run)
            run = 0
widest = sorted(set(runs))[-3:] if runs else []
rendered = {fr.worker for fr in e.frames.values()}
check("purple blocks %d px or wider, one row of them per Nuke" % (jk.HOOK_BLOCK - 3),
      bool(runs) and max(runs) >= jk.HOOK_BLOCK - 3 and len(rendered) >= 2, (widest, rendered))

say("Settings > Show render hooks in Workers off: no purple blocks, and it is remembered")
win.c_hooks.setChecked(False)
pump(0.3)
img = win.gantt.grab().toImage()
purple = sum(1 for y in range(img.height()) for x in range(img.width()) if img.pixel(x, y) == want)
check("none drawn", purple == 0, purple)
check("saved", json.loads(jk.SETTINGS_FILE.read_text(encoding="utf-8")).get("show_hooks") is False)
win.c_hooks.setChecked(True)
pump(0.3)
img = win.gantt.grab().toImage()
check("on again: drawn", any(img.pixel(x, y) == want for y in range(img.height()) for x in range(img.width())))
finish()
