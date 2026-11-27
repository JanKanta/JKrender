"""The pixel check looks at RGB only (1.0.26). A multi-layer EXR's other layers are the artist's: a depth of inf is not a
problem, SmartVector writes longer-distance layers into some frames only (by design, not a frame rendered another way), and
a render of utility layers alone has no RGB to judge ("no RGB", not an error). NaN in R, G or B still is one, in a single
part or in the beauty part of a multi-part EXR. A frame the file check found missing is not read for pixels, and frames
that cannot be read for the same reason, or differ the same way, make one line in the Log, not one each."""
from common import *

jk, app = start({"processes": 1})
try:
    import numpy as np
    import OpenEXR
except ImportError:
    say("No numpy / OpenEXR here: nothing to check")
    finish()

H, W = 24, 32
rng = np.random.default_rng(7)


def write_exr(path, beauty=True, layers=(), nan_in_rgb=False, size=(W, H), parts=False):
    w, h = size
    half = lambda: rng.random((h, w)).astype(np.float16)
    chans = {}
    if beauty:
        rgb = rng.random((h, w, 3)).astype(np.float16)
        if nan_in_rgb:
            rgb[3, 4, 1] = np.nan
        chans["RGB"] = rgb
    for name in layers:
        chans[name] = np.full((h, w), np.inf, np.float32) if name == "depth.Z" else half()
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    if parts:  # the beauty in a part of its own, the other layers in another, as a multi-part EXR
        beauty_part = OpenEXR.Part(dict(header), {"RGB": chans.pop("RGB")}, name="rgba")  # a header each: Part keeps it
        other = OpenEXR.Part(dict(header), chans, name="utility")
        OpenEXR.File([beauty_part, other]).write(path)
    else:
        OpenEXR.File(header, chans).write(path)
    return path


SV = ["smartvector_f01_v01.n_u", "smartvector_f01_v01.n_v", "smartvector_f01_v01.p_u", "smartvector_f01_v01.p_v"]
SV2 = [n.replace("f01", "f02") for n in SV]
SV4 = [n.replace("f01", "f04") for n in SV]

say("check_pixels alone")
d = RUN + "/single"
os.makedirs(d, exist_ok=True)
r = jk.check_pixels([write_exr(d + "/ml.exr", layers=["depth.Z", "N.X", "N.Y", "N.Z"] + SV)])
check("beauty and AOVs: a depth of inf in another layer is no inf", r.get("inf") == 0 and r.get("nan") == 0, r)
check("no channel list among what is compared", "channels" not in r.get("props", {}), r.get("props"))
r = jk.check_pixels([write_exr(d + "/nan.exr", layers=["depth.Z"], nan_in_rgb=True)])
check("NaN in G of the beauty: found", r.get("nan") == 1, r)
r = jk.check_pixels([write_exr(d + "/util.exr", beauty=False, layers=SV + SV2)])
check("utility layers only: no RGB, nothing judged", r.get("no_rgb") is True and r.get("hash") is None and not r.get("nan"), r)
r = jk.check_pixels([write_exr(d + "/mp.exr", layers=["depth.Z"], nan_in_rgb=True, parts=True)])
check("multi-part: the beauty part is found, its NaN too", r.get("nan") == 1 and not r.get("no_rgb"), r)


def engine_with(name, frames_spec, make):
    """A render of EXR frames already on disk, made by make(frame, path); its engine, every frame done."""
    job = make_job(name, 1001, 1001 + len(frames_spec) - 1)
    data = json.load(open(job, encoding="utf-8"))
    data["writes"][0]["output"] = data["writes"][0]["output"].replace(".png", ".exr")
    data["writes"][0]["file_type"] = "exr"
    json.dump(data, open(job, "w", encoding="utf-8"))
    for i, spec in enumerate(frames_spec):
        path = frame_path(job, 1001 + i)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if spec is not None:
            make(spec, path)
    win = jk.MainWindow(job)
    e = win.engine
    for f, fr in e.frames.items():
        fr.state = "done"
    return win, e


def run_pixels(e):
    e.pixels_pending = 0
    before = len(e.log)
    e.check_all_pixels()
    wait(lambda: e.pixels_pending == 0, 60)
    return [x[1] for x in list(e.log)[before:]]


say("SmartVector frames: longer-distance layers on some frames only, as it writes them: nothing differs")
pattern = [SV + SV2 + SV4, SV, SV + SV2, SV, SV + SV2 + SV4, SV]
win, e = engine_with("sv_beauty", pattern, lambda layers, p: write_exr(p, layers=layers))
notes = run_pixels(e)
check("no frame 'differs from the sequence'", not any("differ" in n for n in notes), notes)
check("the Log: pixels checked in 6 frames (RGB)", any("Pixels checked in 6 frames (RGB)" in n for n in notes), notes)

say("A render of SmartVector layers alone (Write channels all on SmartVector): no RGB, not an error")
win, e = engine_with("sv_only", pattern, lambda layers, p: write_exr(p, beauty=False, layers=layers))
notes = run_pixels(e)
check("one line: 6 frames hold no RGB", any("6 frame(s) hold no RGB" in n for n in notes), notes)
check("no error and no 'differs'", not any(n.startswith("!") for n in notes), notes)
win._update_tiles() if hasattr(win, "_update_tiles") else None
win._slow_tick()
check("the Check pixels tile says no RGB, not red", win.t_check_pixels.value.text() == "no RGB"
      and jk.C["failed"] not in win.t_check_pixels.value.styleSheet(), win.t_check_pixels.value.text())
check("the Output list says no RGB for a frame", win.frames_model.data(win.frames_model.index(0, 7)) == "no RGB",
      win.frames_model.data(win.frames_model.index(0, 7)))

say("Missing frames are not read for pixels; frames that cannot be read make one line")
garbage = lambda spec, p: open(p, "wb").write(b"not an exr at all" * 10)
win, e = engine_with("broken", ["ok", "ok", "bad", "bad", "bad", None],
                     lambda spec, p: write_exr(p) if spec == "ok" else garbage(spec, p))
e.frames[1006].state = "bad"
e.frames[1006].checks = {0: ("missing", None, "")}
notes = run_pixels(e)
check("the missing frame is left out", e.pixels_total == 5 and e.frames[1006].pixels is None, (e.pixels_total, e.frames[1006].pixels))
lines = [n for n in notes if "Pixels not checked" in n]
check("the three unreadable frames: one line, with their numbers", len(lines) == 1 and "3 frame(s) (1003-1005)" in lines[0], notes)

say("Frames that differ the same way: one line")
sizes = [(W, H), (W, H), (W, H), (W * 2, H), (W * 2, H), (W, H)]
win, e = engine_with("sizes", sizes, lambda size, p: write_exr(p, size=size))
notes = run_pixels(e)
lines = [n for n in notes if "differ" in n]
check("two frames of another size: one line naming both", len(lines) == 1 and "2 frames (1004-1005)" in lines[0], notes)
finish()
