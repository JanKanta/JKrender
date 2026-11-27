"""A graphics card of any maker (1.0.28): the GPU tile and the VRAM rules read only nvidia-smi before, so with an AMD card
(or Intel Arc) they had nothing and did nothing. On Windows the card's load and memory come from Windows itself (the
counters Task Manager shows, the size from DXGI); on Linux an AMD card's from what the amdgpu driver writes in sysfs, and
each Nuke process's VRAM and GPU time from /proc/<pid>/fdinfo. The processor's own graphics (a Ryzen's 2 GB) is left out.
On a Windows PC with an NVIDIA card nvidia-smi is hidden here, and what Windows says is held against what nvidia-smi says.
The Linux part runs on made-up sysfs and /proc folders, on every system."""
import subprocess
from common import *

jk, app = start({"processes": 1, "use_gpu": True})
GB = 1024 ** 3

say("Linux, AMD: the card from sysfs")
sysfs = RUN + "/sys/class/drm"
for card, vendor, total, used, busy in (("card0", "0x10de", 24 * GB, 1 * GB, 5),   # NVIDIA: not amdgpu's
                                        ("card1", "0x1002", 2 * GB, 1 * GB, 3),    # the processor's graphics: too small
                                        ("card2", "0x1002", 48 * GB, 12 * GB, 61)):  # a Radeon PRO W7900
    d = sysfs + "/" + card + "/device"
    os.makedirs(d, exist_ok=True)
    for name, value in (("vendor", vendor), ("mem_info_vram_total", total), ("mem_info_vram_used", used),
                        ("gpu_busy_percent", busy)):
        open(d + "/" + name, "w").write("%s\n" % value)
os.makedirs(sysfs + "/card2-DP-1", exist_ok=True)  # a connector: no device folder of its own
dev = jk.amdgpu_card(sysfs)
check("the AMD card with the most VRAM, not NVIDIA's, not the processor's graphics", dev and dev.replace("\\", "/").endswith("card2/device"), dev)
s = jk.amdgpu_sample(dev)
check("its load and memory: 61 %, 12 of 48 GB", s == [61.0, 12 * 1024.0, 48 * 1024.0], s)
check("no AMD card: None", jk.amdgpu_card(RUN + "/nothing") is None)

say("Linux, AMD: a process's VRAM and GPU time from /proc/<pid>/fdinfo")
proc = RUN + "/proc"


def fdinfo(pid, files):
    d = "%s/%d/fdinfo" % (proc, pid)
    os.makedirs(d, exist_ok=True)
    for fd, text in files.items():
        open("%s/%s" % (d, fd), "w").write(text)


client = "pos:\t0\nflags:\t02100002\ndrm-driver:\tamdgpu\ndrm-client-id:\t7\ndrm-memory-vram:\t2048000 KiB\n" \
         "drm-engine-gfx:\t%d ns\ndrm-engine-compute:\t%d ns\n"
fdinfo(4242, {"3": client % (1000000000, 0), "4": client % (1000000000, 0),  # one client in two descriptors: once
              "5": "drm-driver:\ti915\ndrm-client-id:\t9\ndrm-memory-vram:\t999 KiB\n",  # another driver
              "6": "pos:\t0\nflags:\t0100000\n"})  # a file
per = jk.AmdgpuPerProcess(proc)
first = per.sample([4242, 99999], now=100.0)
check("its VRAM, the client counted once; no GPU % from one sample", first.get(4242) == (None, 2048000 * 1024), first)
check("a process that is gone: left out", 99999 not in first, first)
fdinfo(4242, {"3": client % (1300000000, 200000000), "4": client % (1300000000, 200000000)})
second = per.sample([4242], now=101.0)
check("0.3 s of gfx and 0.2 s of compute in 1 s: 50 %", second.get(4242) and abs(second[4242][0] - 50.0) < 0.01, second)
fdinfo(4243, {"3": client.replace("drm-memory-vram:\t2048000 KiB", "drm-resident-vram:\t3 GiB") % (0, 0)})
check("newer kernels: drm-resident-vram, in GiB", per.sample([4243], now=1.0).get(4243) == (None, 3 * GB),
      per.sample([4243], now=2.0))

say("Windows: the counters of one card, Task Manager's way")
luid = "0x00000000_0x0000d1f5"
engines = [("pid_10_luid_0x00000000_0x0000D1F5_phys_0_eng_0_engtype_3D", 30.0),
           ("pid_11_luid_0x00000000_0x0000D1F5_phys_0_eng_0_engtype_3D", 25.0),
           ("pid_11_luid_0x00000000_0x0000D1F5_phys_0_eng_3_engtype_Compute", 70.0),
           ("pid_12_luid_0x00000000_0x00017209_phys_0_eng_0_engtype_3D", 90.0)]  # another adapter
memory = [("luid_0x00000000_0x0000D1F5_phys_0", 6 * GB), ("luid_0x00000000_0x00017209_phys_0", 1 * GB)]
s = jk.WindowsGpuCard.combine(luid, engines, memory, 24 * GB)
check("the busiest engine of this card (compute 70 %), its memory, not the other adapter's", s == [70.0, 6 * 1024.0, 24 * 1024.0], s)

if sys.platform.startswith("win"):
    say("Windows: the real card, read from Windows with nvidia-smi hidden")
    adapters = jk.windows_gpu_adapters()
    check("DXGI lists the adapters", bool(adapters), adapters)
    big = [a for a in adapters if a["dedicated"] >= jk.GPU_MIN_VRAM]
    if not big:
        say("  no graphics card with memory of its own on this PC: the rest is skipped")
        finish()
    real_which = jk.shutil.which
    nvsmi = real_which("nvidia-smi")
    jk.shutil.which = lambda name, *a, **k: None if name == "nvidia-smi" else real_which(name, *a, **k)
    job = make_job("gpu_any", 1001, 1004)
    win = jk.MainWindow(job)
    check("the GPU monitor reads the card without nvidia-smi", wait(lambda: win._gpu is not None, 20), win._gpu)
    gpu = win._gpu or [0, 0, 0]
    card = max(big, key=lambda a: a["dedicated"])
    check("it is the card with the most memory of its own", win._gpu_name == card["name"], (win._gpu_name, card))
    win._feed_vram()
    check("the VRAM rules get it", win.manager.vram == gpu, win.manager.vram)
    win._slow_tick()
    check("the GPU tile shows it, with the card's name", win.m_gpu.value.text().endswith("%"), win.m_gpu.value.text())
    if nvsmi:
        out = subprocess.run([nvsmi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout.strip().splitlines()[0]
        used, total = (float(x) for x in out.split(","))
        check("the same memory in use as nvidia-smi says (within 5 % of the card)", abs(gpu[1] - used) < 0.05 * total, (gpu, out))
        check("the same size, within 5 %", abs(gpu[2] - total) < 0.05 * total, (gpu, out))
    jk.shutil.which = real_which
finish()
