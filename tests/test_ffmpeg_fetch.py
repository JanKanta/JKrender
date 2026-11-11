"""ffmpeg for the movie check, fetched on the first Nuke start when the PC has none: from a release served here (no
internet), the newest LGPL shared build picked from its SHA-256 list, only the programs and DLLs kept, nothing left when the
SHA-256 is wrong, the first-start question asked once with numpy, and JKrender finding the fetched ffprobe."""
import hashlib
import http.server
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

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/" % server.server_address[1]

say("2. Picking the build")
check("the newest Windows LGPL shared release build", deps.pick_ffmpeg(open(rel + "/checksums.sha256").read())[0] == new)
check("master when there is no release build",
      deps.pick_ffmpeg("%s  ffmpeg-master-latest-win64-lgpl-shared.zip\n" % ("c" * 64))[0]
      == "ffmpeg-master-latest-win64-lgpl-shared.zip")
check("none when there is no Windows LGPL build", deps.pick_ffmpeg("%s  ffmpeg-n9.0-latest-win64-gpl-9.0.zip\n" % ("d" * 64)) is None)

say("3. A wrong SHA-256 leaves nothing")
target = deps.ffmpeg_target()
check("the target is lib/ffmpeg next to the plugin", os.path.normcase(target) == os.path.normcase(plugin + "/lib/ffmpeg"), target)
check("no ffmpeg yet", deps.ffmpeg_needed() and deps.find_tool("ffprobe") is None)
write_sums(dict(names, **{new: "0" * 64}))
ok, msg = deps.fetch_ffmpeg(target, release=url)
check("the download fails", not ok and "SHA-256" in msg, msg)
check("no folder, no part folder", not os.path.exists(target) and not os.path.exists(target + ".part"))
check("no zip left in temp", not glob.glob(os.path.join(os.environ.get("TEMP", "/tmp"), "jkrender-ffmpeg-*")))

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

say("7. JKrender finds the fetched ffprobe")
deps.fetch_ffmpeg = None
ok, msg = load("jkrender_deps").fetch_ffmpeg(target, release=url)
jk, app = start(clean=False, qt=False)
check("JKrender's find_tool", os.path.normcase(jk.find_tool("ffprobe") or "") == os.path.normcase(target + "/ffprobe.exe"),
      jk.find_tool("ffprobe"))
server.shutdown()
finish()
