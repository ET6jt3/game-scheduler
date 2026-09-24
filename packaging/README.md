# Game Scheduler Portable

Unzip the entire folder, then double-click **Start.cmd**. **Stop.cmd** requests a graceful shutdown. Windows PowerShell is used by the launchers; no system Go, Node, or Python is needed by the scheduler. The dashboard opens after a successful health check. Startup logs are in `Logs`.

Open **Helpers** to register external or managed installations, set discovery roots, preflight commands, and create tasks. Use the main dashboard to add cron plans. Helper executable paths may point anywhere. Disabling an instance blocks its tasks. Removing its registration does not delete any helper files; tasks still referring to it fail preflight explicitly.

For ok-nte, select `task`, enter the task index shown by your installed helper, and choose exit-after-task. Raw argv is also available. No task-number meanings are assumed. Preflight never launches a game. The upstream PC LauncherTask requires administrator rights. For a manual ok-nte trial, use **Run-Elevated.cmd**; it safely stops a non-elevated scheduler first and restarts the portable package through Windows UAC.

Edit `Config/config.json` after its first creation to change the port or API token. Default: `127.0.0.1:8080`, concurrency 1. If a helper requires administrator access, use **Run-Elevated.cmd** for a manual session. For unattended logon, **Setup-Startup.cmd** registers the scheduler for the current signed-in user with the highest available run level; this is a one-time setup that may show UAC. Helpers are installed separately.

`${ROOT}` means the **App** folder containing server.exe. The supplied config sets `${DATA}`, `${HELPERS}`, and `${RUNTIME}` to sibling folders. Stop before moving the whole package. Variable paths relocate; absolute external paths do not change. Do not copy a running SQLite database; stop first and copy Data into Backups.

Add data-only manifest JSON to `Config/helpers` or `Data/helpers`, then click **Reload definitions**. Invalid reloads preserve the previous definitions. The native controller is not bundled by default; an existing compatible controller can be configured explicitly. Its upstream execution path remains available.

CLI examples (global flags precede resource names):

```
App\ctl.exe helpers list
App\ctl.exe -data @Config\one-instance.json helpers add
App\ctl.exe -data "{\"type\":\"task\",\"params\":{\"task_index\":2,\"exit\":true}}" helpers preflight nte-main
App\ctl.exe -paths "D:/Automation;E:/PortableApps" -depth 4 discover
App\ctl.exe helper-definitions reload
```

Use `-server http://127.0.0.1:<port>` and `-token <token>` if configured. See PORTABLE-LAYOUT.md and MIGRATION-NOTES.md for details. No game is launched until a task is manually run or its enabled plan fires.

## Daily chains and automatic startup

Open **每日任务链 / 自动启动** from the dashboard. Set a time, select days, add tasks in order, and save. The same page enables startup at Windows sign-in. `Setup-Startup.cmd` and `Remove-Startup.cmd` are standalone shortcuts. See [AUTOMATION.md](AUTOMATION.md) for catch-up, recovery and preserving your existing settings during an update.
