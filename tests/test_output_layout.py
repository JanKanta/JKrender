"""Output in a low window: the preview, its player bar and the frame info never lie over each other (0.99.72: before, the
bar and the info were drawn over the picture once the pane was lower than the page needs; now the page scrolls)."""
from common import *
from PySide6 import QtCore

jk, app = start({"processes": 2, "threads": 2, "ram": 2})
job = make_job("sh010", 1001, 1006)
win = jk.MainWindow(job)
win.show()
e = win.engine
win._start()
check("rendered", wait(lambda: e.state == "done" and e.pending_checks <= 0, 120), e.state)
win._pick_frame(1003)
pump(1.0)


def rect(widget):
    r = widget.geometry()
    r.moveTo(widget.mapTo(win, QtCore.QPoint(0, 0)))
    return r


for size in ((1600, 900), (1000, 640), (800, 520)):
    win.resize(*size)
    pump(0.8)
    pic, bar, info = rect(win.preview), rect(win.frame_slider), rect(win.preview_info)
    check("%d x %d: player bar under the picture, not on it" % size, not pic.intersects(bar) and bar.top() >= pic.bottom(), (pic, bar))
    check("%d x %d: frame info under the bar" % size, not pic.intersects(info) and info.top() >= bar.bottom(), (bar, info))
finish()
