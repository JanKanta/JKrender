# Changelog

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
