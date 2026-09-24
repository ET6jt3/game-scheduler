"""Minimal Game Scheduler observer for ok-nte's native lifecycle.

This module deliberately does NOT patch LauncherTask, NTEInteraction, capture,
cursor handling, or input backends. ok-nte owns launcher -> game -> daily task.
Game Scheduler only selects DailyRoutineTask, observes truthful completion, and
provides deterministic process-level diagnostics.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time

VERSION = "native-lifecycle-v1"
_EVENT_FILE = None
_EVENT_LOCK = threading.Lock()
_EVENT_BYTES = 0


class Failure(RuntimeError):
    def __init__(self, reason, message, code=21):
        super().__init__(message)
        self.reason = reason
        self.code = code


def emit(event, **data):
    global _EVENT_BYTES
    line = "GS_OK_NTE_EVENT=" + json.dumps(
        dict(event=event, adapter=VERSION, time=time.time(), **data),
        ensure_ascii=True,
        default=str,
    )
    with _EVENT_LOCK:
        if _EVENT_FILE is not None and _EVENT_BYTES < 8 * 1024 * 1024:
            try:
                _EVENT_FILE.write(line + "\n")
                _EVENT_FILE.flush()
                _EVENT_BYTES += len(line) + 1
            except OSError:
                pass
        print(line, flush=True)


def is_elevated():
    if os.name != "nt":
        return False
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.IsUserAnAdmin.argtypes = []
        shell32.IsUserAnAdmin.restype = ctypes.c_int
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def select_native_tasks(instance):
    daily = [
        task for task in instance.task_executor.onetime_tasks
        if type(task).__name__ == "DailyRoutineTask"
        and type(task).__module__ == "src.tasks.daily.DailyRoutineTask"
    ]
    launcher = [
        task for task in instance.task_executor.onetime_tasks
        if type(task).__name__ == "LauncherTask"
        and type(task).__module__ == "src.tasks.LauncherTask"
    ]
    if len(daily) != 1:
        raise Failure(
            "DAILY_TASK_UNAVAILABLE",
            "Expected exactly one native DailyRoutineTask",
            24,
        )
    if len(launcher) != 1 or not bool(getattr(launcher[0], "enable_after_start", False)):
        raise Failure(
            "NATIVE_LAUNCHER_UNAVAILABLE",
            "Expected exactly one native enable_after_start LauncherTask",
            24,
        )
    return daily[0], launcher[0]


class Proof:
    def __init__(self, task):
        self.task = task
        self.started = False
        self.returned = False
        self.done = False
        self.result = None
        original = task.do_run

        def run(*args, **kwargs):
            self.started = True
            self.result = original(*args, **kwargs)
            self.returned = True
            return self.result

        self.original = original
        task.do_run = run

    def on_done(self, task, *_):
        if task is self.task:
            self.done = True

    def restore(self):
        self.task.do_run = self.original

    def verify(self):
        status = getattr(self.task, "task_status", None)
        if not isinstance(status, dict) or not all(
            isinstance(status.get(key), list)
            for key in ("success", "failed", "skipped", "pending")
        ):
            raise Failure(
                "TASK_RESULT_UNVERIFIABLE",
                "DailyRoutineTask result schema unavailable",
                20,
            )
        if not (self.started and self.returned and self.done and self.result is True):
            raise Failure(
                "TASK_NOT_COMPLETED",
                "Native DailyRoutineTask did not prove completion",
                20,
            )
        if status["failed"] or status["pending"]:
            raise Failure(
                "TASK_ITEMS_FAILED",
                "Daily items failed or remain pending",
                20,
            )
        if not status["success"] and not status["skipped"]:
            raise Failure(
                "EMPTY_ROUTINE",
                "No daily items were accounted for",
                20,
            )
        return status


def load_runtime():
    # Force ok-script's own headless app facade while preserving native tasks.
    sys.argv = [os.path.join(os.getcwd(), "main.py"), "--headless"]
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    from src.config import config
    from src.patches.startup_patches import install_startup_patches

    install_startup_patches(config)

    import ok
    from ok.core.events import communicate

    instance = ok.OK(config)
    if getattr(instance, "_app", None) is not None:
        raise Failure(
            "GUI_INITIALIZED_UNEXPECTEDLY",
            "Native unattended lifecycle unexpectedly initialized Qt",
            24,
        )

    task, launcher = select_native_tasks(instance)

    # This is the same runtime-start signal used by ok-script's own
    # start_runtime(). It starts ok-nte RuntimeServices without modifying
    # launcher/capture/input behavior.
    instance.headless_app.initialize_overlay()
    communicate.start_success.emit()

    print("GS_OK_NTE_RUNTIME_READY=1", flush=True)
    emit(
        "RUNTIME_READY",
        version=config.get("version"),
        task_class=type(task).__name__,
        launcher_class=type(launcher).__name__,
        native_launcher=True,
    )
    return instance, task, launcher, communicate


def execute(runtime_loader=load_runtime, elevation_check=is_elevated):
    instance = None
    proof = None
    events = None
    outcome = {
        "ok": False,
        "reason": "NOT_STARTED",
        "status": None,
        "lifecycle": "native",
    }
    code = 24
    try:
        if os.name == "nt" and runtime_loader is load_runtime and not elevation_check():
            raise Failure(
                "ADMIN_REQUIRED",
                "ok-nte's native PC LauncherTask requires administrator rights. "
                "Use Run-Elevated.cmd for a manual run or Setup-Startup.cmd "
                "for unattended elevated logon.",
                30,
            )

        instance, task, launcher, events = runtime_loader()
        proof = Proof(task)
        events.task_done.connect(proof.on_done)

        emit(
            "NATIVE_LIFECYCLE_BEGIN",
            launcher_class=type(launcher).__name__,
            launcher_enable_after_start=bool(
                getattr(launcher, "enable_after_start", False)
            ),
            task_class=type(task).__name__,
        )
        emit("TASK_DISPATCH", task_class=type(task).__name__, lifecycle="native")

        # Native ok-script StartController automatically queues every
        # enable_after_start task (LauncherTask) before this requested task.
        instance.run_onetime_task(task, exit_after=True)

        status = proof.verify()
        outcome.update(
            ok=True,
            reason="TASK_COMPLETED",
            status=status,
            lifecycle="native",
            launcher_owned_by="ok-nte",
        )
        emit("NATIVE_LIFECYCLE_COMPLETED", status=status)
        code = 0
    except Failure as error:
        code = error.code
        outcome.update(ok=False, reason=error.reason, message=str(error))
    except BaseException as error:
        code = 22
        outcome.update(
            ok=False,
            reason="WORKER_EXCEPTION",
            message=type(error).__name__ + ": " + str(error),
        )
    finally:
        if proof is not None:
            outcome["status"] = getattr(proof.task, "task_status", None)
            try:
                if events is not None:
                    events.task_done.disconnect(proof.on_done)
            except Exception:
                pass
            proof.restore()
        if instance is not None:
            done = threading.Event()

            def cleanup_watch():
                if not done.wait(10):
                    emit("CLEANUP_TIMEOUT")
                    os._exit(27)

            threading.Thread(target=cleanup_watch, daemon=True).start()
            try:
                instance.quit()
            finally:
                done.set()
    return code, outcome


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(
                encoding="utf-8",
                errors="backslashreplace",
                line_buffering=True,
            )

    global _EVENT_FILE
    directory = os.environ.get("GS_OK_NTE_EVENT_DIR")
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
            filename = os.path.join(
                directory,
                "nte-native-%d-%d.jsonl" % (time.time_ns(), os.getpid()),
            )
            _EVENT_FILE = open(filename, "x", encoding="utf-8", buffering=1)
            print("GS_OK_NTE_EVENT_LOG=" + filename, flush=True)
        except OSError as error:
            print(
                "GS_OK_NTE_EVENT_LOG_ERROR=" + str(error),
                file=sys.stderr,
                flush=True,
            )

    emit("WORKER_STARTED", pid=os.getpid(), lifecycle="native")
    code, outcome = execute()
    emit("WORKER_RESULT", code=code, outcome=outcome)
    print(
        "GS_OK_NTE_STATUS="
        + json.dumps(outcome, ensure_ascii=True, default=str),
        flush=True,
    )
    if _EVENT_FILE is not None:
        _EVENT_FILE.close()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
