# ok-nte unattended desktop adapter v3: dual input

This document supersedes the v2 launcher guide.

## Modes

The scheduler-owned embedded adapter supports three input modes without modifying
the external ok-nte installation:

| Mode | Behavior |
| --- | --- |
| `cursor-compatible` | Default. Retains the v2 checked foreground/cursor/SendInput compatibility path. |
| `strict-no-mouse` | Never intentionally uses global cursor positioning, SendInput, or BlockInput. In-game clicks use virtual PostMessage hover/clicks. The native launcher tries UI Automation InvokePattern first, then a targeted PostMessage click. |
| `auto` | On the first recognized launcher action, selects strict mode if UI Automation can invoke the control; otherwise selects cursor-compatible before issuing input. The selected mode is then locked for the run. |

The helper task exposes `input_mode`; no external Python files need to be edited.

## Deployment contract

All modes still require a dedicated, active, unlocked interactive Windows desktop
with a usable display surface. This is not Session-0, locked-screen, secure-desktop
or displayless automation. The adapter does not bypass login, UAC, anti-cheat,
launcher authentication, or game security.

The v2-compatible mode remains the production fallback because some Windows/Qt
controls may ignore background messages. Strict mode is intentionally fail-closed:
if an NTE action requests the relative-mouse SendInput path, the worker exits
nonzero instead of silently moving the global pointer.

## Native launcher

Upstream ok-nte remains responsible for finding Start, Update and launcher-popup
controls. The v3 wrapper accepts only those recognized rectangles and still
requires real game process/window/capture readiness after activation.

Strict launcher order:

1. Query the recognized point through Windows UI Automation.
2. If an enabled element exposing InvokePattern contains that point, invoke it.
3. If UIA is unavailable in explicit strict mode, send targeted WM_MOUSEMOVE,
   WM_LBUTTONDOWN and WM_LBUTTONUP messages without moving the global cursor.
4. In `auto`, if UIA is unavailable before any launcher input, select
   `cursor-compatible` and use the v2 checked SendInput path.

A click attempt never counts as GAME_READY. The actual game process, HWND,
matching capture HWND and connected capture are required.

## In-game input

Normal and `operate_click` calls keep upstream task logic intact. In strict mode
the adapter forces `move=False`/no cursor restoration, synthesizes a virtual
PostMessage hover for child-window targeting, and then lets NTEInteraction send
its target-window mouse messages. It suppresses global BlockInput and stops
CursorSync because there is no scheduler-owned real-cursor motion to restore.

Compatibility mode preserves the v2 behavior: verify the active desktop,
foreground the target, position the software cursor inside the target client
area, then use the existing NTE PostMessage/relative SendInput behavior.

## Input proof

Each run records an input audit in the structured event log and final status:

- `post_messages`
- `virtual_moves`
- `uia_invokes`
- `launcher_post_clicks`
- `compat_clicks`
- `cursor_positions`
- `send_input`
- `block_input`

A successful `strict-no-mouse` run additionally requires
`cursor_positions=0`, `send_input=0`, and `block_input=0`. Any breach is
`STRICT_INPUT_CONTRACT_BREACH` and fails the worker.

## Existing safety and completion rules retained

- per-session managed-worker mutex;
- active/unlocked desktop and display checks;
- temporary Windows execution-state power lease;
- bounded runtime/model initialization;
- bounded launcher/update waits and click retries;
- unique flushed `Logs/ok-nte/nte-*.jsonl` event logs;
- class-based DailyRoutineTask selection;
- truthful completion proof requiring task start/return/completion event and no
  failed or pending daily items;
- complete process-tree cleanup by the scheduler runner.

Helper-reported success is not independent proof that a reward was received.

## Qualification sequence

1. Install a Windows artifact built from the v3 commit.
2. Stop any independently running ok-nte GUI.
3. Run ok-nte alone in `cursor-compatible` and verify current v2 behavior.
4. Run ok-nte alone in `strict-no-mouse`; leave the physical pointer untouched
   and inspect `INPUT_AUDIT`.
5. Test `auto` and confirm `INPUT_MODE_SELECTED` records the locked choice.
6. Test the exact enabled DailyRoutineTask items, including any task that uses
   hover-sensitive controls.
7. Qualify with the physical mouse unplugged.
8. Confirm locked/disconnected desktop, missing display, model-init failure,
   launcher failure and task-item failure all return nonzero.
9. Only after those checks re-enable the full daily chain.

Hosted CI exercises fixtures, command transport and Windows packaging but cannot
qualify the user's native NTE launcher, installed game, GPU/display session or
actual rewards.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `GS_OK_NTE_INPUT_MODE` | `cursor-compatible` | `cursor-compatible`, `strict-no-mouse`, or `auto`. Legacy `unattended-desktop` maps to compatibility mode. |
| `GS_OK_NTE_INIT_TIMEOUT` | 180 s | Runtime initialization deadline. |
| `GS_OK_NTE_NO_INPUT_TIMEOUT` | 600 s | Daily input inactivity deadline. |
| `GS_OK_NTE_LAUNCH_TIMEOUT` | 300 s | Native launcher/controller-dispatch deadline. |
| `GS_OK_NTE_UPDATE_TIMEOUT` | 1800 s | One-time visual-update extension. |
| `GS_OK_NTE_EVENT_DIR` | package `Logs/ok-nte` | Live structured event directory. |
| `GS_OK_NTE_DOCTOR` | unset | Set to 1 for desktop/power checks only; never task success. |

## References

- Upstream ok-nte / ok-script source inspected for LauncherTask,
  NTEInteraction, CursorSync, BaseNTETask and PostMessageInteraction.
- Microsoft Win32 documentation for PostMessage mouse messages, SendInput,
  SetCursorPos, UI Automation InvokePattern and SetThreadExecutionState.
