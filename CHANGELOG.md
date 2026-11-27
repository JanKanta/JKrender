# Changelog

## Unreleased

- Delete after a Stop: the frame bar kept the colours of the deleted frames until something else redrew it (it redraws on its own only while rendering or when a check reports). It now redraws at once, the Workers timeline too.

## 1.0.32 — 2026-11-27

- **Movies are checked frame by frame:** black, one-colour and frozen frames (the same as the one before) are found by frame number and shown as errors, as in a sequence. A slow shot with grain is not taken for frozen.
- **The pixel check looks at RGB only.** The other layers of a multi-layer EXR, AOVs and utility passes, are yours: a depth of inf or SmartVector layers that change from frame to frame are no longer reported. A render of utility layers alone shows "no RGB", not an error. Missing frames are not read, and frames with the same problem make one line in the Log.
- **A Write that cannot write fails the frame with Nuke's own error** (no channels to write, a locked file), instead of counting as rendered with nothing on disk.
- **AMD and Intel Arc graphics cards:** the GPU and VRAM tiles and the VRAM rules work with them too (Windows; AMD on Linux).
- **Intel CPUs with efficiency cores:** Windows' Efficiency mode is turned off for the Nuke processes, so a render is not moved onto the slow cores when JKrender is in the background.
- **Settings › Clear Nuke cache without asking**, and **Check for updates** right above Reset to defaults. What's new shows only what is new.
- **Workers is easier to read:** no time axis, and the empty time between two renders is cut out.
- **A pipeline's environment variables** (project, shot, task) go with each render once their name prefix is in settings.json: `"pass_env": ["STUDIO_"]`. Every render already gets the NUKE_PATH, PYTHONPATH and OCIO of the Nuke that sent it; keys, tokens and passwords never go into a job.
- **Writes sent together from Nuke come in picked together:** one click on Render starts them all.
- **Smaller log folder:** the script copies of renders that are not open are packed (.gz, about a fifth of the size) and unpacked when they render again.
- **The manual in six languages is up to date**, with new pictures; the README has a link to the promo video.
- **Fixes:** JKrender and Nuke talk on ports below Windows' reserved ranges; a new render no longer jumps to another process number; a blocked ffprobe no longer marks a movie broken; the first-start ffmpeg download tries a flaky server again; the disk warning names the disk on macOS and Linux.

After updating, restart Nuke once: the port JKrender and Nuke talk on has changed.

## 1.0.1 — 2026-11-23

- Settings > General > **Check for updates**: asks GitHub at once for the newest release. A newer one opens the update window (What's new, Update, Later), also one you said not to show again; otherwise it says "JKrender 1.0.1 is the newest", or that GitHub could not be reached, with the time. `test_update` checks all three.

## 1.0.0 — 2026-11-23

JKrender 1.0: what it does, in short.

- **Renders one Nuke render in several Nuke processes on one PC.** Each process opens the script once and renders chunk after chunk; chunks shrink towards the end so every process finishes together. Up to three renders run side by side and share one budget of processes; a movie (MOV, MP4, MXF) gets a process of its own with every thread. About 1.3–5× faster than a single Nuke: light comps about 1–1.3×, heavy comps about 2–5×, depending on the script and the computer.
- **Test this script** tries processes × threads × cache (and the Reload Nuke rules) on frames from the whole shot, in two rounds, and tells the fastest setup and what each would cost on the whole shot.
- **Keeps the PC from running out of memory.** Reload Nuke at, held for Reload Nuke after, reloads the process holding the most after its frame or chunk; Kill at closes one at once; a full graphics card that stalls the processes closes one; the 1.3 rule reloads a process that keeps memory it no longer needs while a place waits. Places come back one at a time once there is room.
- **Checks every frame.** When a render ends: missing, empty, cut-off, unreadable and suspiciously small files; then the pixels in the background: black, empty and one-colour frames, NaN and inf, frames the same as another, a slate with nothing on it, a different size, channels, bit depth or compression. Movies against the Write: codec, profile, fps, resolution, timecode, frame count. A red or green card, a sound when it ends.
- **Survives what goes wrong.** A frame that fails is tried again in a fresh Nuke, up to three times, and only that frame is given up; a Nuke that crashes loses only its half-written frame; JKrender going down brings its renders back the next time it opens; a render where nothing comes out stops by itself.
- **Works with any pipeline's render hooks** as one Nuke does: afterRender once when every frame is on disk, beforeRender once in each process, the frame hooks on every frame; Workers and the Log show when they ran and how long they took.
- **Shows what happens:** the frame map, an Output panel with a zoomable preview (an EXR on its format or its bbox), a frame player and a movie player; Workers, a timeline of every process with its chunks and hooks; System, the load of the PC and of each process; a Log that also says when the disk holds the render up; a log folder and a report of every render.
- **Tells you when a new version is out** and installs it on a click: at start a window with What's new, Update and Later.
- **Runs on** Windows, macOS and Linux, with Nuke 16 and later in Nuke's own Python and with Nuke 13–15 in a Python of your own; Nuke Non-commercial and Indie too. numpy, OpenEXR and ffmpeg are offered at the first start; it renders without them.

Changes in 1.0.0 itself:
- **Updates on a click.** Every time the JKrender window opens it asks GitHub, in the background, for the newest release; when that is newer, a window says so with **What's new** (the release notes), **Update** and **Later**, and **Don't show this again** for that version. Update downloads the release's zip and unpacks it over the JKrender folder: lib, log and settings stay, the replaced files go to `update_backup/<the version before>` first, and a file that cannot be written puts the old ones back. It does not run while a render runs, refuses a broken zip or one of another version, and leaves a git clone alone (git pull updates that). Then close the window and restart Nuke. Offline or behind a firewall nothing shows; `JKRENDER_NO_UPDATE=1` turns it off. `test_update` checks it against a local stand-in for GitHub.
- The README brought up to date: the speed measured on a heavy comp, Nuke 13–15, macOS and Linux, render hooks, retries per frame, the tests' time.
- The manual rewritten in all six languages for everything since 0.99.56: render hooks, retries per frame, Reload Nuke after, the 1.3 rule, Show render hooks in Workers, the System columns Script and File, the EXR preview on its format, Go on after Stop, the disk warning, the cut-out gaps in Workers, Nuke 13–15, Non-commercial; new screenshots. It is 23 pages now: System, Settings, the State texts and the render hooks have a page of their own.
- The Czech manual is `docs/JKrender_manual_cz.pdf` now, named like the others (it was `JKrender_navod.pdf`).
- Settings: the note under Reload Nuke at reads "Held at this for Reload Nuke after".

