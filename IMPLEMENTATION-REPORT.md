# Implementation report — portable helper hub

Upstream baseline: `xiabee/game-scheduler` commit `14f046281f0385cde8ac82312796e1d34ef70c73`.
Destination: `ET6jt3/game-scheduler`, branch `portable-helper-hub`.

Implemented:

- Canonical executable-relative ROOT, configurable DATA/HELPERS/RUNTIME and supported Windows profile variables. Relative assets relocate; explicit external paths retain their identity.
- Additive transactional persistence for independently configured helper instances, discovery settings and immutable execution-path diagnostics. Legacy game/task APIs remain.
- Built-in helper definitions for BetterGI, March7th/Fhoe-Rail, ok-wuthering-waves and M9A; embedded, first-class ok-nte manifest plus editable sample.
- Version-1 declarative manifests, typed validation, enum/range checks, conditional and repeated argv, literal raw argv, dynamic metadata and atomic reload. No shell construction or template execution.
- Shared instance/path resolution for preflight and execution through the existing runner. Python runtime, project and entry checks; explicit disabled/missing instance errors.
- Read-only configurable discovery with built-in and manifest signatures, explicit match selection and no automatic filesystem mutation.
- Helpers dashboard page for paths, enabled states, discovery, task configuration and preflight. New API and CLI operations, plus a graceful server stop API under the existing auth/origin protections.
- Portable Windows packaging script, batch launchers, PowerShell startup health checks, localhost defaults, startup logs, a per-package launcher mutex and a database OS lock.
- Tests for path relocation, priorities, Unicode/spaces, NTE args, manifest validation/reload, instance isolation, migration idempotence, API authentication, exact child argv and persisted diagnostics. Harmless cross-platform package smoke and Windows Actions workflow.

Preserved execution architecture: bounded shared execution slots, scheduled overlap suppression, manual queueing, retries, timeout, cancellation, Windows process-tree cleanup, stale execution reconciliation, execution history, failure screenshots, SSE, auth and native controller dispatch. The changes do not introduce a second runner or increase default concurrency.

Verification: see TEST-REPORT.md. Compilation alone is not represented as runtime verification. The browser interaction attempt was interrupted; UI interaction/visual acceptance remains unverified. Windows runtime acceptance is delegated to the included GitHub Actions workflow and must be checked separately.

Explicit limitations:

- Optional workflow layer and automatic rediscovery are deferred. Discovered instances retain the selected path; missing paths fail rather than selecting an arbitrary installation.
- Managed mode registers a managed path. It does not download, install or copy helper files.
- Native controller and Python/helper environments are not bundled or modified. The package includes server and ctl, and supports an explicitly supplied compatible controller at build time.
- Windows smoke covers relocation across directories with spaces/Unicode; cross-drive relocation and real interactive game operation still require operator verification. No actual game was launched.

NTE CLI source verified from `BnanZ0/ok-nte/docs/en/guides/quick-start.md`: task selection uses `-t` and exit-after-task uses `-e`. Numeric task meanings are not hard-coded.
