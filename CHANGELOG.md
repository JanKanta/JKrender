# Changelog

## 0.16 alpha — 2026-10-31

- System: a GPU column per Nuke process shows GPU use and dedicated VRAM.
  - It reads the Windows counters Task Manager uses, because nvidia-smi cannot tell processes apart on Windows.
  - Nuke in terminal mode only uses the GPU when started with --gpu, so the column shows about 0 % for now.
- Right-click a process, in System or on the Workers timeline:
  - **Pause / Resume**: freezes only that process mid-frame. Pausing the whole render and resuming it leaves it paused.
  - **Close**: its chunk goes back to the queue, and it stays closed (State: closed by you) until Start again.
  - **Start again**: a closed process comes back under its own name, as soon as the RAM limit allows.
  - **Show process log**.
- State of a closed process is "closed" (or "closed by you"), without Nuke's exit code.

## 0.15 alpha — 2026-10-31

- Auto-tune processes is gone, together with Forget tuned scripts.
  - The split comparison gained little: on 1.nk, 8 x 4 at 1.04 fr/s against 4 x 8 at 0.97.
  - Starting fewer processes from the remembered RAM is gone too.
- Processes and Threads are set by hand under Settings → Nuke processes, and exactly that many start.
- The RAM limit (on by default) is the only thing that closes processes, the one started last first, when RAM runs over.

## 0.14 alpha — 2026-10-30

- Settings → RAM limit is its own checkbox again, separate from Auto-tune processes, and works with Auto-tune on or off.
  - When RAM goes over the limit, the process started last closes first.
  - Off: nothing watches RAM.
- Auto-tune processes now does the split comparison and starts only as many processes as fit (the latter needs RAM limit on).
- With Auto-tune off, all processes set start; only the RAM limit can close some.

## 0.13 alpha — 2026-10-30

- RAM guard: when RAM runs over, the process started last closes first (W08, then W07, ...).
  - A replacement such as "W01 started again" counts as the newest.
  - 0.11 went by when a process started its first frame. All processes open the script within the same second, so that picked one at random; on the CTH comp it closed W01 first.
- The orange progress bar between the stats and the chunk map is gone; the chunk map and Frames show the progress.

## 0.12 alpha — 2026-10-30

- Settings → Auto-tune processes now holds everything that changes the number of Nuke processes.
  - Off: exactly the processes set under Nuke processes run, from start to end.
  - On:
    - the split comparison (8 x 4 or 4 x 8),
    - the RAM guard (the newest close first when RAM runs over),
    - starting only as many processes as fit, from the RAM one took last time.
  - The separate RAM guard checkbox is gone. RAM limit and Forget tuned scripts sit under Auto-tune and are greyed out when it is off.
  - Forget tuned scripts also forgets the RAM per process.
- Fixed: test frames rendered first even with Render test frames first off.
  - After Stop, the test chunks of the stopped run stayed test chunks and were still sent first.
  - Each start now turns them into ordinary chunks.

## 0.11 alpha — 2026-10-30

- The gradual start from 0.10 is gone: it cost time. All Nuke processes start one after another straight away again.
- When RAM runs over, the RAM guard closes the processes that started rendering last, so the least work is lost.
  - It closes idle and still-loading ones first, then busy ones from the newest.
  - Close to full it used to close the one that had worked longest on its frame.
- Later renders of a script still start only as many processes as fit, from the RAM one process took last time.

## 0.10 alpha — 2026-10-30

### Speed and safety

- A script's first render (RAM per process not known yet) starts 2 Nuke processes.
  - Once one has finished a frame and its RAM use is known, as many more start as fit under the RAM limit.
  - Before, all 8 started at once, RAM ran over and the RAM guard had to close the first ones mid-frame.
- Auto-tune waits until that start is over, so it compares the two splits with every process running.
- Later renders of the script start as many processes as fit straight away, as in 0.9.

### Window

- Processes, Threads, RAM and Chunk moved from the row next to Render into Settings, in a first column "Nuke processes".
- The frame range is shown as plain text, "FrameRanges 1000-1095". It comes from Nuke and can no longer be edited in JKrender.
- The Auto button is gone for now.
- System → CPU shows how many threads are in use out of all of them, e.g. "8 of 32 threads in use".

## 0.9.2 alpha — 2026-10-30

- System: the column is called RAM (was RAM used).

## 0.9.1 alpha — 2026-10-30

- System: the RAM used column shows only what each Nuke process takes (e.g. 11.3 GB), without the RAM setting next to it.

## 0.9 alpha — 2026-10-30

- A process that had to close (RAM guard, recycled) is replaced under its own name and on its own timeline row: W01 starts again instead of a new W09.
- JKrender remembers how much RAM one Nuke process of each script takes (after its first chunk).
  - The next render of that script starts only as many processes as fit in RAM right now, so the RAM guard does not have to close any in the middle of a frame.
  - On the CTH 4312x2304 comp that is about 11 GB per process: 8 of them do not fit in 94 GB next to Nuke's GUI.
- The RAM guard adds back as many processes as fit at once, counting the RAM that just-started processes are still about to take, instead of one every 15 s.
- The RAM setting is Nuke's cache limit (-c), not a cap on the whole process; a heavy script takes more. The README says so.

## 0.8 alpha — 2026-10-30

- Fixed: with a heavy script, a new Nuke process started after every chunk (29 processes for 46 frames on a 4312x2304 comp).
  - A process was closed after any chunk that left it over 1.3 x the RAM setting, so a script that simply needs more RAM was reloaded every time.
  - Now a process is recycled only when its RAM grows to 1.5 x what it used after its first chunk (a leak). The Log says once when the RAM setting is lower than what the script needs.
- RAM guard: a process comes back only when one more fits under the limit (measured by the largest running process). Before, RAM 8 % under the limit was enough, so one process was closed and another started, round and round.
- The Log tab's events are also written to jkrender.log in the render's folder, next to the processes' logs.

## 0.7.2 alpha — 2026-10-30

- `INSTALL.txt` now really has the steps in English (0.7.1 renamed the file but kept the Czech text).

## 0.7.1 alpha — 2026-10-30

- The install note is in English: `INSTALACE.txt` is now `INSTALL.txt`.

## 0.7 alpha — 2026-10-30

- README: a screenshot of a 4K comp rendering in 8 Nuke processes, with the EXR preview.
- Output: in a low window, the frame preview shrinks instead of being drawn over the frame info under it.

## 0.6 alpha — 2026-10-30

- Install like JKplayer: copy the JKrender folder into `~/.nuke` and add `nuke.pluginAddPath("./JKrender")` to `~/.nuke/init.py`. `install.bat` is gone.
- numpy and OpenEXR for the EXR preview are no longer shipped in the folder; on the first Nuke start JKrender asks to download them.
  - About 15 MB, in the background, with Nuke's own python.
  - They go into `lib/<platform>-cp<version>` (e.g. `lib/win_amd64-cp311`), or `~/.jkrender/lib/...` when the folder is read-only.
  - Saying no leaves `lib/DECLINED`; rendering works without them. `JKRENDER_NO_FETCH=1` never asks.
  - Without them, the preview says to restart Nuke to download them.

## 0.5 alpha — 2026-10-30

### Install

- The JKrender folder is all a colleague needs.
  - Run `install.bat` to put the folder on Nuke's plugin path. It adds one line to `~/.nuke/init.py`.
  - Restart Nuke. The folder's `menu.py` adds the JKrender menu.
  - `INSTALACE.txt` has the steps in Czech.
- The window runs in Nuke's own Python, which already has PySide6 and psutil, so nothing needs installing.
  - numpy and OpenEXR for the EXR preview ship in `lib/`.
  - Set `JKRENDER_PYTHON` to use another Python.
  - Without Nuke's Python, JKrender looks for an external one as before.

### Logs

- Each render's folder (script snapshot, job file and the log of every Nuke process) now goes to `log/` next to `JKrender.py`, instead of `~/.jkrender/renders/`.
  - If that folder is read-only, they go to `~/.jkrender/renders/` as before.
  - JKrender → Open Renders Folder opens `log/`.
- Settings stay per user in `~/.jkrender/settings.json`.

## 0.4 alpha — 2026-10-30

- The JKrender menu in Nuke has two items: Render Selected Write(s) (Ctrl+Alt+R) and Open Renders Folder.
  - Removed: Render Selected Write(s) - Current Frame.
  - Removed: the Include Writes inside Groups toggle. A selected Group now always renders the Writes inside it.

## 0.3 alpha — 2026-10-30

- **Auto chunk size** (Settings → Speed and safety, on by default). A process is given at most
  remaining frames / (2 × processes) at a time, so chunks shrink towards the end of a render, down to
  single frames, and every process finishes at about the same time. The Chunk field is now the largest chunk.
  - On 48 frames of a 4K Kronos comp with 8 processes, chunk 12 went from 60.4 s to 51.6 s (no process waits any more).
  - Chunk 6 went from 50.8 s to 50.1 s.
- Choosing chunk size from the script's time nodes was tried and left out.
  Frames in a row rendered no faster in one process (7.47 s at chunk 6 against 7.51 s at chunk 1).

## 0.2 alpha — 2026-10-30

### Speed

- **Auto-tune processes.** For renders with at least 80 frames, JKrender tries two splits on the first frames:
  the processes as set (e.g. 8 × 4 threads) and half as many with twice the threads (4 × 8). It keeps the faster one.
  The winner is remembered per script, and later renders of that script start with it.
  If another render starts, or the RAM guard steps in, the comparison is stopped and nothing is remembered.
- Settings → Forget tuned scripts.

### Safety

- **RAM guard.** Over the RAM limit (default 90 %), one Nuke process fewer runs, one at a time; busy ones finish their chunk first.
  Close to full (limit + 6 %), one process is closed straight away and its chunk goes back to the queue.
  When RAM is 8 % below the limit, it adds one process back every 15 s.
- **Test frames first.** The first, middle and last frame render before the rest.
  - If one is broken on disk, the render pauses.
  - If they are fine, the banner shows an estimate for the rest.
  - Optional: pause after the test frames.

### Window

- The Settings tab has two columns: Renders, and Speed and safety.
- The System → RAM tile shows when the RAM guard holds the process count lower.

## 0.1 alpha — 2026-10-30

First version under version control.

### Rendering

- Renders split into chunks across several Nuke processes. Each process opens the script once and takes chunk after chunk.
- Several renders in one window, one card per Write (also Writes inside selected Groups), sharing one process budget.
- Max renders at once (default 3); with 1, the next render starts loading while the current one is on its last chunks.
- Process names are unique across renders (W01, W02, …).
- Movie Writes render in one process that gets the whole PC.
- Pause freezes Nuke mid-frame and stops the clocks; Stop removes half-written frames and Nuke's temp files.
- Frames already on disk that pass the check count as done; Render again renders everything.
- Optional slate frame in front of the range.

### Output

- Check of image sequences: missing, empty, cut-off (EXR, PNG, JPEG, DPX) and suspiciously small frames.
- Movies: ffprobe details and a check against the Write's settings (codec, profile, fps, resolution, timecode, frame count).
- Looping movie player with zoom; frame-accurate stills.
- Delete all.

### Window

- Nuke-style grey and orange look, with its own taskbar icon.
- Workers timeline, Chunks, Log and Details on top; Output, System and Settings below.
- Render all / Pause all when there are several renders.
- Setting to show the processes of all renders or only of the selected one.
