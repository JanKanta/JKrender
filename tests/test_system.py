"""System on Apple silicon: GPU use per process from macOS's GPU time (ioreg AppUsage), RAM and GPU memory per process from
macOS's footprint (RSS left out what macOS compressed), and the
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

say("macOS: a process's memory from footprint, what Activity Monitor shows; its GPU memory a part of it")
FP = {"processes": [
    {"pid": 42, "footprint": 1541410896, "categories": {
        "Malloc Small": {"dirty": 396918784, "swapped": 355893248},
        "IOSurface": {"dirty": 171966464}, "OpenCL": {"dirty": 68485120},
        "IOAccelerator (graphics)": {"dirty": 37748736}, "IOAccelerator": {"dirty": 688128},
        "Owned physical footprint (unmapped) (graphics)": {"dirty": 42991616}}},
    {"pid": 7, "footprint": 231687536, "categories": {"Malloc Small": {"dirty": 93126656}}},
    {"pid": "broken"}]}
got = jk.footprint_parse(FP)
check("the footprint of each process", got.get(42, (0,))[0] == 1541410896 and got.get(7, (0,))[0] == 231687536, got)
check("its GPU memory: Metal, IOSurface and OpenCL", got.get(42, (0, 0))[1] == 171966464 + 68485120 + 37748736 + 688128 + 42991616, got)
check("none for a process without them", got.get(7, (0, 1))[1] == 0, got)
check("a broken entry is left out", "broken" not in got and len(got) == 2, got)
check("no processes: nothing to ask", jk.mac_footprints([]) == {})

if sys.platform == "darwin":
    say("macOS: the window shows it")
    gb = 1024 ** 3
    jk.apple_gpu = lambda: (40.0, 2 * gb)


    class Procs:
        def sample(self):
            return dict(procs)


    procs = {}
    jk.AppleGpuPerProcess = Procs
    fps = {}
    jk.mac_footprints = lambda pids: {p: fps[p] for p in pids if p in fps}
    job = make_job("sh010", 1001, 1016, env={"FAKE_SECS": "0.4"})
    win = jk.MainWindow(job)
    win._start()
    wait(lambda: any(w.state == "busy" for w in win.engine.workers.values()), 60)
    w = next(w for w in win.engine.workers.values() if w.state == "busy")
    procs[w.proc.pid] = 33.0
    fps[w.proc.pid] = (3.5 * gb, 0.75 * gb)
    wait(lambda: win._gpu_procs_mac.get(w.proc.pid) == 33.0 and jk.MAC_MEM.get(w.proc.pid), 10)
    wait(lambda: abs(w.rss - 3.5) < 0.01, 5)
    win._update_system()
    t = win.worker_table
    names = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    row = next(r for r in range(t.rowCount()) if t.item(r, 0) and t.item(r, 0).text() == w.name)
    check("its GPU column: 33 %", t.item(row, names.index("GPU")).text() == "33 %", t.item(row, names.index("GPU")).text())
    check("its RAM column: the footprint, 3.5 GB", t.item(row, names.index("RAM")).text() == "3.5 GB", t.item(row, names.index("RAM")).text())
    check("the memory rules see the same", abs(w.rss - 3.5) < 0.01 and w.rss_peak >= 3.5, (w.rss, w.rss_peak))
    check("its VRAM column: its GPU memory, 0.8 GB", t.item(row, names.index("VRAM")).text() == "0.8 GB", t.item(row, names.index("VRAM")).text())
    check("and the header says it is part of the RAM", "part of the RAM" in t.horizontalHeaderItem(names.index("VRAM")).toolTip())
    share = 2 * gb / jk.psutil.virtual_memory().total * 100
    check("the Load graph's VRAM: the GPU memory as a share of the RAM", abs(list(win.g_load.data["vram"])[-1] - share) < 0.1,
          (list(win.g_load.data["vram"])[-1], share))
    tile = [l.text() for l in win.m_vram.findChildren(jk.QtWidgets.QLabel)]
    check("the VRAM tile: 2.0 GB, its share of the RAM", tile[1] == "2.0 GB" and ("%.0f %% of the RAM" % share) in tile[2], tile)
    check("the GPU tile: 40 %", [l.text() for l in win.m_gpu.findChildren(jk.QtWidgets.QLabel)][1] == "40 %")
    win._stop()
    pump(0.5)

say("Linux: GPU and VRAM per process from nvidia-smi (pmon and the compute apps)")
PMON = "\n".join([
    "# gpu         pid   type     sm    mem    enc    dec    jpg    ofa    command",
    "# Idx           #    C/G      %      %      %      %      %      %    name",
    "    0       4242     C     37     12      -      -      -      -    Nuke17.1",
    "    0       4243     C      -      -      -      -      -      -    Nuke17.1",
    "    0       1000     G      3      1      -      -      -      -    Xorg"])
APPS = "4242, 2048\n4243, 512\n"
got = jk.NvidiaSmiPerProcess.parse(PMON, APPS)
check("Nuke 4242: 37 %, 2 GB", got.get(4242) == (37.0, 2048 * 1024 ** 2), got)
check("Nuke 4243 on the card but idle: 0 %, 0.5 GB", got.get(4243) == (0.0, 512 * 1024 ** 2), got)
check("a graphics process: its use, no memory (only compute apps have one)", got.get(1000) == (3.0, None), got)
check("WSL's empty answer: nothing per process", jk.NvidiaSmiPerProcess.parse("# gpu pid\n    0          -     -      -", "") == {})

say("Linux: the CPU's name from /proc/cpuinfo, not x86_64")
CPUINFO = "processor\t: 0\nvendor_id\t: AuthenticAMD\nmodel name\t: AMD Ryzen 9 9950X 16-Core Processor\nflags\t\t: fpu\n"
check("model name", jk.linux_cpu_name(CPUINFO) == "AMD Ryzen 9 9950X 16-Core Processor", jk.linux_cpu_name(CPUINFO))
check("none: empty", jk.linux_cpu_name("processor\t: 0\n") == "")
if sys.platform.startswith("linux"):
    info = jk.machine_info()
    check("this Linux: a CPU name and a readable OS", info["cpu"] not in ("", "x86_64") and info["os"].startswith("Linux "), info)

say("Dialogs, message boxes and scroll bars dark everywhere (Linux has no dark theme to hand Qt)")
pal = jk.dark_palette()
check("window grey", pal.color(pal.ColorRole.Window).name() == jk.C["bg"], pal.color(pal.ColorRole.Window).name())
check("text light", pal.color(pal.ColorRole.WindowText).name() == jk.C["text"])
check("fields dark", pal.color(pal.ColorRole.Base).name() == jk.C["panel"])
finish()
