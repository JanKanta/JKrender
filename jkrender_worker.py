"""JKrender worker. Runs inside Nuke:  Nuke -i -m <threads> -c <ram> -t jkrender_worker.py <script> <original> <writes>

It opens the script once and then renders whatever JKrender sends it on stdin, one line per command:
    RENDER <chunk> <frames>   frames as "1001-1010,1015"; every frame is its own execute
    RANGE <chunk> <first> <last>   one execute over the whole range (movie files, which cannot be split)
    QUIT
Everything it reports to JKrender starts with "JKR "; all other output is Nuke's own log.
"""

import json
import os
import sys
import time
import traceback

import nuke

FRAME_ATTEMPTS = 3  # a frame that throws is tried again: a locked output (sync client, viewer) frees up in seconds


def say(*parts):
    sys.stdout.write("JKR " + " ".join(str(p) for p in parts) + "\n")
    sys.stdout.flush()


def say_memory():
    """Nuke's cache in use and its limit (-c), in bytes; Nuke 16 has nuke.memory2."""
    try:
        say("MEM", nuke.memory2.usage(), nuke.memory2.maxUsage())
    except Exception:
        pass


def parse_frames(spec):
    frames = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:
            i = part.index("-", 1)
            a, b = int(part[:i]), int(part[i + 1:])
            frames.extend(range(a, b + 1) if b >= a else range(a, b - 1, -1))
        else:
            frames.append(int(part))
    return frames


def in_limit(node, frame):
    k = node.knob("use_limit")
    if k and k.value():
        return int(node["first"].value()) <= frame <= int(node["last"].value())
    return True


def execute(writes, first, last):
    if len(writes) == 1:
        nuke.execute(writes[0], first, last)
    else:
        nuke.executeMultiple(writes, ((first, last, 1),))


def render_frames(writes, chunk, frames):
    for frame in frames:
        active = [w for w in writes if in_limit(w, frame)]
        say("START", frame)
        started = time.time()
        for attempt in range(1, FRAME_ATTEMPTS + 1):
            try:
                if active:
                    execute(active, frame, frame)
                break
            except Exception as exc:
                if attempt == FRAME_ATTEMPTS:
                    traceback.print_exc()
                    say("FAIL", chunk, frame, str(exc).replace("\n", " ")[:400])
                    return
                print("WARNING: frame %d failed (%s), trying again (%d of %d)" % (frame, exc, attempt + 1, FRAME_ATTEMPTS))
                sys.stdout.flush()
                time.sleep(2 * attempt)
        say("FRAME", frame, "%.3f" % (time.time() - started))
        say_memory()
    say("CHUNK", chunk)


def render_range(writes, chunk, first, last):
    clock = {"t": time.time()}

    def after_frame():
        now = time.time()
        say("FRAME", nuke.frame(), "%.3f" % (now - clock["t"]))
        say_memory()
        clock["t"] = now

    for w in writes:
        nuke.addAfterFrameRender(after_frame, nodeClass=w.Class())
    say("START", first)
    try:
        execute(writes, first, last)
    except Exception as exc:
        traceback.print_exc()
        say("FAIL", chunk, first, str(exc).replace("\n", " ")[:400])
        return
    finally:
        for w in writes:
            try:
                nuke.removeAfterFrameRender(after_frame, nodeClass=w.Class())
            except Exception:
                pass
    say("CHUNK", chunk)


TEMPORAL = {"FrameHold", "FrameBlend", "FrameRange", "TimeOffset", "Retime", "TimeWarp", "TimeBlur", "OFlow2", "OFlow", "Kronos",
            "TimeEcho", "Tracker4", "VectorGenerator", "SmartVector", "VectorDistort", "Denoise2", "ParticleEmitter", "VectorBlur2"}
NOT_WORK = {"Dot", "Input", "Output", "Viewer", "BackdropNode", "StickyNote", "NoOp", "Root", "Write"}


def analyze(writes):
    """What feeds the Writes, for Settings > Test this script > Analyze script. Only reads the script.
    A disabled node does no work, but what is above it (through its first input) still counts."""
    seen, stack, classes, gpu, reads, big_blurs = set(), list(writes), {}, {}, [], 0
    while stack:
        n = stack.pop()
        if n is None or n.fullName() in seen:
            continue
        seen.add(n.fullName())
        disabled = bool(n.knob("disable") and n["disable"].value())
        cls = n.Class()
        if not disabled:
            if cls not in NOT_WORK:
                classes[cls] = classes.get(cls, 0) + 1
            if cls in ("Read", "DeepRead"):
                reads.append(n.width() * n.height())
            # a node runs on the GPU only if it has this knob, and it is on
            use_gpu = n.knob("useGPUIfAvailable")
            if use_gpu is not None and use_gpu.value():
                gpu[cls] = gpu.get(cls, 0) + 1
            if cls == "Blur":
                try:
                    big_blurs += float(n["size"].value()) >= 100
                except (TypeError, ValueError):
                    pass
            if cls == "Group":
                stack.extend(x for x in n.nodes() if x.Class() not in ("Input", "Output", "Dot"))
        if cls == "Input" and "." in n.fullName():
            # inside a Group (a Write in a publish Group): what feeds it comes in through the Group's input
            group = nuke.toNode(n.fullName().rsplit(".", 1)[0])
            stack.append(group.input(int(n["number"].value())) if group is not None else None)
        for i in range(n.inputs() if not disabled else min(1, n.inputs())):
            stack.append(n.input(i))
        try:
            if not disabled:
                stack.extend(n.dependencies(nuke.EXPRESSIONS))
        except Exception:
            pass
    return {"all": len(nuke.allNodes(recurseGroups=True)), "above": sum(classes.values()),
            "reads": len(reads), "largest_read": max(reads or [0]), "big_blurs": big_blurs,
            "gpu": gpu, "temporal": {k: v for k, v in classes.items() if k in TEMPORAL},
            "top": sorted(classes.items(), key=lambda kv: -kv[1])[:8]}


def main():
    script, original, names = (sys.argv[1:] + ["", "", ""])[:3]
    if os.environ.get("JKRENDER_ARGS"):
        # JKrender passes them as UTF-8 too: the command line comes in the Windows code page, which breaks
        # paths with Czech letters
        script, original, names = json.loads(os.environ["JKRENDER_ARGS"])
    opened = time.time()
    nuke.scriptOpen(script)
    if original:
        # The snapshot behaves as the original: [value root.name], relative paths, project settings.
        nuke.root()["name"].setValue(original)
    writes = []
    for name in names.split("|"):
        node = nuke.toNode(name)
        if node is None:
            raise RuntimeError("Write node not found: %s" % name)
        writes.append(node)
    if os.environ.get("JKRENDER_ANALYZE"):
        say("INFO", json.dumps(analyze(writes)))
        return 0
    test_out = os.environ.get("JKRENDER_TEST_OUT")
    if test_out:
        # Settings > Test this script: the frames go to its test folder, never over the render's own files.
        # A studio's render hooks must not run: a beforeRender hook can set the real path again just
        # before the render, and an afterRender hook can write files next to the delivery.
        paths = {}
        for node in writes:
            paths[node.fullName()] = test_out + "/" + os.path.basename(node["file"].value())
            node["file"].setValue(paths[node.fullName()])
            for hook in ("beforeRender", "beforeFrameRender", "afterFrameRender", "afterRender"):
                if node.knob(hook):
                    node[hook].setValue("")

        def keep_in_test():
            # added last, so it has the last word over callbacks a pipeline added at start-up
            node = nuke.thisNode()
            if node.fullName() in paths:
                node["file"].setValue(paths[node.fullName()])

        nuke.addBeforeRender(keep_in_test, nodeClass="Write")
    say("READY", "%.2f" % (time.time() - opened))
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        cmd = line.split()
        if not cmd:
            continue
        if cmd[0] == "QUIT":
            break
        if cmd[0] == "RENDER":
            render_frames(writes, cmd[1], parse_frames(cmd[2]))
        elif cmd[0] == "RANGE":
            render_range(writes, cmd[1], int(cmd[2]), int(cmd[3]))
    say("BYE")
    return 0


try:
    code = main()
except Exception as exc:
    traceback.print_exc()
    say("ERROR", str(exc).replace("\n", " ")[:400])
    code = 1
sys.stdout.flush()
sys.exit(code)
