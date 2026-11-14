# JKrender tests

```
python tests/run_tests.py            # all, in about 2 minutes
python tests/run_tests.py disk fresh # only tests whose name has one of these words
python tests/run_tests.py --real     # also a small render and Test this script with the real Nuke
```

Each test runs JKrender offscreen and silent, in a home folder of its own under `tests/_runs/`, so it never touches your settings,
renders or open Nukes. Renders run on **FakeNuke** (`tests/fakenuke/FakeNuke.cs`, built on the first run with the C# compiler that
comes with Windows): it speaks the worker's protocol and writes PNG frames in a fraction of a second, without a licence, and can
crash mid-chunk on purpose. Nuke's Python is found under `C:/Program Files/Nuke*` (or set `NUKE_DIR`). `--real` needs a Nuke licence
and skips itself while any Nuke is open. `JKRENDER_SOURCE=<folder>` runs the tests against another copy, an older version for one.

| Test | What it checks |
|---|---|
| `test_disk_checks` | a render opening with frames whole, missing, cut off, empty; Check frames says the same; frames checked when a render ends, then the pixels; Stop |
| `test_render_again` | Render again on one and on several renders, finished or stopped: Only missing, All frames again, Cancel; card numbers by place, no gaps |
| `test_memory` | the RAM and VRAM limits: closing, coming back, a full card, a card so full no frame finishes, processes stalled on a full card just under the limit, the biggest process reloading for a place that waits 15 s, Reload Nuke after 30 s, the State text |
| `test_fresh_nuke` | Reload Nuke: Never, When memory is full, After every chunk; same places, State, timeline |
| `test_script_test` | Test this script: setups, the same frames for each, Add / Edit test, both rounds, a winner, Use these settings |
| `test_settings` | defaults, saving, Reset, settings moved from older versions, a movie shows 1 process, the slate is the frame before the Framerange |
| `test_movies` | movies like other renders, within the places of Processes, one process each with every thread; the Write column in System |
| `test_process_menu` | the process menu: one process less while paused, while closing for a limit, while waiting to come back |
| `test_robust` | a Nuke process crashing mid-chunk, JKrender going down and coming back, a second JKrender handing over, Czech letters in paths, a crash never turns the card red (also while it renders), red with a broken frame |
| `test_ffmpeg_fetch` | ffmpeg fetched on the first start from a release served locally: the newest LGPL build, only the programs and DLLs kept, nothing left on a wrong SHA-256, asked once together with numpy, the PC's own ffmpeg first |
| `test_rewrite` | All frames again writes every file anew (its write time is newer than the click): all frames on disk, some, a stopped render, a new render opened over an earlier one's frames |
| `test_real_nuke` | (`--real`) a render and Test this script with the real Nuke |
| `test_real_install` | (`--real`) the install as a new user does it: the folder in `~/.nuke`, one line in `init.py`, then the menu's steps and a render with the real Nuke |
