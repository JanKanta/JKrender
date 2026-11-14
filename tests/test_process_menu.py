"""The process menu (right click in System or Workers): one process less in any state. A paused one, one closing for a
limit, one closed and waiting to come back for memory, one you closed, an empty place: Processes goes down by 1 and the
process does not come back."""
from common import *
from PySide6 import QtCore, QtWidgets

jk, app = start({"processes": 4, "threads": 2, "ram": 2, "chunk_auto": False, "chunk_size": 6, "fresh_after_chunk": "off"})
job = make_job("sh010", 1001, 1060, env={"FAKE_SECS": "0.5"})
win = jk.MainWindow(job)
e, m = win.engine, win.manager
win._start()
wait(lambda: sum(1 for w in e.workers.values() if w.state == "busy") >= 4, 60)


REAL_MENU = QtWidgets.QMenu


def menu(w, pick, empty=None):
    """Open the process menu of w; returns {item: enabled}; picks the item named pick, as a click would. Offscreen a menu
    cannot be clicked, so QMenu is swapped for one whose exec returns that item."""
    seen = {}

    class Menu(REAL_MENU):
        def exec(self, *args):
            acts = []
            for a in self.actions():
                acts.append(a)
                if a.menu() is not None:
                    acts += a.menu().actions()
            for a in acts:
                if a.text():
                    seen[a.text()] = a.isEnabled()
            target = next((a for a in acts if a.text() == pick), None)
            return target if target is not None and target.isEnabled() else None
    QtWidgets.QMenu = Menu
    try:
        if empty is not None:
            win._empty_place_menu(empty, QtCore.QPoint(100, 100))
        else:
            win._worker_menu(e, w, QtCore.QPoint(100, 100))
    finally:
        QtWidgets.QMenu = REAL_MENU
    pump(0.2)
    return seen


say("A paused process: Close after this chunk")
w1 = e.workers[1]
e.user_pause(w1, True)
check("the menu opens offscreen", bool(menu(w1, None)))
items = menu(w1, "Close after this chunk")
check("enabled while paused", items.get("Close after this chunk") is True, items)
check("Processes 4 -> 3", m.total == 3 and win.s_workers.value() == 3, m.total)
check("it went on to finish its chunk", not w1.user_paused and w1.retire, (w1.user_paused, w1.retire))

say("A process closing for a limit: Close after this chunk makes it for good")
w2 = e.workers[2]
w2.retire, w2.why_leaving, w2.closed_why = True, "RAM limit", "RAM limit"
items = menu(w2, "Close after this chunk")
check("enabled while closing for the RAM limit", items.get("Close after this chunk") is True, items)
check("Processes 3 -> 2", m.total == 2, m.total)
check("it closes by me now, not for the limit", w2.user_leaving and w2.closed_why == "by me" and e.worker_state(w2) != "closed (RAM limit)", e.worker_state(w2))

say("A process closed for memory and waiting to come back: Remove process")
win.s_workers.setValue(4)
# the two removed above close after their chunks first, and the places fill again: then nothing moves but W03
wait(lambda: not any(w.user_leaving and w.state != "gone" for w in e.workers.values())
     and m.live_count() == 4 and all(w.state == "busy" for w in e.workers.values() if w.state != "gone"), 60)
w3 = e.workers[3]
ram_guard = m._ram_guard
m._ram_guard = lambda: None  # the PC's real RAM would give the place back at once: here it stays short
m.ram_cap = max(1, m.live_count() - 1)  # no room: it waits to come back, as when the RAM is short
w3.closed_why = "RAM limit"
e._kill(w3, requeue=True, note="stopped")
pump(0.3)
check("W03 waits to come back", e.workers[3] is w3 and w3.state == "gone", e.workers[3].state)
total = m.total
items = menu(w3, "Remove process")
check("Remove process is there and enabled", items.get("Remove process") is True, items)
check("Processes goes down by 1", m.total == total - 1, (total, m.total))
check("it is no longer among those waiting to come back", w3.user_leaving)

say("A process you closed (Close keeps its place empty): Remove process")
w4 = e.workers[4]
e.user_close(w4)
pump(0.3)
total = m.total
items = menu(w4, "Remove process")
check("Remove process is there", items.get("Remove process") is True, items)
check("Processes goes down by 1 and its place is let go", m.total == total - 1 and 4 not in e.held, (m.total, e.held))

say("An empty place, no process in it yet: Remove process")
m.ram_cap = max(1, m.live_count())  # the place waits for memory
win.s_workers.setValue(m.total + 1)
pump(0.3)
total = m.total
items = menu(None, "Remove process", empty=9)
check("Remove process is there for an empty place", items.get("Remove process") is True, items)
check("Processes goes down by 1", m.total == total - 1, (total, m.total))
m.ram_cap = None
m._ram_guard = ram_guard
wait(lambda: e.state in ("done", "failed", "stopped"), 180)
check("the render ends whole", e.state == "done" and len(files(job)) == 60, (e.state, len(files(job))))
names = [x[1] for x in e.log if "W03 started again" in x[1] and x[0] == ""]
check("no 'W03 started again' after it was removed", not names, names)
finish()
