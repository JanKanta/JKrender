# JKrender

Fast local rendering for Nuke. JKrender splits a render into chunks and renders them in several
Nuke processes at once, each of which opens the script only once. Every finished frame is checked
on disk.

**Version 0.2 alpha.** It works in tests; expect rough edges.

## What it does

- **Chunks across processes.** By default the work is spread over 8 Nuke processes with 4 threads each.
  Chunks go to whichever process is free, so none sits idle at the end.
- **Several renders in one window.** Each Write is a card at the top. They share one budget of
  Nuke processes, and up to 3 run side by side.
- **Output check.**
  - Image sequences: missing, empty or cut-off files (EXR, PNG, JPEG, DPX), and suspiciously small frames.
  - Movies (MOV / MP4 / MXF): codec, profile, fps, resolution, timecode and frame count against the Write's settings.
    The movie has a looping player with zoom.
- **Pause** freezes every Nuke mid-frame. **Stop** cleans up half-written frames and Nuke's temp files.
- **Workers**: a timeline of every process. **System**: CPU, RAM, disk, network, GPU, and each process.
- **Slate frame**: optional, for projects that start at 1001 with the slate on 1000.
- **Auto-tune**: finds the faster split of the PC per script (e.g. 8 × 4 or 4 × 8 threads) and remembers it.
- **RAM guard**: runs fewer Nuke processes when RAM is nearly full, so Windows never swaps.
- **Test frames first**: renders the first, middle and last frame before the rest, with an estimate.

## Files

| File | Runs in | Role |
|---|---|---|
| `JKrender.py` | external Python 3 with PySide6 and psutil | the window and the scheduler |
| `jkrender_worker.py` | each Nuke process (`nuke -t`) | opens the script once and renders the chunks it is sent |
| `JKrender_NukeIntegration.py` | Nuke GUI | the **JKrender** menu (Ctrl+Alt+R) |

## Setup

1. Install the Python packages:

   ```
   python -m pip install PySide6 psutil
   ```

   For movie details and the player, also put `ffprobe` / `ffmpeg` on PATH:

   ```
   winget install ffmpeg
   ```

2. Copy `JKrender_NukeIntegration.py` to `~/.nuke`.
3. Add these lines to `~/.nuke/menu.py`:

   ```python
   import JKrender_NukeIntegration as _jk
   _jk.install_menu()
   ```

4. Keep `JKrender.py` in `~/JKrender/`, or set `JKRENDER` to its path.

In Nuke, select Writes (or Groups that contain Writes) and choose **JKrender → Render Selected Write(s)**.

Settings and render folders are stored in `~/.jkrender/`.
