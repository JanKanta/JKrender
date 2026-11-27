"""Settings: defaults, what changes is saved, Reset to defaults, settings moved from where older versions kept them,
a movie render shows 1 process with every thread and saves nothing."""
from common import *
from PySide6 import QtWidgets

say("Settings kept where older versions had them move to ~/.nuke/JKrender/settings")
jk, app = start({"processes": 6, "threads": 5, "ram": 3, "script_ram": {"sh010": 7.5}})  # script_ram: from before 0.99.26
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
check("no Memory Limit any more; Reload Nuke at 95 %, Kill at 98 %", "ram_limit" not in d and "vram_limit" not in d
      and (d["reload_ram"], d["reload_vram"], d["kill_ram"], d["kill_vram"]) == (95, 95, 98, 98), d)
check("Fresh Nuke: When memory is short", d["fresh_after_chunk"] == "short")
check("Delete old logs after 15 days", d["log_days"] == 15)

say("What changes is saved")
job = make_job("sh010", 1001, 1020)
win = jk.MainWindow(job)
saved = lambda: json.load(open(SETTINGS_DIR + "/settings.json", encoding="utf-8"))
win.s_workers.setValue(4)
win.s_threads.setValue(6)
win.c_fresh.setCurrentIndex(win.c_fresh.findData("always"))
win.s_reload_ram.setValue(90)
s = saved()
check("Processes, Threads, Fresh Nuke, Reload Nuke at saved", (s["processes"], s["threads"], s["fresh_after_chunk"], s["reload_ram"])
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
check("Settings > Slate frame shows just 1049", win.l_slate_frame.text() == "1049", win.l_slate_frame.text())
win.c_slate.setChecked(False)
check("off: no slate", min(seq.frames) == 1050, min(seq.frames))
win._show_run_info()
check("Settings > Slate frame shows nothing when off", win.l_slate_frame.text() == "", win.l_slate_frame.text())
win.e_frames.setText("")
win._frames_edited()

say("Reload Nuke after: how long the memory stays over Reload Nuke at before a process reloads (0.99.89)")
check("10 s unless set", win.s_reload_hold.value() == 10 and jk.reload_hold({}) == 10.0, win.s_reload_hold.value())
win.s_reload_hold.setValue(30)
check("set to 30 s, saved", saved().get("reload_hold") == 30 and jk.reload_hold(win.settings) == 30.0, saved().get("reload_hold"))
m = win.manager
m._mem_now = lambda: {"RAM": 97.0, "VRAM": None}
m._short_since = {"RAM": time.time() - 20}
check("20 s over the mark: not yet with 30 s", m.memory_short() == "", m.memory_short())
m._short_since = {"RAM": time.time() - 31}
check("31 s over it: the reload is due", m.memory_short().startswith("RAM over"), m.memory_short())

say("Clear Nuke cache without asking (1.0.7): off, Render asks; on, it clears without a question")
asked, cleared = [], []
keep = (jk.open_nukes, jk.clear_open_nukes, QtWidgets.QMessageBox.question)
jk.open_nukes = lambda: 2  # two Nukes open: nothing is sent to a real one
jk.clear_open_nukes = lambda: cleared.append(1)
answer = {"a": QtWidgets.QMessageBox.No}
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(a[2]), answer["a"])[1])
check("off by default", win.c_clear.isChecked() is False and jk.DEFAULT_SETTINGS["clear_cache_auto"] is False)
win._ask_clear_cache()
check("off: it asks, and No clears nothing", len(asked) == 1 and "the 2 open Nukes" in asked[0] and not cleared, asked)
answer["a"] = QtWidgets.QMessageBox.Yes
win._ask_clear_cache()
check("off: Yes clears", len(asked) == 2 and len(cleared) == 1)
win.c_clear.setChecked(True)
check("on: saved", saved().get("clear_cache_auto") is True)
win._ask_clear_cache()
check("on: cleared without a question", len(asked) == 2 and len(cleared) == 2, (len(asked), len(cleared)))
check("and the Log says so", any("Cleared the cache of the 2 open Nukes" in x[1] for x in win.engine.log), [x[1] for x in win.engine.log][-2:])
jk.open_nukes = lambda: 0
win._ask_clear_cache()
check("no Nuke open: nothing at all", len(asked) == 2 and len(cleared) == 2)
jk.open_nukes, jk.clear_open_nukes, QtWidgets.QMessageBox.question = keep

say("Reset to defaults")
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
win._reset_settings()
s = saved()
check("Clear Nuke cache without asking back to off", s.get("clear_cache_auto") is False and not win.c_clear.isChecked(),
      s.get("clear_cache_auto"))
check("Fresh Nuke back to When memory is short", s["fresh_after_chunk"] == "short" and win.c_fresh.currentData() == "short")
check("Reload Nuke at back to 95", s["reload_ram"] == 95 and win.s_reload_ram.value() == 95, s.get("reload_ram"))
check("Reload Nuke after back to 10 s", win.s_reload_hold.value() == 10 and jk.reload_hold(win.settings) == 10.0,
      (win.s_reload_hold.value(), s.get("reload_hold")))
check("Reload after back to Frame", s.get("reload_after") == "frame" and win.c_reload_after.currentData() == "frame", s.get("reload_after"))
check("its menu: Frame, Chunk", [win.c_reload_after.itemText(i) for i in range(win.c_reload_after.count())] == ["Frame", "Chunk"])
check("Reload Nuke at back to RAM 95 %, VRAM 95 %", s["reload_ram"] == 95 and s["reload_vram"] == 95
      and win.s_reload_ram.value() == 95 and win.s_reload_vram.value() == 95, (s.get("reload_ram"), s.get("reload_vram")))
check("nothing is remembered per script (script_ram is dropped)", "script_ram" not in s and "script_ram" not in win.settings, s.get("script_ram"))

say("No Memory Limit row; Reload Nuke at and Kill at, RAM and VRAM side by side, always on")
check("no on/off switches any more", not hasattr(win, "c_guard") and not hasattr(win, "c_vram"))
raw = json.loads(jk.SETTINGS_FILE.read_text(encoding="utf-8"))
raw.update({"ram_guard": False, "vram_guard": False})
jk.SETTINGS_FILE.write_text(json.dumps(raw), encoding="utf-8")
back = jk.read_settings()
check("a guard switched off in older settings is on again", back["ram_guard"] and back["vram_guard"], back)
check("no Memory Limit fields", not hasattr(win, "s_limit") and not hasattr(win, "s_vram"))
check("Reload Nuke at: RAM and VRAM side by side", win.s_reload_ram.parentWidget() is win.s_reload_vram.parentWidget())
check("Kill at: RAM 98 %, VRAM 98 %", (win.s_kill_ram.value(), win.s_kill_vram.value()) == (98, 98))
tabs = [win.sub_tabs.tabText(i) for i in range(win.sub_tabs.count())]
check("tabs: System, Job, Settings, Donation, Author", tabs == ["System", "Job", "Settings", "Donation", "Author"], tabs)
import tempfile, pathlib
tmp = pathlib.Path(tempfile.mkdtemp()) / "donation.json"
tmp.write_text("{}", encoding="utf-8")  # the shipped donation.json has links in it: an empty one of the test's own
keep_file, jk.DONATION_FILE = jk.DONATION_FILE, tmp
page = win._donation_tab()
check("Donation with nothing filled in: coming soon", win._donation_count == 0
      and "coming soon" in " ".join(l.text() for l in page.widget().findChildren(QtWidgets.QLabel)))
jk.DONATION_FILE = keep_file
tmp.write_text(json.dumps({"kofi": "ko-fi.com/example", "bank_iban": "CZ00 0000 0000 0000 0000 0000"}), encoding="utf-8")
keep_file, jk.DONATION_FILE = jk.DONATION_FILE, tmp
page = win._donation_tab()
check("Ko-fi and a bank transfer filled in: both show", win._donation_count == 2, win._donation_count)
texts = " ".join(l.text() for l in page.widget().findChildren(QtWidgets.QLabel))
check("with the Ko-fi link", "https://ko-fi.com/example" in texts, texts[:200])
jk.DONATION_FILE = keep_file
author = " ".join(l.text() for l in win.sub_tabs.widget(tabs.index("Author")).widget().findChildren(QtWidgets.QLabel))
check("Author: the name, LinkedIn and GitHub", "Jan Kanta" in author and "linkedin.com/in/jan-kanta" in author
      and "github.com/JanKanta/JKrender" in author, author[:200])
job_page = win.sub_tabs.widget(tabs.index("Job"))
check("Processes and Chunks are on the Job tab", job_page.isAncestorOf(win.s_workers) and job_page.isAncestorOf(win.s_chunk))
check("Reload Nuke at is on Settings", win.sub_tabs.widget(tabs.index("Settings")).isAncestorOf(win.s_reload_ram))
win.s_reload_ram.setValue(97)
win.s_kill_ram.setValue(90)
check("Kill at never under Reload Nuke at: RAM 97 % at least", win.s_kill_ram.value() == 97, win.s_kill_ram.value())
win.s_reload_ram.setValue(95)
raw = json.loads(jk.SETTINGS_FILE.read_text(encoding="utf-8"))
raw.update({"ram_limit": 93, "vram_limit": 94, "reload_ram": 90, "reload_vram": 90})
jk.SETTINGS_FILE.write_text(json.dumps(raw), encoding="utf-8")
old = jk.read_settings()
check("settings from before 0.99.30: Memory Limit dropped, Reload Nuke at 95 %", "ram_limit" not in old and "vram_limit" not in old
      and (old["reload_ram"], old["reload_vram"]) == (95, 95), old)
say("System tiles: CPU, RAM, GPU, VRAM, then disk and network; RAM says how much of it is Nuke's cache")
from PySide6 import QtWidgets as _QW
row = win.m_cpu.parentWidget().layout() if win.m_cpu.parentWidget().layout() and win.m_cpu.parentWidget().layout().indexOf(win.m_cpu) >= 0 else None
titles = []
lay = None
for child in win.m_cpu.parentWidget().findChildren(_QW.QHBoxLayout):
    if child.indexOf(win.m_cpu) >= 0:
        lay = child
titles = [lay.itemAt(i).widget().title.text() for i in range(lay.count()) if lay.itemAt(i).widget() is not None]
check("tiles in order", titles == ["CPU", "RAM", "GPU", "VRAM", "Disk write", "Disk read", "Network"], titles)
check("no Nuke RAM tile", not hasattr(win, "m_nuke"))
win._update_system()
check("RAM: used GB – cache N GB", " GB – cache " in win.m_ram.sub.text(), win.m_ram.sub.text())
check("its tooltip says it is the Nuke processes' cache", "caches of the Nuke processes" in win.m_ram.toolTip(), win.m_ram.toolTip())
finish()
