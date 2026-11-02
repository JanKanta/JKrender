# JKrender

![JKrender rendering with 8 Nuke processes: render cards on top, chunks under the tiles, the EXR preview and frame list on the left, the Workers timeline and Settings on the right](docs/screenshot.png)

Fast local rendering for Nuke. JKrender splits a render into chunks and renders them in several
Nuke processes at once, each of which opens the script only once. Every finished frame is checked
on disk.

**Version 0.64 alpha.** It works in tests; expect rough edges.

## What it does

- **Chunks across processes.** By default the work is spread over 8 Nuke processes with 4 threads each
  (Settings → Nuke processes). 4 × 8 can be faster for comps one Nuke already fills (Defocus, Denoise) and takes half the RAM.
  Chunks go to whichever process is free and shrink towards the end, so none sits idle.
- **Several renders in one window.** Each Write is a card at the top. They share one budget of
  Nuke processes, and up to 3 run side by side. Shift+click cards to pick several: Render, Pause and Stop then act on all of them.
  Right of the cards: when all renders should be done and how long they have taken so far; once through, how long they took altogether.
  With **Start next render when one ends** (Settings > General, off by default), the next card not yet started starts by itself when a render
  finishes or fails. A render you stopped stays stopped and starts nothing.
- **Render clears the cache of open Nukes.** Each open Nuke frees its RAM cache and Blink cache before the render starts, so the render has the memory.
  The disk cache stays, and Resume clears nothing. A Nuke has to be restarted once after an update to take part.
- **Output check.**
  - Image sequences: missing, empty or cut-off files (EXR, PNG, JPEG, DPX), and suspiciously small frames.
  - Movies (MOV / MP4 / MXF): codec, profile, fps, resolution, timecode and frame count against the Write's settings.
    The movie has a looping player with zoom.
  - Rendered frames: a frame bar with Play and a slider under the preview, and an enlarged view over the panes.
- **Delete** in Output removes all rendered frames (the default) or a frame range, after asking.
- **Test this script** (Settings > Processes and Chunks): renders 20 frames from the middle of the selected render (you can set
  how many) with a few recommended Processes x Threads x Nuke cache setups taken from the CPU and RAM, and any you add,
  and recommends the fastest. The frames go to a test folder and are deleted; nothing changes unless you press
  Use these settings. Tables stay in `~/.jkrender/test_results`. A Write's render hooks do not run in a test, so it cannot write
  to the real output; a frame that does not arrive in the test folder stops the test.
- **Analyze script** (right under it): opens the script without rendering, shows the nodes that really feed the Write (disabled branches left out),
  those on the GPU and those that look at other frames, and recommends a setup to start from. It only counts nodes, so Test this script has the last word.
- **Pause** freezes every Nuke mid-frame. **Stop** cleans up half-written frames and Nuke's temp files.
- **Space** plays and stops the preview. It never presses a button, so it cannot stop a render.
- **Workers**: a timeline of every process. **System**: CPU, RAM, disk, network, GPU, and each process
  (CPU, RAM, GPU and VRAM). Right-click a process to pause, close, start it again or move it to another render (Work on).
- **Slate frame**: on by default, for projects that start at 1001 with the slate on 1000.
- **Use GPU** (Settings, on by default): starts the Nuke processes with `--gpu`, so GPU nodes such as Kronos render on the
  graphics card. 1.nk rendered 3x faster with identical frames. Every process takes its own VRAM, and the VRAM limit
  (on, 95 %) keeps the card from filling: close to the limit, the process that grew the most gets a fresh Nuke after its chunk;
  at the limit fewer processes run. With room on the card nothing restarts.
- **RAM limit** (on by default, 90 %): over the limit fewer Nuke processes run, so Windows never swaps.
  The Nuke cache setting is Nuke's cache limit per process (`-c`); a process takes more than that.
- **Starting and falling.** A render starts all its processes at once. Over a limit, the process started last closes first:
  at once if it is still opening the script, otherwise after its chunk. A process that fell or closed comes back by itself
  as soon as it fits: the RAM left under the limit, split by what one process takes, and room in the VRAM.

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
