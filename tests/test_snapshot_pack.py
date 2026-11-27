"""Script copies packed (1.0.29): a log folder of 337 MB held 313 MB of script copies (snapshots), 42 % of them the same
script sent with several Writes. The copies of renders that are not open and were not touched for an hour are packed into
.gz (about a fifth of the size); the open ones, the ones just rendered, and folders JKrender did not make stay as they are.
The packed copy keeps the time of the copy, so the cleanup after N days goes by the same age. A render of such a folder
unpacks its copy before Nuke opens it. The window packs in the background a few seconds after it opens, and says so."""
import gzip
from common import *

jk, app = start({"processes": 1, "log_cleanup": True, "log_days": 15})
log = RUN + "/log"
HOUR = 3600
script = ("Root {\n inputs 0\n}\n" + "".join("Blur {\n size %d\n name Blur%d\n}\n" % (i, i) for i in range(4000))).encode()


def render_folder(name, age, job=True):
    d = "%s/%s" % (log, name)
    os.makedirs(d + "/logs", exist_ok=True)
    if job:
        open(d + "/job.json", "w").write("{}")
    open(d + "/snapshot_sh010_comp_v003.nk", "wb").write(script)
    open(d + "/logs/worker_01.log", "w").write("JKR READY 1.2\n")
    t = time.time() - age
    for root, dirs, files in os.walk(d):
        for f in files:
            os.utime(os.path.join(root, f), (t, t))
    os.utime(d, (t, t))
    return d


say("Which folders are packed")
a = render_folder("A_old", 2 * HOUR)
b = render_folder("B_old_open", 2 * HOUR)
c = render_folder("C_just_rendered", 60)
d = render_folder("D_not_jkrender", 2 * HOUR, job=False)
e = render_folder("E_old_to_unpack", 2 * HOUR)
old_time = os.path.getmtime(a + "/snapshot_sh010_comp_v003.nk")
packed, before, after = jk.pack_snapshots(log, keep=[b])
check("two packed: the old ones that are not open", packed == 2, (packed, before, after))
check("the copy is gone, its .gz is there", not os.path.exists(a + "/snapshot_sh010_comp_v003.nk")
      and os.path.isfile(a + "/snapshot_sh010_comp_v003.nk.gz"), os.listdir(a))
check("it unpacks to the same script", gzip.open(a + "/snapshot_sh010_comp_v003.nk.gz").read() == script)
check("it keeps the copy's time: the cleanup's age stays", abs(os.path.getmtime(a + "/snapshot_sh010_comp_v003.nk.gz") - old_time) < 2)
check("about a fifth of the size or less", after * 3 < before, (before, after))
check("an open render's copy stays", os.path.isfile(b + "/snapshot_sh010_comp_v003.nk"), os.listdir(b))
check("a render just done stays", os.path.isfile(c + "/snapshot_sh010_comp_v003.nk"), os.listdir(c))
check("a folder JKrender did not make stays", os.path.isfile(d + "/snapshot_sh010_comp_v003.nk"), os.listdir(d))
check("packing again packs nothing", jk.pack_snapshots(log, keep=[b])[0] == 0)

say("Unpacked when needed")
snap = e + "/snapshot_sh010_comp_v003.nk"
check("unpack: the script is back, the same", jk.unpack_snapshot(snap) and open(snap, "rb").read() == script)
check("and the .gz is gone", not os.path.exists(snap + ".gz"), os.listdir(e))
check("a copy that is there: nothing to do", jk.unpack_snapshot(snap) is True)
check("neither there: False", jk.unpack_snapshot(e + "/nothing.nk") is False)

say("The cleanup goes by the same age")
deleted, _ = jk.clean_render_folders(log, 1 / 24.0, keep=[b])  # older than an hour
check("the packed old folder is deleted as it would have been", not os.path.exists(a) and os.path.exists(b) and os.path.exists(c),
      sorted(os.listdir(log)))

say("A render whose copy was packed: unpacked before Nuke opens it")
job = make_job("sh_packed", 1001, 1004, folder=log, env={"FAKE_SECS": "0.2"})
data = json.load(open(job, encoding="utf-8"))
jdir = os.path.dirname(job)
data["snapshot"] = jdir + "/snapshot_sh_packed.nk"
shutil.copy(data["script"], data["snapshot"])
json.dump(data, open(job, "w", encoding="utf-8"))
t = time.time() - 2 * HOUR
for root, dirs, files in os.walk(jdir):
    for f in files + dirs:
        os.utime(os.path.join(root, f), (t, t))
os.utime(jdir, (t, t))
check("it was packed while not open", jk.pack_snapshots(log, keep=[])[0] >= 1 and os.path.isfile(data["snapshot"] + ".gz"))
win = jk.MainWindow(job)
eng = win.engine
win._start()
check("the render runs and finishes", wait(lambda: eng.state == "done", 60), eng.state)
check("its copy is unpacked again, as Nuke opened it", os.path.isfile(data["snapshot"]) and not os.path.exists(data["snapshot"] + ".gz"),
      os.listdir(jdir))

say("The window packs the others by itself, a few seconds after it opens, and says so")
f = render_folder("F_old", 2 * HOUR)
check("it packs F", wait(lambda: os.path.isfile(f + "/snapshot_sh010_comp_v003.nk.gz"), 20), os.listdir(f))
check("the Log says it", wait(lambda: any("Packed the script copies" in x[1] for x in eng.log), 5), [x[1] for x in eng.log][-5:])
check("its own render's copy is not packed (open)", os.path.isfile(data["snapshot"]), os.listdir(jdir))
finish()
