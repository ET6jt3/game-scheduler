# Continuation prompt — Game Scheduler portable helper hub

Continue in https://github.com/ET6jt3/game-scheduler on branch `portable-helper-hub`, PR https://github.com/ET6jt3/game-scheduler/pull/1. Do not merge or replace master automatically. The user authorized implementing the attached portable helper expansion on this fork. Existing First-Repo is unrelated.

Implementation commit: `ad5dc71710439f1cf411326ce11ee1890f13a5d9`.
Current source/harness commit at this handoff: `8f47c5800bfc93efdd585e2a6ed5c912fe14291f` (subsequent report-only commit may be the branch tip; run git rev-parse HEAD and inspect the diff).
Upstream baseline: `14f046281f0385cde8ac82312796e1d34ef70c73`.

Read IMPLEMENTATION-REPORT.md, TEST-REPORT.md, MIGRATION-NOTES.md and PORTABLE-LAYOUT.md before editing. Completed: canonical executable-relative paths, additive helper-instance persistence, manifest validation/reload/argv, ok-nte, API/CLI and Helpers page, discovery settings, exact preflight and execution diagnostics, portable launchers/packaging, lock protection, tests and CI.

Path contract: ROOT is the App directory containing server.exe. The package config explicitly maps sibling Data/Helpers/Runtime/Config. External paths remain external. Never copy/update/delete external helpers automatically. Manifest content is data, never shell code. Normal packaged scheduler requires no Go/Node/Python installation. HSR uses an explicit interpreter or its legacy documented default.

Validation already passed locally: untouched upstream test baseline, gofmt, Linux and Windows vet, 235 Go test/subtest pass events with zero failures, targeted race tests, Linux builds, Windows amd64 cross-build, JS syntax, and 24 Linux package HTTP/process smoke checks. Upstream GitHub CI and security passed at the implementation revision. Windows package/runtime smoke passed at source/harness revision 8f47c5800bfc93efdd585e2a6ed5c912fe14291f: https://github.com/ET6jt3/game-scheduler/actions/runs/35158410555. It includes 25 checks and Start/Stop launcher-script execution. See TEST-REPORT.md for evidence. A transient TestSerialization timing assertion failed once in an intermediate run; later full/race runs passed without suppressing it.

Unresolved items: browser access to localhost was blocked with net::ERR_BLOCKED_BY_CLIENT; visual/interactive UI verification is not claimed. Cross-drive relocation and actual interactive games need a Windows operator environment; never launch real games without opt-in. Optional workflows, automatic rediscovery and helper/runtime installers are deferred. Preserve upstream native controller/input gates.

Next step: verify Helpers UI add/edit/preflight/create-task flows on a reachable local browser. In particular validate task names, multiple instances and surfaced errors. Only after those acceptance gates, consider the optional workflow layer using the existing global execution queue.

The initial Windows smoke used captured stdout/stderr for PowerShell launcher subprocesses and stalled. The harness now writes launcher output to files, prints progress on stderr, and has a five-minute CI step timeout. A second Windows attempt reached relocation but failed a literal managed-path string comparison. The current harness checks actual file identity in the moved folder, accommodating Windows short/long aliases, and also exercises the Stop launcher. The scheduler source did not change in those harness-only commits. Do not report a compiled feature as runtime-verified without observed evidence.

Modified files versus upstream (at source/harness commit):

- `.github/workflows/portable.yml`
- `Config/helpers/ok-nte.json`
- `IMPLEMENTATION-REPORT.md`
- `MIGRATION-NOTES.md`
- `PORTABLE-LAYOUT.md`
- `TEST-REPORT.md`
- `cmd/ctl/main.go`
- `cmd/server/main.go`
- `examples/helper-instances.json`
- `internal/api/api.go`
- `internal/api/discover.go`
- `internal/api/helpers.go`
- `internal/api/helpers_test.go`
- `internal/api/web/helpers.html`
- `internal/api/web/index.html`
- `internal/config/config.go`
- `internal/config/portable.go`
- `internal/discover/discover.go`
- `internal/game/adapter.go`
- `internal/helper/hub.go`
- `internal/helper/hub_test.go`
- `internal/helper/manifest.go`
- `internal/helper/manifest_test.go`
- `internal/helper/ok-nte.json`
- `internal/portable/paths.go`
- `internal/portable/paths_test.go`
- `internal/singleinstance/lock.go`
- `internal/singleinstance/lock_test.go`
- `internal/singleinstance/lock_unix.go`
- `internal/singleinstance/lock_windows.go`
- `internal/store/helpers.go`
- `internal/store/helpers_test.go`
- `internal/store/store.go`
- `internal/task/helper_integration_test.go`
- `internal/task/helpers.go`
- `internal/task/service.go`
- `packaging/Portable.ps1`
- `packaging/README.md`
- `packaging/Start.cmd`
- `packaging/Stop.cmd`
- `packaging/config.example.json`
- `scripts/build-portable.ps1`
- `scripts/portable-smoke.py`
- `scripts/testdata/fake-helper/main.go`
