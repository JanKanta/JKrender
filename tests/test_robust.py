"""When things go wrong: a Nuke process crashes mid-chunk, JKrender itself goes down mid-render and comes back with its
cards, a second JKrender hands its render to the one open, a project path with Czech letters."""
import subprocess
import threading
from common import *

if "--phase-a" in sys.argv:
    # JKrender going down mid-render: render, then end without closing anything
    jk, app = start({"processes": 2, "threads": 2, "ram": 2, "chunk_auto": False, "chunk_size": 2})
    job = make_job("crashed", 1001, 1012, env={"FAKE_SECS": "0.5"})
    win = jk.MainWindow(job)
    win._start()
    wait(lambda: sum(1 for fr in win.engine.frames.values() if fr.state == "done") >= 4, 60)
    win._save_queue()
    say("PHASE_A %d frames done" % sum(1 for fr in win.engine.frames.values() if fr.state == "done"))
    os._exit(0)

say("JKrender goes down mid-render, opens again: the card comes back stopped, Render goes on from the rest")
r = subprocess.run([sys.executable, os.path.abspath(__file__), "--phase-a"], env=dict(os.environ), capture_output=True,
                   text=True, encoding="utf-8", errors="replace", timeout=300)
check("the first JKrender rendered and went down", "PHASE_A" in r.stdout, (r.stdout + r.stderr)[-400:])
jk, app = start({"processes": 2, "threads": 2, "ram": 2, "chunk_auto": False, "chunk_size": 2}, clean=False)
saved = jk.read_queue()
check("its cards were saved", bool(saved and saved.get("renders")), saved)
jk.end_leftovers(saved.get("pids") or [])
restored = [c for c in saved.get("renders") or [] if os.path.isfile(c.get("job") or "")]
win = jk.MainWindow(restored[0]["job"], restoring=True)
going = win.restore(win.engine, restored[0])
e = win.engine
check("it was going when JKrender went down", going)
check("the card comes back stopped", e.state == "stopped", e.state)
check("the Log says it was interrupted", bool(log_lines(e, "Interrupted")))
on_disk = sum(1 for fr in e.frames.values() if fr.state in ("done", "skipped"))
check("the frames on disk count", on_disk >= 4, on_disk)
asked = []
answer("Only missing", asked)
win._start()
check("Render asks: only missing or all again", len(asked) == 1, asked)
wait(lambda: e.state == "done", 120)
check("done, all 12 on disk", e.state == "done" and len(files(restored[0]["job"])) == 12, (e.state, len(files(restored[0]["job"]))))

say("A Nuke process crashes mid-chunk: its chunk goes again, the render ends whole")
job = make_job("sh010", 1001, 1012, env={"FAKE_CRASH_FRAME": "1005"})
win.add_job(job, auto=False)
e1 = win.manager.engines[-1]
win.select(e1)
win._start()
wait(lambda: e1.state in ("done", "failed", "stopped"), 150)
check("done", e1.state == "done", e1.state)
check("all 12 on disk", len(files(job)) == 12, len(files(job)))
check("it crashed once", os.path.exists(os.path.dirname(frame_path(job, 0)) + "/.fake_crashed_1005"))
crash = [x[1] for x in e1.log if "W0" in x[1] and ("crash" in x[1].lower() or "exit" in x[1].lower() or "ended" in x[1].lower())]
check("the Log says a process went down", bool(crash), [x[1] for x in e1.log][-12:])
check("the places stay W01 and W02", sorted(e1.workers) == [1, 2], sorted(e1.workers))
win._refresh_cards()
card = win.cards[e1]
check("its card is red, and says why", jk.C["failed"] in card.styleSheet() and "crashed" in card.toolTip(),
      (card.styleSheet(), card.toolTip()))
ok_card = win.cards[e]
check("a render with no error stays green", jk.C["done"] in ok_card.styleSheet() and not ok_card.toolTip(),
      (ok_card.styleSheet(), ok_card.toolTip()))

say("A second JKrender hands its render to the one open")
win.listen()
job2 = make_job("sh020", 1001, 1004)
before = len(win.manager.engines)
result = {}
th = threading.Thread(target=lambda: result.update(ok=jk.hand_to_running(job2)))
th.start()
wait(lambda: not th.is_alive() and len(win.manager.engines) > before, 20)
check("handed over", result.get("ok") is True, result)
check("a new card in the open JKrender", len(win.manager.engines) == before + 1)
check("it is the render sent", win.manager.engines[-1].job.get("script", "").endswith("sh020.nk"))

say("A project path with Czech letters")
folder = RUN + "/Práce/Žluťoučký kůň"
job3 = make_job("sh030_čšř", 1001, 1006, folder=folder)
win.add_job(job3, auto=False)
e3 = win.manager.engines[-1]
win.select(e3)
win._start()
wait(lambda: e3.state in ("done", "failed", "stopped"), 120)
check("done", e3.state == "done", (e3.state, [x[1] for x in e3.log][-5:]))
check("all 6 frames on disk", len(files(job3)) == 6, files(job3))
finish()
