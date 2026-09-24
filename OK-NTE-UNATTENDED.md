# ok-nte unattended desktop adapter v1

This document supersedes the NTE bootstrap description in AUTOMATION.md.

## Deployment contract

No person needs to move a mouse, hover over the game, or keep generating input.
The adapter owns cursor placement during its NTE step. It still requires a
**dedicated, active, unlocked interactive Windows desktop and display surface**.
This is not a cursor-free, Session-0, locked-screen, or displayless backend.
No physical-mouse-present check is made; Windows cursor APIs must work. Operation
with the user's physical mouse unplugged still requires a real-machine test.

Do not use this mode on a shared working desktop. A session-local mutex prevents
overlapping managed NTE workers, and the existing daily chain serializes helpers.
The mutex does not exclude unrelated applications or helpers started outside the
chain. Close any independent ok-nte GUI before a managed run.

## Changes

The embedded scheduler-owned Python adapter wraps the installed NTEInteraction
in memory. It stops its CursorSync thread and suppresses restoration of an old
user cursor position. It never invokes global BlockInput. Before NTE input it
restores the target window when minimized, requests and verifies foreground
access, and positions the software cursor within the target client area. Clicks
use actual window/capture coordinates, never fixed desktop coordinates. Relative
camera movement is not recentered while the cursor is already inside the game.
SendInput's inserted-event count and PostMessageW's return are checked; upstream
PostMessage's swallowed-error behavior is not accepted as input success.

This does not modify the external helper/game installation, install a driver,
inject into the game, or alter anti-cheat behavior. It does not auto-login,
change lock/screensaver policy, switch secure desktops, wiggle a mouse, or
reattach remote sessions. A temporary Windows execution-state request prevents
idle system sleep/display-off and is released on exit. It does not override user
lock/sleep actions, screen savers, organizational policy, or GPU/display removal.

Desktop/session availability is checked every 500 ms and before wrapped input.
These checks do not make checking and delivering input atomic. Foreground denial,
lock/disconnect, invalid windows, unavailable cursor APIs, and rejected input
produce explicit nonzero failures, rather than asking a person to move a mouse.

## Runtime and truthful completion

The bootstrap sets the installed main.py identity and --headless BEFORE importing
ok-nte. The old python -c bootstrap omitted this flag, potentially initializing a
GUI before creating a separate headless application. No short -h is used.
UTF-8 streams are configured directly because embedded python._pth may ignore
environment variables. The loaded task is selected by its exact class/module,
not a numeric list position. One runtime-ready event initializes RuntimeServices;
OpenVINO Future waits are bounded before the normal StartController dispatch.
LauncherTask remains responsible for native launcher/update/game readiness.

Success now requires the chosen DailyRoutineTask do_run to have actually started
and returned True, task_done for the same task object, valid item-status lists,
no failed/pending entries, at least one successful/explicitly skipped item, and
no recorded desktop/input/watchdog failure. A zero process exit, help output,
empty/missing result, swallowed False return, or external game closure alone is
not proof of successful automation. This is helper-reported task proof, not an
independent verification of game rewards.

The runner retains its existing Windows tracked-process cleanup and complete-tree
rules. This patch does not relax them. Unrelated surviving descendants may still
cause a runner failure after the helper reports completion.

## Diagnostics

Output contains GS_OK_NTE_EVENT structured lines, GS_OK_NTE_RUNTIME_READY=1,
TASK_DISPATCH, and GS_OK_NTE_STATUS with ok/reason/status. BLOCKED includes a
specific cause, such as FOREGROUND_DENIED, DESKTOP_UNAVAILABLE, INPUT_REJECTED,
POSTMESSAGE_FAILED, or NO_INPUT_PROGRESS. A watchdog-forced exit can emit BLOCKED
or CLEANUP_TIMEOUT instead of the final status object; its exit remains nonzero.

Exit codes: 20 task-proof/items failure; 21 desktop/session unavailable; 22 worker
exception/interruption; 23 initialization failure/timeout; 24 unsupported
interface/policy; 25 daily input inactivity; 26 window/input failure; 27 exit or
cleanup timeout; 28 diagnostic-only (never daily-task success).

Worker environment options, inherited from the scheduler:

| Variable | Default | Meaning |
| --- | --- | --- |
| GS_OK_NTE_INPUT_MODE | unattended-desktop | Only supported mode. Other values fail explicitly. |
| GS_OK_NTE_INIT_TIMEOUT | 180 seconds | Initialization limit; accepted range 5-1800. |
| GS_OK_NTE_NO_INPUT_TIMEOUT | 600 seconds | Daily input inactivity limit; range 30-21600. |
| GS_OK_NTE_DOCTOR | unset | Set to 1 for desktop/power-lease checks without importing game code or issuing input; exit 28. |

The inactivity timer runs during DailyRoutineTask, NOT during native launcher
update/download waiting. It detects absence of delivered input, not ineffective
repeated actions or every possible game-progress failure. The existing whole-task
timeout (six-hour fallback when unset) still bounds this helper inside chains.
Completion gets 30 seconds for upstream exit-after behavior; explicit shutdown
gets 10 seconds. Session/input failure gives the worker five seconds to exit
before its watchdog terminates that worker only; OS handles then release.

## Install and qualify

Use a Windows Actions artifact containing this document, not the older builds.
Stop the old scheduler, retain a backup, and preserve Config/Data/Helpers/Runtime
using the existing migration procedure. The fix is embedded in App/server.exe;
do not copy Python files into the external ok-nte directory. Preflight still
checks packaged python.exe, working directory, and main.py; execution uses the
embedded python -c adapter. No normal daily-task JSON changes are required.

Test NTE alone first. Leave the pointer outside the game and do not touch it.
Verify native launcher startup, daily input, final item results, and clean exit.
Then qualify mouse unplugged, minimized window recovery, locked/disconnected
session rejection, missing display, model-init failure, and internal task failure.
Only then enable unattended production chaining. No real game was exercised by
the automated fixtures, and no locked-desktop operation is claimed.

## Tests and research

The Go helper test runs Python behavioral fixtures if a Python 3.10+ test
interpreter exists. Production uses the helper's own packaged Python. Fixtures
cover cursor positioning, no global input blocking/restoration, rejection after
desktop loss, input errors, bounded Futures, stable class selection, completion
proof and cleanup. A Windows-only read-only ABI probe loads Win32 functions but
does not move input or launch a game.

Inspected packaged source: BnanZ0/ok-nte-update at
ff9f4a043aa768e617cc353e859d7075af81b501; NTEInteraction, RuntimeServices, Globals,
OK, PostMessageInteraction, and DailyRoutineTask. Current CursorSync was also read.

Primary references:
https://ok-script.com/ok-nte/en/docs/getting-started/configuration/
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setcursorpos
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate
