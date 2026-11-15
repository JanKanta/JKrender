"""System on Apple silicon: GPU use per process from macOS's GPU time (ioreg AppUsage), no GPU memory per process, and the
GPU's memory, part of the RAM, drawn in the Load graph as a share of the RAM. The parser runs everywhere; the window part
only on a Mac, where JKrender reads the Apple GPU (stand-ins here, so the numbers are known)."""
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2, "use_gpu": True, "chunk_auto": False, "chunk_size": 4})

say("macOS: GPU time per process, every Metal client of a process added up")
IOREG = '''+-o AGXDeviceUserClient  <class AGXDeviceUserClient>
    {
      "AppUsage" = ({"API"="Metal","lastSubmittedTime"=1,"accumulatedGPUTime"=%d},{"API"="Metal","accumulatedGPUTime"=500000000})
      "IOUserClientCreator" = "pid 42, Nuke17.0"
    }
+-o AGXDeviceUserClient  <class AGXDeviceUserClient>
    {
      "AppUsage" = ()
      "IOUserClientCreator" = "pid 7, Safari"
    }
+-o AGXDeviceUserClient  <class AGXDeviceUserClient>
    {
      "AppUsage" = ({"API"="Metal","accumulatedGPUTime"=250000000})
      "IOUserClientCreator" = "pid 42, Nuke17.0"
    }
'''
check("the ns of both clients of pid 42", jk.AppleGpuPerProcess.parse(IOREG % 1000000000) == {42: 1750000000, 7: 0},
      jk.AppleGpuPerProcess.parse(IOREG % 1000000000))
a = jk.AppleGpuPerProcess()
check("the first sample has nothing to compare with", a.sample(IOREG % 1000000000, now=100.0) == {})
got = a.sample(IOREG % 2000000000, now=102.0)
check("1 s of GPU in 2 s: 50 %", abs(got.get(42, 0) - 50.0) < 0.01 and got.get(7) == 0.0, got)

if sys.platform == "darwin":
    say("macOS: the window shows it")
    gb = 1024 ** 3
    jk.apple_gpu = lambda: (40.0, 2 * gb)


    class Procs:
        def sample(self):
            return dict(procs)


    procs = {}
    jk.AppleGpuPerProcess = Procs
    job = make_job("sh010", 1001, 1016, env={"FAKE_SECS": "0.4"})
    win = jk.MainWindow(job)
    win._start()
    wait(lambda: any(w.state == "busy" for w in win.engine.workers.values()), 60)
    w = next(w for w in win.engine.workers.values() if w.state == "busy")
    procs[w.proc.pid] = 33.0
    wait(lambda: win._gpu_procs_mac.get(w.proc.pid) == 33.0, 10)
    win._update_system()
    t = win.worker_table
    names = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    row = next(r for r in range(t.rowCount()) if t.item(r, 0) and t.item(r, 0).text() == w.name)
    check("its GPU column: 33 %", t.item(row, names.index("GPU")).text() == "33 %", t.item(row, names.index("GPU")).text())
    check("its VRAM column empty: no GPU memory per process", t.item(row, names.index("VRAM")).text() == "")
    check("and the header says why", "part of the RAM" in t.horizontalHeaderItem(names.index("VRAM")).toolTip())
    share = 2 * gb / jk.psutil.virtual_memory().total * 100
    check("the Load graph's VRAM: the GPU memory as a share of the RAM", abs(list(win.g_load.data["vram"])[-1] - share) < 0.1,
          (list(win.g_load.data["vram"])[-1], share))
    tile = [l.text() for l in win.m_vram.findChildren(jk.QtWidgets.QLabel)]
    check("the VRAM tile: 2.0 GB, its share of the RAM", tile[1] == "2.0 GB" and ("%.0f %% of the RAM" % share) in tile[2], tile)
    check("the GPU tile: 40 %", [l.text() for l in win.m_gpu.findChildren(jk.QtWidgets.QLabel)][1] == "40 %")
    win._stop()
    pump(0.5)
finish()
