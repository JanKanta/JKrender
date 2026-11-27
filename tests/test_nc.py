"""Nuke Non-commercial and Indie (0.99.57): a render sent from them carries nuke_mode, and every worker Nuke starts in the
same mode (--nc / --indie, right after the program), or it asks for a licence the machine may not have. The menu in Nuke
takes their scripts (.nknc, .nkind) as saved."""
import re
from common import *

jk, app = start({"processes": 2, "threads": 2, "ram": 2})
src = open(os.path.join(os.path.dirname(jk.__file__), "JKrender_NukeIntegration.py"), encoding="utf-8").read()
exts = re.search(r"SCRIPT_EXTS = \(([^)]*)\)", src)
check("the menu takes .nk, .nknc and .nkind as a saved script",
      exts is not None and all('"%s"' % x in exts.group(1) for x in (".nk", ".nknc", ".nkind")), exts and exts.group(1))
check("the job says the mode", '"nuke_mode": _nuke_mode()' in src)

for mode, flag in (("nc", ["--nc"]), ("indie", ["--indie"]), ("", [])):
    path = make_job("mode_" + (mode or "full"), 1001, 1004)
    job = json.load(open(path, encoding="utf-8"))
    if mode:
        job["nuke_mode"] = mode
        job["script"] = job["script"][:-3] + (".nknc" if mode == "nc" else ".nkind")
    json.dump(job, open(path, "w", encoding="utf-8"))
    win = jk.MainWindow(path)
    cmd = win.engine._command(gpu=True)
    check("%s: the worker Nuke starts with %s" % (mode or "commercial Nuke", flag or "no mode flag"),
          cmd[1:1 + len(flag)] == flag and "-i" in cmd and not ({"--nc", "--indie"} - set(flag)) & set(cmd), cmd[:4])
    check("%s: it opens the script it was sent" % (mode or "commercial Nuke"), job["script"] in cmd, cmd[-3:])
    win.close()
finish()
