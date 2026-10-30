"""numpy and OpenEXR for JKrender's EXR preview, fetched on the first Nuke start.

Nuke ships neither. They are compiled, so a build is locked to the platform and the Python
version; fetching with Nuke's own python gets the build that fits that Nuke. They land in
lib/<platform>-cp<version> next to this file (in ~/.jkrender/lib/... when this folder is
read-only), and JKrender.py puts that folder on its path.

Rendering does not need them; only the preview of EXR frames does.
Nothing in here imports numpy or OpenEXR.
"""

import os
import platform
import shutil
import subprocess
import sys

PACKAGES = ("numpy>=1.24,<3", "OpenEXR>=3.2,<4")
DOWNLOAD_MB = 15  # measured: numpy 12.6 + OpenEXR 1.1
NO_FETCH_ENV = "JKRENDER_NO_FETCH"  # a studio rollout that puts the libraries there itself
HERE = os.path.dirname(os.path.abspath(__file__))


def platform_tag(version=None):
    """Like "win_amd64-cp311": one folder per platform and Python version."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        name, arch = "win", ("amd64" if machine in ("amd64", "x86_64") else machine)
    elif sys.platform == "darwin":
        name, arch = "macos", machine or "x86_64"
    else:
        name, arch = "linux", machine or "x86_64"
    major, minor = version or sys.version_info[:2]
    return "%s_%s-cp%d%d" % (name, arch, major, minor)


def lib_dirs(version=None):
    """Where the libraries for this Python may be, best first."""
    tag = platform_tag(version)
    return [os.path.join(HERE, "lib", tag), os.path.join(os.path.expanduser("~"), ".jkrender", "lib", tag)]


def installed(version=None):
    """The folder that has both packages, or None. Looks at the files only."""
    for folder in lib_dirs(version):
        if os.path.isdir(os.path.join(folder, "numpy")) and any(
                n.lower().startswith("openexr") and n.endswith((".pyd", ".so")) for n in _names(folder)):
            return folder
    return None


def _names(folder):
    try:
        return os.listdir(folder)
    except OSError:
        return []


def _writable(path):
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent
    return os.access(probe, os.W_OK)


def target_dir(version=None):
    first, fallback = lib_dirs(version)
    return first if _writable(first) else fallback


def declined_marker():
    return os.path.join(HERE, "lib", "DECLINED")


def nuke_python(nuke_exe=None):
    """Nuke's own python next to the Nuke binary. None when not found."""
    folder = os.path.dirname(os.path.abspath(nuke_exe or sys.executable))
    for name in (["python.exe"] if sys.platform == "win32" else ["python3", "python"]):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def pip_command(target, python_exe):
    return [python_exe, "-m", "pip", "install", "--target", target, "--only-binary=:all:", "--no-input",
            "--disable-pip-version-check", "--no-warn-script-location"] + list(PACKAGES)


def pip_command_text(target, python_exe):
    return " ".join('"%s"' % a if any(c in a for c in ' <>|&^') else a for a in pip_command(target, python_exe))


def _no_window():
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW


def install(target, python_exe, log=None):
    """Fetches the packages into target. Returns (ok, message). Never raises."""
    say = log or (lambda line: None)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        return False, "cannot create %s: %s" % (target, exc)
    say(pip_command_text(target, python_exe))
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        env.pop(key, None)
    try:
        proc = subprocess.Popen(pip_command(target, python_exe), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, env=env, **_no_window())
    except OSError as exc:
        return False, "cannot run pip: %s" % exc
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            say(line)
            tail = (tail + [line])[-12:]
    proc.wait()
    if proc.returncode != 0:
        return False, "pip failed (exit %d):\n  %s" % (proc.returncode, "\n  ".join(tail))
    _strip(target)
    return True, target


def _strip(target):
    """numpy's own test suite and bytecode: about a third of the size, never used."""
    for root, dirs, files in os.walk(target):
        for name in [d for d in dirs if d in ("tests", "__pycache__")]:
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
            dirs.remove(name)
