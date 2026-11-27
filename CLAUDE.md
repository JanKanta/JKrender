# JKrender — rules for Claude

JKrender is a local chunked renderer for Nuke: `JKrender.py` (the window, run in Nuke's own Python), `jkrender_worker.py`
(runs inside each Nuke process), `JKrender_NukeIntegration.py` (the JKrender menu in Nuke) and `jkrender_deps.py`
(numpy, OpenEXR, ffmpeg downloads). `CONTRIBUTING.md` maps the code; read it before a change.

## One codebase, three platforms
- One repository, branch `main`: https://github.com/JanKanta/JKrender. Never fork the code per platform; platform
  differences live in the code (`sys.platform == "win32"`, `"darwin"`, `.startswith("linux")`). When a change touches
  processes, paths, sounds, the GPU or downloads, check the other platforms' branches too.
- Contributors do not bump `APP_VERSION` and do not tag: a line under `## Unreleased` at the top of `CHANGELOG.md`.
- Never rewrite pushed history (no force push, no amend of pushed commits).

## Tests
- `python tests/run_tests.py` runs everything offscreen against a stand-in Nuke: on Windows `tests/fakenuke/FakeNuke.cs`
  (built with the Windows C# compiler), on macOS and Linux `tests/fakenuke/fake_nuke.py` (run with Nuke's Python). Keep the
  two doing the same.
- A small UI or text change: run only the test that covers it (`run_tests.py settings`). A change to the memory rules
  or the scheduler: the whole suite, and release only when it is green. The whole suite only once in a while otherwise.
- `run_tests.py --real` adds tests with the real Nuke (`test_real_nuke`, `test_real_nc`, `test_real_install`). Run them
  only when the machine is free; they need a licence.
- Every fixed bug gets a check in the matching `tests/test_*.py`.
- Tests write only into their own folders, show no windows and play no sounds.

## Never
- Never overwrite or delete the user's rendered frames or scripts. Render copies of scripts into scratch folders.
- Never close, kill or clear Nuke processes you did not start (check the command line and PID first).
- Never start heavy renders or tests while the user works in Nuke.
- Never edit a studio pipeline's files, launcher or configuration, and do not use its environment to run Nuke.
- Never write the name of a real project, its shots, scripts or tools into anything that goes to git (code, comments,
  tests, README, CHANGELOG, manual). Write "a heavy comp" instead.
- Never print API keys, tokens or licence signatures.
- Download nothing (packages, models, tools) without the user's explicit yes.
- Rules stay general: no numbers tuned to one machine or one script.
- Do not bring back what was removed on purpose: Analyze script, VRAM cap, Go on after a crash, Reopen Renders menu
  item, the old RAM/VRAM recycle rules, "missing" gap marks, dark green for reloads, Delete all, Reload Nuke "Never",
  Memory Limit, per-script memory, Frames per setup, the Workers time axis and its dashed marks between renders.
- The manual (`docs/manual`, six languages) is reworked only when asked for.

## Nuke and licences
- Workers run `Nuke -i -m <threads> -c <cache>G [--gpu 0] -t jkrender_worker.py <script> <original> <writes>`.
  `-i` takes the interactive licence (many users have no render licence); without it Nuke asks for one and fails.
- Nuke Non-commercial and Indie: scripts `.nknc` / `.nkind`, the job carries `nuke_mode`, the workers add `--nc` /
  `--indie`. NC renders at most HD and lets Python list only 10 nodes at a time.
- macOS: Nuke is `/Applications/NukeX.YvZ/NukeX.YvZ.app/Contents/MacOS/NukeX.Y`, its Python `Contents/MacOS/python3`,
  PySide6 in `Contents/Resources/pythonextensions/site-packages`, Qt in `Contents/Frameworks`. Apple silicon has no
  NVIDIA card: GPU use comes from `ioreg`, its memory is unified with the RAM, so the VRAM rules stay off there.
- Linux: Nuke's Python has no `lzma`; `.tar.xz` is unpacked with the system's `tar`. Under WSL `nvidia-smi` lists no
  processes, so GPU and VRAM show for the whole card only.
