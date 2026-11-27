"""ffmpeg for the movie check, fetched on the first Nuke start when the PC has none: from a release served here (no
internet), the newest LGPL shared build picked from its SHA-256 list, only the programs and DLLs kept, nothing left when the
SHA-256 is wrong, the first-start question asked once with numpy, and JKrender finding the fetched ffprobe. On macOS the
static ffmpeg and ffprobe of the Mac's processor, each checked against its .sha256; on Linux the LGPL shared .tar.xz,
its programs in bin/ and the libraries they load in lib/."""
import hashlib
import http.server
import subprocess
import tempfile
import importlib.util
import threading
import types
import zipfile
from common import *

shutil.rmtree(RUN, ignore_errors=True)
plugin = RUN + "/plugin"  # a copy of the plugin, so lib/ is this test's
os.makedirs(plugin)
for f in ("jkrender_deps.py", "JKrender_NukeIntegration.py"):
    shutil.copy(REPO + "/" + f, plugin)
os.environ["PATH"] = os.path.dirname(sys.executable)  # no ffmpeg of the PC's own
os.environ["LOCALAPPDATA"] = RUN + "/localappdata"
os.environ.pop("JKRENDER_NO_FETCH", None)


def load(name):
    spec = importlib.util.spec_from_file_location(name, plugin + "/" + name + ".py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


deps = load("jkrender_deps")

say("1. A release like BtbN's, served here")
rel = RUN + "/release"
os.makedirs(rel)


def make_zip(name, top):
    with zipfile.ZipFile(rel + "/" + name, "w") as z:
        for f in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe", "avcodec-63.dll", "swscale-10.dll"):
            z.writestr(top + "/bin/" + f, "fake " + f)
        z.writestr(top + "/doc/ffmpeg.html", "doc")
        z.writestr(top + "/include/libavcodec/avcodec.h", "h")
        z.writestr(top + "/lib/avcodec.lib", "lib")
        z.writestr(top + "/LICENSE.txt", "LGPL")
    return hashlib.sha256(open(rel + "/" + name, "rb").read()).hexdigest()


new = "ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip"
names = {new: make_zip(new, new[:-4]),
         "ffmpeg-n8.1-latest-win64-lgpl-shared-8.1.zip": make_zip("ffmpeg-n8.1-latest-win64-lgpl-shared-8.1.zip", "old"),
         "ffmpeg-n10.0-latest-win64-gpl-shared-10.0.zip": "a" * 64,
         "ffmpeg-n10.0-latest-linux64-lgpl-shared-10.0.tar.xz": "b" * 64,
         "ffmpeg-master-latest-win64-lgpl-shared.zip": "c" * 64}


def write_sums(sums):
    with open(rel + "/checksums.sha256", "w") as f:
        f.write("".join("%s  %s\n" % (h, n) for n, h in sums.items()))


write_sums(names)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=rel, **k)

    def do_GET(self):  # macOS: like ffmpeg.martin-riedl.de, the "latest" URL sends on to the build's own folder
        kind = self.path.split("/")[1]
        if kind in ("flicker", "gone", "refused"):  # a server that fails now and then, always, or says no
            COUNTS[self.path] = COUNTS.get(self.path, 0) + 1
            if kind == "refused":
                self.send_error(403)
            elif kind == "gone" or COUNTS[self.path] <= 2:
                self.send_error(404)
            else:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
            return
        if self.path.startswith("/redirect/latest/macos/"):
            arch, tool = self.path.split("/")[4], self.path.split("/")[-1]
            self.send_response(307)
            self.send_header("Location", "/download/macos/%s/1_9.0/%s" % (arch, tool))
            self.end_headers()
            return
        super().do_GET()

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


COUNTS = {}
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/" % server.server_address[1]

say("1b. A server that fails now and then is asked again (1.0.24: Apple silicon's ffmpeg redirect gave 404 every other time)")
import urllib.error
with deps._get(url + "flicker/a") as r:
    check("404 twice, then the file: it gets the file on the third try", r.read() == b"ok" and COUNTS["/flicker/a"] == 3, COUNTS)
try:
    deps._get(url + "gone/a")
    check("404 every time: an error", False)
except urllib.error.HTTPError as exc:
    check("404 every time: given up after %d tries" % deps.GET_TRIES, exc.code == 404 and COUNTS["/gone/a"] == deps.GET_TRIES, COUNTS)
try:
    deps._get(url + "refused/a")
    check("403: an error", False)
except urllib.error.HTTPError as exc:
    check("403 (refused): no second try", exc.code == 403 and COUNTS["/refused/a"] == 1, COUNTS)

say("2. Picking the build")
check("the newest Windows LGPL shared release build", deps.pick_ffmpeg(open(rel + "/checksums.sha256").read())[0] == new)
check("master when there is no release build",
      deps.pick_ffmpeg("%s  ffmpeg-master-latest-win64-lgpl-shared.zip\n" % ("c" * 64))[0]
      == "ffmpeg-master-latest-win64-lgpl-shared.zip")
check("none when there is no Windows LGPL build", deps.pick_ffmpeg("%s  ffmpeg-n9.0-latest-win64-gpl-9.0.zip\n" % ("d" * 64)) is None)

if sys.platform == "darwin":
    say("3. macOS: ffmpeg and ffprobe of this Mac's processor, each checked against its .sha256")
    target = deps.ffmpeg_target()
    check("the target is lib/ffmpeg next to the plugin", os.path.normcase(target) == os.path.normcase(plugin + "/lib/ffmpeg"), target)
    check("no ffmpeg yet", deps.ffmpeg_needed() and deps.find_tool("ffprobe") is None)
    builds = rel + "/download/macos/" + deps.mac_arch() + "/1_9.0"
    os.makedirs(builds)
    sums = {}
    for tool in ("ffmpeg", "ffprobe"):
        with zipfile.ZipFile(builds + "/" + tool + ".zip", "w") as z:
            z.writestr(tool, "#!/bin/sh\necho fake %s\n" % tool)
        sums[tool] = hashlib.sha256(open(builds + "/" + tool + ".zip", "rb").read()).hexdigest()

    def mac_sums(bad=None):
        for tool, h in sums.items():
            open(builds + "/" + tool + ".zip.sha256", "w").write("%s  %s.zip\n" % ("0" * 64 if tool == bad else h, tool))

    cafile = deps.ca_file()
    check("Nuke's Python, whose own certificates are missing, gets the Mac's for HTTPS", cafile and os.path.isfile(cafile), cafile)
    mac_sums(bad="ffprobe")
    ok, msg = deps.fetch_ffmpeg(target, release=url)
    check("a wrong SHA-256: it fails", not ok and "SHA-256" in msg, msg)
    check("no folder, no part folder", not os.path.exists(target) and not os.path.exists(target + ".part"))
    check("no zip left in temp", not glob.glob(os.path.join(tempfile.gettempdir(), "jkrender-ffmpeg-*")))
    ok, msg = deps.fetch_ffmpeg(target, release="http://127.0.0.1:1/")
    check("no server: it fails, and no empty zip is left in temp",
          not ok and not glob.glob(os.path.join(tempfile.gettempdir(), "jkrender-ffmpeg-*")), msg)
    mac_sums()
    ok, msg = deps.fetch_ffmpeg(target, release=url)
    check("it is fetched", ok, msg)
    kept = sorted(os.listdir(target)) if os.path.isdir(target) else []
    check("only the two programs and SOURCE.txt", kept == ["SOURCE.txt", "ffmpeg", "ffprobe"], kept)
    run = subprocess.run([target + "/ffprobe"], capture_output=True, text=True) if "ffprobe" in kept else None
    check("ffprobe runs", run is not None and run.stdout.strip() == "fake ffprobe", run)
    check("SOURCE.txt says where from", "/download/macos/%s/1_9.0/ffprobe.zip" % deps.mac_arch() in open(target + "/SOURCE.txt").read())
    check("ffprobe is found there", deps.find_tool("ffprobe") == target + "/ffprobe", deps.find_tool("ffprobe"))
    check("no longer needed", not deps.ffmpeg_needed())

    say("4. macOS: the first-start question offers ffmpeg, as on Windows")
    asked = []
    answer = [False]
    fake = types.ModuleType("nuke")
    fake.ask = lambda text: (asked.append(text), answer[0])[1]
    fake.message = lambda text: asked.append("MESSAGE " + text)
    fake.executeInMainThread = lambda f, args=(): f(*args)
    fake.GUI = True
    sys.modules["nuke"] = fake
    integ = load("JKrender_NukeIntegration")
    deps.nuke_python = lambda nuke_exe=None: sys.executable
    integ._offer_downloads()
    check("not asked while there is one", not any("- ffmpeg" in t for t in asked), asked)
    shutil.rmtree(target)
    asked.clear()
    integ._offer_downloads()
    check("asked about ffmpeg, with its size", asked and "- ffmpeg, about %d MB" % deps.FFMPEG_MB in asked[0], asked)
    check("no: its marker", os.path.isfile(deps.ffmpeg_declined_marker()))
    asked.clear()
    integ._offer_downloads()
    check("not asked again", not any("- ffmpeg" in t for t in asked), asked)
    os.remove(deps.ffmpeg_declined_marker())
    answer[0] = True
    deps.fetch_ffmpeg = lambda t, log=None, release=None: (False, "no internet")
    asked.clear()
    integ._offer_downloads()
    deadline = time.time() + 10
    while not any(t.startswith("MESSAGE") for t in asked) and time.time() < deadline:
        time.sleep(0.05)
    check("a failed download says how by hand: brew", any(t.startswith("MESSAGE") and "brew install ffmpeg" in t for t in asked), asked)
    server.shutdown()
    finish()

say("3. A wrong SHA-256 leaves nothing")
target = deps.ffmpeg_target()
check("the target is lib/ffmpeg next to the plugin", os.path.normcase(target) == os.path.normcase(plugin + "/lib/ffmpeg"), target)
check("no ffmpeg yet", deps.ffmpeg_needed() and deps.find_tool("ffprobe") is None)
if not sys.platform.startswith("linux"):  # Linux checks its own .tar.xz below
    write_sums(dict(names, **{new: "0" * 64}))
    ok, msg = deps.fetch_ffmpeg(target, release=url)
    check("the download fails", not ok and "SHA-256" in msg, msg)
    check("no folder, no part folder", not os.path.exists(target) and not os.path.exists(target + ".part"))
    check("no zip left in temp", not glob.glob(os.path.join(os.environ.get("TEMP", "/tmp"), "jkrender-ffmpeg-*")))

if sys.platform.startswith("linux"):
    say("4. Linux: the LGPL shared .tar.xz, programs in bin/, the libraries they load in lib/")
    lin = "ffmpeg-n9.0-latest-linux64-lgpl-shared-9.0.tar.xz"

    def make_tar(name, top, probe_exit=0):
        """A build like BtbN's, packed by the system's tar (Nuke's Python has no lzma for tarfile's .xz)."""
        stage = RUN + "/stage"
        shutil.rmtree(stage, ignore_errors=True)
        d = stage + "/" + top
        for sub in ("bin", "lib/pkgconfig", "doc"):
            os.makedirs(d + "/" + sub)
        for tool in ("ffmpeg", "ffprobe", "ffplay"):  # ffprobe checks its library is beside it, in ../lib, as RPATH does
            with open(d + "/bin/" + tool, "w") as f:
                f.write('#!/bin/sh\nd="${0%%/*}"\n[ -f "$d/../lib/libavcodec.so.63" ] || exit 7\n'
                        'echo "%s version fake"\nexit %d\n' % (tool, probe_exit if tool == "ffprobe" else 0))
            os.chmod(d + "/bin/" + tool, 0o755)
        open(d + "/lib/libavcodec.so.63.1.102", "wb").write(b"ELF avcodec")
        os.symlink("libavcodec.so.63.1.102", d + "/lib/libavcodec.so.63")
        os.symlink("libavcodec.so.63", d + "/lib/libavcodec.so")
        open(d + "/lib/libswscale.so.10.1.102", "wb").write(b"ELF swscale")
        os.symlink("libswscale.so.10.1.102", d + "/lib/libswscale.so.10")
        open(d + "/lib/pkgconfig/libavcodec.pc", "w").write("pc")
        open(d + "/doc/ffmpeg.html", "w").write("doc")
        open(d + "/LICENSE.txt", "w").write("LGPL")
        subprocess.run(["/usr/bin/env", "PATH=/usr/bin:/bin", "tar", "-cJf", rel + "/" + name, "-C", stage, top], check=True)
        return hashlib.sha256(open(rel + "/" + name, "rb").read()).hexdigest()

    lin_sums = dict(names, **{lin: make_tar(lin, lin[:-7]),
                              "ffmpeg-n8.1-latest-linux64-lgpl-shared-8.1.tar.xz": make_tar("ffmpeg-n8.1-latest-linux64-lgpl-shared-8.1.tar.xz", "old"),
                              "ffmpeg-n9.0-latest-linux64-lgpl-9.0.tar.xz": "e" * 64})
    lin_sums.pop("ffmpeg-n10.0-latest-linux64-lgpl-shared-10.0.tar.xz")
    write_sums(lin_sums)
    check("the newest Linux LGPL shared build, not the static one nor a Windows zip",
          deps.pick_ffmpeg(open(rel + "/checksums.sha256").read(), "linux64")[0] == lin)
    check("Linux master when there is no release build",
          deps.pick_ffmpeg("%s  ffmpeg-master-latest-linux64-lgpl-shared.tar.xz\n" % ("c" * 64), "linux64")[0]
          == "ffmpeg-master-latest-linux64-lgpl-shared.tar.xz")
    write_sums(dict(lin_sums, **{lin: "0" * 64}))
    ok, msg = deps.fetch_ffmpeg(target, release=url)
    check("a wrong SHA-256: it fails", not ok and "SHA-256" in msg, msg)
    check("no folder, no part folder", not os.path.exists(target) and not os.path.exists(target + ".part"))
    check("no download left in temp", not glob.glob(os.path.join(tempfile.gettempdir(), "jkrender-ffmpeg-*")))
    write_sums(lin_sums)
    ok, msg = deps.fetch_ffmpeg(target, release=url)
    check("it is fetched", ok, msg)
    kept = sorted(os.path.relpath(os.path.join(r, f), target) for r, _, fs in os.walk(target) for f in fs) if os.path.isdir(target) else []
    check("bin/ffmpeg, bin/ffprobe, the libraries under the names they load, SOURCE.txt",
          kept == ["SOURCE.txt", "bin/ffmpeg", "bin/ffprobe", "lib/libavcodec.so.63", "lib/libswscale.so.10"], kept)
    check("a library is the file, not a link", not os.path.islink(target + "/lib/libavcodec.so.63")
          and open(target + "/lib/libavcodec.so.63", "rb").read() == b"ELF avcodec")
    run = subprocess.run([target + "/bin/ffprobe", "-version"], capture_output=True, text=True)
    check("ffprobe runs and finds its library", run.returncode == 0 and "ffprobe version fake" in run.stdout, (run.returncode, run.stdout))
    check("SOURCE.txt says where from", lin in open(target + "/SOURCE.txt").read())
    check("ffprobe is found there", deps.find_tool("ffprobe") == target + "/bin/ffprobe", deps.find_tool("ffprobe"))
    check("no longer needed", not deps.ffmpeg_needed())
    shutil.rmtree(target)
    write_sums(dict(lin_sums, **{lin: make_tar(lin, lin[:-7], probe_exit=3)}))
    ok, msg = deps.fetch_ffmpeg(target, release=url)
    check("an ffprobe that does not run: it fails, nothing left", not ok and "does not run" in msg and not os.path.exists(target)
          and not os.path.exists(target + ".part") and not os.path.exists(target + ".part.x"), msg)
    write_sums(dict(lin_sums, **{lin: make_tar(lin, lin[:-7])}))

    say("5. Linux: the first-start question offers ffmpeg, as on Windows")
    asked = []
    answer = [False]
    fake = types.ModuleType("nuke")
    fake.ask = lambda text: (asked.append(text), answer[0])[1]
    fake.message = lambda text: asked.append("MESSAGE " + text)
    fake.executeInMainThread = lambda f, args=(): f(*args)
    fake.GUI = True
    sys.modules["nuke"] = fake
    integ = load("JKrender_NukeIntegration")
    deps.nuke_python = lambda nuke_exe=None: sys.executable
    integ._offer_downloads()
    check("asked about ffmpeg, with its size", asked and "- ffmpeg, about %d MB" % deps.FFMPEG_MB in asked[0], asked)
    check("no: its marker", os.path.isfile(deps.ffmpeg_declined_marker()))
    asked.clear()
    integ._offer_downloads()
    check("not asked again", not any("- ffmpeg" in t for t in asked), asked)
    os.remove(deps.ffmpeg_declined_marker())
    answer[0] = True
    deps.fetch_ffmpeg = lambda t, log=None, release=None: (False, "no internet")
    asked.clear()
    integ._offer_downloads()
    deadline = time.time() + 10
    while not any(t.startswith("MESSAGE") for t in asked) and time.time() < deadline:
        time.sleep(0.05)
    check("a failed download says how by hand: apt", any(t.startswith("MESSAGE") and "sudo apt install ffmpeg" in t for t in asked), asked)
    server.shutdown()
    finish()

say("4. The real fetch")
write_sums(names)
ok, msg = deps.fetch_ffmpeg(target, release=url)
check("it is fetched", ok, msg)
kept = sorted(os.listdir(target)) if os.path.isdir(target) else []
check("only the programs, their DLLs and SOURCE.txt",
      kept == ["SOURCE.txt", "avcodec-63.dll", "ffmpeg.exe", "ffprobe.exe", "swscale-10.dll"], kept)
check("from the newest build", open(target + "/ffprobe.exe").read() == "fake ffprobe.exe")
check("SOURCE.txt says where from", new in open(target + "/SOURCE.txt").read())
check("ffprobe is found there", os.path.normcase(deps.find_tool("ffprobe") or "") == os.path.normcase(target + "/ffprobe.exe"))
check("no longer needed", not deps.ffmpeg_needed())

say("5. The PC's own ffmpeg comes first")
links = os.environ["LOCALAPPDATA"] + "/Microsoft/WinGet/Links"
os.makedirs(links)
open(links + "/ffprobe.exe", "w").close()
check("WinGet's ffprobe", os.path.normcase(deps.find_tool("ffprobe")) == os.path.normcase(links + "/ffprobe.exe"))
os.remove(links + "/ffprobe.exe")

say("6. The first-start question")
asked = []
answer = [False]
fake = types.ModuleType("nuke")
fake.ask = lambda text: (asked.append(text), answer[0])[1]
fake.message = lambda text: asked.append("MESSAGE " + text)
fake.executeInMainThread = lambda f, args=(): f(*args)
fake.GUI = True
sys.modules["nuke"] = fake
integ = load("JKrender_NukeIntegration")
deps.nuke_python = lambda nuke_exe=None: sys.executable
integ._nuke_python = lambda: (sys.executable, {})  # Nuke 16 and later: the window runs in Nuke's own Python
shutil.rmtree(target)
integ._offer_downloads()
check("asked once, about both", len(asked) == 1 and "ffmpeg" in asked[0] and "numpy and OpenEXR" in asked[0], asked)
check("with the size of each", "about %d MB" % deps.FFMPEG_MB in asked[0] and "about %d MB" % deps.DOWNLOAD_MB in asked[0])
check("no: both markers", os.path.isfile(deps.ffmpeg_declined_marker()) and os.path.isfile(deps.declined_marker()))
asked.clear()
integ._offer_downloads()
check("not asked again", not asked, asked)
os.remove(deps.ffmpeg_declined_marker())
answer[0] = True
fetched = []
deps.fetch_ffmpeg = lambda t, log=None, release=None: (fetched.append(t), (True, t))[1]
integ._offer_downloads()
deadline = time.time() + 10
while not fetched and time.time() < deadline:
    time.sleep(0.05)
check("only ffmpeg asked after its marker went", len(asked) == 1 and "ffmpeg" in asked[0] and "numpy" not in asked[0], asked)
check("yes: ffmpeg is fetched into lib/ffmpeg", fetched == [target], fetched)
os.environ["JKRENDER_NO_FETCH"] = "1"
asked.clear()
integ._offer_downloads()
check("JKRENDER_NO_FETCH: never asked", not asked)
os.environ.pop("JKRENDER_NO_FETCH")

say("6b. Nuke 15 and earlier (PySide2): numpy and OpenEXR for the Python the window runs in, not for Nuke's")
# 0.99.77: Nuke 15.1 offered them for its own Python 3.10, while the window ran in another Python with PySide6
for m in (deps.declined_marker(), deps.ffmpeg_declined_marker()):
    if os.path.exists(m):
        os.remove(m)
integ._nuke_python = lambda: None
cache = os.path.join(RUN, "jkrender_python.txt")
integ.PYTHON_CACHE = __import__("pathlib").Path(cache)
answer[0] = False
asked.clear()
integ._offer_downloads()
check("no window Python yet (no render sent): not asked about numpy", not any("numpy" in t for t in asked), asked)
open(cache, "w").write(sys.executable)
integ._python_version = lambda exe, env: (3, 12)
asked.clear()
integ._offer_downloads()
check("asked for that Python's folder (cp312), not Nuke's", asked and "numpy and OpenEXR" in asked[0] and "-cp312" in asked[0], asked)
os.remove(deps.declined_marker())
os.remove(deps.ffmpeg_declined_marker())
answer[0] = True
installs = []
deps.install = lambda t, exe, log=None: (installs.append((t, exe)), (True, t))[1]
deps.fetch_ffmpeg = lambda t, log=None, release=None: (True, t)
asked.clear()
integ._offer_downloads()
deadline = time.time() + 10
while not installs and time.time() < deadline:
    time.sleep(0.05)
check("installed with that Python, into its folder", installs and installs[0][1] == sys.executable and installs[0][0].endswith("-cp312"), installs)

say("6c. Clearing an open Nuke's cache in a Nuke without nuke.memory2 (older versions)")
cleared = []
fake.clearRAMCache = lambda: cleared.append("ram")
fake.clearBlinkCache = lambda: cleared.append("blink")
try:
    integ._clear_cache()
    ok = True
except Exception as exc:
    ok = exc
check("no error, the caches it has are cleared", ok is True and cleared == ["ram", "blink"], (ok, cleared))

say("7. JKrender finds the fetched ffprobe")
deps.fetch_ffmpeg = None
ok, msg = load("jkrender_deps").fetch_ffmpeg(target, release=url)
jk, app = start(clean=False, qt=False)
check("JKrender's find_tool", os.path.normcase(jk.find_tool("ffprobe") or "") == os.path.normcase(target + "/ffprobe.exe"),
      jk.find_tool("ffprobe"))
server.shutdown()
finish()
