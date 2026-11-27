"""A Write whose path has no frame number (sss.exr instead of sss.####.exr) would write every frame over one file: the menu
in Nuke refuses it with a message (0.99.58). A movie, a single frame and every way Nuke writes a number pass."""
import types
from common import *

jk, app = start({})
sys.modules.setdefault("nuke", types.ModuleType("nuke"))   # the menu module needs nuke only once it is used
sys.path.insert(0, os.path.dirname(jk.__file__))
import JKrender_NukeIntegration as integ  # noqa: E402

nf = integ.no_frame_number
for path, ftype, first, last, want in (
        ("C:/shots/sh020/render/sss.exr", "exr", 1000, 1701, True),
        ("C:/shots/sh010/render/comp.exr", "", 1001, 1100, True),
        ("C:/shots/sh010/render/comp.png", "png", 1, 2, True),
        ("C:/shots/sh010/render/comp.####.exr", "exr", 1001, 1100, False),
        ("C:/shots/sh010/render/comp.%04d.exr", "exr", 1001, 1100, False),
        ("C:/shots/sh010/render/comp_%d.dpx", "dpx", 1001, 1100, False),
        ("C:/shots/sh010/render/comp.$F4.exr", "exr", 1001, 1100, False),
        ("C:/shots/sh010/render/comp.[frame].exr", "exr", 1001, 1100, False),
        ("C:/shots/sh010/render/still.exr", "exr", 1001, 1001, False),
        ("C:/shots/sh010/render/comp_v001.mov", "mov", 1001, 1100, False),
        ("C:/shots/sh010/render/comp_v001.mp4", "", 1001, 1100, False),
        ("C:/shots/####/render/comp.exr", "exr", 1001, 1100, True)):   # a number in a folder is not the frame's
    check("%s, %d-%d: %s" % (os.path.basename(path), first, last, "refused" if want else "renders"),
          nf(path, ftype, first, last) == want, nf(path, ftype, first, last))
finish()
