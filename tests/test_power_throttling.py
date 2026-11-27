"""Windows: every Nuke process of a render runs with Windows' power throttling off (1.0.27). Windows 11 moves what it takes
for background work into "Efficiency mode" (EcoQoS): onto the efficiency cores of an Intel CPU (12th gen and up, Core
Ultra) at a low clock; a Nuke process of JKrender has no window of its own. Here a render runs on FakeNuke and Windows is
asked what it has for the process. Elsewhere there is no such thing: the test says so and ends."""
import ctypes
from common import *

if not sys.platform.startswith("win"):
    say("Not Windows: no power throttling to turn off")
    finish()
from ctypes import wintypes

jk, app = start({"processes": 1, "threads": 2, "ram": 2})


class State(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


def throttling(pid):
    """(ControlMask, StateMask) Windows holds for the process, or None when it will not tell."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetProcessInformation.restype = wintypes.BOOL
    k32.GetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        s = State(1, 0, 0)
        if not k32.GetProcessInformation(h, 4, ctypes.byref(s), ctypes.sizeof(s)):
            return None
        return s.ControlMask, s.StateMask
    finally:
        k32.CloseHandle(h)


say("A render's Nuke process: Windows holds power throttling off for it")
job = make_job("sh_eco", 1001, 1012, env={"FAKE_SECS": "0.5"})
win = jk.MainWindow(job)
e = win.engine
win._start()
check("a process starts", wait(lambda: any(w.state in ("busy", "idle") for w in e.workers.values()), 60),
      [w.state for w in e.workers.values()])
w = next(iter(e.workers.values()))
state = throttling(w.proc.pid)
check("Windows says: execution speed is controlled by the program, and not throttled",
      state is not None and state[0] & jk.THROTTLE_SPEED and not state[1] & jk.THROTTLE_SPEED, state)
if sys.getwindowsversion().build >= 22000:  # Windows 11 knows the timer resolution part too
    check("Windows 11: the timer is not throttled either", state is not None and state[0] & jk.THROTTLE_TIMER
          and not state[1] & jk.THROTTLE_TIMER, state)
w.log.flush()
text = open(w.log_path, encoding="utf-8", errors="replace").read()
check("its log says so", "power throttling (Efficiency mode) off" in text, text[:400])
check("the render goes on and finishes", wait(lambda: e.state == "done", 120), e.state)
check("no_power_throttling on a pid that does not exist: False", jk.no_power_throttling(999999) is False)
finish()
