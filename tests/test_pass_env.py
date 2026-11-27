"""A render gets NUKE_PATH, PYTHONPATH and OCIO from the Nuke that sent it, and a pipeline's own variables once their name
prefix is in the pass_env setting (1.0.32). Never a key, token, secret or password in a job; JKrender's own variables of
those prefixes never reach a render of another session. Reset to defaults keeps pass_env (it has no switch)."""
import types
from common import *
from PySide6 import QtWidgets

jk, app = start({"pass_env": ["STUDIO_", "show_"], "processes": 1, "threads": 1, "end_sound": False})
sys.modules.setdefault("nuke", types.ModuleType("nuke"))   # the menu module needs nuke only once it is used
sys.path.insert(0, os.path.dirname(jk.__file__))
import JKrender_NukeIntegration as integ  # noqa: E402

say("The setting")
check("none by default", jk.DEFAULT_SETTINGS.get("pass_env") == [], jk.DEFAULT_SETTINGS.get("pass_env"))
check("a list, in any case", jk.env_prefixes(["studio_", " SHOW_ ", ""]) == ("STUDIO_", "SHOW_"),
      jk.env_prefixes(["studio_", " SHOW_ ", ""]))
check("one string, split by commas or spaces", jk.env_prefixes("STUDIO_, SHOW_") == ("STUDIO_", "SHOW_"),
      jk.env_prefixes("STUDIO_, SHOW_"))
check("nothing or junk: no prefixes", jk.env_prefixes(None) == jk.env_prefixes(5) == jk.env_prefixes([]) == ())

say("What the Nuke session puts into the job")
os.environ.update({"STUDIO_PROJECT": "alpha", "STUDIO_API_KEY": "k1", "STUDIO_SERVER_TOKEN": "t1", "SHOW_TASK": "comp",
                   "OTHER_THING": "x", "NUKE_PATH": "C:/pipe/nuke"})
sent = integ._session_env()
check("its search paths", sent.get("NUKE_PATH") == "C:/pipe/nuke" and "PYTHONPATH" in sent and "OCIO" in sent, sorted(sent))
check("the variables of both prefixes", (sent.get("STUDIO_PROJECT"), sent.get("SHOW_TASK")) == ("alpha", "comp"), sorted(sent))
check("no key, no token, nothing else", not {"STUDIO_API_KEY", "STUDIO_SERVER_TOKEN", "OTHER_THING"} & set(sent), sorted(sent))
os.replace(SETTINGS_DIR + "/settings.json", SETTINGS_DIR + "/settings.off")
check("no settings file: the search paths only", set(integ._session_env()) == {"NUKE_PATH", "PYTHONPATH", "OCIO"},
      sorted(integ._session_env()))
os.replace(SETTINGS_DIR + "/settings.off", SETTINGS_DIR + "/settings.json")

say("What the render gets")
os.environ.update({"STUDIO_PROJECT": "beta", "STUDIO_SHOT": "sh999"})  # JKrender itself opened by a Nuke of another shot
env = jk.nuke_render_env(sent, jk.env_prefixes(["STUDIO_", "SHOW_"]))
check("the sender's project", env.get("STUDIO_PROJECT") == "alpha", env.get("STUDIO_PROJECT"))
check("not the shot of the Nuke that opened JKrender", "STUDIO_SHOT" not in env, env.get("STUDIO_SHOT"))
check("the key stays: never in a job, still there for the render", env.get("STUDIO_API_KEY") == "k1", env.get("STUDIO_API_KEY"))
check("the sender's NUKE_PATH", env.get("NUKE_PATH") == "C:/pipe/nuke", env.get("NUKE_PATH"))
env = jk.nuke_render_env(sent, ())
check("no prefixes: JKrender's own ones stay as they are", env.get("STUDIO_SHOT") == "sh999", env.get("STUDIO_SHOT"))

say("A render with FakeNuke uses the setting")
seen = []
keep = jk.nuke_render_env
jk.nuke_render_env = lambda job_env, prefixes=(): (seen.append(prefixes), keep(job_env, prefixes))[1]
job = make_job("sh010", 1001, 1003, env={"STUDIO_PROJECT": "alpha"})
win = jk.MainWindow(job)
e = win.manager.engines[0]
win._start()
check("rendered", wait(lambda: e.state == "done" and e.pending_checks <= 0, 90), e.state)
check("each Nuke started with the prefixes of the setting", seen and all(p == ("STUDIO_", "SHOW_") for p in seen), seen)
jk.nuke_render_env = keep

say("Reset to defaults keeps it")
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
win._reset_settings()
check("pass_env as it was", jk.read_settings().get("pass_env") == ["STUDIO_", "show_"], jk.read_settings().get("pass_env"))
check("the rest back to the defaults (Sound at the end on again)", jk.read_settings().get("end_sound") is True,
      jk.read_settings().get("end_sound"))
win.close()
finish()
