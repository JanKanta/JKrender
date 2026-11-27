"""Updates on a click (1.0.1): at start the window asks for the newest public release (here a local server standing in
for GitHub) and, when it is newer and not skipped, shows the update window with What's new, Update and Later. Update
downloads the release zip and unpacks it over the JKrender folder (here a folder of its own), keeping lib, log and
settings, the replaced files in update_backup/; a broken zip, a zip of another version and a git clone change nothing;
it never runs while a render runs."""
import http.server
import threading
import zipfile
from common import *

serve = os.path.join(RUN, "server")
target = os.path.join(RUN, "install")
os.environ["JKRENDER_UPDATE_DIR"] = target
os.environ.pop("JKRENDER_NO_UPDATE", None)  # run_tests turns the check off; here it asks only the local server below
jk, app = start({"processes": 1})
from PySide6 import QtWidgets

os.makedirs(serve, exist_ok=True)


def make_zip(name, version, extra=None):
    with zipfile.ZipFile(os.path.join(serve, name), "w") as z:
        z.writestr("JKrender/JKrender.py", 'APP_VERSION = "%s"\nprint("new")\n' % version)
        z.writestr("JKrender/jkrender_worker.py", "# new worker\n")
        z.writestr("JKrender/docs/JKrender_manual_en.pdf", b"%PDF new")
        z.writestr("JKrender/lib/numpy.txt", "must not land")  # lib is the user's: never written
        z.writestr("JKrender/settings/settings.json", "{}")
        for k, v in (extra or {}).items():
            z.writestr(k, v)


make_zip("JKrender-9.9.9.zip", "9.9.9")
make_zip("JKrender-wrong.zip", "9.9.8")
open(os.path.join(serve, "JKrender-broken.zip"), "wb").write(b"PK\x03\x04 not a zip")


def latest(version, zip_name):
    json.dump({"tag_name": "v" + version, "html_url": "page",
               "body": "## %s\n- something new\n\n**Install:** unpack the zip into ~/.nuke and add a line to init.py" % version,
               "assets": [{"name": zip_name, "browser_download_url": URL + zip_name}]},
              open(os.path.join(serve, "latest.json"), "w"))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=serve, **k)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d/" % server.server_address[1]
os.environ["JKRENDER_UPDATE_URL"] = URL + "latest.json"


def fresh_install():
    shutil.rmtree(target, ignore_errors=True)
    for rel, text in (("JKrender.py", 'APP_VERSION = "%s"\nprint("old")\n' % jk.APP_VERSION), ("jkrender_worker.py", "# old\n"),
                      ("lib/numpy.txt", "the user's numpy"), ("settings/settings.json", '{"processes": 3}'), ("log/a.log", "a")):
        p = os.path.join(target, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(text)


def read(rel):
    return open(os.path.join(target, *rel.split("/"))).read()


say("The newest release")
latest("9.9.9", "JKrender-9.9.9.zip")
info = jk.latest_release()
check("found: version, zip, notes", info and info["version"] == "9.9.9" and info["zip"].endswith("JKrender-9.9.9.zip")
      and "something new" in info["notes"], info)
check("versions compare as numbers", jk.version_tuple("1.0.10") > jk.version_tuple("1.0.9") > jk.version_tuple("0.99.91"))

say("At start: a newer release brings up the update window; a skipped one or no newer one does not")
win = jk.MainWindow(make_job("sh010", 1001, 1004))
win.check_update()
check("the update window comes up", wait(lambda: (win._slow_tick() or True) and getattr(win, "update_dialog", None) is not None
                                         and win.update_dialog.isVisible(), 10))
d = win.update_dialog
check("its buttons: What's new, Update, Later", [d.b_new.text(), d.b_update.text(), d.b_later.text()] == ["What's new", "Update", "Later"])
d.b_new.click()
check("What's new shows the release notes", d.notes.isVisible() and "something new" in d.notes.toPlainText())
check("and only what is new: not the install steps of the release page", "init.py" not in d.notes.toPlainText()
      and "Install" not in d.notes.toPlainText(), d.notes.toPlainText())
d.skip.setChecked(True)
check("Don't show this again remembers the version", json.loads(jk.SETTINGS_FILE.read_text(encoding="utf-8")).get("update_skip") == "9.9.9")
d.reject()
win.update_dialog = None
win.check_update()
pump(2.0)
win._slow_tick()
check("skipped: not shown again", win.update_dialog is None)
d.skip.setChecked(False)
latest(jk.APP_VERSION, "JKrender-9.9.9.zip")
win.check_update()
pump(2.0)
win._slow_tick()
check("the same version: nothing", win.update_dialog is None)

say("Settings > Check for updates (1.0.1): asks now, opens the window also for a skipped version, says when it is the newest")
check("the button and what it shows now", win.b_update.text() == "Check for updates" and jk.APP_VERSION in win.update_status.text(),
      win.update_status.text())
win.update_dialog = None
win.b_update.click()
check("the same version: 'the newest'", wait(lambda: "is the newest" in win.update_status.text(), 10) and win.update_dialog is None,
      win.update_status.text())
latest("9.9.9", "JKrender-9.9.9.zip")
win.settings["update_skip"] = "9.9.9"
win.b_update.click()
check("a newer one skipped before: the window opens all the same", wait(lambda: win.update_dialog is not None
                                                                         and win.update_dialog.isVisible(), 10)
      and "9.9.9 is out" in win.update_status.text(), win.update_status.text())
win.update_dialog.reject()
os.environ["JKRENDER_UPDATE_URL"] = "http://127.0.0.1:%d/latest.json" % free_port()  # nothing listens there
win.b_update.click()
check("GitHub out of reach: it says so", wait(lambda: "Could not reach GitHub" in win.update_status.text(), 15), win.update_status.text())
os.environ["JKRENDER_UPDATE_URL"] = URL + "latest.json"

say("Update: refused while a render runs")
latest("9.9.9", "JKrender-9.9.9.zip")
fresh_install()
d = jk.UpdateDialog(win, jk.latest_release())
win.engine.state = "rendering"
d._update()
check("it says to stop the renders first, nothing changed", "Stop or finish the renders first" in d.text.text() and "old" in read("JKrender.py"))
win.engine.state = "ready"

say("Update: downloads and installs over the JKrender folder")
d._update()
check("done", wait(lambda: d.result_text != "", 30), d.text.text())
check("it says so, and to restart Nuke", "is installed" in d.result_text and "restart Nuke" in d.result_text, d.result_text)
check("the program files are the new ones", 'APP_VERSION = "9.9.9"' in read("JKrender.py") and "new worker" in read("jkrender_worker.py")
      and os.path.isfile(os.path.join(target, "docs", "JKrender_manual_en.pdf")))
check("lib, settings and log are untouched", read("lib/numpy.txt") == "the user's numpy" and "processes" in read("settings/settings.json")
      and read("log/a.log") == "a")
check("the old files are kept in update_backup/<old version>",
      "old" in read("update_backup/%s/JKrender.py" % jk.APP_VERSION), os.listdir(os.path.join(target, "update_backup")))

say("A broken zip, another version's zip, a git clone: nothing changes")
fresh_install()
ok, text = jk.install_update(os.path.join(serve, "JKrender-broken.zip"), "9.9.9")
check("broken: refused", not ok and "old" in read("JKrender.py"), text)
ok, text = jk.install_update(os.path.join(serve, "JKrender-wrong.zip"), "9.9.9")
check("another version: refused", not ok and "9.9.8" in text and "old" in read("JKrender.py"), text)
os.makedirs(os.path.join(target, ".git"))
ok, text = jk.install_update(os.path.join(serve, "JKrender-9.9.9.zip"), "9.9.9")
check("a git clone: git pull instead", not ok and "git pull" in text and "old" in read("JKrender.py"), text)
server.shutdown()
finish()
