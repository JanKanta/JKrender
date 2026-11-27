"""Blank and frozen frames in a movie (1.0.13), as the pixel check finds them in a sequence: ffmpeg makes small movies here
(H.264 and ProRes) with a moving picture, black frames, a one-colour frame and frames held from the one before; the check
names them by frame number (a still shot whose grain moves is not frozen) in the Log and in the movie's Check table, as errors (1.0.14: red, as in a sequence), yet the
movie still counts as rendered (rendering it again gives the same frames); a movie without them is ok; a result is dropped once the file on disk changes. Without ffmpeg the test says so and checks nothing."""
import subprocess
from common import *

jk, app = start({"processes": 1, "threads": 2, "ram": 2})
ffmpeg = jk.find_tool("ffmpeg")
if not ffmpeg:
    say("No ffmpeg on this machine: nothing to check")
    finish()


def make_movie(path, codec, faults=True):
    """24 frames of 640x360 at 24 fps, frames 1001-1024. With faults: black 1006-1008 (indexes 5-7), the same as the one
    before 1013-1016 (12-15 hold 11), one colour 1021 (20)."""
    chain = "[0]split[a][b];[a][b]freezeframes=first=12:last=15:replace=11[f];" \
            "[f]drawbox=t=fill:c=black:enable='between(n,5,7)',drawbox=t=fill:c=red:enable='eq(n,20)'" if faults else "[0]null"
    args = {"h264": ["-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p"],
            "prores": ["-c:v", "prores_ks", "-profile:v", "2"]}[codec]
    r = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=1",
                        "-filter_complex", chain] + args + [path], capture_output=True, text=True)
    check("ffmpeg made %s" % os.path.basename(path), r.returncode == 0 and os.path.isfile(path), r.stderr[-300:])


say("The decoder alone: indexes from 0")
for codec in ("h264", "prores"):
    p = RUN + "/faults_%s.mov" % codec
    make_movie(p, codec)
    got = jk.movie_pixels(p, 640, 360)
    check("%s: 24 frames read" % codec, got.get("count") == 24, got)
    check("%s: black 5-7, one colour 20" % codec, got.get("blank") == {"black": [5, 6, 7], "one colour": [20]}, got.get("blank"))
    check("%s: 12-15 the same as the one before" % codec, got.get("same") == [12, 13, 14, 15], got.get("same"))
    p = RUN + "/clean_%s.mov" % codec
    make_movie(p, codec, faults=False)
    got = jk.movie_pixels(p, 640, 360)
    check("%s: a moving picture has none" % codec, got.get("count") == 24 and not got.get("blank") and not got.get("same"), got)
say("A still shot with grain (1.0.15: a slow UHD ProRes read as frozen): only the grain moves, by a few levels")
for codec in ("prores",):  # not H.264: it drops grain this fine, its still is held in the file too
    p = RUN + "/grain_%s.mov" % codec
    chain = "[0]trim=end_frame=1,loop=loop=23:size=1:start=0,setpts=N/24/TB,noise=alls=3:allf=t+u,split[a][b];"             "[a][b]freezeframes=first=12:last=15:replace=11"
    args = {"h264": ["-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p"], "prores": ["-c:v", "prores_ks", "-profile:v", "2"]}
    r = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=1", "-filter_complex",
                        chain] + args[codec] + [p], capture_output=True, text=True)
    check("ffmpeg made %s" % os.path.basename(p), r.returncode == 0, r.stderr[-300:])
    got = jk.movie_pixels(p, 640, 360)
    check("%s: only the held frames 12-15, not the grainy still around them" % codec, got.get("same") == [12, 13, 14, 15],
          got.get("same"))
check("a file that is no movie: an error, not a crash", "error" in jk.movie_pixels(RUN + "/faults_h264.mov.missing", 640, 360))

say("A movie render: the check after it, by frame number, in the Log and the Check table")
job = make_job("mov_px", 1001, 1024, movie=True)
out = frame_path(job, 0)
make_movie(out, "prores")
win = jk.MainWindow(job)
e = win.engine
e.probe()
e.pixels_pending = 0
e.check_all_pixels()
check("it runs", wait(lambda: e.pixels_pending == 0, 60) and e.movie_pixel_result() is not None, e.pixels_pending)
found = e.movie_pixel_result()
check("black frames 1006-1008 and one colour 1021", found["blank"] == {"black": [1006, 1007, 1008], "one colour": [1021]},
      found["blank"])
check("frozen 1013-1016", found["same"] == [1013, 1014, 1015, 1016], found["same"])
rows = {what: (state, text) for what, state, text in jk.movie_checks(e.movie_info, e.movie_expected(), found)}
check("Check table: Black frames, One-colour frames and Frozen frames, as errors (1.0.14)",
      rows.get("Black frames") == ("bad", "3: 1006-1008") and rows.get("One-colour frames") == ("bad", "1: 1021")
      and rows.get("Frozen frames") == ("bad", "4 the same as the one before: 1013-1016"), rows)
check("the movie's verdict: broken (red)", e.movie_verdict() == "bad", e.movie_verdict())
check("the file itself is fine: it is not rendered again for it", e.movie_verdict(pixels=False) == "ok"
      and e._scan_movie() == 24, e.movie_verdict(pixels=False))
e.state, e.started, e.ended = "done", time.time() - 10, time.time()
win.engine = e
win._slow_tick()
check("the Check video tile is red: broken", win.t_check_frames.value.text() == "broken"
      and jk.C["failed"] in win.t_check_frames.value.styleSheet(), win.t_check_frames.value.text())
win._state_changed()
check("the banner is red and names them", "1013-1016" in win.banner.text() and jk.C["failed"] in win.banner.styleSheet(),
      win.banner.text())
text = "\n".join(entry[1] for entry in e.log)
check("the Log names them", "1006-1008" in text and "1013-1016" in text and "Fine if it is meant to hold" in text, text[-600:])
win._update_movie()
html = win.movie_view.toPlainText()
check("the movie's Output page lists them", "Frozen frames" in html and "1013-1016" in html, html[-400:])

say("The file changes: the result is dropped; a movie without faults is ok")
make_movie(out, "prores", faults=False)
os.utime(out, (time.time() + 5, time.time() + 5))
e.probe()
check("a new file: no old result", e.movie_pixel_result() is None)
e.check_all_pixels()
wait(lambda: e.pixels_pending == 0, 60)
found = e.movie_pixel_result()
check("clean: nothing found, verdict ok", found and not found["blank"] and not found["same"] and e.movie_verdict() == "ok",
      (found, e.movie_verdict()))
finish()
