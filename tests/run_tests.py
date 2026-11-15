"""Run JKrender's tests: every tests/test_*.py, each in its own process with Nuke's Python, offscreen and silent.

    python tests/run_tests.py              all tests, with FakeNuke (a few minutes, no Nuke licence)
    python tests/run_tests.py disk fresh   only the tests whose name has one of these words
    python tests/run_tests.py --real       also the tests with the real Nuke (needs a licence and a free PC)

Any Python starts it; the tests run in Nuke's (the newest under C:/Program Files/Nuke*, /Applications/Nuke* or /usr/local/Nuke*,
or set NUKE_DIR to Nuke's folder, on macOS the .app). On Windows FakeNuke is built from tests/fakenuke/FakeNuke.cs with the
.NET Framework compiler that comes with Windows; on macOS and Linux it is tests/fakenuke/fake_nuke.py, run with Nuke's Python
by a launcher named like Nuke. Each test's files stay in
tests/_runs/<test> until the next run, to look at when one fails. Exit code: how many tests failed.
"""
import glob
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
FAKE_SRC = HERE + "/fakenuke/FakeNuke.cs"
FAKE_PY = HERE + "/fakenuke/fake_nuke.py"
WIN = sys.platform.startswith("win")
FAKE = HERE + "/fakenuke/build/Nuke16.0v9/Nuke16.0" + (".exe" if WIN else "")
LINE = re.compile(r"FAILED: (\d+) \| exceptions: (\d+)")


def nuke_dir():
    """Nuke's folder: the one with python.exe (Windows), python3 (Linux), or the .app (macOS)."""
    if os.environ.get("NUKE_DIR"):
        return os.environ["NUKE_DIR"].rstrip("/\\")
    if WIN:
        found = [os.path.dirname(p) for p in glob.glob("C:/Program Files/Nuke*/python.exe")]
    elif sys.platform == "darwin":
        found = [p for p in glob.glob("/Applications/Nuke*/Nuke*.app") if os.path.isfile(p + "/Contents/MacOS/python3")]
    else:
        found = [os.path.dirname(p) for p in glob.glob("/usr/local/Nuke*/python3") + glob.glob(os.path.expanduser("~/Nuke*/python3"))]
    if not found:
        sys.exit("No Nuke found; set NUKE_DIR to Nuke's folder (on macOS the Nuke*.app)")
    return sorted(found, key=os.path.getmtime)[-1].replace("\\", "/")


def nuke_layout(nd):
    """(python, environment) of the Nuke in nd, for the tests: offscreen Qt from Nuke's own plugins."""
    if WIN:
        return nd + "/python.exe", {"PATH": nd + os.pathsep + os.environ.get("PATH", ""),
                                     "PYTHONPATH": nd + "/pythonextensions/site-packages", "QT_PLUGIN_PATH": nd + "/qtplugins",
                                     "QT_QPA_FONTDIR": "C:/Windows/Fonts"}
    if sys.platform == "darwin":
        c = nd + "/Contents"
        return c + "/MacOS/python3", {"PYTHONPATH": c + "/Resources/pythonextensions/site-packages",
                                      "QT_PLUGIN_PATH": c + "/MacOS/qtplugins", "DYLD_FRAMEWORK_PATH": c + "/Frameworks",
                                      "QT_QPA_FONTDIR": "/System/Library/Fonts"}
    return nd + "/python3", {"PYTHONPATH": nd + "/pythonextensions/site-packages", "QT_PLUGIN_PATH": nd + "/qtplugins",
                             "LD_LIBRARY_PATH": nd + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")}


def build_fake(python):
    if not WIN:
        # a launcher named like Nuke that runs fake_nuke.py with Nuke's Python, as FakeNuke.exe does on Windows
        os.makedirs(os.path.dirname(FAKE), exist_ok=True)
        with open(FAKE, "w") as f:
            f.write('#!/bin/sh\nexec "%s" -u "%s" "$@"\n' % (python, FAKE_PY))
        os.chmod(FAKE, 0o755)
        return
    if os.path.isfile(FAKE) and os.path.getmtime(FAKE) >= os.path.getmtime(FAKE_SRC):
        return
    csc = sorted(glob.glob("C:/Windows/Microsoft.NET/Framework64/v4*/csc.exe"))
    if not csc:
        sys.exit("No C# compiler (C:/Windows/Microsoft.NET/Framework64/v4.*/csc.exe) to build FakeNuke")
    os.makedirs(os.path.dirname(FAKE), exist_ok=True)
    r = subprocess.run([csc[-1], "-nologo", "-optimize", "-r:System.Drawing.dll", "-out:" + FAKE, FAKE_SRC],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("FakeNuke did not build:\n" + r.stdout + r.stderr)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    real = "--real" in sys.argv
    tests = sorted(glob.glob(HERE + "/test_*.py"))
    if not real:
        tests = [t for t in tests if "test_real_" not in t]  # the real Nuke: only with --real
    if args:
        tests = [t for t in tests if any(a in os.path.basename(t) for a in args)]
    nd = nuke_dir()
    python, nuke_env = nuke_layout(nd)
    build_fake(python)
    env = dict(os.environ)
    env.update(nuke_env)
    env.update({"QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "JKRENDER_FAKE_NUKE": FAKE,
                "JKRENDER_REAL_HOME": os.path.expanduser("~"), "NUKE_DIR": nd})
    results = []
    t0 = time.time()
    for i, test in enumerate(tests):
        name = os.path.splitext(os.path.basename(test))[0]
        env["JKRENDER_PORT"] = str(54900 + i)  # a JKrender of its own: never hands a render to yours
        env["JKRENDER_TEST_RUN"] = HERE + "/_runs/" + name
        print("== %s" % name, flush=True)
        t = time.time()
        try:
            r = subprocess.run([python, "-X", "faulthandler", test], env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800, cwd=HERE)  # a busy PC: test_fresh_nuke took 17 min
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            out += "\nTIMEOUT after 1800 s"
        os.makedirs(HERE + "/_runs", exist_ok=True)
        with open(HERE + "/_runs/%s.log" % name, "w", encoding="utf-8") as f:
            f.write(out)
        m = LINE.search(out)
        ok = bool(m) and m.group(1) == "0" and m.group(2) == "0"
        for line in out.splitlines():
            if line.startswith("  FAIL") or line.startswith("EXCEPTION") or "Traceback" in line or "TIMEOUT" in line or (not ok and line.strip()):
                print("   " + line[:300], flush=True)
        verdict = "ok" if ok else ("FAILED %s, exceptions %s" % m.groups() if m else "did not finish")
        print("   %s in %.0f s" % (verdict, time.time() - t), flush=True)
        results.append((name, ok, verdict))
    bad = [r for r in results if not r[1]]
    print("\n%d tests, %d ok, %d failed in %.0f s%s" % (len(results), len(results) - len(bad), len(bad), time.time() - t0,
                                                          (": " + ", ".join(r[0] for r in bad)) if bad else ""))
    sys.exit(len(bad))


if __name__ == "__main__":
    main()
