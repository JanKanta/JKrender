# Changing JKrender

JKrender is plain Python, run with Nuke's own Python and its PySide6. Nothing to build, nothing to install. Fork it, change
it, run the tests, send a pull request. This page says where things are and what to keep.

## The files

| File | Runs in | What it does |
|---|---|---|
| `menu.py` | Nuke | Nuke runs it because the folder is on its plugin path; it only calls `install_menu()`. |
| `JKrender_NukeIntegration.py` | Nuke | The JKrender menu. Reads the selected Writes, writes `job.json` into the render folder, finds Nuke's Python with PySide6 and starts the window with it. Offers the downloads on the first start. Listens on a local port (54340–54349) so the window can ask this Nuke to clear its cache before a render. |
| `JKrender.py` | Nuke's Python, as its own process | The window and the render engine, one file. Sections, top to bottom: helpers and GPU per process (Windows, Linux `nvidia-smi`, macOS `ioreg`), the output check, movie files, the render engine (`Chunk`, `Frame`, `Worker`, `RenderEngine`, `RenderManager`), the look (`C` colours, style sheet, dark palette), widgets, `MainWindow`, `main()`. |
| `jkrender_worker.py` | inside each worker Nuke (`Nuke -i -t`) | Opens the script once, then renders what the window sends on stdin (`RENDER`, `RANGE`, `QUIT`). Everything it reports starts with `JKR `. |
| `jkrender_deps.py` | both | numpy and OpenEXR (pip into `lib/`) and ffmpeg (Windows, Linux: BtbN's LGPL build; macOS: martin-riedl.de) into `lib/ffmpeg`. Every download is checked against a published SHA-256; a folder appears only when it is whole. |
| `donation.json` | the window | The donation link. |
| `tests/` | your Python | The test suite, see below. |
| `docs/manual/` | | The PDF manual's sources (HTML per language, screenshot scripts). |

Folders that are not in git and belong to the user: `lib/` (downloads), `log/` (each render's logs and job), `settings/`.

## Platforms

One codebase for Windows, macOS and Linux. Platform differences live in the code (`sys.platform == "win32"`,
`"darwin"`, `.startswith("linux")`), never in a fork or a second file. When you change something that touches processes,
paths, sounds, the GPU or downloads, look for the other two branches and keep them doing the same.

- Windows: GPU and VRAM per process from Windows' own counters; sounds through `winsound`.
- macOS: Apple silicon has no NVIDIA card; GPU use per process comes from `ioreg`, memory is shared with the RAM, so the
  VRAM rules stay off. Nuke's Python has no HTTPS certificates of its own; downloads use `/etc/ssl/cert.pem`.
- Linux: GPU per process from `nvidia-smi`; under WSL the driver lists no processes, so there only the whole card shows.
  Nuke's Python has no `lzma`, so `.tar.xz` is unpacked with the system's `tar`. Sounds through paplay, pw-play or aplay.
- Workers always start with `-i` (the interactive licence), and with `--nc` / `--indie` for Non-commercial and Indie
  scripts (`.nknc`, `.nkind`). Without `-i` a machine with no render licence fails.

## Tests

```
python tests/run_tests.py             # all, offscreen, a few minutes
python tests/run_tests.py settings    # only tests whose name has this word
python tests/run_tests.py --real      # also renders with the real Nuke (needs a licence; skips while a Nuke is open)
```

The tests need no Nuke licence. Renders run on FakeNuke, a stand-in that speaks the worker's protocol and writes frames
in a fraction of a second: `tests/fakenuke/FakeNuke.cs` on Windows (built on the first run with the C# compiler Windows
ships), `tests/fakenuke/fake_nuke.py` on macOS and Linux. Change one, change the other. Nuke's Python is found in the
usual install folders, or set `NUKE_DIR`. See `tests/README.md` for what each test covers.

- Every fixed bug gets a check in the matching `tests/test_*.py`.
- A small change: run the test that covers it. A change to the memory rules (Reload Nuke at, Kill at) or the scheduler:
  the whole suite.
- Tests write only into `tests/_runs/`, show no windows and play no sounds. Keep it so: someone may be working in Nuke on
  the same machine.

## What to keep

- **The user's files are sacred.** Never overwrite or delete rendered frames or scripts unless the user asked for exactly
  that (Render again). Render copies of scripts, never the original.
- **Only JKrender's own Nukes.** Never close, kill or clear a Nuke process JKrender did not start; check the PID.
- **Rules stay general.** No numbers tuned to one machine, one card or one script.
- **No downloads without asking.** Everything optional is offered once, can be declined, and rendering works without it.
- **No names of real projects, shots or clients** in code, comments, tests or docs.
- **Removed on purpose; please do not bring back:** Analyze script, a VRAM cap, Go on after a crash, a Reopen Renders
  menu item, the old RAM/VRAM recycle rules, "missing" marks for gaps, dark green for reloads, Delete all, Reload Nuke
  "Never", Memory Limit, memory per script, Frames per setup. They were tried and made renders slower or harder to read.
- The window's labels, buttons and settings are in English. Keep the look: the colours in `C`, the style sheet, the dark
  palette.

## Sending a change

1. One change per pull request, with a line on what it fixes or adds and which platforms you tried it on.
2. Leave the version number alone. Add a line under `## Unreleased` at the top of `CHANGELOG.md`; the maintainer folds
   it into the next release, bumps the version and tags it.
3. Say which tests you ran.

## With Claude Code

`CLAUDE.md` holds the same rules for Claude, so Claude Code follows them in a clone of this repository without being
told.
