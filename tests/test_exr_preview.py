"""The preview of an EXR shows its format (display window), not its bbox (data window): a bbox smaller than the format
sits in its place on black, an overscan bbox is cut to the format (0.99.75: before, the bbox filled the preview)."""
from common import *

jk, app = start({"processes": 1})
import numpy as np
import OpenEXR

d = os.path.join(RUN, "exr_preview")
os.makedirs(d, exist_ok=True)


def write(name, data_window, fmt=(640, 360)):
    (x0, y0), (x1, y1) = data_window
    w, h = x1 - x0 + 1, y1 - y0 + 1
    rgb = np.ones((h, w, 3), dtype=np.float32)  # white inside the bbox
    header = {"dataWindow": ((x0, y0), (x1, y1)), "displayWindow": ((0, 0), (fmt[0] - 1, fmt[1] - 1)),
              "compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    path = os.path.join(d, name)
    with OpenEXR.File(header, {"RGB": rgb}) as f:
        f.write(path)
    return path


def white(img, x, y):
    return QtGui.QColor(img.pixel(x, y)).value() > 200


from PySide6 import QtGui

say("A bbox 200 x 100 at 300, 150 in a 640 x 360 format")
img = jk.preview_image(write("small_bbox.exr", ((300, 150), (499, 249))), 640, 360)
check("the preview has the format's size", (img.width(), img.height()) == (640, 360), (img.width(), img.height()))
check("inside the bbox: the picture", white(img, 400, 200))
check("outside the bbox: black, not the bbox stretched over it", not white(img, 20, 20) and not white(img, 620, 340))

say("Overscan: a bbox larger than the format")
img = jk.preview_image(write("overscan.exr", ((-40, -20), (679, 379))), 640, 360)
check("cut to the format", (img.width(), img.height()) == (640, 360), (img.width(), img.height()))
check("all of the format shows the picture", white(img, 0, 0) and white(img, 639, 359))

say("The bbox is the format")
img = jk.preview_image(write("full.exr", ((0, 0), (639, 359))), 640, 360)
check("as before", (img.width(), img.height()) == (640, 360) and white(img, 320, 180))
finish()
