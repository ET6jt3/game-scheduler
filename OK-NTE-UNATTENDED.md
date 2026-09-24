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
