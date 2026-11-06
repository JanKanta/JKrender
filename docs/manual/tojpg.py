import os, re
from PySide6 import QtGui, QtWidgets
app = QtWidgets.QApplication([])
html = open("navod.html", encoding="utf-8").read()
for name in sorted(set(re.findall(r'shots/([\w]+)\.png', html))):
    img = QtGui.QImage("shots/%s.png" % name)
    if img.width() >= 900:
        img.save("shots/%s.jpg" % name, "JPG", 88)
        html = html.replace("shots/%s.png" % name, "shots/%s.jpg" % name)
    print(name, img.width(), img.height())
open("navod_pdf.html", "w", encoding="utf-8").write(html)
