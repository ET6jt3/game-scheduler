# Download a ready-to-run Windows package from GitHub

You do **not** need to clone the repository or install Go just to run/test a checkpoint.

The `Portable Windows acceptance` GitHub Actions workflow builds the Windows x64
portable package on every push to `portable-helper-hub`, on pull requests, and
when manually dispatched. After a successful run, open the run's **Artifacts**
section and download **GameScheduler-Portable-Windows-x64**. GitHub workflow
artifacts are the supported way to retain and download build output from an
Actions run.

The artifact contains the runtime ZIP and SHA256 checksum. Extract
`GameScheduler-Portable-Windows-x64.zip`, then double-click `Start.cmd`.
The runtime package does not require Go, Node, Python, or an installer. Python is
only needed by a helper that itself requires Python.

For an existing installation, stop it first and retain its `Config`, `Data`,
`Helpers`, and `Runtime` directories when updating. See `AUTOMATION.md`.

---

# Build on Windows without installing Go

From the repository folder, run `Build.cmd` (PowerShell: `.\Build.cmd`).
Windows PowerShell 5.1 is sufficient. Administrator access, a Go MSI, and system
PATH edits are unnecessary. The first build needs internet access for the official
Go ZIP and module dependencies. Subsequent builds reuse the local toolchain/cache.

The compiler is pinned to Go 1.26.8, matching go.mod. The bootstrap downloads
`https://go.dev/dl/go1.26.8.windows-amd64.zip` and checks its pinned SHA256 against
the value published at https://go.dev/dl/#go1.26.8 before extracting or executing it.
The scheduler package and build toolchain target Windows x64.

Everything managed by the Go build stays beneath `Toolchain/`:

| Directory | Contents |
| --- | --- |
| `Toolchain/go1.26.8-windows-amd64/go` | Compiler and standard library |
| `Toolchain/downloads` | Verified Go ZIP |
| `Toolchain/cache/build` | Go build cache |
| `Toolchain/cache/modules` | Downloaded Go modules |
| `Toolchain/gopath` | GOPATH |
| `Toolchain/tmp` | Go and child-process temporary files |
| `Toolchain/bin` | Go-installed tools, if requested |
| `Toolchain/profile` | Go settings and disabled telemetry state |

The wrapper invokes the compiler by absolute path. GOENV is disabled, automatic
toolchain downloads and telemetry are disabled, and inherited workspace/GOFLAGS
settings are ignored. All changed process environment settings are restored on
success or failure. No user/machine environment variables are changed. OS-managed
activity outside the repository is not controlled by these scripts.

The first package goes into `dist/GameScheduler-Portable`. If that folder or its
ZIP already exists, a new `dist/build-<timestamp>-<id>/GameScheduler-Portable`
folder is chosen automatically. Existing configurations, databases, and binaries
are preserved. Explicit `-OutputDir` still requires a fresh destination. A failed
build can leave its partial output; another default build selects a new folder.

After success, the console prints the full `Start.cmd` path, also recorded in
`dist/last-build.txt`. Stop any old package with its own `Stop.cmd` before starting
the new package on the same port. Fresh builds have fresh settings; migration of
existing Config/Data is an explicit separate operation after stopping the server.

For direct compiler commands in PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\portable-go.ps1 version
```

Keep the repository together when relocating it. Paths are recalculated on every
invocation. `Toolchain/` and generated packages are ignored by Git and are not
included in the runtime ZIP. The runtime package itself needs no compiler.

CI verifies checksum rejection, cold installation with Go/Git removed from PATH,
two successful builds, preservation of the old package, local Go paths, caller
environment restoration, compiler relocation into a path with spaces, and the
existing Windows package smoke tests. See the PR Actions results for execution
status; adding these checks is not itself a claim they passed.
