# Changelog

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
