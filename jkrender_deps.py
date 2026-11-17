"""numpy and OpenEXR for JKrender's EXR preview, fetched on the first Nuke start.

Nuke ships neither. They are compiled, so a build is locked to the platform and the Python
version; fetching with Nuke's own python gets the build that fits that Nuke. They land in
lib/<platform>-cp<version> next to this file (in ~/.jkrender/lib/... when this folder is
read-only), and JKrender.py puts that folder on its path.

Rendering does not need them; only the preview of EXR frames does.
Nothing in here imports numpy or OpenEXR.

ffmpeg and ffprobe, for checking movies and the movie player, are fetched the same way, on Windows and macOS, when the PC
has none of its own, into lib/ffmpeg: on Windows the LGPL build from github.com/BtbN/FFmpeg-Builds, checked against the
release's SHA-256 list (only the programs and their DLLs); on macOS the static builds from ffmpeg.martin-riedl.de, for
Apple silicon or Intel, each checked against the SHA-256 published next to it.
"""

import os
import platform
import hashlib
import json
import shutil
import subprocess
import sys
import re
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
FFMPEG_MB = {"win32": 77, "darwin": 60}.get(sys.platform, 63)  # the download: Windows and Linux the LGPL shared build, macOS both zips
FFMPEG_RELEASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
FFMPEG_MAC = "https://ffmpeg.martin-riedl.de/"
FFMPEG_TOOLS = ("ffmpeg", "ffprobe")


def _exe(name):
    return name + (".exe" if sys.platform == "win32" else "")


def ffmpeg_dirs():
    """Where JKrender's own ffmpeg may be, best first."""
    return [os.path.join(HERE, "lib", "ffmpeg"), os.path.join(os.path.expanduser("~"), ".jkrender", "lib", "ffmpeg")]


def _tool_in(folder, name):
    """ffmpeg / ffprobe in JKrender's own ffmpeg folder: in bin/ on Linux (its libraries in lib/ beside it), else at the top."""
    in_bin = os.path.join(folder, "bin", name)
    return in_bin if os.path.isfile(in_bin) else os.path.join(folder, _exe(name))


def ffmpeg_installed():
    """JKrender's own ffmpeg folder, with both programs in it, or None."""
    for folder in ffmpeg_dirs():
        if all(os.path.isfile(_tool_in(folder, n)) for n in FFMPEG_TOOLS):
            return folder
    return None


def system_tool(name):
    """ffmpeg / ffprobe the PC has of its own: on PATH, or where WinGet puts them."""
    found = shutil.which(name)
    if not found and sys.platform == "win32":
        link = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", _exe(name))
        found = link if os.path.isfile(link) else None
    if not found and sys.platform == "darwin":  # Homebrew: not on the PATH of an app started from the Dock
        found = next((p for p in ("/opt/homebrew/bin/" + name, "/usr/local/bin/" + name) if os.path.isfile(p)), None)
    return found


def find_tool(name):
    """ffmpeg / ffprobe: the PC's own first, then the one JKrender fetched. None when there is neither."""
    found = system_tool(name)
    if not found:
        folder = ffmpeg_installed()
        found = _tool_in(folder, name) if folder else None
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


def pick_ffmpeg(checksums, build="win64"):
    """From the release's checksums.sha256 text: (file name, sha256) of the newest release build of the LGPL shared
    ffmpeg for Windows ("win64", a .zip) or Linux ("linux64", a .tar.xz), or the master build when no release build is
    listed. None when there is neither."""
    ext = ".zip" if build == "win64" else ".tar.xz"
    found = {}
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            found[parts[1].lstrip("*")] = parts[0].lower()
    named = [n for n in found if n.startswith("ffmpeg-n") and "-%s-lgpl-shared-" % build in n and n.endswith(ext)]
    if named:
        best = max(named, key=_version_key)
        return best, found[best]
    master = "ffmpeg-master-latest-%s-lgpl-shared%s" % (build, ext)
    if master in found:
        return master, found[master]
    return None


def mac_arch():
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"


def _download(url, timeout=120):
    """url into a temp file: (path, sha256, the URL it came from after redirects)."""
    digest = hashlib.sha256()
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="jkrender-ffmpeg-")
    try:
        with os.fdopen(fd, "wb") as out, _get(url, timeout=timeout) as r:
            final = r.geturl()
            while True:
                block = r.read(1 << 20)
                if not block:
                    break
                digest.update(block)
                out.write(block)
    except BaseException:
        os.remove(tmp)  # no empty zip left in temp when the server cannot be reached
        raise
    return tmp, digest.hexdigest(), final


def _fetch_ffmpeg_mac(target, say, base):
    """macOS: ffmpeg.zip and ffprobe.zip of the newest release build for this Mac's processor; each zip's SHA-256 is read
    from <its URL>.sha256. The folder appears only when both are whole."""
    part = target + ".part"
    tmp = None
    sources = []
    try:
        shutil.rmtree(part, ignore_errors=True)
        os.makedirs(part)
        for tool in FFMPEG_TOOLS:
            url = "%sredirect/latest/macos/%s/release/%s.zip" % (base, mac_arch(), tool)
            say("downloading %s" % url)
            tmp, got, final = _download(url)
            with _get(final + ".sha256") as r:
                listed = r.read().decode("utf-8", "replace").split()
            want = listed[0].lower() if listed and len(listed[0]) == 64 else None
            if got != want:
                return False, "%s does not match its SHA-256 (got %s, the server lists %s)" % (final, got, want)
            with zipfile.ZipFile(tmp) as z:
                found = [i for i in z.infolist() if not i.is_dir() and i.filename.replace("\\", "/").split("/")[-1] == tool]
                if not found:
                    return False, "%s has no %s" % (final, tool)
                with z.open(found[0]) as src, open(os.path.join(part, tool), "wb") as dst:
                    shutil.copyfileobj(src, dst)
            os.chmod(os.path.join(part, tool), 0o755)
            os.remove(tmp)
            tmp = None
            sources.append("%s\nsha256 %s\n" % (final, want))
        with open(os.path.join(part, "SOURCE.txt"), "w", encoding="utf-8") as f:
            f.write("".join(sources) + "FFmpeg (these builds are GPL): https://ffmpeg.org/legal.html\n"
                    "Delete this folder to remove it; JKrender then uses Homebrew's or the PATH's ffmpeg, if there is one.\n")
        shutil.rmtree(target, ignore_errors=True)
        os.replace(part, target)
        say("ffmpeg is in %s" % target)
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


CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


def ca_file():
    """The certificates HTTPS checks servers against, when Python's own are missing, else None. Nuke's Python on macOS
    looks for them where Foundry built it, so it trusts no server (CERTIFICATE_VERIFY_FAILED); the system's then, or
    certifi's. Windows takes them from the system store."""
    import ssl

    paths = ssl.get_default_verify_paths()
    if sys.platform == "win32" or os.environ.get(paths.openssl_cafile_env) or os.path.isfile(paths.openssl_cafile or ""):
        return None
    for path in CA_FILES:
        if os.path.isfile(path):
            return path
    try:
        import certifi
    except ImportError:
        try:
            from pip._vendor import certifi
        except ImportError:
            return None
    return certifi.where()


def _get(url, timeout=60):
    import ssl
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "JKrender"})
    cafile = ca_file() if url.startswith("https:") else None
    context = ssl.create_default_context(cafile=cafile) if cafile else None
    return urllib.request.urlopen(req, timeout=timeout, context=context)


SONAME = re.compile(r"^lib\w+\.so\.\d+$")  # libavcodec.so.63: the names the programs load


def _fetch_ffmpeg_linux(target, say, release):
    """Linux: the newest LGPL shared build (.tar.xz), checked against the SHA-256 the release lists. Kept: bin/ffmpeg,
    bin/ffprobe and the libraries they load in lib/, each under the name they look for (the programs find them through
    their RPATH, $ORIGIN/../lib). ffprobe has to run before the folder appears."""
    part = target + ".part"
    tmp = None
    try:
        with _get(release + "checksums.sha256") as r:
            picked = pick_ffmpeg(r.read().decode("utf-8", "replace"), "linux64")
        if not picked:
            return False, "no Linux LGPL build of ffmpeg in %s" % release
        name, want = picked
        say("downloading %s%s" % (release, name))
        tmp, got, _ = _download(release + name, timeout=120)
        if got != want:
            return False, "%s does not match its SHA-256 (got %s, the release lists %s)" % (name, got, want)
        shutil.rmtree(part, ignore_errors=True)
        unpacked = part + ".x"
        shutil.rmtree(unpacked, ignore_errors=True)
        os.makedirs(unpacked)
        os.makedirs(os.path.join(part, "bin"))
        os.makedirs(os.path.join(part, "lib"))
        # the system's tar: Nuke's Python on Linux has no lzma module, so tarfile cannot read .xz
        env = dict(os.environ, PATH=os.pathsep.join(p for p in (os.environ.get("PATH", ""), "/usr/bin", "/bin") if p))  # tar runs xz
        tar = shutil.which("tar", path=env["PATH"]) or "tar"
        run = subprocess.run([tar, "-xJf", tmp, "-C", unpacked, "--no-same-owner"], capture_output=True, text=True, timeout=300,
                             env=env)
        if run.returncode != 0:
            return False, "cannot unpack %s: %s" % (name, (run.stderr or "tar exit %d" % run.returncode).strip()[-300:])
        tops = [d for d in os.listdir(unpacked) if os.path.isdir(os.path.join(unpacked, d))]
        top = os.path.join(unpacked, tops[0]) if len(tops) == 1 else unpacked
        root = os.path.realpath(unpacked)
        kept = 0
        for sub, wanted in (("bin", lambda n: n in FFMPEG_TOOLS), ("lib", SONAME.match)):
            folder = os.path.join(top, sub)
            for n in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
                src = os.path.realpath(os.path.join(folder, n))  # a library's symlink: the file it points to
                if not wanted(n) or not src.startswith(root + os.sep) or not os.path.isfile(src):
                    continue
                dest = os.path.join(part, sub, n)
                shutil.copyfile(src, dest)
                os.chmod(dest, 0o755)
                kept += 1
        missing = [n for n in FFMPEG_TOOLS if not os.path.isfile(os.path.join(part, "bin", n))]
        if missing:
            return False, "%s has no %s" % (name, ", ".join(missing))
        try:
            run = subprocess.run([os.path.join(part, "bin", "ffprobe"), "-version"], capture_output=True, text=True, timeout=30)
            why = "" if run.returncode == 0 else (run.stderr or run.stdout or "exit %d" % run.returncode)
        except Exception as exc:
            why = str(exc)
        if why:
            return False, "the downloaded ffprobe does not run: %s" % why.strip()[-300:]
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
        shutil.rmtree(part + ".x", ignore_errors=True)


def fetch_ffmpeg(target, log=None, release=None):
    """Downloads ffmpeg into target. Returns (ok, message). Never raises.

    Only ffmpeg, ffprobe and the DLLs they load are kept; the zip is checked against the SHA-256 the release lists, and
    the folder appears only when it is whole."""
    say = log or (lambda line: None)
    if sys.platform == "darwin":
        return _fetch_ffmpeg_mac(target, say, release or FFMPEG_MAC)
    if sys.platform.startswith("linux"):
        return _fetch_ffmpeg_linux(target, say, release or FFMPEG_RELEASE)
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
