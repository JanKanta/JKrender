# JKrender tests

```
python tests/run_tests.py            # all, in about 8 minutes
python tests/run_tests.py disk fresh # only tests whose name has one of these words
python tests/run_tests.py --real     # also a small render and Test this script with the real Nuke
```

Each test runs JKrender offscreen and silent, in a home folder of its own under `tests/_runs/`, so it never touches your settings,
renders or open Nukes. Renders run on **FakeNuke**: on Windows `tests/fakenuke/FakeNuke.cs`, built on the first run with the C# compiler
that comes with Windows (without it, the build kept in git, `tests/fakenuke/FakeNuke.exe`); on macOS and Linux `tests/fakenuke/fake_nuke.py`, run with Nuke's Python by a launcher named like Nuke.
It speaks the worker's protocol and writes PNG frames in a fraction of a second, without a licence, and can crash mid-chunk on
purpose. Nuke's Python is found under `C:/Program Files/Nuke*`, `/Applications/Nuke*/Nuke*.app` or `/usr/local/Nuke*` (or set
`NUKE_DIR` to Nuke's folder, on macOS the `.app`). `--real` needs a Nuke licence
and skips itself while any Nuke is open (`JKRENDER_TESTS_BESIDE_NUKE=1` runs it anyway). `JKRENDER_SOURCE=<folder>` runs the tests against another copy, an older version for one.

| Test | What it checks |
|---|---|
| `test_disk_checks` | a render opening with frames whole, missing, cut off, empty; Check frames says the same; frames checked when a render ends, then the pixels; Stop |
| `test_render_again` | Render again on one and on several renders, finished or stopped: Only missing, All frames again, Cancel; card numbers by place, no gaps |
| `test_memory` | Reload Nuke at and Kill at for RAM and VRAM: closing, coming back, a full card, a card so full no frame finishes, processes stalled on a full card just under the limit, the biggest process reloading for a place that waits 15 s, Reload Nuke after 30 s, the State text |
| `test_fresh_nuke` | Reload Nuke: Never, When memory is full, After every chunk; same places, State, timeline |
| `test_schedule` | at the end of a render a split chunk reaches every idle process in the same tick |
| `test_script_test` | Test this script: setups, the same frames for each, Add / Edit test, both rounds, a winner, Use these settings |
| `test_settings` | defaults, saving, Reset, settings moved from older versions, a movie shows 1 process, the slate is the frame before the Framerange |
| `test_movies` | movies like other renders, within the places of Processes, one process each with every thread; the File column in System, and saved columns renamed |
| `test_output_layout` | Output in a low window: the player bar and the frame info stay under the preview, never on it |
| `test_exr_preview` | the EXR preview shows the format: a smaller bbox in its place on black, an overscan bbox cut to the format; the right-click switch to the bbox alone |
| `test_process_menu` | the process menu: one process less while paused, while closing for a limit, while waiting to come back |
| `test_robust` | a Nuke process crashing mid-chunk, JKrender going down and coming back, a second JKrender handing over, Czech letters in paths, a crash never turns the card red (also while it renders), red with a broken frame |
| `test_disk_stall` | processes held up together: three or more slow frames ending at the same moment, twice, warn once about the disk; slow frames apart, two processes, first frames: nothing |
| `test_frame_retries` | retries per frame: a frame that always fails is given up alone and the rest of its chunk renders; frames failing once each in one chunk all get their tries; nothing rendering stops after three frames |
| `test_workers_gap` | Workers cuts out the time with no process between two renders, a mark stands there; shorter pauses stay; chunk tooltips still find their frames |
| `test_update` | updates on a click against a local stand-in for GitHub: the window at start, What's new, Don't show this again, Update installs over the folder keeping lib, log and settings with a backup; refused while rendering, for a broken zip, another version's zip and a git clone |
| `test_render_hooks` | a pipeline's afterRender hooks run once, when every frame is on disk, in the process that rendered the last chunk, which closes after them; the render waits for them; a hook that fails, a Nuke that ends while they run, Stop; no hooks, no change |
| `test_ffmpeg_fetch` | ffmpeg fetched on the first start from a release served locally: the newest LGPL build, only the programs and DLLs kept, nothing left on a wrong SHA-256, asked once together with numpy, the PC's own ffmpeg first; macOS (both zips) and Linux (the .tar.xz, programs in bin/, libraries in lib/) as well |
| `test_rewrite` | All frames again writes every file anew (its write time is newer than the click): all frames on disk, some, a stopped render, a new render opened over an earlier one's frames |
| `test_system` | macOS: GPU use per process from macOS's GPU time, RAM and GPU memory per process from footprint (for the RAM column and the memory rules), the GPU's memory in the Load graph as a share of the RAM; Linux: GPU and VRAM per process from `nvidia-smi`, the CPU's name, the dark palette, the end sounds' player |
| `test_frame_number` | a Write with no frame number in its path is refused; movies, single frames and every way Nuke writes a number pass |
| `test_pixels_rgb` | the pixel check on RGB only: a depth of inf or SmartVector layers that differ from frame to frame are no problem, a render of utility layers alone is "no RGB" (not red), NaN in the beauty is found (one part or multi-part); missing frames are not read, unreadable or differing frames make one Log line |
| `test_power_throttling` | Windows: every Nuke process of a render runs with Windows' power throttling (Efficiency mode, EcoQoS) off, and its log says so |
| `test_gpu_any` | a card of any maker: Windows' own readings of the card with nvidia-smi hidden match nvidia-smi and reach the VRAM rules and the GPU tile; Linux AMD from made-up sysfs and /proc fdinfo (the card with the most VRAM, not the processor's graphics; a process's VRAM once per client, its GPU %) |
| `test_snapshot_pack` | the script copies of renders not open and an hour old are packed (.gz), the open ones, the ones just rendered and folders JKrender did not make are not; the packed copy and its folder keep their time, so the cleanup's age stays; a render unpacks its copy before Nuke opens it; the window packs a few seconds after it opens and says so |
| `test_probe_tool` | ffprobe that cannot run (blocked by Windows or an antivirus, a DLL missing) leaves a movie not checked, never broken; a file that is no movie stays broken |
| `test_nc` | Non-commercial and Indie: the job carries the mode, every worker starts with `--nc` / `--indie`, `.nknc` and `.nkind` scripts are taken |
| `test_real_nuke` | (`--real`) a render and Test this script with the real Nuke |
| `test_real_hooks` | (`--real`) render hooks with the real Nuke, on the Write and from a start-up script on NUKE_PATH: beforeRender once in each Nuke before its first frame, the frame hooks once a frame, afterRender once after the last frame with `nuke.thisNode()` the Write; an afterRender that raises |
| `test_real_nc` | (`--real`) with the real Nuke Non-commercial: EXR, PNG and ProRes renders, every check, Test this script |
| `test_real_write_error` | (`--real`) a Write that cannot write (no channels): the frame fails with Nuke's error instead of counting as rendered after a quiet second try; a file locked for a moment fails in that Nuke and renders in a fresh one; a good Write renders as before |
| `test_real_install` | (`--real`) the install as a new user does it: the folder in `~/.nuke`, one line in `init.py`, then the menu's steps and a render with the real Nuke |
