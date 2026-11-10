"""Settings: defaults, what changes is saved, Reset to defaults, settings moved from where older versions kept them,
a movie render shows 1 process with every thread and saves nothing."""
from common import *
from PySide6 import QtWidgets

say("Settings kept where older versions had them move to ~/.nuke/JKrender/settings")
jk, app = start({"processes": 6, "threads": 5, "ram": 3, "script_ram": {"sh010": 7.5}})
old = HOME + "/.jkrender"
os.makedirs(old + "/test_results", exist_ok=True)
os.makedirs(HOME + "/.jkrender/renders/r1", exist_ok=True)
# what 0.93 kept beside the program wins over ~/.jkrender: put the newer settings there, the older in ~/.jkrender
os.replace(SETTINGS_DIR + "/settings.json", HOME + "/.nuke/JKrender/settings.json")
json.dump({"processes": 3}, open(old + "/settings.json", "w"))
open(old + "/test_results/t.txt", "w").write("t")
jk.move_old_home()
prog = sorted(os.listdir(HOME + "/.nuke/JKrender"))
check("settings.json moved into settings/", os.path.isfile(SETTINGS_DIR + "/settings.json") and "settings.json" not in prog, prog)
check("the newer one (0.93's) won", jk.read_settings().get("processes") == 6, jk.read_settings().get("processes"))
check("test tables moved", os.path.isfile(SETTINGS_DIR + "/test_results/t.txt"))
check("old render folders left where they are", os.path.isdir(HOME + "/.jkrender/renders/r1"))
jk.move_old_home()
check("a second time changes nothing", jk.read_settings().get("processes") == 6)

say("Defaults")
d = jk.DEFAULT_SETTINGS
check("RAM limit 95 %, VRAM limit 95 %", d["ram_limit"] == 95 and d["vram_limit"] == 95, (d["ram_limit"], d["vram_limit"]))
check("Fresh Nuke: When memory is short", d["fresh_after_chunk"] == "short")
check("Delete old logs after 15 days", d["log_days"] == 15)

say("What changes is saved")
job = make_job("sh010", 1001, 1020)
win = jk.MainWindow(job)
saved = lambda: json.load(open(SETTINGS_DIR + "/settings.json", encoding="utf-8"))
win.s_workers.setValue(4)
win.s_threads.setValue(6)
win.c_fresh.setCurrentIndex(win.c_fresh.findData("always"))
win.s_limit.setValue(90)
s = saved()
check("Processes, Threads, Fresh Nuke, RAM limit saved", (s["processes"], s["threads"], s["fresh_after_chunk"], s["ram_limit"])
      == (4, 6, "always", 90), s)
check("the manager follows", (win.manager.total, win.manager.threads) == (4, 6))

say("A movie render: 1 process with every thread, shown only")
movie = make_job("mov010", 1001, 1020, movie=True)
win.add_job(movie, auto=False)
mov = win.manager.engines[-1]
seq = win.manager.engines[0]
win.select(mov)
cpu = os.cpu_count()
check("Processes 1, Threads %d, both locked" % cpu, (win.s_workers.value(), win.s_threads.value(), win.s_workers.isEnabled(),
                                                      win.s_threads.isEnabled()) == (1, cpu, False, False),
      (win.s_workers.value(), win.s_threads.value(), win.s_workers.isEnabled()))
check("the row by Render says 1 process", win.l_info["processes"].text() == "1")
check("nothing saved", (saved()["processes"], saved()["threads"]) == (4, 6), saved())
win.select(seq)
check("a sequence picked again: 4 x 6, unlocked", (win.s_workers.value(), win.s_threads.value(), win.s_workers.isEnabled()) == (4, 6, True))

say("Slate: the frame before the Framerange, not a set frame")
win.settings["slate_frame"] = 900  # what older versions saved: no longer used
win.c_slate.setChecked(True)
check("1001-1020 renders 1000-1020", (min(seq.frames), max(seq.frames), len(seq.frames)) == (1000, 1020, 21),
      (min(seq.frames), max(seq.frames)))
check("a movie too: 1000-1020", mov.default_range() == "1000-1020", mov.default_range())
win.e_frames.setText("1050-1060")
win._frames_edited()
check("Framerange 1050-1060: the slate is 1049", (min(seq.frames), len(seq.frames)) == (1049, 12), (min(seq.frames), len(seq.frames)))
check("the row by Render says 1049", win.l_slate.text() == "1049", win.l_slate.text())
win.c_slate.setChecked(False)
check("off: no slate", min(seq.frames) == 1050, min(seq.frames))
win.e_frames.setText("")
win._frames_edited()

say("Reset to defaults")
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
win._reset_settings()
s = saved()
check("Fresh Nuke back to When memory is short", s["fresh_after_chunk"] == "short" and win.c_fresh.currentData() == "short")
check("RAM limit back to 95", s["ram_limit"] == 95 and win.s_limit.value() == 95, s.get("ram_limit"))
check("what JKrender learned of scripts is kept", s.get("script_ram") == {"sh010": 7.5}, s.get("script_ram"))
finish()
