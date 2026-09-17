# Daily chains and Windows sign-in startup

Open **每日任务链 / 自动启动** on the dashboard, or `/automation`.

## Set up Windows startup

Click **启用 / 指向此目录** (Enable / point startup here). Leave administrator privileges checked for helpers that require elevation, and accept the Windows UAC prompt. Alternatively double-click `Setup-Startup.cmd` in the portable package. `Remove-Startup.cmd` removes the registration.

This creates one Task Scheduler registration for the current Windows user, at sign-in with a 30-second delay. It launches this package without opening a browser. It does not sign in for you, wake a powered-off PC, or run game automation before login. The chain engine waits for an unlocked interactive desktop before starting the next step. Tasks already running are not automatically suspended when you lock the screen; keep the desktop available for them.

The page shows whether the registered path matches this package. After moving the package or choosing a new build directory, click Enable again to update the same registration. No stored password is used. Elevated setup must use the same Windows account; using another administrator account is rejected.

## Create the daily chain

1. Configure and test each real helper task from the dashboard first. The helper command must execute and finish its work, rather than merely open a settings window.
2. Enter a name, choose the daily time and weekdays, and choose `Local` or an IANA time zone such as `Asia/Shanghai` or `America/Los_Angeles`.
3. Add existing tasks in the desired order, e.g. BetterGI → HSR → NTE. Reorder with ↑ / ↓.
4. Leave catch-up checked to run today's unfinished occurrence after late startup or resume. No previous days are replayed.
5. Choose stop on failure (default) or continue to the next game. Retries and retry delays come from each task's existing settings. Each attempt uses the task timeout, defaulting to one hour for chains when unset.
6. Leave **关闭这些任务原有的独立计划** checked to disable their separate cron schedules and prevent duplicate scheduled runs. Other chains are independent: do not put the same daily work in multiple enabled chains unless you intend separate runs.
7. Save with Enabled checked. If today's time has already passed and catch-up is selected, the chain becomes eligible immediately.

The engine checks once per second, including after sleep resumes. Today's run and successful steps are persisted in the existing SQLite database. Run today uses the same daily occurrence; repeated clicks do not run successful steps again. The daily key is the calendar date in the chain's selected zone (not the game's server reset time). Choose the zone and start time accordingly. DST repeated times run once; a nonexistent time runs at the first available minute after it.

The chain waits for previous manual work, then reserves the runner for its entire sequence, regardless of the general concurrency setting. During the reservation, other manual runs are rejected and old cron fires are skipped. Two chains are processed in creation order of their due occurrences. Disable overlapping legacy schedules when adopting chains.

## Pause, resume and failures

- **Pause** lets the current step finish and holds later steps. Completed steps stay recorded.
- **Resume unfinished** retries failed/interrupted steps and keeps successful steps. It applies to today's run. Fix the helper's configuration before retrying uncertain work.
- **Cancel** terminates the current step and prevents further automatic work for this occurrence.
- Disabling a chain prevents new steps but lets an existing step finish. Re-enable to continue today's pending steps.
- A restart during a step marks its outcome interrupted and requires Resume unfinished. The scheduler cannot know whether a game-side action succeeded just before a crash. It does not claim exactly-once game-side effects.
- An unfinished prior-day occurrence expires before starting another step; it remains in history. If a step spans midnight it is allowed to finish; today's occurrence waits for the runner.
- Editing an unfinished running/paused/interrupted chain is blocked. Finish or cancel it first so today's snapshot stays unambiguous.

Each row has a log button. Success means the configured process exited successfully; it is not independent verification of game rewards. On Windows chain steps, a launcher exiting while its child processes remain is treated as unknown completion and fails the step, with remaining tracked processes cleaned up. For ok-nte, use an invocation whose process lifetime represents the actual work; the launcher-only pattern previously observed must be corrected (for example its automatic launcher-exit setting). Real helper/game behavior still needs testing on your desktop.

## Update an existing portable installation without losing configuration

Stop the old package with its `Stop.cmd` and back up that package first. Build the updated branch with `Build.cmd`, or download the Windows acceptance ZIP. To retain the active package's settings and history, copy the new package's **App** files plus **Start.cmd**, **Stop.cmd**, **Setup-Startup.cmd**, and **Remove-Startup.cmd** into the old package. Keep the old **Config**, **Data**, **Helpers**, and **Runtime** directories. Do not copy the new package's empty Data or example Config over your active installation.

Start the retained package. New chain tables are added automatically to its database. Existing games, tasks and helpers stay available. Re-enable Windows startup from the page to confirm the active package path.

## Verification scope

Automated fixtures cover cross-game order, exclusive execution, late catch-up, daily deduplication, pause/resume, failure policy, restart recovery, disabled independent schedules, atomic execution links, time zones/DST, API validation and origin rejection. Windows acceptance additionally verifies logon registration, repeat enable, relocation/update/removal, and rejection/cleanup of launcher-only completion. These tests launch harmless fixture processes, not games.

References: [Windows interactive/elevated task contexts](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks), [Task actions](https://learn.microsoft.com/en-us/windows/win32/taskschd/task-actions). Catch-up and daily progress are implemented by this app rather than Windows missed-task settings.
