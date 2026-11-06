import os, sys
from PySide6 import QtGui, QtWidgets, QtCore
app = QtWidgets.QApplication([])
S = "shots/"
def crop(src, name, x0, y0, x1, y1):
    img = QtGui.QImage(S + src + ".png")
    img.copy(QtCore.QRect(x0, y0, x1 - x0, y1 - y0)).save(S + name + ".png")
    print(name, img.width(), img.height())
crop("03_rendering_workers", "c_tiles", 0, 205, 3300, 322)
crop("10_problems", "c_map", 0, 322, 3300, 418)
crop("03_rendering_workers", "c_output", 14, 422, 1240, 1160)
crop("03_rendering_workers", "c_workers", 1238, 422, 3290, 1086)
crop("03_rendering_workers", "c_system", 1238, 1086, 3290, 1840)
crop("05_chunks", "c_chunks", 14, 422, 1240, 1840)
crop("06_details", "c_details", 14, 422, 1240, 1840)
crop("04_log", "c_log", 1238, 422, 3290, 1086)
crop("10_problems", "c_problems", 0, 150, 3300, 1240)
