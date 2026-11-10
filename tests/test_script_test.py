"""Test this script: the setups it makes (processes x threads x cache x fresh Nuke), the same frames for every setup of a
round, Add test / Edit test with the fourth field, setups saved before Fresh Nuke, both rounds with FakeNuke, a winner,
Use these settings."""
from common import *
from PySide6 import QtCore, QtWidgets

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "test_frames": 8, "chunk_auto": False, "chunk_size": 3,
                 "test_setups": [[3, 2, 2]]})  # saved before 0.91: three fields

say("The setups")
job = make_job("sh010", 1001, 1060)
win = jk.MainWindow(job)
win.c_fresh.setCurrentIndex(win.c_fresh.findData("short"))
t = jk.ScriptTest(win, win.engine)
check("every setup has four fields", all(len(k) == 4 for k in t.setups), t.setups[:3])
check("a setup saved with three fields gets the setting as it is now", (3, 2, 2, "short") in t.added, t.added)
check("Create tests makes up to %d setups" % t.TESTS, 1 <= len(t.plan(os.cpu_count() or 8, t._free_gb())) <= t.TESTS)
plan = t.plan(32, 80.0)
check("32 threads, 80 GB: from 1 process with all threads", plan[0][:2] == (1, 32), plan)
check("every setup at least %d threads and %d GB cache" % (t.MIN_THREADS, t.MIN_CACHE),
      all(k[1] >= t.MIN_THREADS and k[2] >= t.MIN_CACHE for k in plan), plan)

say("The same frames for every setup of a round, spread over the shot")
t.round, t.round_procs = "quick", [1, 2, 4]
quick = [t._frames_for(p) for p in (1, 2, 4)]
check("Round 1: the same frames for every setup", quick[0] == quick[1] == quick[2], [len(q) for q in quick])
check("from the start of the shot to its end", quick[0][0] < 1010 and quick[0][-1] > 1050, (quick[0][0], quick[0][-1]))
t.round, t.round_procs = "final", [2, 4]
check("Round 2: the same frames for both", t._frames_for(2) == t._frames_for(4))
t.s_frames.setValue(20)
t.round, t.round_procs = "final", [2, 3, 4]
check("Frames per setup 20: Round 2 renders 20, not each stretch rounded up to 24", len(t._frames_for(2)) == 20,
      len(t._frames_for(2)))
t.round_procs = [2, 6]
check("at least 4 a process: 6 processes get 24", len(t._frames_for(6)) == 24, len(t._frames_for(6)))
t.s_frames.setValue(57)
t.round_procs = [2]
check("57 of 60, stretches that meet: still 57", len(t._frames_for(2)) == 57, len(t._frames_for(2)))
t.s_frames.setValue(8)

say("Add test and Edit test, with Fresh Nuke after every chunk")


def fill(values, fresh):
    d = app.activeModalWidget()
    if d is None:
        QtCore.QTimer.singleShot(30, lambda: fill(values, fresh))
        return
    spins = d.findChildren(QtWidgets.QSpinBox)
    for s, v in zip(spins, values):
        s.setValue(v)
    combo = d.findChildren(QtWidgets.QComboBox)[0]
    combo.setCurrentIndex(combo.findData(fresh))
    d.accept()


QtCore.QTimer.singleShot(30, lambda: fill((2, 2, 2), "always"))
t._add_setup()
check("Add test: 2 x 2, 2 GB, fresh every chunk", (2, 2, 2, "always") in t.setups, t.setups)
labels = [t.table.item(r, 0).text() for r in range(t.table.rowCount())]
rules = [(t.table.item(r, 0).text(), t.table.item(r, t.C_RULE).text()) for r in range(t.table.rowCount())]
check("the table says it, Reload Nuke right after Processes x threads", ("2 x 2, cache 2 GB", "every chunk") in rules
      and t.table.horizontalHeaderItem(1).text() == "Reload Nuke", rules)
index = t.setups.index((2, 2, 2, "always"))
QtCore.QTimer.singleShot(30, lambda: fill((2, 2, 2), "off"))
t._edit_setup(index)
check("Edit test: now never fresh", (2, 2, 2, "off") in t.setups and (2, 2, 2, "always") not in t.setups, t.setups)
saved = json.load(open(SETTINGS_DIR + "/settings.json", encoding="utf-8")).get("test_setups")
check("kept for next time", [2, 2, 2, "off"] in saved, saved)

say("Both rounds with FakeNuke: never fresh against fresh every chunk")
t.setups, t.added = [(2, 2, 2, "off"), (2, 2, 2, "always")], set()
t._fill_table()
t.start()
wait(lambda: not t.timer.isActive() and t.run is None, 400)
rows = {t.table.item(r, t.C_RULE).text(): [t.table.item(r, c).text() if t.table.item(r, c) else "" for c in range(t.table.columnCount())]
        for r in range(t.table.rowCount())}
check("both setups went through Round 2", all(v[t.C_STATE].startswith("Round 2") for v in rows.values()),
      {k: v[t.C_STATE] for k, v in rows.items()})
check("a winner", getattr(t, "best", None) in t.setups, getattr(t, "best", None))
fresh = {k: v[t.C_FRESH] for k, v in rows.items()}
check("Fresh Nuke column: none never fresh, some fresh every chunk",
      fresh.get("never") == "0" and int(fresh.get("every chunk") or 0) > 0, fresh)
check("the test folder is gone", not os.path.exists(str(jk.TEST_DIR)))
check("the render's own frames untouched", files(job) == [])
best = t.best

say("Winner: the whole shot from the test's time and the rate, as on a 79 frame shot")
keep = (t.setups, t.results, t.all_frames)
t.setups = [(6, 5, 8, "short"), (1, 32, 68, "short")]
t.all_frames = list(range(1001, 1080))
t._fill_table()
f24 = list(range(24))
t.results = {t.setups[0]: {"rate": 271.0, "frames": f24, "done": 24, "took": 18.0, "complete": True, "round": "final"},
             t.setups[1]: {"rate": 72.6, "frames": f24, "done": 24, "took": 28.0, "complete": True, "round": "final"}}
t._rank(t.setups[0])
cell = lambda i, c: t.table.item(t._row(i), c)
slower = cell(1, t.C_SLOWER)
check("1 x 32: +0:43 (+143 %), not +0:48 from the rates alone", slower.text().startswith("+0:43") and "+143 %" in slower.text(),
      slower.text())
check("the tooltip says the whole shot's time", "1:13" in slower.toolTip() and "0:30" in slower.toolTip(), slower.toolTip())
test = cell(1, t.C_TEST)
check("Winner test: 1 x 32 +0:10 (+56 %) on the test's 24 frames", test.text().startswith("+0:10") and "+56 %" in test.text(),
      test.text())
check("its tooltip has both tests' times", "0:28" in test.toolTip() and "0:18" in test.toolTip(), test.toolTip())
check("both fastest in the 6 x 5 row", cell(0, t.C_TEST).text() == "fastest" and cell(0, t.C_SLOWER).text() == "fastest")
check("Frames / s right after Frames / min: 4.52 and 1.21", (cell(0, t.C_FPS).text(), cell(1, t.C_FPS).text()) == ("4.52", "1.21")
      and t.table.horizontalHeaderItem(t.C_FPS).text() == "Frames / s" and t.C_FPS == t.C_RATE + 1,
      (cell(0, t.C_FPS).text(), cell(1, t.C_FPS).text()))
heads = [t.table.horizontalHeaderItem(c).text() for c in range(t.table.columnCount())]
check("the columns Winner test and Winner shot", heads[t.C_TEST] == "Winner test" and heads[t.C_SLOWER] == "Winner shot", heads)
t.setups, t.results, t.all_frames = keep
t._fill_table()
t.use()
check("Use these settings: processes, threads, cache and fresh Nuke",
      (win.s_workers.value(), win.s_threads.value(), win.s_ram.value(), win.settings.get("fresh_after_chunk")) == best,
      (win.s_workers.value(), win.s_threads.value(), win.s_ram.value(), win.settings.get("fresh_after_chunk"), best))
finish()
