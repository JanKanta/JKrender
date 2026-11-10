# Changelog

## 0.95.1 alpha — 2026-11-10

- Remove process also for a process you closed with Close (its place is let go) and for an empty place no process has taken yet (a row waiting for memory or for a render). Processes goes down by 1.

## 0.95 alpha — 2026-11-10

- One process less in any state, from its menu (right click in System or Workers). Close after this chunk works also on a paused process (it goes on to finish its chunk first) and on one already closing for the RAM or VRAM limit or for another render (it then closes for good, by you). A process closed for memory and waiting to come back, or closed after a crash, has Remove process: it does not come back. Processes goes down by 1 each time. Before, all of these could not be removed. A removed process lends its name last, so the ones waiting keep theirs.
- New test `test_process_menu` (0.94.4 fails it).

## 0.94.4 alpha — 2026-11-09

- The manual is up to date for 0.94 and in six languages, 19 pages each: Czech (`docs/JKrender_navod.pdf`), English, German, Spanish, Polish and Russian (`docs/JKrender_manual_<lang>.pdf`). New in it: Fresh Nuke after every chunk, the frame check when a render ends, Render again on several renders, Output All, the new Settings, where the settings live, the State texts, the RAM limit at 95 %, no VRAM cap per process, installing from a GitHub zip and updating. Screenshots taken again. JKrender itself is unchanged.

## 0.94.3 alpha — 2026-11-09

- Install checked end to end with the real Nuke (new test `test_real_install`, with `--real`): the JKrender folder copied into `~/.nuke`, one line in `init.py`, then what JKrender > Render Selected Write(s) does (finds JKrender.py, the log folder, makes the job and snapshot, picks Nuke's own Python with PySide6) and a render. It works. INSTALL.txt and README: a zip from GitHub unpacks as `JKrender-main`, rename it to `JKrender`; logs go after 15 days, not 7; where the settings are; how to update.

## 0.94.2 alpha — 2026-11-09

- Fresh Nuke when memory is short goes one process at a time: the next only once the last fresh one has rendered its first frame, and only if the RAM or VRAM is still short then. The others go on with their next chunk meanwhile, so the render never stops while several reload together, and a single reload is often enough. Always is unchanged. The Fresh Nuke test checks it with 4 processes (0.94.1 reloaded all 4 at once).

## 0.94.1 alpha — 2026-11-09

- Tests: `python tests/run_tests.py` runs JKrender offscreen through 7 tests (about 120 checks, 2 minutes) on FakeNuke, a stand-in for Nuke that writes PNG frames and can crash on purpose: the disk checks, Render again, the RAM and VRAM limits, Fresh Nuke, Test this script, Settings, and crashes / coming back / handing over / Czech paths. `--real` adds a small render with the real Nuke. Against 0.93.2 and 0.93.5 they find the bugs fixed since. JKrender itself is unchanged.

## 0.94 alpha — 2026-11-09

- Frames are checked once the render ends, not each right after it renders: render, then Check frames (is each file there, not empty, whole, readable, not far smaller than the rest), then Check pixels. Stop checks what was rendered before it. Nothing reads the output folder while Nuke writes into it. The setting is now Check frames when a render ends. Test this script still checks each frame as it comes, it counts its frames by that.

## 0.93.9 alpha — 2026-11-09

- A frame not on disk says "not on disk" again, also when other frames of the render are on disk: with one frame on disk, 0.93.7 called all the others "missing" and warned about them. Output All lists them anyway (0.93.8), and Check frames no longer calls them broken (0.93.6).

## 0.93.8 alpha — 2026-11-09

- Output, All lists every frame: of the range (rendered, found on disk, missing, broken, still to render) and those on disk outside it. Rendered, Problems and Framerange are parts of it. Before, All left out frames not on disk yet.

## 0.93.7 alpha — 2026-11-09

- A gap in a sequence shows as soon as the render opens: a frame of the range not on disk while other frames are (a render stopped, a file deleted) is listed in Output (All and Problems) as "missing", counted in the summary and in the Check frames tile ("29 checked · 1 missing"), and the Log says which: "1 frame(s) of the range are not on disk: 1024". Before, it was left out of the list until Check frames, which then called it "broken on disk". Missing is not counted as broken. With nothing of the render on disk, frames still to render are not listed.

## 0.93.6 alpha — 2026-11-09

- Check frames no longer calls a frame that is simply not on disk yet "broken on disk" (red, counted as bad). The check when a render opens had it right, "not on disk"; the button now says the same. A frame rendered or found on disk that is gone since is still reported.

## 0.93.5 alpha — 2026-11-09

- A movie render picked: Settings > Processes shows 1 and Threads every thread, both locked, and the row by the Render button says the same; a movie is one file and always rendered so. What is set for the other renders stays as it is and comes back when one of them is picked.

## 0.93.4 alpha — 2026-11-09

- State of a process waiting for VRAM shows the whole target: with the room kept for the processes still starting (just started or a fresh Nuke, each counted at the size one process takes), e.g. "wait: VRAM 75 → 14 % (3 starting)". Before, it showed "75 → 68 %" while the decision also counted those, so it looked as if the process should be back already.

## 0.93.3 alpha — 2026-11-09

- Fresh Nuke after every chunk, When memory is short: only once the RAM or VRAM has stayed within 5 % of its limit for 30 s, also while a process closed for memory waits to come back. Before, a waiting process alone was enough, so the processes got a fresh Nuke after every chunk with the card at 60 %.

## 0.93.2 alpha — 2026-11-09

- Render again on several renders picked at once (Shift+click on the cards): finished ones render again too, not only the one shown. One question for all of them, Only missing / All frames again / Cancel, with the frames on disk of all of them; a movie renders whole without being asked.

## 0.93.1 alpha — 2026-11-09

- Settings and state in their own folder, `~/.nuke/JKrender/settings`, not loose beside the program. JKrender moves them there the first time it opens: from `~/.nuke/JKrender` (0.93) first, then from `~/.jkrender`; open cards (`queue.json`) too.

## 0.93 alpha — 2026-11-09

- Settings and state live in `~/.nuke/JKrender` with the program, not in `~/.jkrender`: `settings.json`, `queue.json`, `sounds/`, `test_results/` and Test this script's `test/`. The first time it opens, JKrender moves the settings, test tables and sounds there; what is already in the new place stays. Old render folders in `~/.jkrender/renders` are left alone, and the folder goes once it is empty.
- When the `log` folder next to JKrender.py is read-only, renders go to `~/.nuke/JKrender/log`, not `~/.jkrender/renders`.

## 0.92.4 alpha — 2026-11-09

- Fresh Nuke after every chunk, When memory is short: the RAM or VRAM has to stay within 5 % of its limit for 30 s without a break before the processes get a fresh Nuke; one reading over it is not enough. A process closed for RAM or VRAM still counts at once.

## 0.92.3 alpha — 2026-11-09

- State says what the fresh Nuke is for: "busy → fresh Nuke (RAM 91 %)", "(VRAM 93 %)" or both, not "(memory short)". The Log says it too.

## 0.92.2 alpha — 2026-11-09

- RAM limit 95 % by default, like the VRAM limit (was 90 %). Settings saved before keep their own value; Reset to defaults sets 95.

## 0.92.1 alpha — 2026-11-09

- Workers timeline: every script load is grey, a fresh Nuke after a chunk too (the darker green for a fresh Nuke for the VRAM is gone with it).

## 0.92 alpha — 2026-11-09

- State says it ahead: a process that gets a fresh Nuke after its chunk shows "busy → fresh Nuke (memory short)", or "busy → fresh Nuke" with Always.
- The old renewing is gone, Fresh Nuke after every chunk does it: the process that grew the most in VRAM between 90 and 95 % (When memory is short now renews every process from 90 %), and a process whose RAM grew past 1.5 times (it cost a script load also with plenty of RAM free). With Off no process is renewed. At the VRAM limit fewer processes run straight away, as before when none had grown.

## 0.91 alpha — 2026-11-09

- **Fresh Nuke after every chunk** (Settings > Processes and Chunks): Off / When memory is short (default) / Always. A process closes after its chunk and a new one opens the script again in its place, with its RAM and VRAM back at the start; it starts at once, without waiting its turn to come back. When memory is short: only while a process waits for RAM or VRAM, or the PC is within 5 % of a limit. Never for the last chunks, when there is nothing left to hand out.
- Test this script: Add test and Edit test have the same field, so a setup can be compared with and without it; the table shows "never fresh" or "fresh every chunk", Use these settings sets it too. Setups saved before keep the setting as it is now.
- Measured on a heavy comp, 199 frames: 3 x 8 without it 9:43, 4 x 8 Always 8:00 (29 script loads), 4 x 8 When memory is short 7:43.

## 0.90.4 alpha — 2026-11-09

- Frames already on disk when a render opens show as on disk, skipped (dark green) straight away, not green as if this render had made them; Rendered lists nothing until the render runs. Frames this render made earlier stay green when the Framerange changes or frames are deleted.

## 0.90.3 alpha — 2026-11-09

- Rendering again after Stop with Only missing: the frames rendered before Stop show as on disk, skipped (dark green), like every other frame the new run does not render; Rendered lists only the new run's.

## 0.90.2 alpha — 2026-11-09

- Render asks Only missing / All frames again whenever the render has frames on disk, not only once it is finished: also a render that opens with some frames already there, and one you stopped. Before, those rendered the rest without asking. Frames found on disk when the render opened show dark green (skipped) after Only missing, like the rest.

## 0.90.1 alpha — 2026-11-09

- A process closed for RAM comes back when the RAM is free again. What the processes still starting are going to take counted the ones already going too: their RAM goes up and down under their highest, and that gap was added as RAM still to come ("and the ones starting 8.8 GB more" with none starting), so on a heavy comp a third process never came back at 63 % of 95. Now only processes opening the script or before their second frame count.

## 0.90 alpha — 2026-11-09

- Settings > General: Hide done chunks when rendering again is gone (the timeline always shows the new run), and so is Export render data (each render still writes its report.json in its log folder). Sound when a render ends sits under Start next render when one ends; thin lines set apart the checks and the rest. Delete old render logs keeps them 15 days by default instead of 7.
- Test this script's button is at the bottom of the Job card.
- No more note under Cache saying what the script took per process last time.

## 0.89.8 alpha — 2026-11-09

- System › State of a waiting process is short again: "wait: VRAM 75 → 69 %" (now 75 %, one more process fits under 69 %), "wait: 25 s", "wait: W03 1st frame", "wait: VRAM ok, checking". The tooltip keeps the whole sentence.

## 0.89.7 alpha — 2026-11-09

- System › State of a closed process says what it waits for: "waits for VRAM under 69 % (now 75 %)" instead of "wait: VRAM 75 % / 95 %", which read as if it waited for no reason (under the limit, one more process has to fit too). Also "waits 25 s (one just closed)", "waits for W03's first frame" and "VRAM 60 % fits: checking". The tooltip says it whole: the limit, how much one more process takes and what the ones starting will take; the same text goes into the log.

## 0.89.6 alpha — 2026-11-08

- Test this script: Winner, written last, also gets the width it needs.

## 0.89.5 alpha — 2026-11-08

- Test this script: the columns grow with what they show again. The last column stretching with the window counted as sizing a column by hand, which stopped them growing, so the times came out cut ("1:09 (24 of 2…").

## 0.89.4 alpha — 2026-11-08

- A full graphics card with no frame finishing is told from the last frame the render finished, not from when a process last started: a process started again would have hidden that nothing renders.
- Test this script counts the frames of a process that closed and started again (RAM or VRAM) as one lane, the restart included; before, the frames from before the restart dropped out of its frames per minute.

## 0.89.3 alpha — 2026-11-08

- VRAM per process is gone again, from Settings, from Add test / Edit test and from the worker. It cleared the Blink cache after frames (230 times in one render), yet the processes still grew to 7 GB within each frame and the card filled: the VRAM a frame needs while it renders is not what clearing frees. Setups saved with it come back without it.

## 0.89.2 alpha — 2026-11-08

- VRAM per process takes tenths of a GB (5.5 GB), in Settings and in Add test / Edit test; the arrows step by 0.5.

## 0.89.1 alpha — 2026-11-08

- Settings: VRAM per process sits in Processes and Chunks, under Cache and above Chunk.

## 0.89 alpha — 2026-11-08

- VRAM per process (Settings > Performance, off by default): a Nuke process holding more VRAM than this clears its Blink cache after its frame, the VRAM Nuke keeps but no longer needs (on one comp it grew 1.5 GB a frame, to 15 GB; clearing took it from 16.3 GB to 7.7). It goes on, nothing restarts. It costs time, so it is off: a heavy comp at 4 x 8 rendered in 11:49 with a 5 GB cap against 8:32 without. Not the RAM cache: clearing that frees no VRAM and made the next frame 8 x slower.
- Test this script: Add test and Edit test have VRAM per process too (off by default), so a setup can be tried with and without it side by side; the table shows it ("4 x 8, cache 13 GB, VRAM 6 GB"), and Use these settings sets it in Settings with the rest.

## 0.88.4 alpha — 2026-11-08

- A render no longer hangs with a full graphics card. On a heavy comp at 4 x 8 the card filled, Nuke fell back to the CPU and all four processes sat on one frame each for 8 minutes: one waited to be replaced after its chunk and the VRAM limit waited for that, but the chunk never ended. Now, with VRAM over the limit and no frame finished for 4 x the usual frame time (a minute at least), the process started last closes at once and its chunk goes back to the queue.
- The VRAM limit tooltip says the process started last closes at 97 % (since 0.88.2), not the one taking the most.

## 0.88.3 alpha — 2026-11-08

- After processes closed for RAM or VRAM again within 10 minutes, they come back after half the wait: 30, 60 and 150 s instead of 60, 120 and 300 (the first time still 5 s).

## 0.88.2 alpha — 2026-11-08

- VRAM limit like the RAM limit: when the card has room again, as many processes come back at once as fit under the limit (with what the ones still starting will take), not one at a time. On the edge of spilling into the RAM (97 %) the process started last closes at once, as with the RAM, instead of the one taking the most VRAM: it has the least work to lose.

## 0.88.1 alpha — 2026-11-08

- Test this script on a PC with more than 64 threads (a big Threadripper, a server): Windows splits the threads into processor groups of at most 64 and one process runs in one group, so a setup gives a process at most 64 threads and starts with enough processes to use every group (192 threads: from 3 x 64).
- The line about the PC says how many performance and efficiency cores an Intel 12th gen or later has, how many physical CPUs a server has, and how many processor groups there are, from Windows (GetLogicalProcessorInformationEx).

## 0.88 alpha — 2026-11-08

- Test this script: the cache of each setup comes from the whole RAM (its RAM limit, less what Windows usually holds) split by the number of processes, no longer from what is free when the window opens: with a render or a Nuke open, 8 x 4 got 1 GB of cache and lost for that. On 94 GB: 1 x 32 64 GB, 2 x 16 30, 4 x 8 13, 8 x 4 4.
- Create tests (above the table) looks at the PC, shown in a line under the buttons (CPU, cores and threads, RAM, graphics card), and makes 7 setups from 1 process with all the threads to as many as the CPU (at least 4 threads each) and the RAM (at least 4 GB of cache each) take: on 32 threads 1 x 32, 2 x 16, 3 x 10, 4 x 8, 5 x 6, 6 x 5, 8 x 4. They replace the setups in the table, your own too. The window opens with them as well.

## 0.87.7 alpha — 2026-11-08

- Go on after a crash is gone from Settings > General. After a crash the cards still come back the next time JKrender opens, with what is on disk counted; a render that was going waits for Render.

## 0.87.6 alpha — 2026-11-08

- Test this script: the column Slower is called Winner.

## 0.87.5 alpha — 2026-11-08

- Test this script: a right click on a setup offers Edit test, the window of Add test with its Processes, Threads and Cache; Save replaces the setup, which is then kept as one of your own. Not while a test runs.

## 0.87.4 alpha — 2026-11-08

- Test this script: no "Fastest for this script…" line under the table any more; the colours in the table say which is fastest, and the line still goes into the saved table. A line shows only when something went wrong (a frame that did not arrive in the test folder, no setup that finished a frame).

## 0.87.3 alpha — 2026-11-08

- Test this script: every setup of a round renders the same frames, as many as the setup with the most processes needs (Round 1: 3 a process of that one, Round 2: Frames per setup and at least 4 a process of the finalist with the most). Before, a setup with more processes rendered more frames (1 x 32 12, 6 x 6 18), which was confusing and compared them on different frames.

## 0.87.2 alpha — 2026-11-08

- Test this script: the column Average processes is called Avg processes. Columns can be made wider or narrower and dragged elsewhere; they grow with what they show until you size one yourself, and stay as you left them the next time the window opens.

## 0.87.1 alpha — 2026-11-08

- Test this script: Frames per setup sits top right, on the row of Add test and Remove test.

## 0.87 alpha — 2026-11-08

- Test this script: no text above the table, only Add test and Remove test. Add test opens a small window with Processes, Threads and Cache for a setup of your own (kept for next time); Remove test removes the selected setups, also when the table is sorted. The Processes / Threads / Cache boxes are gone from the bottom; Frames per setup stays there.

## 0.86.1 alpha — 2026-11-08

- Test this script: the column Running is called Average processes.

## 0.86 alpha — 2026-11-08

- Test this script's table: the quick and the final round are Round 1 and Round 2, each with its own column (how long it took, the frames done and the start) instead of one Took. A new column Slower says how much longer the whole shot would take than with the fastest setup, in time and per cent.
- The fastest setup is green and the next one orange. A click on a column sorts by it (numbers as numbers), a second click the other way; the test goes on writing into the right rows.
- Ran is called Running (processes rendering at once on average), and every column has a tooltip. The setups no longer say (yours) or (added).

## 0.85 alpha — 2026-11-08

- Test this script takes its frames from 6 places spread over the whole shot, from its first frame to its last, instead of the middle: a shot heavier at its start or end counts as it is. Every setup renders the same places, so they are compared on the same frames; at each a stretch of frames in a row (at least 2), as a render hands a process a chunk, so a node that looks at other frames is not always starting cold. A setup that needs more frames gets longer stretches.
- Frames per setup can be set above the shot's length: next to it, it says the shot has only N frames and every setup renders all of them, and so does the result.

## 0.84 alpha — 2026-11-08

- Test this script runs in two rounds: a quick one of 3 frames a process for every setup (3 minutes at most each), then only the two fastest, and any within 5 % of the best, with all the frames (Frames per setup, at least 4 a process). The slower ones keep their quick result. A test that took 20-30 minutes takes about a third of that.
- Frames per minute are counted once the processes are going: from the end of each process's first frame to the end of its last. The start of Nuke and the first frame (warming up, a time node filling its cache) no longer count, as they come once in a whole render; before, they made the light scripts look fastest with one process and a heavy frame slower than it is. The start shows in Took.

## 0.83 alpha — 2026-11-08

- Analyze script is gone, with its button in Settings: its recommendation did not hold on real comps. Its commands left the worker too (ANALYZE, TIMERS, PROFILE). Test this script stays: it measures the setups instead of guessing them.

## 0.82.1 alpha — 2026-11-08

- Analyze script no longer counts Nuke's cache as RAM a process needs. Nuke fills the cache it is given (16 GB in the analysis), so a heavy comp that showed 23 GB was held to 2 x 16, while 4 x 8 with 12 GB of cache each rendered faster and kept the CPU busier. It now reads how much of the RAM is cache and allows the rest plus at least 4 GB of cache (8 GB on a script of 60 nodes or more). On the 25 test scripts its picks stay as good: 3 % off the fastest on average.

## 0.82 alpha — 2026-11-08

- Analyze script recommends from an estimate of frames per minute for each Processes x Threads setup of this CPU, shown in a new table: how many threads one Nuke keeps busy (a script that keeps nearly all busy gains nothing from more processes), what processes lose sharing the memory and the one graphics card, and what fits in the RAM and the VRAM. Fitted on 24 scripts measured with Test this script (light and heavy comps, CPU and GPU nodes, 3D, time nodes, reading from disk): its pick was within 5 % of the fastest on 19 and 3 % off on average, against 15 % before. The rule before nearly always said 8 x 4; on a TimeBlur over a heavy tree that rendered no frame in 6 minutes.
- When its frames are quick (more than 180 a minute in any setup) it picks the setup with the fewest processes: they all render about as fast, and it takes the least RAM.
- One slow frame says little about the RAM a script takes later, so with fewer than 3 frames over 20 s it allows for three times as much.
- When Nuke goes down during the analysis, the frames it rendered until then still count, and it says so. Before, everything measured was lost.

## 0.81.1 alpha — 2026-11-08

- A script that Nuke opens with errors (a knob it cannot read, such as a node saved by another version) renders as Nuke opened it, like the GUI does, as long as its Writes are there. Before, every process stopped with "Nuke did not open the script" and not one frame rendered. The message goes to the process log.

## 0.81 alpha — 2026-11-06

- Render again on a render with frames on disk asks: **Only missing** renders just the frames missing or broken on disk, **All frames again** renders every frame anew, Cancel leaves it. A movie is always rendered whole.
- Frames a render finds on disk and does not render show dark green (on disk, skipped) on the frame map, which has it in its legend now. Rendered lists only what the run rendered, not the skipped frames.

## 0.80.2 alpha — 2026-11-06

- The JKrender menu in Nuke no longer has Reopen Renders After a Crash: the renders come back the next time JKrender opens.
- Rendering an unsaved script asks only "Save it and render?", without the note in brackets.

## 0.80.1 alpha — 2026-11-06

- Render again no longer empties Output: frames still on disk stay listed as `on disk, queued again`, with their size and time, and can be looked at and played until the new frame replaces them. The same for Render this frame / batch and a chunk rendered again. Rendered lists only the frames of the run now.

## 0.80 alpha — 2026-11-06

- Check pixels reads the pixels of PNG, JPEG, TIFF and DPX too (8, 10, 12 and 16 bit DPX as Nuke writes it): black, empty and one-colour frames, and a size, channels or bit depth unlike the rest. Identical frames are found by their pixels, so two the same with another time in the file count as the same. Before, only EXR was looked at; a black PNG passed.
- The banner of a finished render follows Check frames and Check pixels run by hand: a broken frame turns it red instead of leaving "pass the check", and it says "Checking frames…" meanwhile.
- A Czech manual: docs/JKrender_navod.pdf.

## 0.79.1 alpha — 2026-11-06

- The sound of a finished render comes when the checks that are on are through: right after the render with them off, after the frame check, and with the pixel check on after that. When the pixel check finds a problem it is the error sound.

## 0.79 alpha — 2026-11-05

- Analyze script and Measure nodes are one: Analyze script. One Nuke with all threads opens the script, says what feeds the Write, renders frames from the middle, 90 s at most in all, read every 0.5 s, then one more frame with Nuke's timers on when it fits in the time (they slow a heavy comp about 6 times, so the measured frames go without them). It shows how long Nuke took to start and the script to open, the first and the other frames, the threads it kept busy, the GPU use, RAM and VRAM and how the VRAM grows per frame, the slowest nodes, and recommends Processes, Threads and Cache with the reasons. What it found is kept as text in the test results folder.
- The Measure nodes button is gone.

## 0.78 alpha — 2026-11-05

- report.json of every render also holds the PC (CPU, threads, RAM, graphics card and VRAM, Windows, an id made from the PC's name), the settings, frames per minute, the median frame time, the most processes at once, RAM and VRAM peaks, processes closed by the RAM or VRAM limit, crashes, how long Nuke took to start and the script to open, the frames with pixel problems, the resolution and how many nodes of each class the script has.
- Settings > General > Export render data: all those reports in one file, without script names, paths or errors. A script is known by an id that is the same for all its versions.

## 0.77.3 alpha — 2026-11-05

- The slate frame is never flagged as much smaller than the rest, and does not count when the others are measured.

## 0.77.2 alpha — 2026-11-05

- The Check frames tile counts frames found broken on disk too; it said ok next to them.
- The banner of a finished render says only what was checked: with Check frames as they render off it says how many frames were not checked, in yellow; when the pixel check ends and found NaN, inf, black, duplicated or different frames, the banner turns red and says how many. While the pixel check runs it says so.

## 0.77.1 alpha — 2026-11-05

- Every line of a Nuke process's log has the time it came, and what start-up scripts print comes when they print it.
- The log says where a process's start went: `W08 ready in 60.5 s: Nuke started in 60.3 s, script opened in 0.18 s`. More than 15 s of Nuke starting is a warning. "Script opened in" used to count the whole start.

## 0.77 alpha — 2026-11-05

- The frame preview zooms like the movie player: mouse wheel zooms around the cursor, drag moves, double-click fits the frame again (a second double-click makes the preview big). Zoomed in, the frame is shown at its own size, so the pixels are real, and the zoom stays when you go to the next frame. Playing shows frames at the preview's size and the real pixels come back when it stops.
- Settings: the card Frames is now called Job.

## 0.76.9 alpha — 2026-11-05

- Output has a fourth filter, Framerange: every frame of the render's Framerange, also those not rendered yet, with how each one stands. A frame still to render says `not on disk`, a broken one found on disk `broken on disk` in red. Problems now lists such a frame too.

## 0.76.8 alpha — 2026-11-05

- The row of the Render button says Framerange instead of Frames, like Settings.

## 0.76.7 alpha — 2026-11-05

- The window opens at 2560 x 1369, or as much of the screen as there is, with new sizes for its panes. Output's Check frames column is a little wider.

## 0.76.6 alpha — 2026-11-05

- System comes with new column widths: Process 53, Render 225, State 273, Cache 92 and Frames 62 px.

## 0.76.5 alpha — 2026-11-05

- System comes with Frame and Peak RAM off as well. Tick them in its header to see them.

## 0.76.4 alpha — 2026-11-05

- System comes with a new order of columns: Process, Render, State, CPU used, RAM, Cache, GPU, Frames, Last frame, Avg frame, Script load, Uptime, Chunk, Chunks, Frame, Peak RAM. PID is off, the others are on, and State is wider. Reset to defaults goes back to this.
- Settings: the card Jobs is now called Frames, and its Frames is now called Framerange. A frame left out of it shows as `not in Framerange`.

## 0.76.3 alpha — 2026-11-05

- Changing Frames or the slate no longer takes frames on disk out of Output. Those the range leaves out stay in the list as `not in Frames`, can be previewed and deleted, and nothing renders them. Rendered frames keep their process, time, chunk and pixel check.

## 0.76.2 alpha — 2026-11-05

- Settings: a new card Jobs, with Slate frame and Frames, sits over Performance and Optimization. Test this script, Analyze script and Measure nodes moved to Performance and Optimization. The other card is Processes and Chunks again.

## 0.76.1 alpha — 2026-11-05

- Output starts empty: a frame comes into the list when it renders or is found on disk, so changing Frames or the slate does not fill or reorder it. What you selected stays selected while frames come in.
- Chunks start at C01 again after the chunk size changes before a render, and a frame found on disk shows no chunk.

## 0.76 alpha — 2026-11-05

- Frames can be changed: first item of the card, now called Job, Processes and Chunks. Every render has its own, kept when you switch cards and after a crash. A slate frame comes on top of them.
- The row of the Render button: FrameRanges is now Frames and shows them without the slate frame; Chunk shows only its size.
- Nuke cache is now called Cache.

## 0.75 alpha — 2026-11-05

- The row of the Render button shows, to read: the slate frame or off before FrameRanges; Processes, Threads, Nuke cache and Chunk after it. They change in Settings.

## 0.74.1 alpha — 2026-11-05

- Processes, Threads, Nuke cache, Chunk, Smaller chunks at the end, Test this script, Analyze script and Measure nodes moved back from the row of the Render button to Settings, in the Processes and Chunks card.

## 0.74 alpha — 2026-11-05

- Right-click in System or on the Workers timeline: Add process starts one more process in a running render, Close after this chunk lets one finish its chunk and close. Processes follows and stays at the new number. With several renders the added or closed place stays with the render you picked.
- Fix: starting a process by hand past a RAM or VRAM limit did nothing while another process you closed held a place. System no longer says a process is coming back when the free place is held for one you closed.

## 0.73.4 alpha — 2026-11-05

- The sound of a finished render is now a stronger, happier ta-da: two firm notes and a full major chord, louder than before. The error sound stays.

## 0.73.3 alpha — 2026-11-05

- The sound of a finished render is now a short cheerful run of rising notes instead of the drums. The error sound stays.

## 0.73.2 alpha — 2026-11-05

- The sound of a finished render is now a drum fill ending on a kick and a crash. The error sound stays.

## 0.73.1 alpha — 2026-11-05

- Sound when a render ends: JKrender's own two sounds instead of a list of Windows' sounds. A short modern chime when a render finishes, an error sound when it fails or leaves broken frames. Settings keeps only the on/off switch.

## 0.73 alpha — 2026-11-05

- Settings > General: Sound when a render ends, on by default, with a choice of Windows' sounds and a Play button. It plays when a render finishes or fails. A render you stopped and a render already on disk when it opens stay silent.

## 0.72.1 alpha — 2026-11-05

- Clearing the cache of open Nukes now frees their buffers (Cache > Clear Buffers, F12), which is what fills the RAM. Until now it cleared only the playback cache and the Blink cache. An open Nuke has to be restarted once to take part.

## 0.72 alpha — 2026-11-05

- Processes, Threads, Nuke cache, Chunk, Smaller chunks at the end, Test this script, Analyze script and Measure nodes moved from Settings to the row of the Render button. In a narrow window the row goes on in a second line.

## 0.71.1 alpha — 2026-11-05

- The graphs and CPU threads in System are as high as before 0.71 again. The smallest window is about 780 x 840.

## 0.71 alpha — 2026-11-04

- The window shrinks: its smallest size went from about 1740 x 990 to about 780 x 755, so it fits a Full HD monitor with room to spare, with any number of renders.
- Settings: the cards stand side by side as far as they fit, the rest under them, and the tab scrolls.
- The render cards scroll sideways when they do not fit, and a new one scrolls into view.
- Tiles take the width they get; what does not fit is in the tooltip. The preview, graphs and timeline can get smaller.

## 0.70.9 alpha — 2026-11-04

- System table: what a closed process waits for is shorter, like next try 5 s or wait: RAM 99 % / 95 %. The tooltip has it in full.

## 0.70.8 alpha — 2026-11-04

- System table: a closed process that will start again no longer just says closed. In yellow, State says it is trying to come back and what it waits for: the seconds to the next try, RAM or VRAM against the limit, that the memory stays free, or the process before it. The column grows to show it. A process you closed by hand, or with no chunks left, stays closed.

## 0.70.7 alpha — 2026-11-04

- A process that closed for memory comes back sooner: the wait after the first fall is 5 s instead of 10, and RAM or VRAM has to stay free for 5 s instead of 15. When it happens again the wait is still 60, 120 and 300 s, and processes still come back one at a time.

## 0.70.6 alpha — 2026-11-04

- Check pixels when a render opens: a new setting, on by default. Frames already on disk get the pixel check in the background as soon as the card opens. The frame check already ran at that moment.
- Render stops a pixel check that is under way, as some of those frames may be about to be written. The check at the end covers them all.
- Reset to defaults now also covers Check frames as they render.

## 0.70.5 alpha — 2026-11-04

- After a process closes for memory, the next may come back after 10 s instead of 30. When it happens again the wait is still 60, 120 and 300 s.

## 0.70.4 alpha — 2026-11-04

- Render no longer clears the cache of open Nukes without asking. With a Nuke open it asks Yes or No, No by default, and the render starts either way. With no Nuke open it does not ask.
- The next render starting by itself and a render resuming after a crash no longer clear anything: nobody is there to answer.

## 0.70.3 alpha — 2026-11-04

- Analyze no longer takes a script that fills the cache of its short render as needing all of it: past 13 GB per process more cache does not cut the recommended processes. That gave 2 x 16 where 4 x 8 is faster.

## 0.70.2 alpha — 2026-11-04

- The Check column of the frame list is split into Check frames and Check pixels. The widths saved for that list start afresh once.

## 0.70.1 alpha — 2026-11-04

- A movie shows one Check video tile, and its button is Check video.

## 0.70 alpha — 2026-11-04

- Check all again is now Check frames.
- Settings > General: Check frames as they render, on by default. Off, a broken frame counts as done until you press Check frames; Render still checks what is on disk before it starts.
- The Output check tile is split into Check frames and Check pixels, each with its own state.

## 0.69.1 alpha — 2026-11-04

- Check pixels shows a progress bar under the Output summary while it runs, and its progress in the Output check tile.

## 0.69 alpha — 2026-11-03

- Measure nodes shows which nodes have GPU on, and the GPU use, VRAM peak and RAM peak of the whole test. Nuke counts these for no node, and its timers hold no time for work on the GPU, so they are not given per node.

## 0.68 alpha — 2026-11-03

- Check pixels also finds black, empty and one-colour frames, and frames whose size, channels, bit depth or compression differ from the rest of the sequence. All shown in red; it only reports.
- Measured: 702 frames at 4K in 55 s, with no false finding of these. Every 8th pixel is looked at first, so a normal frame costs next to nothing more.

## 0.67.1 alpha — 2026-11-03

- Duplicate frames show in red, like NaN and inf: in the chunk map, the frame list, the Output check tile and the Log.

## 0.67 alpha — 2026-11-03

- Output check looks at the pixels: NaN and inf in every frame, and frames exactly the same as another anywhere in the sequence. Duplicates are found by the pixels, as an EXR's header differs from frame to frame.
- It runs in the background when a render ends (Settings > General > Check pixels when a render ends, on by default) and with the Check pixels button in Output: about 0.1 s a 4K frame, 702 frames in about a minute. It only reports: Problems lists the frames, the chunk map marks them, the Log has them. EXR needs numpy and OpenEXR.

## 0.66.1 alpha — 2026-11-03

- Rendering a card again starts all its processes at once. Since 0.65 it started one, and the others about 10 s apart, each after the one before had rendered a frame: the render still knew the single place it had on its last chunks, so the rule for processes coming back took over from the start.
- A render starting when nothing runs no longer inherits the lower process count and the waiting from the one before.
- A render that gets more places later, when another ends, takes them without waiting. The rule for coming back starts when a process falls.

## 0.66 alpha — 2026-11-03

- Analyze script recommends from a short render instead of the number of nodes: a few frames in one Nuke to a test folder, 11 to 56 s on the scripts tried. As many processes as fit in the VRAM, counting one that keeps growing, and in the RAM with the cache the script takes.
- On the 3 scripts compared it now picks the fastest setup measured: 8 x 4 at 262 frames / min on a light script, 1 x 32 at 15.7 on one whose VRAM keeps growing (4 x 8: 4.3), 4 x 8 at 16 on a heavy one (1 x 32: 15.7, 5 x 6: 11.9). With a render going it uses the number of nodes.

## 0.65.4 alpha — 2026-11-03

- Measure nodes no longer looks at VRAM: it shows the frame time and the slowest nodes.

## 0.65.3 alpha — 2026-11-03

- Settings > Measure nodes, under Analyze script: 6 frames in one Nuke to a test folder, then the slowest nodes from Nuke's timers and whether the VRAM keeps growing. Nothing is saved or changed.
- When 2 processes close for VRAM within 5 minutes, the render says that fewer processes with more threads may be much faster. It changes nothing by itself.

## 0.65.2 alpha — 2026-11-03

- With the card nearly full (97 %), the process taking the most VRAM closes at once, not the last one started, which frees little next to one that has grown to 15 GB. Measured on a script whose VRAM keeps growing, 4 x 8: 4.1 frames / min against 4.3, so no faster, 4 such closes against 5. One process with all threads stays far the fastest for it (15.7).
- Tested with the RAM really full: another program pushed the PC to 97 % during a render. Two processes closed, the page file did not grow, nothing crashed, all frames rendered, and they came back within about 20 s of the memory coming free.

## 0.65.1 alpha — 2026-11-03

- Processes closing one after another within a minute count as one overload. Each counted on its own, so one overload made the next wait 5 minutes with the RAM free. Measured: 5 processes back to 8 about 30 s after the memory came free, one at a time.

## 0.65 alpha — 2026-11-03

- A process comes back only if it really fits. The old count took a process just restarted at 2 GB as full size, so three restarts let 8 back where 1 fitted. Now the RAM left under the limit, less a 2 % margin and what the running processes will still take, must hold one process of the size seen this run (before that, its cache + 4 GB).
- They come back one at a time, each after the one before has rendered a frame. After one closes for memory the next waits 30 s, then 60, 120 and 300 s if they keep closing; after 10 minutes without one it is forgotten.
- After a crash the cards come back the next time JKrender opens, or with JKrender > Reopen Renders After a Crash in Nuke (a Nuke needs a restart for the menu). What is on disk counts, and Nuke processes left behind are ended, only ones with the noted start time.
- Settings > General > Go on after a crash (off by default): the renders that were going start again by themselves. Off, they wait for Render.

## 0.64.5 alpha — 2026-11-02

- One more project detail out of this changelog.

## 0.64.4 alpha — 2026-11-02

- Names of projects and studio tools are out of the code comments, the README and this changelog. The README picture no longer shows a file path.

## 0.64.3 alpha — 2026-11-02

From running real small renders through the window:

- A script that cannot render stops itself: after 3 failed chunks with no frame rendered in the run, instead of three fresh Nukes for every chunk. A missing file on 67 chunks took 10 Nuke starts instead of about 200.
- Test this script and Analyze script no longer hide Qt's window() with their own attribute. A key or a click on such a dialog itself could raise an error and bring JKrender down.

## 0.64.2 alpha — 2026-11-02

From reading the rest of the application:

- Frame time and × at once count only frames of this run. Frames from a run before made 8 processes show ×17 and a frame time from other settings.
- A process that does not quit within 8 s is ended in every state. While a render was finishing, or after it, one could stay and hold its memory.
- The settings file is written whole or not at all; a crash mid-write could bring every setting back to its default.
- Closing the window also closes an open Test this script or Analyze script, with their Nukes.
- Details shows the Nuke command with --gpu when processes use it.
- Render this frame on an idle render also starts its crash count at zero.
- Removed more that nothing used, such as the render-current-frame path in the Nuke menu.

## 0.64.1 alpha — 2026-11-02

From a review of the scheduler and the unused code:

- A new run starts with its crash count at zero. After Nuke keeps crashing stopped a render, its next run stopped at its first crash.
- A process is recycled for RAM only above its cache plus 8 GB and 1.5 x its first chunk. Before, a normal process (cache + 4 GB) could pass the 1.3 x cache mark after a small first chunk.
- System shows CPU used in threads, like everywhere else.
- Removed what nothing used: the hidden Render all and Pause all, Create Read in Nuke, a log filter value.

## 0.64 alpha — 2026-11-02

- One frame per fresh Nuke is gone. It started by mistake on a light script, whose processes simply warm up from 1 GB to 2.3 GB of VRAM, and spent 3.3 s on a script load for each 0.7 s frame with the card at 39 %.
- A process is no longer restarted for going over its share of the VRAM while the card has room. Only close to the VRAM limit does the process that grew the most get a fresh Nuke after its chunk.

## 0.63.2 alpha — 2026-11-02

- Analyze script names the script and Write at the top, and its texts fit any script instead of naming measured ones. Every number has a short explanation.
- The recommendation no longer depends on the RAM free at that moment, which could give 1 process with 1 GB of cache while something else was running. It counts from the PC's RAM, the RAM limit and what Windows and other programs usually hold.

## 0.63.1 alpha — 2026-11-02

- The Analyze script window is laid out to read: the recommended Processes, Threads and Nuke cache big at the top, next to your current ones, with the reason and Use these settings. Below it tiles for nodes, format, Reads and big Blurs, then lists of nodes on the GPU, nodes that look at other frames and the most used ones.

## 0.63 alpha — 2026-11-02

- Settings > General has Start next render when one ends (off by default): when a render finishes, the next one on the cards that has not been started starts by itself, one after another. Failed renders also pass on to the next. A render you stopped stays stopped and does not start another.
- Analyze script moved from the test window to Settings, right under Test this script, and opens its own window.

## 0.62 alpha — 2026-11-02

- Test this script has an Analyze script button: a Nuke opens the script without rendering, goes through the nodes that really feed this Write (disabled branches left out), and reports their number, Reads, big Blurs, nodes on the GPU, nodes that look at other frames and the most used ones. It recommends processes, threads and Nuke cache to start from; Use these settings takes them. It is a guess from a few measured scripts, and Start measures it.
- Render clears the RAM cache and Blink cache of every open Nuke before the render starts. The disk cache stays. Resume does not clear. A Nuke has to be restarted once to get this.

## 0.61.1 alpha — 2026-11-02

- Over the RAM or VRAM limit, the last process started closes first, whatever it is doing: one still opening the script closes at once, one rendering after its chunk. A process with more work behind it stays. Under the VRAM limit this replaces closing the one taking the most VRAM.
- The same goes for the emergency close near full memory, which now also takes processes still opening the script.
- One you started by hand still closes last. Places handed to another render go gently as before.

## 0.61 alpha — 2026-11-02

- Render starts all its processes straight away. The Start at once setting and the wait to see how much VRAM one process takes are gone.
- A process that fell comes back by itself as soon as it fits: the RAM left under the limit, split by what one process takes. Before a process has rendered a frame, that is its Nuke cache + 4 GB. It also has to fit in the VRAM.
- The RAM and VRAM limits still close processes when they are reached.
- An empty place in System says "not running: why" instead of "waiting".

## 0.60.5 alpha — 2026-11-02

- Space plays and stops the preview. It no longer presses the button clicked last, which could stop a render.

## 0.60.4 alpha — 2026-11-02

- Test this script no longer recommends 10 x 3; it can still be added by hand.

## 0.60.3 alpha — 2026-11-02

Test this script wrote its frames to the render's own folder on scripts whose Write has a beforeRender hook that sets the path again (0 of 20 frames, and a recommendation at 0.0 frames / min).

- In a test, the Write's render hooks do not run and its frames stay in the test folder, also against a callback the pipeline adds at start-up. Only in the test's own Nuke processes: nothing is saved, and real renders still run the hooks.
- A frame that does not arrive in the test folder stops the test at once, with a warning to check the render's folder.
- A setup with no frames is never recommended.
- The test table's columns fit their content all the time and the window grows to show them, with shorter texts.

## 0.60.2 alpha — 2026-11-02

- Test this script: Frames per setup is 20 by default and can be set to any number, also fewer.

## 0.60.1 alpha — 2026-11-02

- Test this script: at least 20 frames per setup (or the whole render, when it is shorter).

## 0.60 alpha — 2026-11-02

Test this script:
- 10 frames per setup by default (Frames per setup sets it), at least 2 per process. Frames per minute now count from when the script is open: with so few frames, counting from the first finished frame favoured setups with many processes.
- Fewer recommended setups: on 32 threads 1x32, 2x16, 4x8, 6x6, 8x4, 10x3, and your own settings.
- Add setup with your own Processes, Threads and Nuke cache, kept for next time; Remove selected takes out any row. Setups you add are never left out.
- A setup stops at 1.5 x the best time per frame (with so few frames, 1.25 x could stop one still starting up).

## 0.59 alpha — 2026-11-02

Test this script, after a full run on a heavy comp (40 frames): 2-6 processes rendered them in about 2 minutes; 8 x 4 with 4 GB of cache took 69 s a frame, and 10 x 3, 16 x 2 and 32 x 1 with 2 GB and 1 GB did not finish one frame in 6 minutes each.

- Setups go in order of the number of processes.
- Once a setup has rendered the frames, another stops when it has taken 25 % longer: it cannot win. After two like that in a row, the setups with still more processes are left out. Your own settings always run.
- A planned setup with your processes and threads and about your cache is not run twice.
- The recommendation comes from setups that rendered all the frames.
- The table is saved to ~/.jkrender/test_results.

## 0.58.3 alpha — 2026-11-02

- Test this script takes its setups from the CPU's threads: 64 threads test 1x64, 64x1, 2x32, 32x2, 4x16, 16x4, 6x12, 10x6; 16 threads 1x16, 16x1, 2x8, 8x2, 4x4, 6x3, 10x2.
- CPU average also in threads, in the test and in the System tile ("average 39 % · 12.5 threads").

## 0.58.2 alpha — 2026-11-02

- Test this script: RAM avg and VRAM avg show the average in % and in GB, not the maximum.

## 0.58.1 alpha — 2026-11-02

- Test this script: a Took column, how long each setup's whole render of the frames took, script loads included (with how far it got when the time limit ended it).

## 0.58 alpha — 2026-11-02

- Test this script tests 1x32, 32x1, 2x16, 16x2, 4x8, 8x4, 6x6 and 10x3 (processes x threads, scaled to the CPU), and your own settings. The Nuke cache of each comes from the PC's RAM: what is free up to the RAM limit, split by the number of processes, less 4 GB per process.
- Every setup renders the same frames from the middle of the render: a quarter of it, at least 30. At most 6 minutes a setup.
- The table shows average CPU, RAM, GPU and VRAM, with the most RAM and VRAM in brackets.

## 0.57.2 alpha — 2026-11-02

- Settings > Processes goes up to the CPU's thread count (32 here), not half of it.

## 0.57.1 alpha — 2026-11-02

- System: the CPU, RAM and GPU tiles show the average while rendering ("average 62 %", the GPU tile also VRAM). A pause does not count; the numbers stay after the renders end, until the next ones start.

## 0.57 alpha — 2026-11-02

- Settings > Processes and Chunks > Test this script: the selected render's script rendered with a few Processes x Threads x Nuke cache setups, 2-6 minutes each. A table shows the processes that ran, frames per minute, frame time, RAM, VRAM and how many fell; it recommends the fastest, and Use these settings takes it. The frames go to a test folder and are deleted; the render's own files stay as they are.
- Settings > Start at once (default 6): GPU processes a render starts before it has seen how much VRAM one takes. It was fixed at 3.
- One frame per Nuke switches on as soon as a script's Nuke shows it grows and the card is within 10 % of the VRAM limit, and the limit then no longer lowers the number of processes: closed places fill again when one more fits. Before, the limit could go down to 2 and processes stayed closed until started by hand.
- Workers: a fresh Nuke for the VRAM is a darker green block; other script loads stay grey.
- "One more Nuke process would not fit" shows the card's real VRAM, with the processes still starting named apart (it could say 134 %).

## 0.56 alpha — 2026-11-01

- System: every place of Settings > Processes has a row (W01-W08), also the ones no process has taken. Their State says why: "not running", "waiting: VRAM 88 %, limit 95 % · NukeX (pid 39760) holds 16.5 GB of it", "not running (RAM limit)", "not needed (no chunks left)". Right-click one to start a process there by hand in a render still going (Start in).

## 0.55.3 alpha — 2026-11-01

- The VRAM emergency close never closes the last GPU process. With NukeX holding 16.5 GB of the card, the one process left was closed at 97 % every minute and started again, throwing its chunk away each time.
- When the VRAM keeps processes out, the log says who holds it: "VRAM 83 %, limit 95 %; NukeX (pid 39760) holds 16.5 GB of it".

## 0.55.2 alpha — 2026-11-01

- Fix: a script whose path has Czech letters (D:/Práce/Žluťoučký kůň/...) did not render: Nuke reads its command line in the Windows code page, so the original script's path reached the worker broken and every Nuke stopped while opening the script. JKrender now passes it as UTF-8 too.
- A Nuke that stops while opening the script is logged with its own error, not a guess from its log (which said "Missing file" because the script also names a missing Alembic camera).

## 0.55.1 alpha — 2026-11-01

- Workers: a process started again under its name (a fresh Nuke for the VRAM, the RAM, or by hand) shows its script load as a grey block, from when the previous one finished; before, the timeline had gaps there. The tooltip says why it loaded again.
- Tried and left out: `nuke.clearBlinkCache()` after every frame. A GPU-heavy script's Nuke then grew 0.6 GB per frame instead of 1.5 GB, but every frame took 3 s longer, about what a fresh Nuke costs, and it still grew.

## 0.55 alpha — 2026-11-01

- One frame per Nuke when the VRAM keeps processes out: a script whose Nuke takes more VRAM with every frame (3 GB after one frame, 9 GB after six) switches to one-frame chunks, each process making way for a fresh Nuke after its frame. A fresh Nuke stays small, so more of them fit on the card; a script load per frame (3-5 s) is the price. 1.nk and sh0050, whose VRAM stays flat, never switch.
- A script not seen yet in the run starts three GPU processes, the rest once one has finished a chunk (all eight at once on a GPU-heavy script meant five closed mid-chunk).

Tested, a GPU-heavy script alone with 8 processes: 10:58 → 7:27 (3 → 4 processes on average, frames 34 s → 28 s since a fresh Nuke does not slow down). All three renders at once: 16:06 → 11:29, one process closed mid-chunk instead of three.

## 0.54 alpha — 2026-11-01

Tested on three real renders all at once, one after another, and with processes moved between them.

- A GPU process whose VRAM keeps growing gets a fresh Nuke after its chunk. Nuke keeps VRAM it no longer needs: one script took 5 GB after its first chunk and 16 GB later, its frames 25 s at first and 143 s at the end. Recycled when it grows past its part of the card, or from 5 % below the VRAM limit the one that grew the most. Only when none grew does the VRAM limit run fewer processes. Changing Nuke's GPU cache preference does not stop the growth (tested).
- VRAM other programs hold is now measured while none of JKrender's processes run. Before, it was guessed from the per-process counters, which miss part of Nuke's own VRAM, so 7 GB of JKrender's own showed as "other programs" and the limit stopped working.
- A new process is counted at the VRAM its script's processes took after their first chunk, not at what one grew to (one grown process blocked every new start, and for 15 minutes one process rendered alone). Processes still opening the script count at full size, and for a script not seen yet in this run they start one at a time once the card is half full.
- Start again on a closed process of a finished render starts it in a render still going ("Start again in"); before, it was greyed out. A start by hand goes past the RAM and VRAM limits, and says so when all places are taken.
- The VRAM limit message no longer blames other programs when it is JKrender's processes that grew.
- System > Load: a VRAM curve.

Test results, all three renders (minutes): all at once 20+ (the heavy one half done) → 16; one after another 24 → 19; processes closed mid-chunk 6 → 3-4 per run.

## 0.53.1 alpha — 2026-11-01

- A new Nuke process starts only when it fits: in the RAM, counting what the ones still opening the script will take (from what the script took last time), and in the graphics card's memory. Before, all of them started at once and a heavy script pushed the RAM to 100 %, so one was killed mid-chunk straight away (8 × 12 GB on a 94 GB PC). The log says once a minute when one more does not fit.
- Start on a closed process now gets the place in its own render; before, the extra place could go to another render first.
- The RAM and VRAM emergency closes never fire in the same few seconds, so one shortage does not close two processes.

## 0.53 alpha — 2026-11-01

- Fix: 0.52.1 and 0.52.2 did not open once column widths had been saved (the Frames table check failed).
- The All renders tile also shows how long the renders have taken so far ("finishes at 19:50 · 1:40 so far · 47 frames"). Once all are through it stays, as "ALL RENDERS TOOK 1:38" (green when all are done), from the first one starting to the last one ending, until the next render starts.

## 0.52.2 alpha — 2026-11-01

- Start on a closed process now works under the VRAM limit too (it only went past the RAM limit before: the log said "starts again" but nothing started). The RAM and VRAM limits then leave it alone for 90 s.
- The VRAM limit no longer takes processes away when that does not help: when other programs hold the graphics card's memory, or the last process closed for the limit freed almost none of it, JKrender keeps the processes it has and says so in the log. Only at 97 % does one close at once, as before.

## 0.52.1 alpha — 2026-11-01

- System: the Mode column is gone (every process uses the GPU when Use GPU is on). Column widths saved with a different number of columns are dropped instead of breaking the header; hidden columns and their order stay.
- Delete in Output: "All frames" is always picked first; frames selected in the list are still filled in under "Frames".

## 0.52 alpha — 2026-11-01

- No more CPU mode: with Use GPU on, every Nuke process uses the GPU. The VRAM limit works like the RAM limit again: over it, one process fewer at a time, the one taking the most VRAM finishing its chunk and closing ("busy → closes (VRAM limit)"); places come back when one more fits. Only about to spill into the RAM (97 %) does a process close at once.
- VRAM limit default 95 % (was 90 %).

## 0.51 alpha — 2026-11-01

- VRAM shares are shared: what one process leaves unused, another may take. A process may go over its share while the card is under the VRAM limit. At the limit, the process furthest over its share finishes its chunk and goes on without the GPU, one at a time.
- The VRAM emergency close (a process killed mid-chunk) now only happens at 97 %, when VRAM is about to spill into the RAM, no longer at limit + 5 %.

## 0.50 alpha — 2026-11-01

- VRAM limit, simpler: every Nuke process gets an equal share of it (24 GB × 90 % / 8 processes = 2.7 GB each). A GPU process that takes more than its share finishes its chunk and goes on without the GPU until the renders end. Nothing switches back and forth any more. Close to full (limit + 5 %) the GPU process taking the most closes at once and comes back without the GPU.
- System, Mode: GPU shows its share ("GPU · share 2.7 GB"); one moved off it shows "CPU (over VRAM share)".

## 0.49.2 alpha — 2026-11-01

- The tile right of the cards is headed All renders done in.

## 0.49.1 alpha — 2026-11-01

- The tile right of the cards is now Remaining for all renders: the time left is the big number; under it the finish time, frames left and how many renders.

## 0.49 alpha — 2026-11-01

- An All renders tile right of the render cards: when every render still rendering or waiting should be done ("done ≈ 14:32", or tomorrow), the time and frames left, how many renders, and a bar with the percentage. It counts the frames left at the speed all Nuke processes render together now, never sooner than a single render on its own. Hidden when nothing renders.

## 0.48 alpha — 2026-11-01

- The column widths and order of every table (Output frames, Chunks, System) are saved when the window closes and come back next time. Settings > Reset to defaults puts them back too.
- Chunks: the columns are fitted once, no longer again each time you pick another render.
- System, State: a process closing because of the RAM limit says so: "busy → closes (RAM limit)" while it finishes its chunk, then "closed (RAM limit)". One closed at once by the RAM or VRAM limit shows "closed (RAM limit)" or "closed (VRAM limit)".

## 0.47 alpha — 2026-11-01

- System: change the order of the columns. In the header menu each column has ▲ ▼ arrows (the menu stays open), or drag a column by its header. The order is remembered.

## 0.46 alpha — 2026-11-01

- A render whose whole frame range is already on disk and passes the check opens as Done: green card, Render again on the button, and the banner says Already rendered. The same after changing the slate or deleting frames. Frames are checked when the render is added, as before.

## 0.45.1 alpha — 2026-11-01

- Fix: two renders could run a process under the same number at once (two W03s on one Workers row). When a render restarts a closed process (after the VRAM or RAM limit closed it), it no longer reuses a number another render has started meanwhile; it takes a free one.

## 0.45 alpha — 2026-11-01

- System: five new columns, off until ticked in the header menu: Mode (CPU, GPU, or GPU → CPU when the VRAM limit moved it), Uptime, Peak RAM (the most the process took), Chunks (done, and how many went back to the queue) and Last frame.

## 0.44 alpha — 2026-11-01

- System: click (or right-click) the table header for a list of columns with tick boxes; untick to hide one. The menu stays open while ticking, and the choice is remembered. Process always shows.

## 0.43 alpha — 2026-11-01

- Settings > General: Hide done chunks when rendering again (on by default). When a render is rendered again, the Workers timeline shows only the new run, without the earlier run's done chunks and their script loading. Turn it off to keep them.

## 0.42 alpha — 2026-11-01

- Chunks: right-click a chunk (or several) and choose Delete frames from chunk. It deletes just those frames from disk, after asking, like Delete in Output.

## 0.41.3 alpha — 2026-11-01

- System: the Render column is wider (240 px), so a full script name such as shot_010_comp_v004 fits.

## 0.41.2 alpha — 2026-11-01

- A click anywhere off the render cards drops the cards picked with Shift; the render shown stays.
  - Clicks on Render, Pause and Stop keep them picked.

## 0.41.1 alpha — 2026-11-01

- When a render opens or is picked and it has frames on disk, its first rendered frame shows in the preview straight away. The open tab is left as it is.

## 0.41 alpha — 2026-11-01

- Shift+click render cards to pick several (Shift+click again takes one out). A plain click picks one, as before.
- With two or more picked, Render, Pause and Stop act on all of them.
  - Render starts the ones that have frames to render and leaves finished ones as they are.
  - Render turns into Resume when the picked renders are paused.
- The Render all and Pause all buttons are gone.

## 0.40 alpha — 2026-11-01

- Settings → General → **Dim other renders in Workers** (on), under Show processes of all renders.
  - On: in the Workers timeline, the render you picked is bright and the others are dimmed.
  - Off: every render's chunks are equally bright.

## 0.39.1 alpha — 2026-11-01

- System → State says "closed" for a process you closed too (was "closed by you").

## 0.39 alpha — 2026-11-01

- **VRAM limit** (Settings → Performance and Optimization, on, 90 %) replaces GPU processes and works like the RAM limit.
  - Every new process uses the GPU while VRAM allows.
  - Over the limit, the GPU process started last finishes its chunk, closes, and one without the GPU takes its place, so the number of processes stays.
  - Close to full (limit + 5 %), one GPU process closes at once and its chunk goes back to the queue.
  - One GPU process fewer at a time, and only once the last one has gone, because VRAM is freed only then.
  - GPU places come back when one more fits.
  - In tests, a full card made Windows push VRAM into RAM, and RAM ran to 100 %: the same as GPU 100 % / CPU 2 % before.
- A process told to quit that is still there after 8 s is ended. Nuke can take long to let go of the GPU and held the place meanwhile.
- System → State shows where a process goes after its chunk: "busy → 2 shotB" (Work on), "busy → CPU" (VRAM limit), "busy → another render".
- Output: when nothing is picked, the first frame rendered shows in the preview at once.

## 0.38 alpha — 2026-11-01

- Settings → GPU processes (default 2): only that many Nuke processes of all renders together use the graphics card (`--gpu`).
  - The others render GPU nodes on the CPU.
  - One card shared by many processes made them all wait: GPU at 100 %, CPU at 2 %, no frames.
- Right-click a process → **Work on ▸ render**.
  - It finishes its chunk, then goes on to that render under the same name.
  - The place stays with that render while both run.
- A finished render's card at the top turns green.

## 0.37.1 alpha — 2026-11-01

- The processes' logs (log/<render>/logs/worker_NN.log) are written line by line.
  - Before, Nuke's output stayed in memory until the process ended, so the log of a process that hung was empty.

## 0.37 alpha — 2026-11-01

- Workers timeline: one row per process (W01, W02 ...).
  - A process that moves on to another render keeps its row.
  - With several renders, each chunk says which render it was for, e.g. "1 · C33", "3 · C35".
- System: the first column is Process, the second Render. There is one row per process, and Render changes as the process moves.
- Stop also clears the stopped render's processes from the timeline and the System table. Their logs stay on disk.

## 0.36 alpha — 2026-10-31

- A render started while others run now gets its share gently, without throwing work away.
  - A busy process finishes its chunk, then hands its place over.
  - One still opening the script opens it, renders one chunk, then hands over.
  - Before, processes still opening the script were closed at once, and their loading was lost.
- The log says "closing (its place goes to another render)".
- If room comes back before then (another render done), processes waiting to hand over stay.

## 0.35 alpha — 2026-10-31

- Several renders: when one is done, its processes move on to the renders still running under the same names.
  - E.g. W01 of render A goes on in render C, so the names stay W01-W08 instead of W09, W10...
  - The free slots already went to the other renders; a Nuke process cannot switch scripts, so a new one starts there under the free name.
- Render cards show the time left next to the frame count, e.g. "41 / 96 · 0:13 left".

## 0.34 alpha — 2026-10-31

- Output, right-click on frames: **Delete this frame**, or **Delete this batch (N frames)** with several selected.
  - Asks first, with the number of files and their size.
  - The deleted frames go back to the queue.
  - Greyed out while the render runs.

## 0.33 alpha — 2026-10-31

- Movies: the player has the Enlarge button too. It makes the player, with Play and the slider, as big as the three panes; Esc goes back.
- Fixed: Delete movie did nothing since 0.31.
  - The button's click came in as an empty list of frames to delete.
  - It is now called **Delete video** and deletes the file again, after asking.

## 0.32 alpha — 2026-10-31

- The coloured Check cells from 0.31 are undone: Check shows its result in coloured text as before. The narrower columns stay.
- Output, right-click on frames: **Render this frame**, or **Render this batch (N frames)** with several selected.
  - Only those frames render. Before, every frame still to render rendered with them.
  - Other frames that are not rendered yet wait for Render.
  - While the whole render is running, the frames just join it.

## 0.31 alpha — 2026-10-31

- Output: the whole Check cell takes the colour of what the check found: green ok, yellow suspiciously small, red broken.
- The list's columns are narrower, so Check fits in the pane without scrolling.
- Delete (was Delete all) asks first what to delete:
  - **Frames**: a range typed in, e.g. 1001-1010, 1050, which starts as the frames selected in the list;
  - **All frames**.
  - Then it asks once more, with the number of files and their size. Deleted frames go back to the queue.

## 0.30.1 alpha — 2026-10-31

- Output: the Render problem frames again button is gone.
  - Broken frames: filter Problems, select them, right-click → Render again.
  - The banner after a render with broken frames says so.

## 0.30 alpha — 2026-10-31

- A frame bar under the preview in Output:
  - previous / Play / next, a slider over every frame of the render, and the frame number;
  - the Output list follows it: the frame is selected and scrolled to.
- Play goes through the rendered frames at the script's fps and loops, skipping frames not on disk yet.
  - The first pass decodes each EXR (about 5 frames/s for 4K).
  - Later passes come from a cache of preview pictures (about 300 MB) and play at full speed.
- Enlarge (the corners button, or a double-click on the preview) makes the preview, with its bar, as big as the three panes. The same button, a double-click or Esc goes back.

## 0.29 alpha — 2026-10-31

- A click on a frame in the chunk map also selects that frame in the Output list and scrolls the list to it (in the middle), besides showing it in the preview.
  - The left pane switches to Output if Chunks or Details was open.

## 0.28.2 alpha — 2026-10-31

- Movies (MOV, MP4, ...): the one Nuke process still gets every thread, but its cache is now the Nuke cache setting.
  - It was 70 % of the RAM, about 64 GB, for every movie render, so two movies or a movie next to other renders could run RAM out.

## 0.28.1 alpha — 2026-10-31

- The result of a render ("Done in ...") now runs from the controls to the right edge of the window.

## 0.28 alpha — 2026-10-31

- More room for the panels.
  - The script name, its path and the state badge on the right are gone; the render card at the top already shows them.
  - Render, Pause, Stop and FrameRanges sit in their place, and the result of the render follows right after them, e.g. "Done in 0:25. All 96 frames are on disk and pass the check."
- Details moved to the left pane, next to Output and Chunks. On the right, top: Workers and Log.

## 0.27 alpha — 2026-10-31

Experiment, to be kept or undone (0.26 has the old layout).

- The window has three panes:
  - On the left, full height: Output and Chunks.
  - On the right, top: Workers, Log and Details.
  - On the right, bottom: System and Settings.
- In Output, the preview sits above the list of frames (and above the movie details).

## 0.26 alpha — 2026-10-31

- New defaults, for a first start and for Reset to defaults:
  - Delete old render logs after 7 days (was 30).
  - Render slate frame on, on frame 1000.
  - Use GPU on.
- Settings already saved keep their values.

## 0.25 alpha — 2026-10-31

- Settings has a new look.
  - Three cards (General, Performance and Optimization, Processes and Chunks), each a grid with the setting's name on the left and its control on the right, so everything lines up.
  - On / off switches in Nuke orange instead of checkboxes; value boxes all the same width.
  - Short grey notes under the rows that need them.
  - Chunk is an Auto | Fixed switch with the size next to it.

## 0.24 alpha — 2026-10-31

- Test frames are gone, together with Render test frames first and Pause after the test frames.
  - Every frame is still checked on disk as soon as it is rendered.
- Smaller chunks towards the end moved to Settings → Processes and Chunks, under Chunk.

## 0.23 alpha — 2026-10-31

- Settings in three columns:
  - **General**: Start Render on open, Max renders at once, slate frame, Show processes of all renders, Delete render logs, and Reset to defaults at the bottom.
  - **Performance and Optimization**: Use GPU, RAM limit, Smaller chunks towards the end, test frames.
  - **Processes and Chunks**: Processes, Threads, Nuke cache (with what the script took last time), Chunk.

## 0.22 alpha — 2026-10-31

- System: a Cache column after RAM shows how much of its cache each Nuke process uses, against the Nuke cache limit, e.g. "3.4 / 8 GB".
  - Each process reports it after every frame, from Nuke's `nuke.memory2`.
  - RAM stays the whole process, so the gap between the two is what the nodes themselves take.
  - On a light 4K script, each process held about 3 GB in cache and took about 4 GB in all.

## 0.21 alpha — 2026-10-31

- Nuke cache is a setting again, under Settings → Nuke processes, as in 0.19. The automatic cache from 0.20 is undone.

## 0.20 alpha — 2026-10-31

- Nuke cache (`-c`) is no longer a setting.
  - It is set automatically to 70 % of the PC's RAM shared by the processes: 8 GB with 8 processes on 94 GB, 16 GB with 4.
  - It follows Processes when you change it.
- The log says "8 GB cache" and, after the first chunk, how much RAM one process of the script takes.

## 0.19 alpha — 2026-10-31

### Settings

- **Chunk: Auto or a size for every render.**
  - Auto gives about 4 chunks per Nuke process, at most 10 frames each, and now uses the Processes setting instead of a guess of 8. For example, 46 frames are chunks of 2 with 8 processes and of 3 with 4.
  - A size typed in applies to every render that is not running.
  - "(this render)" is gone.
- **RAM is now "Nuke cache"**, since it is Nuke's cache limit (`-c`), not what a process takes. Under it, JKrender shows what the picked script took per process last time.
- **Auto chunk size is now "Smaller chunks towards the end"**, which says what it does.
- **Delete render logs older than [30] days** (on by default).
  - When JKrender opens, it deletes render folders in `log/` (script snapshot, job, the processes' logs) whose newest file is older than that.
  - Only folders JKrender made are deleted, never the renders open now, and never rendered frames.
- **Reset to defaults** puts every setting back, after asking. What JKrender learned about scripts (RAM per process) is kept.

## 0.18.2 alpha — 2026-10-31

- Remaining shows a time up to the last frame.
  - It now also counts how long the frames being rendered still need, from the average frame time.
  - Before, it went by the speed of the last minute only, and on the last frames it showed 0 or nothing.

## 0.18.1 alpha — 2026-10-31

- System: processes keep their place in the table (render, then W01, W02, ...). A closed one no longer moves to the bottom.

## 0.18 alpha — 2026-10-31

- Settings → Nuke processes → Use GPU (off by default) starts the Nuke processes with `--gpu`.
  - Nuke in terminal mode renders GPU nodes (Kronos, ZDefocus, Denoise, ...) on the CPU without it.
  - On 48 frames of a light 4K script (2x Kronos) with 8 processes, the render took 15.3 s against 48.7 s, and the frames were identical to the CPU ones.
  - Each process takes its own VRAM, about 2.3 GB there: 18 of 24 GB with 8 processes. System → GPU shows it per process.

## 0.17 alpha — 2026-10-31

- Start again also works on a process the RAM limit closed. It starts right away, and the RAM limit lets one more process run for it.
  - Before, it waited until RAM dropped, so it looked as if only processes you closed yourself could start again.
- A process you started again by hand is the last one the RAM limit closes; the others go first.
- When the RAM limit goes up again, processes still marked to close after their chunk stay instead.
  - Before, one was closed and started again straight away.

## 0.16 alpha — 2026-10-31

- System: a GPU column per Nuke process shows GPU use and dedicated VRAM.
  - It reads the Windows counters Task Manager uses, because nvidia-smi cannot tell processes apart on Windows.
  - Nuke in terminal mode only uses the GPU when started with --gpu, so the column shows about 0 % for now.
- Right-click a process, in System or on the Workers timeline:
  - **Pause / Resume**: freezes only that process mid-frame. Pausing the whole render and resuming it leaves it paused.
  - **Close**: its chunk goes back to the queue, and it stays closed (State: closed by you) until Start again.
  - **Start again**: a closed process comes back under its own name, as soon as the RAM limit allows.
  - **Show process log**.
- State of a closed process is "closed" (or "closed by you"), without Nuke's exit code.

## 0.15 alpha — 2026-10-31

- Auto-tune processes is gone, together with Forget tuned scripts.
  - The split comparison gained little: on a light 4K script, 8 x 4 at 1.04 fr/s against 4 x 8 at 0.97.
  - Starting fewer processes from the remembered RAM is gone too.
- Processes and Threads are set by hand under Settings → Nuke processes, and exactly that many start.
- The RAM limit (on by default) is the only thing that closes processes, the one started last first, when RAM runs over.

## 0.14 alpha — 2026-10-30

- Settings → RAM limit is its own checkbox again, separate from Auto-tune processes, and works with Auto-tune on or off.
  - When RAM goes over the limit, the process started last closes first.
  - Off: nothing watches RAM.
- Auto-tune processes now does the split comparison and starts only as many processes as fit (the latter needs RAM limit on).
- With Auto-tune off, all processes set start; only the RAM limit can close some.

## 0.13 alpha — 2026-10-30

- RAM guard: when RAM runs over, the process started last closes first (W08, then W07, ...).
  - A replacement such as "W01 started again" counts as the newest.
  - 0.11 went by when a process started its first frame. All processes open the script within the same second, so that picked one at random; on a heavy comp it closed W01 first.
- The orange progress bar between the stats and the chunk map is gone; the chunk map and Frames show the progress.

## 0.12 alpha — 2026-10-30

- Settings → Auto-tune processes now holds everything that changes the number of Nuke processes.
  - Off: exactly the processes set under Nuke processes run, from start to end.
  - On:
    - the split comparison (8 x 4 or 4 x 8),
    - the RAM guard (the newest close first when RAM runs over),
    - starting only as many processes as fit, from the RAM one took last time.
  - The separate RAM guard checkbox is gone. RAM limit and Forget tuned scripts sit under Auto-tune and are greyed out when it is off.
  - Forget tuned scripts also forgets the RAM per process.
- Fixed: test frames rendered first even with Render test frames first off.
  - After Stop, the test chunks of the stopped run stayed test chunks and were still sent first.
  - Each start now turns them into ordinary chunks.

## 0.11 alpha — 2026-10-30

- The gradual start from 0.10 is gone: it cost time. All Nuke processes start one after another straight away again.
- When RAM runs over, the RAM guard closes the processes that started rendering last, so the least work is lost.
  - It closes idle and still-loading ones first, then busy ones from the newest.
  - Close to full it used to close the one that had worked longest on its frame.
- Later renders of a script still start only as many processes as fit, from the RAM one process took last time.

## 0.10 alpha — 2026-10-30

### Speed and safety

- A script's first render (RAM per process not known yet) starts 2 Nuke processes.
  - Once one has finished a frame and its RAM use is known, as many more start as fit under the RAM limit.
  - Before, all 8 started at once, RAM ran over and the RAM guard had to close the first ones mid-frame.
- Auto-tune waits until that start is over, so it compares the two splits with every process running.
- Later renders of the script start as many processes as fit straight away, as in 0.9.

### Window

- Processes, Threads, RAM and Chunk moved from the row next to Render into Settings, in a first column "Nuke processes".
- The frame range is shown as plain text, "FrameRanges 1000-1095". It comes from Nuke and can no longer be edited in JKrender.
- The Auto button is gone for now.
- System → CPU shows how many threads are in use out of all of them, e.g. "8 of 32 threads in use".

## 0.9.2 alpha — 2026-10-30

- System: the column is called RAM (was RAM used).

## 0.9.1 alpha — 2026-10-30

- System: the RAM used column shows only what each Nuke process takes (e.g. 11.3 GB), without the RAM setting next to it.

## 0.9 alpha — 2026-10-30

- A process that had to close (RAM guard, recycled) is replaced under its own name and on its own timeline row: W01 starts again instead of a new W09.
- JKrender remembers how much RAM one Nuke process of each script takes (after its first chunk).
  - The next render of that script starts only as many processes as fit in RAM right now, so the RAM guard does not have to close any in the middle of a frame.
  - On a heavy comp of about 10 MP that is about 11 GB per process: 8 of them do not fit in 94 GB next to Nuke's GUI.
- The RAM guard adds back as many processes as fit at once, counting the RAM that just-started processes are still about to take, instead of one every 15 s.
- The RAM setting is Nuke's cache limit (-c), not a cap on the whole process; a heavy script takes more. The README says so.

## 0.8 alpha — 2026-10-30

- Fixed: with a heavy script, a new Nuke process started after every chunk (29 processes for 46 frames on a heavy comp).
  - A process was closed after any chunk that left it over 1.3 x the RAM setting, so a script that simply needs more RAM was reloaded every time.
  - Now a process is recycled only when its RAM grows to 1.5 x what it used after its first chunk (a leak). The Log says once when the RAM setting is lower than what the script needs.
- RAM guard: a process comes back only when one more fits under the limit (measured by the largest running process). Before, RAM 8 % under the limit was enough, so one process was closed and another started, round and round.
- The Log tab's events are also written to jkrender.log in the render's folder, next to the processes' logs.

## 0.7.2 alpha — 2026-10-30

- `INSTALL.txt` now really has the steps in English (0.7.1 renamed the file but kept the Czech text).

## 0.7.1 alpha — 2026-10-30

- The install note is in English: `INSTALACE.txt` is now `INSTALL.txt`.

## 0.7 alpha — 2026-10-30

- README: a screenshot of a 4K comp rendering in 8 Nuke processes, with the EXR preview.
- Output: in a low window, the frame preview shrinks instead of being drawn over the frame info under it.

## 0.6 alpha — 2026-10-30

- Install like JKplayer: copy the JKrender folder into `~/.nuke` and add `nuke.pluginAddPath("./JKrender")` to `~/.nuke/init.py`. `install.bat` is gone.
- numpy and OpenEXR for the EXR preview are no longer shipped in the folder; on the first Nuke start JKrender asks to download them.
  - About 15 MB, in the background, with Nuke's own python.
  - They go into `lib/<platform>-cp<version>` (e.g. `lib/win_amd64-cp311`), or `~/.jkrender/lib/...` when the folder is read-only.
  - Saying no leaves `lib/DECLINED`; rendering works without them. `JKRENDER_NO_FETCH=1` never asks.
  - Without them, the preview says to restart Nuke to download them.

## 0.5 alpha — 2026-10-30

### Install

- The JKrender folder is all a colleague needs.
  - Run `install.bat` to put the folder on Nuke's plugin path. It adds one line to `~/.nuke/init.py`.
  - Restart Nuke. The folder's `menu.py` adds the JKrender menu.
  - `INSTALACE.txt` has the steps in Czech.
- The window runs in Nuke's own Python, which already has PySide6 and psutil, so nothing needs installing.
  - numpy and OpenEXR for the EXR preview ship in `lib/`.
  - Set `JKRENDER_PYTHON` to use another Python.
  - Without Nuke's Python, JKrender looks for an external one as before.

### Logs

- Each render's folder (script snapshot, job file and the log of every Nuke process) now goes to `log/` next to `JKrender.py`, instead of `~/.jkrender/renders/`.
  - If that folder is read-only, they go to `~/.jkrender/renders/` as before.
  - JKrender → Open Renders Folder opens `log/`.
- Settings stay per user in `~/.jkrender/settings.json`.

## 0.4 alpha — 2026-10-30

- The JKrender menu in Nuke has two items: Render Selected Write(s) (Ctrl+Alt+R) and Open Renders Folder.
  - Removed: Render Selected Write(s) - Current Frame.
  - Removed: the Include Writes inside Groups toggle. A selected Group now always renders the Writes inside it.

## 0.3 alpha — 2026-10-30

- **Auto chunk size** (Settings → Speed and safety, on by default). A process is given at most
  remaining frames / (2 × processes) at a time, so chunks shrink towards the end of a render, down to
  single frames, and every process finishes at about the same time. The Chunk field is now the largest chunk.
  - On 48 frames of a 4K Kronos comp with 8 processes, chunk 12 went from 60.4 s to 51.6 s (no process waits any more).
  - Chunk 6 went from 50.8 s to 50.1 s.
- Choosing chunk size from the script's time nodes was tried and left out.
  Frames in a row rendered no faster in one process (7.47 s at chunk 6 against 7.51 s at chunk 1).

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
