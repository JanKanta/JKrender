# The Czech manual (docs/JKrender_navod.pdf)

`navod.html` is the source; Chrome prints it to PDF. The screenshots come from `shots.py`: JKrender offscreen with its own home,
and `FakeNuke.cs` (build with `csc -r:System.Drawing.dll FakeNuke.cs`) standing in for Nuke, so no licence is taken and no
real script or frame is touched. It draws PNG frames and speaks the worker protocol.

1. `subst J: <a scratch folder>` so the paths read like a project, then run `shots.py` with Nuke's python
   (`QT_QPA_PLATFORM=offscreen`, `QT_QPA_FONTDIR=C:\Windows\Fonts`, or the text comes out as boxes).
2. `crops.py` cuts the panels out of the window shots; `tojpg.py` writes `navod_pdf.html` with JPEGs for the big ones.
3. `chrome --headless=new --no-pdf-header-footer --print-to-pdf=JKrender_navod.pdf navod_pdf.html`
