"""Minimal Game Scheduler observer for ok-nte's native lifecycle.

This module deliberately does NOT patch LauncherTask, NTEInteraction, capture,
cursor handling, or input backends. ok-nte owns launcher -> game -> daily task.
Game Scheduler only selects DailyRoutineTask, observes truthful completion, and
provides deterministic process-level diagnostics.
"""
from __future__ import annotations

import ctypes
import hashlib
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


class LauncherCaptureObserver:
    """Observe the native launcher capture without changing OK-NTE behavior."""

    def __init__(self, instance, launcher):
        self.instance = instance
        self.launcher = launcher
        self.stop_event = threading.Event()
        self.thread = None
        self.last_signature = None
        self.last_hash = None
        self.same_hash_since = None
        self.last_snapshot = 0.0
        self.snapshot_index = 0
        self.max_snapshots = 48
        self.last_stale_emit = 0.0
        self.directory = None
        base = os.environ.get("GS_OK_NTE_EVENT_DIR")
        if base:
            self.directory = os.path.join(base, "launcher-capture")
            try:
                os.makedirs(self.directory, exist_ok=True)
            except OSError:
                self.directory = None

    def start(self):
        emit(
            "LAUNCHER_CAPTURE_DIAGNOSTICS_READY",
            directory=self.directory,
            max_snapshots=self.max_snapshots,
        )
        self.thread = threading.Thread(
            target=self._run,
            name="GS-NTE-LauncherCaptureObserver",
            daemon=True,
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

    def _window_metadata(self, hwnd_window):
        data = {
            "hwnd": int(getattr(hwnd_window, "hwnd", 0) or 0),
            "top_hwnd": int(getattr(hwnd_window, "top_hwnd", 0) or 0),
            "exists": bool(getattr(hwnd_window, "exists", False)),
            "visible": bool(getattr(hwnd_window, "visible", False)),
            "pos_valid": bool(getattr(hwnd_window, "pos_valid", False)),
            "x": int(getattr(hwnd_window, "x", 0) or 0),
            "y": int(getattr(hwnd_window, "y", 0) or 0),
            "width": int(getattr(hwnd_window, "width", 0) or 0),
            "height": int(getattr(hwnd_window, "height", 0) or 0),
            "window_width": int(getattr(hwnd_window, "window_width", 0) or 0),
            "window_height": int(getattr(hwnd_window, "window_height", 0) or 0),
            "client_width": int(getattr(hwnd_window, "client_width", 0) or 0),
            "client_height": int(getattr(hwnd_window, "client_height", 0) or 0),
            "real_x_offset": int(getattr(hwnd_window, "real_x_offset", 0) or 0),
            "real_y_offset": int(getattr(hwnd_window, "real_y_offset", 0) or 0),
            "real_width": int(getattr(hwnd_window, "real_width", 0) or 0),
            "real_height": int(getattr(hwnd_window, "real_height", 0) or 0),
        }
        hwnd = data["hwnd"]
        if hwnd and os.name == "nt":
            try:
                import win32gui
                import win32process
                data["class_name"] = win32gui.GetClassName(hwnd)
                data["title"] = win32gui.GetWindowText(hwnd)
                data["window_rect"] = list(win32gui.GetWindowRect(hwnd))
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                data["pid"] = int(pid)
            except Exception as error:
                data["win32_error"] = type(error).__name__ + ": " + str(error)
        return data

    def _write_snapshot(self, frame, metadata, reason):
        if self.directory is None or self.snapshot_index >= self.max_snapshots:
            return None
        self.snapshot_index += 1
        stamp = "%d-%03d" % (time.time_ns(), self.snapshot_index)
        png_path = os.path.join(self.directory, "launcher-%s.png" % stamp)
        json_path = os.path.join(self.directory, "launcher-%s.json" % stamp)
        try:
            import cv2
            ok, encoded = cv2.imencode(".png", frame)
            if ok:
                encoded.tofile(png_path)
            else:
                png_path = None
        except Exception as error:
            metadata["snapshot_error"] = type(error).__name__ + ": " + str(error)
            png_path = None
        metadata = dict(metadata)
        metadata["reason"] = reason
        metadata["snapshot_png"] = png_path
        try:
            with open(json_path, "x", encoding="utf-8") as handle:
                json.dump(metadata, handle, ensure_ascii=True, indent=2, default=str)
        except OSError:
            json_path = None
        emit(
            "LAUNCHER_CAPTURE_SNAPSHOT",
            reason=reason,
            png=png_path,
            metadata=json_path,
            frame_hash=metadata.get("frame_hash"),
            hwnd=metadata.get("hwnd"),
            window_rect=metadata.get("window_rect"),
            pos_valid=metadata.get("pos_valid"),
        )
        return png_path

    def _sample(self):
        executor = self.instance.task_executor
        if getattr(executor, "current_task", None) is not self.launcher:
            return
        device_manager = getattr(executor, "device_manager", None)
        capture = getattr(device_manager, "capture_method", None)
        hwnd_window = getattr(device_manager, "hwnd_window", None)
        if capture is None or hwnd_window is None:
            emit("LAUNCHER_CAPTURE_UNAVAILABLE")
            return

        metadata = self._window_metadata(hwnd_window)
        metadata["capture_method"] = type(capture).__name__
        metadata["capture_connected"] = bool(capture.connected())
        metadata["capture_target_signature"] = repr(
            getattr(hwnd_window, "capture_target_signature", None)
        )

        # Observe the exact frame already acquired by OK-NTE's LauncherTask.
        # Do not request a second capture frame from this diagnostic thread:
        # another capture consumer could itself perturb WGC/BitBlt timing.
        frame = getattr(self.launcher, "frame", None)
        if frame is None:
            emit("LAUNCHER_CAPTURE_EMPTY", **metadata)
            return
        try:
            frame = frame.copy()
        except Exception:
            pass

        metadata["frame_shape"] = list(getattr(frame, "shape", ()))
        try:
            shape = metadata["frame_shape"]
            if len(shape) >= 2:
                height, width = int(shape[0]), int(shape[1])
                x1 = max(0, min(width, round(width * 0.8137)))
                y1 = max(0, min(height, round(height * 0.8678)))
                x2 = max(x1, min(width, round(width * 0.8387)))
                y2 = max(y1, min(height, round(height * 0.9022)))
                metadata["launcher_button_probe_box"] = [x1, y1, x2, y2]
                pixels = frame[:, :, :3]
                metadata["frame_mean"] = round(float(pixels.mean()), 4)
                metadata["frame_min"] = int(pixels.min())
                metadata["frame_max"] = int(pixels.max())
                if x2 > x1 and y2 > y1:
                    import cv2
                    region = frame[y1:y2, x1:x2, :3]
                    mask = cv2.inRange(region, (215, 215, 215), (225, 225, 225))
                    total = float(region.size) / 3.0
                    metadata["launcher_button_ready_percentage"] = (
                        float(cv2.countNonZero(mask)) / total if total else 0.0
                    )
        except Exception as error:
            metadata["frame_metric_error"] = type(error).__name__ + ": " + str(error)

        try:
            raw = memoryview(frame).cast("B")
            digest = hashlib.sha256(raw).hexdigest()
        except Exception:
            digest = hashlib.sha256(frame.tobytes()).hexdigest()
        metadata["frame_hash"] = digest

        signature = (
            metadata["hwnd"],
            metadata["top_hwnd"],
            metadata["x"],
            metadata["y"],
            metadata["width"],
            metadata["height"],
            metadata["capture_target_signature"],
        )
        now = time.monotonic()
        reason = None
        if self.last_signature != signature:
            reason = "target-signature-changed" if self.last_signature is not None else "launcher-observer-start"
            emit(
                "LAUNCHER_CAPTURE_TARGET",
                previous=repr(self.last_signature),
                current=repr(signature),
                **metadata,
            )
            self.last_signature = signature

        if self.last_hash == digest:
            if self.same_hash_since is None:
                self.same_hash_since = now
            stale_seconds = now - self.same_hash_since
            metadata["stale_seconds"] = round(stale_seconds, 3)
            if stale_seconds >= 15:
                if now - self.last_stale_emit >= 10:
                    emit(
                        "LAUNCHER_CAPTURE_STALE",
                        stale_seconds=round(stale_seconds, 3),
                        frame_hash=digest,
                        hwnd=metadata["hwnd"],
                        window_rect=metadata.get("window_rect"),
                        pos_valid=metadata["pos_valid"],
                        launcher_button_ready_percentage=metadata.get(
                            "launcher_button_ready_percentage"
                        ),
                    )
                    self.last_stale_emit = now
                if now - self.last_snapshot >= 30:
                    reason = reason or "stale-frame"
        else:
            if self.last_hash is not None:
                stale_before_change = (
                    now - self.same_hash_since
                    if self.same_hash_since is not None
                    else 0.0
                )
                emit(
                    "LAUNCHER_CAPTURE_CHANGED",
                    previous_hash=self.last_hash,
                    frame_hash=digest,
                    hwnd=metadata["hwnd"],
                    stale_before_change=round(stale_before_change, 3),
                )
                if stale_before_change >= 15:
                    emit(
                        "LAUNCHER_CAPTURE_RECOVERED",
                        stale_seconds=round(stale_before_change, 3),
                        previous_hash=self.last_hash,
                        frame_hash=digest,
                        hwnd=metadata["hwnd"],
                        launcher_button_ready_percentage=metadata.get(
                            "launcher_button_ready_percentage"
                        ),
                    )
                    reason = reason or "stale-frame-recovered"
            self.last_hash = digest
            self.same_hash_since = now

        if not metadata["pos_valid"]:
            emit(
                "LAUNCHER_CAPTURE_POSITION_INVALID",
                hwnd=metadata["hwnd"],
                x=metadata["x"],
                y=metadata["y"],
                width=metadata["width"],
                height=metadata["height"],
                window_rect=metadata.get("window_rect"),
            )
            if now - self.last_snapshot >= 10:
                reason = reason or "position-invalid"

        if reason and (now - self.last_snapshot >= 5 or reason == "launcher-observer-start"):
            self._write_snapshot(frame, metadata, reason)
            self.last_snapshot = now

    def _run(self):
        active = False
        while not self.stop_event.wait(2):
            try:
                is_launcher = getattr(
                    self.instance.task_executor, "current_task", None
                ) is self.launcher
                if is_launcher and not active:
                    active = True
                    emit("LAUNCHER_CAPTURE_OBSERVER_ACTIVE")
                elif active and not is_launcher:
                    emit("LAUNCHER_CAPTURE_OBSERVER_IDLE")
                    active = False
                if is_launcher:
                    self._sample()
            except BaseException as error:
                emit(
                    "LAUNCHER_CAPTURE_OBSERVER_ERROR",
                    error=type(error).__name__ + ": " + str(error),
                )


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
    observer = None
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
        observer = LauncherCaptureObserver(instance, launcher)
        observer.start()

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
        if observer is not None:
            observer.stop()
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
