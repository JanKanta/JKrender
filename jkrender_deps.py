"""numpy and OpenEXR for JKrender's EXR preview, fetched on the first Nuke start.

Nuke ships neither. They are compiled, so a build is locked to the platform and the Python
version; fetching with Nuke's own python gets the build that fits that Nuke. They land in
lib/<platform>-cp<version> next to this file (in ~/.jkrender/lib/... when this folder is
read-only), and JKrender.py puts that folder on its path.

Rendering does not need them; only the preview of EXR frames does.
Nothing in here imports numpy or OpenEXR.

ffmpeg and ffprobe, for checking movies and the movie player, are fetched the same way, on Windows only, when the PC has
none of its own: the LGPL build from github.com/BtbN/FFmpeg-Builds, checked against the release's SHA-256 list, into
lib/ffmpeg (only the programs and their DLLs).
"""

import os
import platform
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile

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


# ----------------------------------------------------------------------------
# ffmpeg
# ----------------------------------------------------------------------------
FFMPEG_MB = 77  # the zip of the LGPL shared build, 9.0
FFMPEG_RELEASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
FFMPEG_TOOLS = ("ffmpeg", "ffprobe")


def _exe(name):
    return name + (".exe" if sys.platform == "win32" else "")


def ffmpeg_dirs():
    """Where JKrender's own ffmpeg may be, best first."""
    return [os.path.join(HERE, "lib", "ffmpeg"), os.path.join(os.path.expanduser("~"), ".jkrender", "lib", "ffmpeg")]


def ffmpeg_installed():
    """JKrender's own ffmpeg folder, with both programs in it, or None."""
    for folder in ffmpeg_dirs():
        if all(os.path.isfile(os.path.join(folder, _exe(n))) for n in FFMPEG_TOOLS):
            return folder
    return None


def system_tool(name):
    """ffmpeg / ffprobe the PC has of its own: on PATH, or where WinGet puts them."""
    found = shutil.which(name)
    if not found and sys.platform == "win32":
        link = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", _exe(name))
        found = link if os.path.isfile(link) else None
    return found


def find_tool(name):
    """ffmpeg / ffprobe: the PC's own first, then the one JKrender fetched. None when there is neither."""
    found = system_tool(name)
    if not found:
        folder = ffmpeg_installed()
        found = os.path.join(folder, _exe(name)) if folder else None
    return found


def ffmpeg_needed():
    """True when there is no ffmpeg or ffprobe at all, so movies cannot be checked or played."""
    return any(find_tool(n) is None for n in FFMPEG_TOOLS)


def ffmpeg_target():
    first, fallback = ffmpeg_dirs()
    return first if _writable(first) else fallback


def ffmpeg_declined_marker():
    return os.path.join(HERE, "lib", "DECLINED_FFMPEG")


def _version_key(name):
    """ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip -> (9, 0); master builds come last."""
    try:
        return tuple(int(x) for x in name.split("-n", 1)[1].split("-", 1)[0].split("."))
    except (IndexError, ValueError):
        return ()


def pick_ffmpeg(checksums):
    """From the release's checksums.sha256 text: (file name, sha256) of the newest release build of the Windows LGPL
    shared ffmpeg, or the master build when no release build is listed. None when there is neither."""
    found = {}
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            found[parts[1].lstrip("*")] = parts[0].lower()
    named = [n for n in found if n.startswith("ffmpeg-n") and "-win64-lgpl-shared-" in n and n.endswith(".zip")]
    if named:
        best = max(named, key=_version_key)
        return best, found[best]
    if "ffmpeg-master-latest-win64-lgpl-shared.zip" in found:
        return "ffmpeg-master-latest-win64-lgpl-shared.zip", found["ffmpeg-master-latest-win64-lgpl-shared.zip"]
    return None


def _get(url, timeout=60):
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "JKrender"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_ffmpeg(target, log=None, release=None):
    """Downloads ffmpeg into target. Returns (ok, message). Never raises.

    Only ffmpeg, ffprobe and the DLLs they load are kept; the zip is checked against the SHA-256 the release lists, and
    the folder appears only when it is whole."""
    say = log or (lambda line: None)
    release = release or FFMPEG_RELEASE
    tmp = None
    part = target + ".part"
    try:
        with _get(release + "checksums.sha256") as r:
            picked = pick_ffmpeg(r.read().decode("utf-8", "replace"))
        if not picked:
            return False, "no Windows LGPL build of ffmpeg in %s" % release
        name, want = picked
        say("downloading %s%s" % (release, name))
        digest = hashlib.sha256()
        fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="jkrender-ffmpeg-")
        with os.fdopen(fd, "wb") as out, _get(release + name, timeout=120) as r:
            while True:
                block = r.read(1 << 20)
                if not block:
                    break
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != want:
            return False, "%s does not match its SHA-256 (got %s, the release lists %s)" % (name, digest.hexdigest(), want)
        shutil.rmtree(part, ignore_errors=True)
        os.makedirs(part)
        kept = 0
        with zipfile.ZipFile(tmp) as z:
            for info in z.infolist():
                parts = info.filename.replace("\\", "/").split("/")
                if len(parts) != 3 or parts[1] != "bin" or info.is_dir():
                    continue  # only <top>/bin/<file>
                base = parts[2]
                low = base.lower()
                if not (low.endswith(".dll") or low in [_exe(n).lower() for n in FFMPEG_TOOLS]):
                    continue
                with z.open(info) as src, open(os.path.join(part, base), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                kept += 1
        missing = [n for n in FFMPEG_TOOLS if not os.path.isfile(os.path.join(part, _exe(n)))]
        if missing:
            return False, "%s has no %s" % (name, ", ".join(missing))
        with open(os.path.join(part, "SOURCE.txt"), "w", encoding="utf-8") as f:
            f.write("%s%s\nsha256 %s\nFFmpeg is LGPL: https://ffmpeg.org/legal.html\n"
                    "Delete this folder to remove it; JKrender then uses an ffmpeg on PATH, if there is one.\n"
                    % (release, name, want))
        shutil.rmtree(target, ignore_errors=True)
        os.replace(part, target)
        say("ffmpeg is in %s (%d files)" % (target, kept + 1))
        return True, target
    except Exception as exc:
        return False, "cannot download ffmpeg: %s" % exc
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
        shutil.rmtree(part, ignore_errors=True)
