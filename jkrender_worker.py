"""JKrender worker. Runs inside Nuke:  Nuke -i -m <threads> -c <ram> -t jkrender_worker.py <script> <original> <writes>

It opens the script once and then renders whatever JKrender sends it on stdin, one line per command:
    RENDER <chunk> <frames>   frames as "1001-1010,1015"; every frame is its own execute
    RANGE <chunk> <first> <last>   one execute over the whole range (movie files, which cannot be split)
    AFTER   the render is through: the Writes' afterRender hooks, once for the whole render (see RenderHooks)
    QUIT
Everything it reports to JKrender starts with "JKR "; all other output is Nuke's own log.
"""

import contextlib
import json
import os
import sys
import time
import traceback
import types

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


def output_states(writes, frame):
    """The file of each Write for this frame as it is on disk now: {path: (size, modified) or None when it is not there}.
    A Write whose path holds the view (%V) is left out, and one whose path cannot be told: about those nothing is said."""
    out = {}
    try:
        nuke.frame(frame)
    except Exception:
        return out
    for w in writes:
        try:
            raw = w["file"].value() or ""
            path = w["file"].evaluate() if "%v" not in raw.lower() else None
        except Exception:
            path = None
        if not path:
            continue
        try:
            st = os.stat(path)
            out[path] = (st.st_size, st.st_mtime)
        except OSError:
            out[path] = None
    return out


def wrote_nothing(before, after):
    """True when a Write's file for the frame is still not there, or not touched, after a try that gave no error."""
    return any(state is None or state == before.get(path) for path, state in after.items())


def execute(writes, first, last):
    if len(writes) == 1:
        nuke.execute(writes[0], first, last)
    else:
        nuke.executeMultiple(writes, ((first, last, 1),))


# Nuke's registry of the render callbacks a pipeline adds (nuke.addBeforeRender, addAfterRender): nuke_internal.callbacks
# in Nuke 16 and later, nuke.callbacks before
CALLBACKS = sys.modules.get(getattr(nuke.addAfterRender, "__module__", None) or "")


# The Writes' own beforeFrameRender / afterFrameRender knobs are timed (0.99.85): their code gets a line before and after
# that call this module, which any namespace can import, and the worker reports the times: JKR FRAMEHOOKS.
HOOK_TIMER = types.ModuleType("jkrender_hooktime")
HOOK_TIMER.started = 0.0
HOOK_TIMER.times = []  # (kind, frame, seconds) since the worker last reported them


def _hook_start():
    HOOK_TIMER.started = time.time()


def _hook_end(kind):
    try:
        frame = int(nuke.frame())
    except Exception:
        frame = None
    HOOK_TIMER.times.append((kind, frame, time.time() - HOOK_TIMER.started))


HOOK_TIMER.start, HOOK_TIMER.end = _hook_start, _hook_end
sys.modules["jkrender_hooktime"] = HOOK_TIMER


def report_frame_hooks(frame=None):
    """JKR FRAMEHOOKS <frame> <kind>=<seconds>...: the per-frame hook knobs timed since the last report; frame, when
    given, is the one the execute rendered."""
    by = {}
    for kind, f, secs in HOOK_TIMER.times:
        f = frame if frame is not None else f
        if f is not None:
            by.setdefault(f, {})[kind] = by.get(f, {}).get(kind, 0.0) + secs
    del HOOK_TIMER.times[:]
    for f in sorted(by):
        say("FRAMEHOOKS", f, *["%s=%.3f" % (k, v) for k, v in sorted(by[f].items())])


def same_node(a, b):
    if a is b:
        return True
    try:
        return a.fullName() == b.fullName()
    except Exception:
        return False


class AsThisNode(object):
    """nuke.thisNode() and thisClass() give the Write, as while Nuke runs its afterRender hooks. A render a hook starts
    (a movie of the frames, say) still gets its own node: Nuke sets that one."""

    def __init__(self, write):
        self.write = write
        found = (nuke, getattr(CALLBACKS, "nuke", None), sys.modules.get("nuke_internal"))
        self.modules = list({id(m): m for m in found if m is not None and hasattr(m, "thisNode")}.values())
        self.saved = []

    def _node(self, real):
        try:
            node = real()
            return self.write if node is None or node.Class() == "Root" else node
        except Exception:
            return self.write

    def _class(self, real):
        try:
            cls = real()
            return self.write.Class() if not cls or cls == "Root" else cls
        except Exception:
            return self.write.Class()

    def __enter__(self):
        self.saved = [(m, m.thisNode, getattr(m, "thisClass", None)) for m in self.modules]
        for m, real_node, real_class in self.saved:
            m.thisNode = lambda real=real_node: self._node(real)
            if real_class is not None:
                m.thisClass = lambda real=real_class: self._class(real)
        return self

    def __exit__(self, *exc):
        for m, real_node, real_class in self.saved:
            m.thisNode = real_node
            if real_class is not None:
                m.thisClass = real_class
        return False


class RenderHooks(object):
    """The Writes' render hooks run as when one Nuke renders the whole range, whatever the pipeline. This worker renders
    frame by frame, and Nuke runs beforeRender and afterRender (a Write's knobs, and what a pipeline adds with
    nuke.addBeforeRender / addAfterRender) around every execute: before 0.99.81 around every frame, in every process, so
    a hook that writes a file next to the delivery or publishes the render did it dozens of times. Now:

    - beforeRender runs once in each Nuke, before the first frame it renders of the Write: each Nuke needs what it sets
      up (the Write's path, say). Again after a frame that failed. The worker runs it itself, just before that execute,
      and says how many ran and how long they took (JKR BEFORE, 0.99.85); Nuke's execute finds them empty.
    - afterRender waits for AFTER, which JKrender sends once the whole render is through, to the Nuke that rendered the
      last frames: there it runs once, with nuke.thisNode() the Write.
    - beforeFrameRender and afterFrameRender run for every frame, as in Nuke.

    Test this script holds afterRender too, and never gets AFTER."""

    def __init__(self, writes, test=False):
        self.writes = writes
        self.test = test
        self.before_code = {}  # a Write's beforeRender knob, emptied: the worker runs it (see before)
        self.after_code = {}  # a Write's afterRender knob, emptied: it runs at AFTER
        self.before_calls = {}  # the pipeline's beforeRender callbacks, out of Nuke's registry: the worker runs them
        self.after_calls = {}  # its afterRender callbacks, out of the registry until AFTER
        self.before_done = set()  # the Writes that have rendered in this Nuke
        self.reported = None
        for w in writes:  # time the Write's own per-frame hooks (see HOOK_TIMER)
            for kind in ("beforeFrameRender", "afterFrameRender"):
                knob = w.knob(kind)
                if knob is not None and knob.value().strip():
                    knob.setValue("__import__('jkrender_hooktime').start()\n%s\n__import__('jkrender_hooktime').end(%r)\n"
                                  % (knob.value(), kind))
        self.watch(True)

    @staticmethod
    def registry(name):
        found = getattr(CALLBACKS, name, None)
        return found if isinstance(found, dict) else {}

    def watch(self, on):
        """Hold the afterRender hooks now, and again before every frame: a beforeRender hook may add one, which Nuke
        would run at the end of that very execute."""
        try:
            if on:
                self.hold_before()
                self.hold_after()
                nuke.addBeforeFrameRender(self.hold_after, nodeClass="*")
            else:
                nuke.removeBeforeFrameRender(self.hold_after, nodeClass="*")
        except Exception:
            traceback.print_exc()

    def hold_before(self):
        for w in self.writes:
            knob = w.knob("beforeRender")
            if knob is not None and knob.value().strip():
                self.before_code[w.fullName()] = knob.value()
                knob.setValue("")
        for key, calls in self.registry("beforeRenders").items():
            held = self.before_calls.setdefault(key, [])
            held.extend(c for c in calls if c not in held)
            del calls[:]

    def hold_after(self):
        for w in self.writes:
            knob = w.knob("afterRender")
            if knob is not None and knob.value().strip():
                self.after_code[w.fullName()] = knob.value()
                knob.setValue("")
        for key, calls in self.registry("afterRenders").items():
            held = self.after_calls.setdefault(key, [])
            held.extend(c for c in calls if c not in held)
            del calls[:]

    @staticmethod
    def calls_for(w, registry):
        """The callbacks Nuke runs for this Write: for its class or every class, for any node or this one."""
        return [c for key in (w.Class(), "*") for c in registry.get(key, []) if c[3] is None or same_node(c[3], w)]

    def count(self):
        """How many afterRender hooks AFTER runs."""
        if self.test:
            return 0
        return sum((w.fullName() in self.after_code) + len(self.calls_for(w, self.after_calls)) for w in self.writes)

    def report(self):
        count = self.count()
        if count != self.reported:
            self.reported = count
            say("HOOKS", count)

    def before(self, active):
        """Before an execute: the beforeRender hooks of the Writes that have not rendered in this Nuke yet, run here
        rather than inside Nuke's execute, so JKrender can show them: JKR BEFORE <how many> <seconds>. One that raises
        fails the frame, as in Nuke, and they run again with its next try. Returns the names of those Writes."""
        self.hold_before()
        fresh = [w for w in active if w.fullName() not in self.before_done]
        started, ran = time.time(), 0
        namespace = sys.modules["__main__"].__dict__  # where Nuke runs a knob's Python
        for w in fresh:
            calls = [("the beforeRender knob of %s" % w.name(), exec, (self.before_code[w.fullName()], namespace), {})
                     ] if w.fullName() in self.before_code else []
            calls += [("%s for %s" % (getattr(c[0], "__name__", "a callback"), w.name()), c[0], c[1], c[2])
                      for c in self.calls_for(w, self.before_calls)]
            with AsThisNode(w):
                for what, call, args, kwargs in calls:
                    try:
                        call(*args, **kwargs)
                    except Exception as exc:
                        raise RuntimeError("%s: %s" % (what, " ".join(str(exc).split())[:300]))
                    ran += not getattr(call, "jkrender_own", False)  # Test this script's own guard is no hook of the script
        if ran:
            say("BEFORE", ran, "%.2f" % (time.time() - started))
        return {w.fullName() for w in fresh}

    @contextlib.contextmanager
    def around(self, active):
        """One execute: a Write that rendered has had its beforeRender in this Nuke; one that failed has it again."""
        fresh = self.before(active)
        ok = False
        try:
            yield
            ok = True
        finally:
            if ok:
                self.before_done |= fresh
            self.hold_after()
            self.report()

    def run_after(self):
        """AFTER: each Write's held afterRender hooks, once, as Nuke runs them after a render; the callbacks are back in
        Nuke's registry meanwhile, so a render a hook starts runs them for its own node. Returns (ran, errors)."""
        self.watch(False)
        for name, held in (("beforeRenders", self.before_calls), ("afterRenders", self.after_calls)):
            registry = self.registry(name)
            for key, calls in held.items():
                registry.setdefault(key, [])[:0] = calls
            held.clear()
        for w in self.writes:
            if w.fullName() in self.before_code:
                w["beforeRender"].setValue(self.before_code[w.fullName()])
        namespace = sys.modules["__main__"].__dict__  # where Nuke runs a knob's Python
        ran, errors = 0, []
        for w in self.writes:
            calls = [("the afterRender knob of %s" % w.name(), exec, (self.after_code[w.fullName()], namespace), {})
                     ] if w.fullName() in self.after_code else []
            calls += [("%s for %s" % (getattr(c[0], "__name__", "a callback"), w.name()), c[0], c[1], c[2])
                      for c in self.calls_for(w, self.registry("afterRenders"))]
            with AsThisNode(w):
                for what, call, args, kwargs in calls:
                    try:
                        call(*args, **kwargs)
                        ran += 1
                    except Exception as exc:
                        traceback.print_exc()
                        errors.append("%s: %s" % (what, " ".join(str(exc).split())[:200]))
        sys.stdout.flush()
        self.before_done = set()  # should another render follow in this Nuke, its hooks run as for a new render
        self.watch(True)
        return ran, errors


def render_frames(writes, chunk, frames, hooks):
    for frame in frames:
        active = [w for w in writes if in_limit(w, frame)]
        say("START", frame)
        started = time.time()
        error = None  # what Nuke said on the first try that failed
        for attempt in range(1, FRAME_ATTEMPTS + 1):
            before = output_states(active, frame) if error is not None else None
            try:
                if active:
                    with hooks.around(active):
                        execute(active, frame, frame)
                if before is not None and wrote_nothing(before, output_states(active, frame)):
                    # 1.0.26: Nuke reports an error once; a try after it may return at once and write nothing (Write1
                    # with no channels: every frame "done" in 0.00 s, nothing on disk). That is the same error, not a frame.
                    print("WARNING: frame %d: try %d gave no error but wrote no file: Nuke keeps quiet about an error it "
                          "reported once" % (frame, attempt))
                    say("FAIL", chunk, frame, error[:400])
                    return
                break
            except Exception as exc:
                error = error or str(exc).replace("\n", " ").strip()
                if attempt == FRAME_ATTEMPTS:
                    traceback.print_exc()
                    say("FAIL", chunk, frame, str(exc).replace("\n", " ")[:400])
                    return
                print("WARNING: frame %d failed (%s), trying again (%d of %d)" % (frame, exc, attempt + 1, FRAME_ATTEMPTS))
                sys.stdout.flush()
                time.sleep(2 * attempt)
        say("FRAME", frame, "%.3f" % (time.time() - started))
        report_frame_hooks(frame)
        say_memory()
    say("CHUNK", chunk)


def render_range(writes, chunk, first, last, hooks):
    clock = {"t": time.time()}

    def after_frame():
        now = time.time()
        say("FRAME", nuke.frame(), "%.3f" % (now - clock["t"]))
        report_frame_hooks()
        say_memory()
        clock["t"] = now

    for w in writes:
        nuke.addAfterFrameRender(after_frame, nodeClass=w.Class())
    say("START", first)
    try:
        with hooks.around(writes):
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
    report_frame_hooks()
    say("CHUNK", chunk)


def main():
    say("BOOT")  # Nuke has started and run its start-up scripts: from here on it is JKrender's time
    script, original, names = (sys.argv[1:] + ["", "", ""])[:3]
    if os.environ.get("JKRENDER_ARGS"):
        # JKrender passes them as UTF-8 too: the command line comes in the Windows code page, which breaks
        # paths with Czech letters
        script, original, names = json.loads(os.environ["JKRENDER_ARGS"])
    opened = time.time()
    try:
        nuke.scriptOpen(script)
    except RuntimeError as exc:
        # Nuke raises for knobs it could not read (a node saved by another version, a missing plug-in's knob),
        # yet the script is open, as the GUI opens it with the same message. Go on when the Writes are there.
        if not names or not all(nuke.toNode(n) is not None for n in names.split("|")):
            raise
        print("JKrender: the script opened with errors, rendering it as Nuke opened it: %s"
              % str(exc).replace("\n", " ")[:400], flush=True)
    if original:
        # The snapshot behaves as the original: [value root.name], relative paths, project settings.
        nuke.root()["name"].setValue(original)
    writes = []
    for name in names.split("|"):
        node = nuke.toNode(name)
        if node is None:
            raise RuntimeError("Write node not found: %s" % name)
        writes.append(node)
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

        keep_in_test.jkrender_own = True
        nuke.addBeforeRender(keep_in_test, nodeClass="Write")
    hooks = RenderHooks(writes, test=bool(test_out))
    say("READY", "%.2f" % (time.time() - opened))
    hooks.report()  # how many afterRender hooks wait for AFTER
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
            render_frames(writes, cmd[1], parse_frames(cmd[2]), hooks)
        elif cmd[0] == "RANGE":
            render_range(writes, cmd[1], int(cmd[2]), int(cmd[3]), hooks)
        elif cmd[0] == "AFTER":
            ran, errors = hooks.run_after()
            say("AFTERDONE", ran, len(errors), " | ".join(errors))
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
