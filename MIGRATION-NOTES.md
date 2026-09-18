# Migration notes

1. Stop the old scheduler cleanly. Back up its config and entire data directory while stopped, including any SQLite WAL/SHM files still present.
2. Existing games/tasks/routes/plans/executions remain in place. Opening the database adds helper_instances, helper_settings and execution_diagnostics in an idempotent transaction. Existing rows are not rewritten and helper instances are optional.
3. Paths are now anchored to the executable directory instead of the launcher's current directory. Before using an existing database, set data_dir and db_path to its actual location. Convert any legacy paths that depended on another launch directory to absolute paths or explicit `${ROOT}` variables. Do not point a new package at an unintended empty database.
4. A packaged server lives in App. The shipped config explicitly maps the sibling Data, Helpers, Runtime and Config folders. Legacy absolute helper paths stay absolute. Stop the server before moving the entire package.
5. Add instances through Helpers or the API. Connect new tasks through helper_instance_id; existing unlinked games continue working. For HSR Python projects, supply runtime_path and the project's main.py path, or retain the original python_path/march7th_dir/fhoe_dir extra_config keys.
6. Manifest and instance executables do not silently fall back to similarly named PATH programs. Set an explicit path; a manifest may explicitly allow PATH. The legacy HSR default `python` retains its documented lookup behavior.
7. Disable or update plans before removing a referenced instance: the task is retained and future executions fail explicitly. No external helper files are changed by migration, discovery, scheduling, or registration removal.
8. Roll back using the stopped backup and original server. Do not run old and new servers concurrently on one database. The new server holds an OS file lock next to the database before orphan reconciliation.

No automatic copying of helper installations, portable Python downloads, native-controller replacement, workflow layer or real game test is performed.
