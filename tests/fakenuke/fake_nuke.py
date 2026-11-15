"""A stand-in for Nuke in JKrender's tests on macOS and Linux, the same as FakeNuke.cs on Windows: speaks the jkrender_worker
protocol and writes PNG frames, so a render runs through JKrender as with Nuke, in seconds and without a licence.
run_tests.py starts it through a small launcher named like Nuke (build/Nuke16.0v9/Nuke16.0), with Nuke's Python.

It reads what the worker gets: -m threads, -c cache, JKRENDER_ARGS (script, original, Write names), JKRENDER_TEST_OUT.
Environment, for the tests:
  FAKE_OUT          output pattern with %04d (else <script folder>/render/<script name>.%04d.png)
  FAKE_SECS         seconds a frame takes (default 0.3)
  FAKE_LOAD_SECS    seconds opening the script takes (default 0.5)
  FAKE_CRASH_FRAME  the process exits without a word when it starts this frame, the first time only (a crash mid-chunk)
Only the standard library: no Qt, no numpy.
"""
import json
import os
import random
import struct
import sys
import threading
import time
import zlib

rnd = random.Random(os.getpid())
burn = threading.Event()


def say(text):
    sys.stdout.write("JKR " + text + "\n")
    sys.stdout.flush()


def env(name, fallback):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return fallback


def burner():
    x = 0.0
    while True:
        if burn.is_set():
            for i in range(20000):
                x += i ** 0.5
        else:
            time.sleep(0.02)


def frames(spec):
    out = []
    for part in spec.split(","):
        if not part:
            continue
        i = part.find("-", 1)
        if i > 0:
            out += range(int(part[:i]), int(part[i + 1:]) + 1)
        else:
            out.append(int(part))
    return out


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def draw(path, frame):
    """A 640x360 PNG: a gradient that moves with the frame, a bar as wide as the frame number, and grain, so no two frames
    are alike and the sizes are like a real sequence's."""
    w, h = 640, 360
    t = (frame % 100) / 100.0
    rows = []
    bar = 30 + (frame % 500)
    for y in range(h):
        k = y / (h - 1)
        r = int(20 + k * 180)
        g = int(30 + k * (50 + t * 150))
        b = int(60 - k * 0)
        row = bytearray([r, g, b]) * w
        if 30 <= y < 50:
            row[30 * 3:bar * 3] = b"\xff" * ((bar - 30) * 3)
        rows.append(b"\x00" + bytes(row))
    raw = bytearray(b"".join(rows))
    stride = w * 3 + 1
    for _ in range(3000):  # grain
        x, y = rnd.randrange(w), rnd.randrange(h)
        i = y * stride + 1 + x * 3
        raw[i:i + 3] = bytes((rnd.randrange(80), rnd.randrange(80), rnd.randrange(80)))
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"  # as Nuke: a temp file, renamed when whole
    with open(tmp, "wb") as f:
        f.write(png)
    os.replace(tmp, path)


def main(args):
    threads, cache = 4, 8
    for i in range(len(args) - 1):
        if args[i] == "-m":
            try:
                threads = int(args[i + 1])
            except ValueError:
                pass
        if args[i] == "-c":
            try:
                cache = int(args[i + 1].rstrip("GM"))
            except ValueError:
                pass
    pattern = os.environ.get("FAKE_OUT", "")
    if not pattern:
        try:
            script = json.loads(os.environ.get("JKRENDER_ARGS", ""))[0].replace("\\", "/")
            pattern = os.path.dirname(script) + "/render/" + os.path.splitext(os.path.basename(script))[0] + ".%04d.png"
        except Exception:
            pattern = ""
    test_out = os.environ.get("JKRENDER_TEST_OUT")
    if test_out and pattern:
        pattern = test_out + "/" + os.path.basename(pattern)
    secs, load = env("FAKE_SECS", 0.3), env("FAKE_LOAD_SECS", 0.5)
    crash_at = int(env("FAKE_CRASH_FRAME", -1))
    for _ in range(max(1, min(threads // 3, 4))):
        threading.Thread(target=burner, daemon=True).start()
    sys.stdout.write("Nuke 16.0v9 (stand-in for tests)\n")
    say("BOOT")
    time.sleep(load)
    ram = bytearray((200 + rnd.randrange(100)) * 1024 * 1024)
    for i in range(0, len(ram), 4096):
        ram[i] = 1
    say("READY %.2f" % load)
    for line in sys.stdin:
        c = line.split()
        if not c:
            continue
        if c[0] == "QUIT":
            break
        if c[0] == "RENDER":
            for f in frames(c[2]):
                say("START %d" % f)
                if f == crash_at and pattern:
                    mark = os.path.join(os.path.dirname(pattern), ".fake_crashed_%d" % f)
                    if not os.path.exists(mark):
                        open(mark, "w").close()
                        sys.stdout.write("Segmentation fault (stand-in)\n")
                        sys.stdout.flush()
                        os._exit(3)
                t = time.time()
                burn.set()
                want = secs * (0.8 + rnd.random() * 0.4)
                if pattern:
                    draw(pattern.replace("%04d", "%04d" % f), f)
                while time.time() - t < want:
                    time.sleep(0.02)
                burn.clear()
                say("FRAME %d %.3f" % (f, time.time() - t))
                say("MEM %d %d" % (int(cache * 1024 ** 3 * (0.3 + rnd.random() * 0.4)), cache * 1024 ** 3))
            say("CHUNK " + c[1])
    say("BYE")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
