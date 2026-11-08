# JKrender

![JKrender rendering with 8 Nuke processes: render cards on top, chunks under the tiles, the EXR preview and frame list on the left, the Workers timeline and Settings on the right](docs/screenshot.png)

Fast local rendering for Nuke. JKrender splits a render into chunks and renders them in several
Nuke processes at once, each of which opens the script only once. Every finished frame is checked
on disk.

**Version 0.87.3 alpha.** It works in tests; expect rough edges.

A manual in Czech, with every button, panel and setting: [docs/JKrender_navod.pdf](docs/JKrender_navod.pdf).

## What it does

- **Chunks across processes.** By default the work is spread over 8 Nuke processes with 4 threads each
  (Settings > Processes and Chunks). 4 × 8 can be faster for comps one Nuke already fills (Defocus, Denoise) and takes half the RAM.
  Chunks go to whichever process is free and shrink towards the end, so none sits idle.
- **Several renders in one window.** Each Write is a card at the top. They share one budget of
  Nuke processes, and up to 3 run side by side. Shift+click cards to pick several: Render, Pause and Stop then act on all of them.
  Right of the cards: when all renders should be done and how long they have taken so far; once through, how long they took altogether.
  With **Start next render when one ends** (Settings > General, off by default), the next card not yet started starts by itself when a render
  finishes or fails. A render you stopped stays stopped and starts nothing.
- **Render asks before it clears the cache of open Nukes.** With a Nuke open, Render asks whether it should free its buffers, playback cache and Blink cache, so the render has the memory. The render starts either way, and No is the default.
  The disk cache stays. Resume, the next render starting by itself and a render resuming after a crash clear nothing. A Nuke has to be restarted once after an update to take part.
- **Output check.**
  - Image sequences, as each frame renders or with Check frames: missing, empty or cut-off files (EXR, PNG, JPEG, DPX), and suspiciously small frames. Settings > General can turn the automatic check off.
  - Pixels, when a render opens, when it ends or with Check pixels: black, empty or one-colour frames, frames exactly the same as another anywhere in the sequence, and a size, channels or bit depth unlike the rest, in EXR, PNG, JPEG, TIFF and DPX; in EXR also NaN and inf, and the compression. Other formats: only frames that are the same file. In the background, it only reports, and Render stops the check at opening (Settings > General, both on by default).
  - Movies (MOV / MP4 / MXF): codec, profile, fps, resolution, timecode and frame count against the Write's settings.
    The movie has a looping player with zoom.
  - Rendered frames: a frame bar with Play and a slider under the preview, and an enlarged view over the panes.
  - Output lists what is on disk: All has every frame rendered or found there, also one queued to render again (`on disk, queued again`) until the new one replaces it; Rendered only those rendered in the run now. Framerange shows every frame of the range.
- **Delete** in Output removes all rendered frames (the default) or a frame range, after asking.
- **Test this script** (Settings > Performance and Optimization): renders frames from 6 places spread over the whole shot (the same for every setup, a stretch of frames in a row at each; a shot with no more frames than that renders them all and says so) with a few
  recommended Processes x Threads x Cache setups taken from the CPU and RAM, and any you add with Add test, in two rounds: Round 1 of 3 frames
  a process for every setup, then Round 2: the two fastest (and any within 5 %) again with 20 frames (you can set how many). Frames per minute
  are counted once the processes are going, each process's first frame and the start left out, as in a long render. It recommends the fastest.
  The table shows each round's time, Slower (how much longer the whole shot would take than with the fastest, in time and per cent),
  the fastest setup in green and the next in orange; a click on a column sorts by it. The frames go to a test folder and are deleted; nothing changes unless you press
  Use these settings. Tables stay in `~/.jkrender/test_results`. A Write's render hooks do not run in a test, so it cannot write
  to the real output; a frame that does not arrive in the test folder stops the test.
- A render says so when processes keep closing for VRAM, and suggests fewer processes with more threads. It changes nothing by itself.
- **Pause** freezes every Nuke mid-frame. **Stop** cleans up half-written frames and Nuke's temp files.
- **Space** plays and stops the preview. It never presses a button, so it cannot stop a render.
- **Workers**: a timeline of every process. **System**: CPU, RAM, disk, network, GPU, and each process
  (CPU, RAM, GPU and VRAM). Right-click a process to pause, close, start it again or move it to another render (Work on).
- **Add and remove processes while rendering**: right-click in System or on the Workers timeline. **Add process** starts one more; **Close after this chunk** lets a process finish its chunk and close. Processes follows and stays at the new number. The RAM and VRAM limits still apply.
- **Render data** (Settings > General > Export render data): every render writes a report.json with this PC's CPU, RAM and graphics card, the settings, speed, memory peaks, limit closes, how long Nuke took to start and the script to open, and the kinds of nodes in the script. Export puts them all in one file to send, without script names, paths or errors; a script is known only by an id that stays the same over its versions.
- **Sound when a render ends** (Settings, on by default): one sound when a render finishes, another when it fails, leaves broken frames or the pixel check finds a problem. It comes when the checks that are on are through. A render you stopped stays silent.
- **Framerange** (Settings > Job): the frames of the render picked at the top, like 1001-1100 or 1001-1010,1050. Every render has its own; empty goes back to what Nuke sent, and a new send from Nuke starts from its range again. Frames on disk stay where they are, and stay in Output marked `not in Framerange`.
- **Slate frame**: on by default, for projects that start at 1001 with the slate on 1000.
- **Use GPU** (Settings, on by default): starts the Nuke processes with `--gpu`, so GPU nodes such as Kronos render on the
  graphics card. A light 4K script with Kronos rendered 3x faster with identical frames. Every process takes its own VRAM, and the VRAM limit
  (on, 95 %) keeps the card from filling: close to the limit, the process that grew the most gets a fresh Nuke after its chunk;
  at the limit fewer processes run. With room on the card nothing restarts.
- **RAM limit** (on by default, 90 %): over the limit fewer Nuke processes run, so Windows never swaps.
  The Cache setting is Nuke's cache limit per process (`-c`); a process takes more than that.
- **Starting and falling.** A render starts all its processes at once. Over a limit, the process started last closes first:
  at once if it is still opening the script, otherwise after its chunk. Only when the card is nearly full (97 %) does the process taking the most VRAM close at once. A process that fell or closed comes back by itself
  when it fits: the RAM left under the limit, less a 2 % margin and what the running processes are still going to take, must hold
  one process of the size seen this run (before that, its cache + 4 GB), and there must be room in the VRAM. They come back one at a time,
  each after the one before has rendered a frame. After an overload the next waits 5 s and its memory has to stay free for 5 s, then 60, 120 and 300 s if it happens again within 10 minutes. Meanwhile the State column of the System table says in short what it waits for, like wait: RAM 99 % / 95 %.
- **After a crash.** If JKrender goes down, its cards come back the next time it opens (the next render sent from Nuke).
  What is on disk counts, and Nuke processes left behind are ended. A render that was going waits for Render, unless
  **Go on after a crash** (Settings > General, off by default) is on. Closing the window properly brings nothing back.

## Files

| File | Runs in | Role |
|---|---|---|
| `JKrender.py` | Nuke's own Python (PySide6 and psutil) | the window and the scheduler |
| `jkrender_worker.py` | each Nuke process (`nuke -t`) | opens the script once and renders the chunks it is sent |
| `JKrender_NukeIntegration.py` | Nuke GUI | the **JKrender** menu (Ctrl+Alt+R) |
| `menu.py` | Nuke GUI | adds the menu when this folder is on Nuke's plugin path |
| `jkrender_deps.py` | Nuke GUI and the window | fetches numpy and OpenEXR for the EXR preview, and finds them |
| `INSTALL.txt` | | the install steps in short |
| `lib/<platform>-cp<version>/` | the window | numpy and OpenEXR, fetched on the first Nuke start (not in git) |
| `log/` | | one folder per render: script snapshot, job file and the log of every Nuke process (not in git) |

## Setup

1. Copy the whole `JKrender` folder into `~/.nuke/` (Windows: `%USERPROFILE%\.nuke\`).
2. Add one line to `~/.nuke/init.py`, creating the file if it is not there:

   ```python
   nuke.pluginAddPath("./JKrender")
   ```

3. Restart Nuke.
   - On the first start, JKrender asks to download numpy and OpenEXR for the EXR preview (about 15 MB).
   - They download in the background, with Nuke's own python, into `lib/win_amd64-cp311` (for Nuke 16).
   - If you say no, rendering still works and you are not asked again.
   - Delete `lib/DECLINED` to be asked again on the next start.

In Nuke, select Writes (or Groups that contain Writes) and choose **JKrender → Render Selected Write(s)**.

The window runs in Nuke's own Python, which already has PySide6 and psutil, so nothing else needs installing.

- **Behind a firewall:** set `JKRENDER_NO_FETCH=1` and fill the folder by hand:

  ```
  "C:\Program Files\Nuke16.0v9\python.exe" -m pip install --only-binary=:all: --target lib\win_amd64-cp311 numpy OpenEXR
  ```

- **Another Python:** set `JKRENDER_PYTHON`. That Python needs `python -m pip install PySide6 psutil numpy OpenEXR`.

Optional: for movie details and the movie player, put `ffprobe` / `ffmpeg` on PATH (`winget install ffmpeg`).

Renders and logs go to `log/` next to `JKrender.py`; folders older than 7 days are deleted when JKrender opens (Settings). If that folder is read-only, they go to `~/.jkrender/renders/`.
Settings are per user, in `~/.jkrender/settings.json`.
