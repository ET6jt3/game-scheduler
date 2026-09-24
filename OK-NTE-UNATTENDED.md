# ok-nte autonomous native lifecycle

Game Scheduler now defaults to an OK-NTE-owned lifecycle instead of adapting
OK-NTE's launcher/input implementation.

## Default production path: native

The `task` helper type defaults to:

`lifecycle_mode = native`

The scheduler starts OK-NTE's bundled Python runtime from the installed helper,
loads the installed `src.config`, installs OK-NTE's own startup patches, and
selects the installed `DailyRoutineTask`.

It then uses OK-Script's native start controller:

1. emit OK-Script's normal runtime-start signal so OK-NTE RuntimeServices start;
2. request `DailyRoutineTask`;
3. OK-Script automatically queues every task with `enable_after_start=True`;
4. OK-NTE's installed `LauncherTask` therefore owns launcher discovery,
   update waiting, Start-button recognition/clicking, game-process waiting,
   launcher-to-game capture switching and resolution/capture checks;
5. after launcher/game readiness, OK-NTE runs its configured DailyRoutineTask;
6. Game Scheduler observes the DailyRoutineTask result and exits the worker.

The scheduler does **not** patch or replace any of these in native mode:

- `LauncherTask.run()`
- `LauncherTask.click()`
- `_launcher_button_state()`
- `NTEInteraction`
- CursorSync
- PostMessage behavior
- SendInput behavior
- UI Automation
- screen-saver handling
- launcher/game capture switching

The external OK-NTE installation remains untouched.

## Why this is the default

The previous v2/v3 adapters duplicated launcher/input ownership that OK-NTE
already implements. Real-machine runs showed that the additional wrapper could
interfere with changing launcher HWND/capture geometry and made failures harder
to distinguish from upstream launcher state.

Native mode reduces the scheduler's responsibility to:

- scheduling and same-day dedupe;
- ordered game chains;
- timeout/cancellation and process-tree cleanup;
- elevated interactive startup;
- stdout/stderr/event capture;
- truthful DailyRoutineTask completion checking.

## Truthful completion

Native mode still does not treat process exit alone as success.

The observer requires:

- the installed DailyRoutineTask was actually entered;
- its `do_run()` returned;
- OK-Script emitted `task_done` for that exact task;
- the task returned `True`;
- `task_status` contains `success/failed/skipped/pending` lists;
- no failed or pending daily item remains;
- at least one item is accounted for as success or skipped.

Failure remains nonzero so a daily chain does not advance to the next helper on
an unknown or partial OK-NTE result.

## Administrator and desktop requirements

The installed OK-NTE PC LauncherTask requires administrator rights and a signed-in
interactive Windows desktop.

For a manual test, start Game Scheduler with `Run-Elevated.cmd`.

For unattended production, run `Setup-Startup.cmd` once. It registers the
scheduler at Windows logon for the current interactive user with the highest run
level. The desktop must remain logged in and unlocked for game automation.

A non-elevated native run fails early with:

`ADMIN_REQUIRED` / exit code 30

rather than pretending the launcher failed.

## Diagnostic events

Native runs write:

`Logs\ok-nte\nte-native-*.jsonl`

Expected high-level sequence:

- `WORKER_STARTED` with `lifecycle=native`
- `RUNTIME_READY` with `native_launcher=true`
- `NATIVE_LIFECYCLE_BEGIN`
- `TASK_DISPATCH`
- normal upstream OK-NTE / OK-Script launcher and task logs
- `NATIVE_LIFECYCLE_COMPLETED`
- `WORKER_RESULT`

The detailed launcher/update/game messages between those events come directly
from the installed OK-NTE implementation.

## Legacy fallback modes

The older adapter remains available only for explicit troubleshooting by setting
`lifecycle_mode = adapted` and then choosing one of:

- `cursor-compatible`
- `strict-no-mouse`
- `auto`

Existing tasks saved by older builds may contain only `input_mode`; because they
do not contain `lifecycle_mode=adapted`, they now migrate automatically to the
native lifecycle instead of silently retaining the unstable adapter.

## Qualification

1. Install the new portable Game Scheduler artifact and migrate persistent state.
2. Stop any independently running OK-NTE instance.
3. Start Game Scheduler with `Run-Elevated.cmd`.
4. Edit the OK-NTE helper task and confirm `lifecycle_mode` is `native`.
5. Run OK-NTE alone.
6. Confirm the log says `native-lifecycle-v1` and `native_launcher=true`.
7. Confirm OK-NTE itself launches/updates the native launcher, enters the game,
   switches capture to HTGame.exe, and runs DailyRoutineTask.
8. Confirm `NATIVE_LIFECYCLE_COMPLETED` and exit code 0.
9. Repeat through `Setup-Startup.cmd` unattended logon startup.
10. Only then place OK-NTE back between March7thAssistant and BetterGI.

Hosted CI can validate command routing, lifecycle observation, packaging and
process supervision, but only the user's Windows machine can qualify the actual
installed NTE launcher/game state.


## Production autonomous launcher entry

The normal `task` type is now native-only.

Game Scheduler selects the installed `DailyRoutineTask`, verifies that exactly
one upstream `LauncherTask` exists with `enable_after_start=True`, initializes
the normal headless OK-Script runtime services, and calls the upstream
`run_onetime_task(..., exit_after=True)` entry. From that point forward OK-NTE
owns the full lifecycle:

1. native LauncherTask checks/starts the official NTE launcher;
2. native launcher capture detects popup/update/start state;
3. native OK-NTE waits for updates without scheduler click injection;
4. native OK-NTE launches HTGame.exe;
5. native OK-NTE switches capture from launcher to game and verifies readiness;
6. native DailyRoutineTask runs its configured daily subtasks;
7. Game Scheduler only observes task completion, supervises timeout/cancellation,
   and records the final result.

The older adapted launcher/input implementation remains embedded only as
diagnostic/recovery code. It is no longer selectable from the ordinary task
schema and must not be used by scheduled production chains.

Existing saved tasks that contain historical `input_mode` values continue to
resolve to the native lifecycle so users do not have to recreate their chains.


## Native launcher capture diagnostics

The native production lifecycle includes a **read-only launcher observer**. It
does not call `capture.get_frame()`, does not replace `LauncherTask`, does not
click, resize, recenter or rebind capture, and does not consume a second WGC or
BitBlt stream. It only inspects the `LauncherTask.frame` that OK-NTE already
acquired for its own recognition.

While the upstream `LauncherTask` is the current task, diagnostics are written
under:

`Logs/ok-nte/launcher-capture/`

Each saved pair contains:

- `launcher-<timestamp>.png` — the exact task frame OK-NTE was analyzing;
- matching `.json` metadata with HWND, PID, class/title (when available),
  window rect, OK-Script position/size/crop values, capture backend,
  `capture_target_signature`, frame SHA-256, frame min/max/mean, and the exact
  launcher-ready probe rectangle;
- `launcher_button_ready_percentage` calculated from the same BGR range
  (215–225 on each channel) used by upstream `LauncherTask`.

Relevant JSONL events:

- `LAUNCHER_CAPTURE_DIAGNOSTICS_READY`
- `LAUNCHER_CAPTURE_OBSERVER_ACTIVE`
- `LAUNCHER_CAPTURE_TARGET` — HWND/geometry/capture signature changed;
- `LAUNCHER_CAPTURE_POSITION_INVALID`
- `LAUNCHER_CAPTURE_STALE` — the same task-frame SHA-256 persisted for at
  least 15 seconds;
- `LAUNCHER_CAPTURE_RECOVERED` — a previously stale task frame changed;
- `LAUNCHER_CAPTURE_SNAPSHOT` — records the PNG and metadata paths.

Snapshots are bounded to 48 files per run. Normal animation frames are not
continuously dumped. The observer saves the first launcher frame, target/signature
changes, invalid-position evidence, periodic stale-frame evidence, and the first
frame after a stale period recovers.

For the remote-connection symptom, leave OK-NTE stalled for at least 20–30
seconds before connecting remotely. After it begins working, preserve the
newest stale-before and `stale-frame-recovered` PNG/JSON pairs together with
the run's `nte-native-*.jsonl`.
