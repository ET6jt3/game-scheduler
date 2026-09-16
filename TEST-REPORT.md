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
| Windows runtime, launchers and process-tree tests | Pending GitHub Actions; not verified locally |
| Helpers page browser interaction/visual QA | Not completed; browser call interrupted |
| Real game execution | Not run; intentionally requires operator opt-in |

The package smoke executed only a purpose-built harmless fixture. It verified health, duplicate-server rejection, external and managed preflight, exact external path, NTE exit true/false and invalid index, child argv with spaces/Unicode/shell metacharacters/empty strings, execution diagnostics, two instances, independent enabled state, manifest reload without rebuild, restart persistence, saved discovery roots, relocation of managed paths, unchanged external paths and registration deletion without deleting helper files.

An intermediate upstream TestSerialization timing assertion failed once during a full test run; the next full run and later race/full runs passed. That observation is retained here rather than hidden. No test was disabled or altered to obtain a pass.

`scripts/ci-local.ps1` is the upstream Windows development-machine gate and includes optional environment-dependent scanners/native-controller work. It was not executed on this Linux host. Required Go equivalents were run; native Rust/controller code was not modified. The new portable workflow runs Windows Go gates and package smoke; it does not claim completion of unavailable upstream developer-machine tools.
