# JKrender — rules for Claude on every machine

JKrender is a local chunked renderer for Nuke: `JKrender.py` (the window, run in Nuke's own Python), `jkrender_worker.py`
(runs inside each Nuke process), `JKrender_NukeIntegration.py` (the JKrender menu in Nuke) and `jkrender_deps.py`
(numpy, OpenEXR, ffmpeg downloads). It is developed by one user on three machines, each with its own Claude session.

## The user
- A compositor. **Reply in Czech.** Button, setting and menu names stay in English, as in the program.
- Short answers; say plainly what was done, what was tested and what was not.

## One codebase, three machines
- **One repository, branch `main`:** https://github.com/JanKanta/JKrender. Never fork the code per platform; platform
  differences live in the code (`if sys.platform == "darwin"`, `"win32"`, `"linux"`), so a change made on one machine
  reaches the others.
- **Windows is the main machine** and the only one that **releases** (version number, tag, CHANGELOG heading).
- **macOS and Linux sessions** fix and test their platform, then commit and push to `main` with a message starting
  `macOS:` or `Linux:` and a line under `## Unreleased` at the top of `CHANGELOG.md`. They do not bump `APP_VERSION`
  and do not tag. The Windows session folds `## Unreleased` into the next release.
- **Before any work:** `git pull --rebase`. **After a tested change:** commit and push at once, so the others build on it.
- Never rewrite pushed history (no force push, no amend of pushed commits).
- On macOS and Linux, `~/.nuke/JKrender` is itself the git clone, and `~/.nuke/init.py` has
  `nuke.pluginAddPath("./JKrender")`. Updating there is `git pull` and a Nuke restart.
- On Windows the repo is `C:\Users\Honza WorkStation\JKrender`; after a release the program files are copied to
  `~/.nuke/JKrender` and `Desktop/JKrender` (JKrender.py, jkrender_worker.py, JKrender_NukeIntegration.py,
  jkrender_deps.py, donation.json, README.md, CHANGELOG.md, INSTALL.txt). Never touch `lib/`, `log/` or `settings/`
  there.

## Releasing (Windows session only)
After every finished change, without asking: bump `APP_VERSION` and the version in the first docstring line of
`JKrender.py`, the version line in `README.md`, add a dated entry on top of `CHANGELOG.md` (folding in `## Unreleased`),
commit, tag `vX.Y.Z` (no "alpha"), push the commit and the tag, copy the files as above. Tell the user the new version.

## Tests
- `python tests/run_tests.py` runs everything offscreen against a stand-in Nuke (`docs/manual/FakeNuke.cs`, built with
  the Windows C# compiler). **FakeNuke is Windows-only for now**; a Python stand-in for macOS and Linux is still to do.
- A small UI or text change: run only the test that covers it (`run_tests.py settings`). A change to the memory rules
  or the scheduler: the whole suite, and release only when it is green. The whole suite only once in a while otherwise.
- `run_tests.py --real` adds tests with the real Nuke (`test_real_nuke`, `test_real_nc`). Run them only when the PC is
  free; they need a licence.
- Every fixed bug gets a check in the matching `tests/test_*.py`.
- Tests write only into their own folders, show no windows and play no sounds.

## Never
- Never overwrite or delete the user's rendered frames or scripts. Render copies of scripts into scratch folders.
- Never close, kill or clear Nuke processes you did not start (check the command line and PID first).
- Never start heavy renders or tests while the user works in Nuke.
- Never edit the studio pipeline's files, launcher or configuration, and do not use its environment to run Nuke.
- Never write the name of the studio's project, its shots, scripts or tools into anything that goes to git (code,
  comments, tests, README, CHANGELOG, manual). Write "a heavy comp" instead.
- Never print API keys, tokens or licence signatures.
- Download nothing (packages, models, tools) without the user's explicit yes.
- Rules stay general: no numbers tuned to one machine or one script.
- Do not bring back what the user had removed: Analyze script, VRAM cap, Go on after a crash, Reopen Renders menu
  item, the old RAM/VRAM recycle rules, "missing" gap marks, dark green for reloads, Delete all, Reload Nuke "Never",
  Memory Limit, per-script memory, Frames per setup.
- The manual (`docs/manual`, six languages) is reworked only when the user asks for it.

## Nuke and licences
- Workers run `Nuke -i -m <threads> -c <cache>G [--gpu 0] -t jkrender_worker.py <script> <original> <writes>`.
  `-i` takes the interactive licence (the user has no render licence); without it Nuke asks for one and fails.
- Nuke Non-commercial and Indie: scripts `.nknc` / `.nkind`, the job carries `nuke_mode`, the workers add `--nc` /
  `--indie`. NC renders at most HD and lets Python list only 10 nodes at a time.
- macOS: Nuke is `/Applications/NukeX.YvZ/NukeX.YvZ.app/Contents/MacOS/NukeX.Y`, its Python `Contents/MacOS/python3`,
  PySide6 in `Contents/Resources/pythonextensions/site-packages`, Qt in `Contents/Frameworks`. Apple silicon has no
  NVIDIA card: GPU use comes from `ioreg`, its memory is unified with the RAM, so the VRAM rules stay off there.
