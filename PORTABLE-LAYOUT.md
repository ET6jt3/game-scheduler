# Portable layout and helper contracts

```
GameScheduler-Portable/
  Start.cmd
  Stop.cmd
  App/server.exe
  App/ctl.exe
  App/Portable.ps1
  Config/config.example.json
  Config/helpers/ok-nte.json
  Config/helper-instances.example.json
  Helpers/README.txt
  Runtime/README.txt
  Data/
  Logs/
  Backups/
  README.md
  LICENSE
```

`${ROOT}` is always the canonical directory containing server.exe (`App` in this layout). Default unconfigured paths are `${ROOT}/Helpers`, `${ROOT}/Runtime`, `${ROOT}/data`. The package explicitly configures sibling Helpers, Runtime, Data and Config folders. Config file paths are resolved once at server startup; stored game and helper variables are resolved on every preflight/run, never written back as absolute replacements.

Supported variables: ROOT, DATA, HELPERS, RUNTIME, USERPROFILE, LOCALAPPDATA, APPDATA, PROGRAMFILES. Missing or unknown variables fail explicitly. Task raw argv strings are preserved literally, including `${ROOT}`; expand paths in executable, working directory and typed manifest path fields instead.

## Instances and tasks

`GET/POST /api/helpers`, `GET/PUT/DELETE /api/helpers/{id}` manage registrations. `POST /api/helpers/{id}/preflight` accepts `{"type":"task","params":{"task_index":2,"exit":true}}`. No preflight launches anything. Deleting a registration never removes its files, games, tasks, or another instance.

Link a task using `params.helper_instance_id`, or set `game.extra_config` to the JSON string `{"helper_instance_id":"nte-main"}`. A task override takes precedence over the game link. Instance definitions select their adapter; legacy unlinked games continue to use their adapter. Missing or disabled referenced instances fail preflight and execution.

Executable priority: task `exe`, referenced instance executable, legacy `tool_path`, manifest default. A discovered registration contributes its stored executable at the instance priority. No automatic ambiguous scan result is selected. An explicit missing path fails instead of switching to another executable.

Working directory priority: task `working_dir`, instance working directory, legacy working directory, HSR project directory / manifest working directory, executable parent. Explicit paths are expanded and checked before execution. Legacy HSR keeps its explicit `python_path`, project and entry settings, with `runtime_path` on a helper instance overriding the interpreter. Only its documented default `python` uses PATH; manifest PATH use requires `launch.allow_path_lookup:true`. External configured paths are never relocated under ROOT.

The preflight JSON contains the resolved instance, executable, working directory, exact `args` array, checks, warnings and errors. Successful setup also stores this snapshot at `GET /api/executions/{id}/diagnostics`; subsequent configuration edits do not rewrite that history. Existing execution, stdout/stderr, exit code, retry and cancellation endpoints remain.

## Manifests

Place version-1 JSON under Config/helpers or Data/helpers. `GET /api/helper-definitions` lists definitions; `POST /api/helper-definitions/reload` atomically reloads them. The data directory wins on duplicate IDs. Invalid schemas, field types, placeholders, unknown members and escaping symlink files reject the reload, retaining the previous snapshot. Built-in adapters cannot be replaced. No asset-inclusion feature or shell templating is implemented.

Fields support string, integer, number, boolean, enum (`enum` is a list of strings), file, directory, required, default, numeric min/max, and `repeated:true`. Arguments support literals, `{{field}}` substitution, `{"if":"bool_field","value":"--flag"}`, and `{"repeat":"array_field","flag":"--tag"}`. Repeated elements become separate argv items. Raw task types use `"raw_args":true` and require an array of strings. A definition is loaded as data, and the existing runner invokes the executable directly.

## Discovery and CLI

`GET/PUT /api/helper-settings/discovery` persists roots, `scan_all_local_drives` and max_depth. `POST /api/helpers/discover` accepts optional paths/max_depth overrides. Scanning is bounded and read-only, using built-in and manifest signatures. Without configured roots or drive scanning, only HELPERS is scanned. Discovery selection is explicit; no copy, installation, update or automatic rediscovery occurs.

```
ctl helpers list
ctl helpers get nte-main
ctl -data @instance.json helpers add
ctl -data @instance.json helpers update nte-main
ctl helpers delete nte-main
ctl -data @preflight.json helpers preflight nte-main
ctl helper-definitions list
ctl helper-definitions reload
ctl -paths "D:/Automation;E:/PortableApps" -depth 4 discover
```

The upstream CLI requires global flags before resource names; that behavior remains. Existing games/tasks/plans/execs commands remain available. In the Helpers page, paths are entered as text because browser uploads do not provide a usable server-side installation picker.
