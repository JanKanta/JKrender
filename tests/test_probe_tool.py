"""ffprobe that cannot run (1.0.24): Windows' Smart App Control or an antivirus blocks it, or a DLL of it is missing. That
says nothing about the movie: it is "not checked", as without ffprobe, never "broken" (a broken movie renders again and
turns the render red). A movie ffprobe can read but finds broken stays broken."""
import subprocess
from common import *

jk, app = start({"processes": 1})
job = make_job("mov_tool", 1001, 1010, movie=True)
out = frame_path(job, 0)
with open(out, "wb") as f:
    f.write(b"not a movie at all" * 100)
real_find = jk.find_tool

say("ffprobe blocked: starting it fails")
blocked = RUN + "/blocked/ffprobe" + (".exe" if sys.platform.startswith("win") else "")
os.makedirs(os.path.dirname(blocked), exist_ok=True)
with open(blocked, "w") as f:
    f.write("this is no program\n")  # Windows: not a valid application; elsewhere: no exec bit
jk.find_tool = lambda name: blocked if name == "ffprobe" else real_find(name)
info = jk.probe_movie(out)
check("not an error of the movie", not info.get("error"), info)
check("not checked, and says why", info.get("no_probe") and "could not run" in (info.get("probe_error") or ""), info)
rows = {what: (state, text) for what, state, text in jk.movie_checks(info, {"frames": 10})}
check("the Check table: File ok, Details says ffprobe could not run",
      rows.get("File", ("",))[0] == "ok" and rows.get("Details", ("",))[0] == "warn" and "could not run" in rows["Details"][1], rows)

say("ffprobe starts but dies at once (a DLL missing)")
if sys.platform.startswith("win"):
    dead = RUN + "/dead/ffprobe.cmd"
    os.makedirs(os.path.dirname(dead), exist_ok=True)
    with open(dead, "w") as f:
        f.write("@exit /b -1073741515\n")  # 0xC0000135, STATUS_DLL_NOT_FOUND
else:
    dead = RUN + "/dead/ffprobe"
    os.makedirs(os.path.dirname(dead), exist_ok=True)
    with open(dead, "w") as f:
        f.write("#!/bin/sh\nexit 127\n")
    os.chmod(dead, 0o755)
jk.find_tool = lambda name: dead if name == "ffprobe" else real_find(name)
info = jk.probe_movie(out)
check("not an error of the movie either", not info.get("error") and info.get("no_probe"), info)
check("the exit code is named", "exit code" in (info.get("probe_error") or ""), info)

jk.find_tool = real_find
if real_find("ffprobe"):
    say("The real ffprobe on a file that is no movie: broken, as before")
    info = jk.probe_movie(out)
    check("an error of the movie", bool(info.get("error")) and not info.get("no_probe"), info)
    check("the Check table says File: broken", jk.movie_checks(info, {})[0][1] == "bad", jk.movie_checks(info, {}))
else:
    say("No ffprobe on this machine: the last part is skipped")
finish()
