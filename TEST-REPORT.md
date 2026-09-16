# Test report

Local platform: Linux amd64; Go 1.26.8. Baseline: upstream 14f046281f0385cde8ac82312796e1d34ef70c73.

| Gate | Result |
| --- | --- |
| Unmodified upstream go test ./... | PASS |
| gofmt -l . | PASS, no output |
| go vet ./... | PASS |
| go test -json ./... | PASS, 235 named test/subtest pass events, zero failures |
| go test -race ./internal/helper ./internal/game ./internal/task ./internal/store | PASS |
| go build ./cmd/server | PASS |
| go build ./cmd/ctl | PASS |
| Helpers JavaScript node --check | PASS |
| Packaged Linux HTTP/process smoke | PASS, 24 checks |
| Windows amd64 cross-build and vet | PASS (CGO disabled; server.exe and ctl.exe produced) |
| Windows Go gates and package smoke | PASS on GitHub Windows Server 2025; 25 smoke checks plus graceful Stop launcher |
| Helpers page browser interaction/visual QA | Blocked: browser returned net::ERR_BLOCKED_BY_CLIENT for localhost |
| Real game execution | Not run; intentionally requires operator opt-in |

The package smoke executed only a purpose-built harmless fixture. It verified health, duplicate-server rejection, external and managed preflight, exact external path, NTE exit true/false and invalid index, child argv with spaces/Unicode/shell metacharacters/empty strings, execution diagnostics, two instances, independent enabled state, manifest reload without rebuild, restart persistence, saved discovery roots, relocation of managed paths, unchanged external paths and registration deletion without deleting helper files.

An intermediate upstream TestSerialization timing assertion failed once during a full test run; the next full run and later race/full runs passed. That observation is retained here rather than hidden. No test was disabled or altered to obtain a pass.

`scripts/ci-local.ps1` is the upstream Windows development-machine gate and includes optional environment-dependent scanners/native-controller work. It was not executed on this Linux host. Required Go equivalents were run; native Rust/controller code was not modified. The new portable workflow runs Windows Go gates and package smoke; it does not claim completion of unavailable upstream developer-machine tools.

## GitHub acceptance evidence

Validated source/harness revision: `8f47c5800bfc93efdd585e2a6ed5c912fe14291f`. Production Go/UI source is unchanged from `ad5dc71710439f1cf411326ce11ee1890f13a5d9`; the later commits refine only the smoke harness/workflow.

- Portable Windows gates, ZIP build, exact argv, persistence, same-file managed relocation, external-path preservation and PowerShell Start/Stop smoke: https://github.com/ET6jt3/game-scheduler/actions/runs/35158410555
- Upstream CI (Linux and Windows): https://github.com/ET6jt3/game-scheduler/actions/runs/35157549767 (initial implementation), with subsequent same-source runs also passing.
- Security (govulncheck and full race suite): https://github.com/ET6jt3/game-scheduler/actions/runs/35158410591

The first Windows smoke attempt stalled with captured launcher pipes. A revised harness removed that capture and reached relocation, where a literal string comparison failed for Windows path aliases. The final assertion requires `samefile` identity with the existing helper in the moved directory, then executes both helpers again. This keeps the relocation requirement and accommodates canonical short/long Windows paths. No production assertion was removed or real game launched. Failed attempts remain visible in Actions.

A documentation-only follow-up commit may be newer than the verified source/harness revision above; compare its diff before attributing these results to code changes.
