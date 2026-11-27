"""The preview of an EXR shows its format (display window), not its bbox (data window): a bbox smaller than the format
sits in its place on black, an overscan bbox is cut to the format (0.99.75: before, the bbox filled the preview). 0.99.76: the bbox alone again,
when Show the whole format is off in the preview's right-click menu."""
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

say("Show the whole format off (right click on the preview): the bbox alone fills it, for a close look")
img = jk.preview_image(os.path.join(d, "small_bbox.exr"), 640, 360, on_format=False)
check("the bbox's size", (img.width(), img.height()) == (200, 100), (img.width(), img.height()))
check("all of it the picture", white(img, 0, 0) and white(img, 199, 99))
check("on by default", jk.DEFAULT_SETTINGS.get("preview_format") is True)

say("The right-click menu on the preview turns it off and remembers it")
from PySide6 import QtCore
win = jk.MainWindow(make_job("sh010", 1001, 1002))


from PySide6 import QtWidgets
win.show()
tries = [0]


def untick():
    menus = [m for m in app.topLevelWidgets() if isinstance(m, QtWidgets.QMenu) and m.isVisible()]
    tries[0] += 1
    if not menus:
        if tries[0] < 100:
            QtCore.QTimer.singleShot(30, untick)
        return
    act = next((x for x in menus[0].actions() if x.text() == "Show the whole format"), None)
    if act is not None:
        act.trigger()
    menus[0].close()


QtCore.QTimer.singleShot(30, untick)
win._preview_menu(QtCore.QPoint(10, 10))
check("off after the click", win.settings.get("preview_format") is False, win.settings.get("preview_format"))
check("and saved", json.loads(jk.SETTINGS_FILE.read_text(encoding="utf-8")).get("preview_format") is False)

say("Overscan: a bbox larger than the format")
img = jk.preview_image(write("overscan.exr", ((-40, -20), (679, 379))), 640, 360)
check("cut to the format", (img.width(), img.height()) == (640, 360), (img.width(), img.height()))
check("all of the format shows the picture", white(img, 0, 0) and white(img, 639, 359))

say("The bbox is the format")
img = jk.preview_image(write("full.exr", ((0, 0), (639, 359))), 640, 360)
check("as before", (img.width(), img.height()) == (640, 360) and white(img, 320, 180))
finish()
