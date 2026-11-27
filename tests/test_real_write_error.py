"""A Write that cannot write, with the real Nuke (run_tests.py --real; 1.0.26). Nuke reports an error once: a Write with no
channels to write ("No channels selected (or available) for write") failed the first try, and the try after it returned at
once without a word and without a file, so the frame counted as rendered: a render of 292 frames "finished" with nothing
on disk. Now a try that gives no error but writes nothing fails the frame with Nuke's first error. A Write that only met a
passing trouble (its file locked for a moment, as a sync client or a viewer does) still renders on the next try. And a
Write that is fine renders as before. The worker runs as JKrender starts it, fed by hand; no window."""
import subprocess
import threading
from common import *

if nuke_is_open():
    say("skipped: a Nuke is open")
    finish()
exe = real_nuke_exes(os.environ.get("NUKE_DIR", ""))
if not exe:
    say("skipped: no Nuke in NUKE_DIR")
    finish()
shutil.rmtree(RUN, ignore_errors=True)
os.makedirs(RUN + "/render", exist_ok=True)
script = RUN + "/write_error.nk"
FMT = '"64 64 0 0 64 64 1 jktiny"'
open(script, "w").write("""Root {
 inputs 0
 first_frame 1001
 last_frame 1003
 format %(fmt)s
}
CheckerBoard2 {
 inputs 0
 format %(fmt)s
 name CheckerBoard1
}
Remove {
 channels rgba
 name Remove1
}
Write {
 channels rgb
 file "%(run)s/render/err.####.exr"
 file_type exr
 name Write1
}
CheckerBoard2 {
 inputs 0
 format %(fmt)s
 name CheckerBoard2
}
Write {
 channels rgb
 file "%(run)s/render/ok.####.exr"
 file_type exr
 name Write2
}
""" % {"fmt": FMT, "run": RUN})
worker = REPO + "/jkrender_worker.py"


def run_worker(write, commands, during=None):
    """The worker as JKrender starts it (-i -t), fed these lines on stdin; its output."""
    env = dict(os.environ)
    for k in ("NUKE_PATH", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM"):
        env.pop(k, None)
    env["JKRENDER_ARGS"] = json.dumps([script, "", write])
    proc = subprocess.Popen([exe[0], "-i", "-t", worker], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, cwd=RUN)
    lines = []

    def read():
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    t = time.time()
    while not any(l.startswith("JKR READY") for l in lines) and time.time() - t < 120 and proc.poll() is None:
        time.sleep(0.1)
    if during:
        during()
    proc.stdin.write("".join(c + "\n" for c in commands) + "QUIT\n")
    proc.stdin.flush()
    try:
        proc.wait(timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
    reader.join(5)
    return lines


def jkr_lines(out):
    """The worker's messages, wherever on the line: Nuke's progress can stand before them (".9JKR FRAME 1003")."""
    return [l[l.find("JKR "):] for l in out if "JKR " in l]


say("A Write with no channels to write: the frame fails with Nuke's error, it does not count as rendered")
out = run_worker("Write1", ["RENDER C01 1001-1002"])
jkr = jkr_lines(out)
fail = next((l for l in jkr if l.startswith("JKR FAIL C01 1001")), "")
check("Nuke started and opened the script", any(l.startswith("JKR READY") for l in jkr), out[-15:])
check("frame 1001 fails", bool(fail), jkr)
check("with what Nuke said", "channel" in fail.lower(), fail)
check("it is not counted as rendered", not any(l.startswith("JKR FRAME 1001") for l in jkr), jkr)
check("it did not take three tries of waiting: it ends after the quiet try", "trying again (3 of 3)" not in "\n".join(out),
      [l for l in out if "trying again" in l])
check("no file", not glob.glob(RUN + "/render/err.*.exr"), glob.glob(RUN + "/render/err.*"))

say("A Write that is fine renders as before")
out = run_worker("Write2", ["RENDER C02 1001-1002"])
jkr = jkr_lines(out)
check("both frames rendered", any(l.startswith("JKR FRAME 1001") for l in jkr) and any(l.startswith("JKR FRAME 1002") for l in jkr), jkr)
check("both files there", len(glob.glob(RUN + "/render/ok.*.exr")) == 2, glob.glob(RUN + "/render/ok.*"))

if sys.platform.startswith("win"):
    # Measured in Nuke 16: once a frame failed, no later execute of it in that Nuke writes anything (clearing the caches,
    # forceValidate, touching the Write's knobs: nothing helps). JKrender then gives the frame to a fresh Nuke.
    say("A file locked for a moment (a sync client, a viewer): this Nuke fails the frame, a fresh one renders it")
    target = RUN + "/render/ok.1003.exr"
    held = {}

    def lock():
        # held open with no sharing at all, as a sync client or a viewer may hold it: Nuke cannot write it for 1.5 s
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.HANDLE]
        handle = k32.CreateFileW(target.replace("/", "\\"), 0x40000000, 0, None, 2, 0x80, None)  # GENERIC_WRITE, CREATE_ALWAYS
        held["h"] = handle
        threading.Timer(1.5, lambda: k32.CloseHandle(wintypes.HANDLE(handle))).start()

    out = run_worker("Write2", ["RENDER C03 1003"], during=lock)
    jkr = jkr_lines(out)
    tried = [l for l in out if "trying again" in l]
    check("the first try failed on the locked file", bool(tried), out[-20:])
    check("the quiet try after it is not taken for a frame: it fails with the lock's error",
          any(l.startswith("JKR FAIL C03 1003") for l in jkr) and not any(l.startswith("JKR FRAME 1003") for l in jkr), jkr)
    time.sleep(0.5)
    out = run_worker("Write2", ["RENDER C04 1003"])  # what JKrender does next: the frame in a fresh Nuke
    jkr = jkr_lines(out)
    check("a fresh Nuke renders it", any(l.startswith("JKR FRAME 1003") for l in jkr), jkr)
    check("and the file is a real one now", os.path.isfile(target) and os.path.getsize(target) > 100,
          os.path.getsize(target) if os.path.isfile(target) else None)
finish()
