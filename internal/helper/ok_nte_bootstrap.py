"""Dual-mode unattended interactive-desktop adapter for ok-nte.

Embedded by Game Scheduler; never installed into the external helper/game.
Supports a strict no-global-mouse mode and the v2 cursor-compatible mode.
Neither mode supports a locked desktop, Session 0, or displayless execution.
"""
from __future__ import annotations
import ctypes
import functools
import inspect
import json
import os
import sys
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from ctypes import wintypes as W
from dataclasses import dataclass

VERSION = "unattended-desktop-v3-dual-input"
_EVENT_FILE = None
_EVENT_LOCK = threading.Lock()
_EVENT_BYTES = 0


class Failure(RuntimeError):
    def __init__(self, reason, message, code=21):
        super().__init__(message)
        self.reason, self.code = reason, code


def emit(event, **data):
    global _EVENT_BYTES
    line = "GS_OK_NTE_EVENT=" + json.dumps(dict(event=event, adapter=VERSION,
            time=time.time(), **data), ensure_ascii=True)
    with _EVENT_LOCK:
        if _EVENT_FILE is not None and _EVENT_BYTES < 8 * 1024 * 1024:
            try:
                _EVENT_FILE.write(line + "\n")
                _EVENT_FILE.flush()
                _EVENT_BYTES += len(line) + 1
            except OSError:
                pass  # stdout remains authoritative if the diagnostic disk fails
        print(line, flush=True)


def is_elevated():
    if os.name != "nt":
        return False
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.IsUserAnAdmin.argtypes = []
        shell32.IsUserAnAdmin.restype = W.BOOL
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def deadline_option(name, default, low, high):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = float("nan")
    if not low <= value <= high:
        raise Failure("INVALID_POLICY", name + " is outside its allowed range", 24)
    return value


@dataclass(frozen=True)
class State:
    session: int
    active: bool
    visible: bool
    desktop: str
    input_desktop: str
    monitors: int
    cursor: bool


def validate(state):
    if state.session == 0 or not state.visible:
        raise Failure("NONINTERACTIVE_SESSION", "Run after user sign-in, not as a Windows service")
    if not state.active:
        raise Failure("SESSION_DISCONNECTED", "The worker's user session is not active")
    if (state.desktop.casefold() != "default" or
            state.input_desktop.casefold() != state.desktop.casefold()):
        raise Failure("DESKTOP_UNAVAILABLE", "Input desktop is locked, switched, or unavailable")
    if state.monitors < 1:
        raise Failure("DISPLAY_UNAVAILABLE", "No active display surface is enumerated")


class Desktop:
    def __init__(self):
        if os.name != "nt":
            raise Failure("WINDOWS_REQUIRED", "Windows interactive desktop required", 24)
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.w = ctypes.WinDLL("wtsapi32", use_last_error=True)
        self.mutex = None
        self.power = False
        # Explicit pointer-sized signatures: default ctypes int truncates HWNDs.
        signatures = [
            (self.k, "ProcessIdToSessionId", [W.DWORD, ctypes.POINTER(W.DWORD)], W.BOOL),
            (self.k, "GetCurrentThreadId", [], W.DWORD),
            (self.k, "CreateMutexW", [W.LPVOID, W.BOOL, W.LPCWSTR], W.HANDLE),
            (self.k, "CloseHandle", [W.HANDLE], W.BOOL),
            (self.k, "SetThreadExecutionState", [W.DWORD], W.DWORD),
            (self.u, "GetProcessWindowStation", [], W.HANDLE),
            (self.u, "GetThreadDesktop", [W.DWORD], W.HANDLE),
            (self.u, "OpenInputDesktop", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            (self.u, "CloseDesktop", [W.HANDLE], W.BOOL),
            (self.u, "GetUserObjectInformationW", [W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD)], W.BOOL),
            (self.u, "GetCursorPos", [ctypes.POINTER(W.POINT)], W.BOOL),
            (self.u, "SetCursorPos", [ctypes.c_int, ctypes.c_int], W.BOOL),
            (self.u, "GetSystemMetrics", [ctypes.c_int], ctypes.c_int),
            (self.u, "IsWindow", [W.HWND], W.BOOL),
            (self.u, "IsIconic", [W.HWND], W.BOOL),
            (self.u, "GetAncestor", [W.HWND, W.UINT], W.HWND),
            (self.u, "GetForegroundWindow", [], W.HWND),
            (self.u, "SetForegroundWindow", [W.HWND], W.BOOL),
            (self.u, "ShowWindowAsync", [W.HWND, ctypes.c_int], W.BOOL),
            (self.u, "GetWindowThreadProcessId", [W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
            (self.u, "GetClientRect", [W.HWND, ctypes.POINTER(W.RECT)], W.BOOL),
            (self.u, "ClientToScreen", [W.HWND, ctypes.POINTER(W.POINT)], W.BOOL),
            (self.u, "PostMessageW", [W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            (self.w, "WTSQuerySessionInformationW", [W.HANDLE, W.DWORD, ctypes.c_int, ctypes.POINTER(W.LPVOID), ctypes.POINTER(W.DWORD)], W.BOOL),
            (self.w, "WTSFreeMemory", [W.LPVOID], None),
        ]
        for dll, name, args, result in signatures:
            proc = getattr(dll, name)
            proc.argtypes, proc.restype = args, result

    def name(self, handle):
        buffer, size = ctypes.create_unicode_buffer(256), W.DWORD()
        if not handle or not self.u.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(size)):
            return ""
        return buffer.value

    def snapshot(self):
        session, size, pointer = W.DWORD(), W.DWORD(), W.LPVOID()
        if not self.k.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise Failure("SESSION_QUERY_FAILED", "Process session lookup failed")
        active = False
        if self.w.WTSQuerySessionInformationW(None, session.value, 8, ctypes.byref(pointer), ctypes.byref(size)):
            try:
                if pointer and size.value >= ctypes.sizeof(ctypes.c_int):
                    active = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] == 0
            finally:
                self.w.WTSFreeMemory(pointer)
        class Flags(ctypes.Structure):
            _fields_ = [("inherit", W.BOOL), ("reserved", W.BOOL), ("flags", W.DWORD)]
        flags = Flags()
        visible = bool(self.u.GetUserObjectInformationW(self.u.GetProcessWindowStation(),
                       1, ctypes.byref(flags), ctypes.sizeof(flags), ctypes.byref(size)) and flags.flags & 1)
        desktop = self.name(self.u.GetThreadDesktop(self.k.GetCurrentThreadId()))
        handle = self.u.OpenInputDesktop(0, False, 1)  # read only; never switch/unlock
        try:
            input_desktop = self.name(handle)
        finally:
            if handle:
                self.u.CloseDesktop(handle)
        point = W.POINT()
        return State(session.value, active, visible, desktop, input_desktop,
                     self.u.GetSystemMetrics(80), bool(self.u.GetCursorPos(ctypes.byref(point))))

    def check(self):
        state = self.snapshot()
        validate(state)
        return state

    def __enter__(self):
        state = self.check()
        self.owner = threading.get_ident()
        ctypes.set_last_error(0)
        self.mutex = self.k.CreateMutexW(None, False, "Local\\GameScheduler-ok-nte-desktop-v1")
        error = ctypes.get_last_error()
        if not self.mutex:
            raise Failure("DESKTOP_LEASE_FAILED", "Cannot acquire NTE worker lease")
        if error == 183:
            self.close()
            raise Failure("DESKTOP_BUSY", "Another managed NTE worker owns this session")
        if not self.k.SetThreadExecutionState(0x80000003):
            self.close()
            raise Failure("POWER_LEASE_FAILED", "Cannot hold idle display/system awake")
        self.power = True
        emit("DESKTOP_READY", session=state.session, monitors=state.monitors,
             human_activity_required=False, interactive_desktop_required=True)
        return self

    def close(self):
        if self.power:
            if self.owner != threading.get_ident():
                raise RuntimeError("Power request must be released on its owning thread")
            self.k.SetThreadExecutionState(0x80000000)
            self.power = False
        if self.mutex:
            self.k.CloseHandle(self.mutex)
            self.mutex = None

    def __exit__(self, *_):
        self.close()

    def target(self, hwnd):
        state = self.check()
        pid, session = W.DWORD(), W.DWORD()
        if not hwnd or not self.u.IsWindow(hwnd):
            raise Failure("GAME_WINDOW_UNAVAILABLE", "Target window unavailable", 26)
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or not self.k.ProcessIdToSessionId(pid.value, ctypes.byref(session)) or session.value != state.session:
            raise Failure("WRONG_WINDOW_SESSION", "Target is outside the worker session", 26)

    def foreground(self, hwnd):
        self.target(hwnd)
        root = self.u.GetAncestor(hwnd, 2) or hwnd
        if self.u.IsIconic(root):
            self.u.ShowWindowAsync(root, 9)
        if self.u.GetForegroundWindow() != root:
            self.u.SetForegroundWindow(root)
        end = time.monotonic() + 2
        while self.u.IsIconic(root) or self.u.GetForegroundWindow() != root:
            self.check()
            if time.monotonic() >= end:
                raise Failure("FOREGROUND_DENIED", "Windows did not grant foreground input", 26)
            time.sleep(0.05)

    def position(self, hwnd, point=None):
        self.foreground(hwnd)
        rect, corner, current = W.RECT(), W.POINT(), W.POINT()
        if not self.u.GetClientRect(hwnd, ctypes.byref(rect)) or rect.right <= 0 or rect.bottom <= 0:
            raise Failure("INVALID_CLIENT_AREA", "Target has no usable client area", 26)
        if not self.u.ClientToScreen(hwnd, ctypes.byref(corner)):
            raise Failure("COORDINATE_FAILURE", "Cannot resolve target coordinates", 26)
        def inside(x, y):
            return corner.x <= x < corner.x + rect.right and corner.y <= y < corner.y + rect.bottom
        if point is None:
            if self.u.GetCursorPos(ctypes.byref(current)) and inside(current.x, current.y):
                return  # Never recenter an already valid relative camera movement.
            point = corner.x + rect.right // 2, corner.y + rect.bottom // 2
        x, y = map(round, point)
        if not inside(x, y):
            raise Failure("CURSOR_TARGET_OUTSIDE_GAME", "Refusing out-of-client cursor target", 26)
        if not self.u.SetCursorPos(x, y):
            raise Failure("CURSOR_MOVE_FAILED", "SetCursorPos failed before input", 26)
        if not self.u.GetCursorPos(ctypes.byref(current)) or abs(current.x-x) > 2 or abs(current.y-y) > 2:
            raise Failure("CURSOR_MOVE_REJECTED", "Cursor clipped or displaced before input", 26)

    def post(self, hwnd, message, wparam, lparam):
        self.target(hwnd)
        if not self.u.PostMessageW(hwnd, message, wparam, lparam):
            raise Failure("POSTMESSAGE_FAILED", "Windows rejected target window input", 26)


class Guard:
    def __init__(self, desktop, mode="cursor-compatible", requested_mode=None):
        self.desktop, self.failure, self.instance = desktop, None, None
        self.mode = mode
        self.requested_mode = requested_mode or mode
        self.audit = {
            "post_messages": 0, "virtual_moves": 0, "uia_invokes": 0,
            "launcher_post_clicks": 0, "compat_clicks": 0,
            "cursor_positions": 0, "send_input": 0, "block_input": 0,
        }
        self.stop, self.lock = threading.Event(), threading.Lock()
        self.last_input, self.running, self.completion_deadline = time.monotonic(), False, None
        self.init_deadline = time.monotonic() + deadline_option("GS_OK_NTE_INIT_TIMEOUT", 180, 5, 1800)
        self.launch_deadline = None
        self.update_extension = False
        self.launch_budget = deadline_option("GS_OK_NTE_LAUNCH_TIMEOUT", 300, 30, 1800)
        self.update_budget = deadline_option("GS_OK_NTE_UPDATE_TIMEOUT", 1800, 30, 21600)
        self.next_heartbeat = time.monotonic()
        self.idle = deadline_option("GS_OK_NTE_NO_INPUT_TIMEOUT", 600, 30, 21600)

    def count(self, name, amount=1):
        with self.lock:
            self.audit[name] = self.audit.get(name, 0) + amount

    def audit_snapshot(self):
        with self.lock:
            return dict(self.audit)

    def verify_input_contract(self):
        if self.mode == "strict-no-mouse":
            audit = self.audit_snapshot()
            forbidden = {name: audit.get(name, 0) for name in
                         ("cursor_positions", "send_input", "block_input")
                         if audit.get(name, 0)}
            if forbidden:
                raise self.fail(Failure(
                    "STRICT_INPUT_CONTRACT_BREACH",
                    "Strict mode used global mouse APIs: " + repr(forbidden), 26))
        return self.audit_snapshot()

    def fail(self, error):
        with self.lock:
            if self.failure is None:
                self.failure = error
                emit("BLOCKED", reason=error.reason, message=str(error))
        self.stop.set()
        return error

    def check(self):
        if self.failure:
            raise self.failure
        try:
            self.desktop.check()
        except Failure as error:
            raise self.fail(error)

    def poll(self, now=None):
        self.check()
        now = time.monotonic() if now is None else now
        if now >= self.next_heartbeat:
            phase = "initializing" if self.init_deadline else "launcher" if self.launch_deadline else "daily" if self.running else "dispatch-or-cleanup"
            emit("HEARTBEAT", phase=phase, launcher_seconds_left=(round(max(0, self.launch_deadline-now)) if self.launch_deadline else None))
            self.next_heartbeat = now + 5
        if self.launch_deadline and now >= self.launch_deadline:
            raise self.fail(Failure("LAUNCHER_TIMEOUT", "Native launcher did not establish game/capture readiness within its fixed budget", 29))
        if self.init_deadline and now >= self.init_deadline:
            raise self.fail(Failure("RUNTIME_INIT_TIMEOUT", "Initialization deadline exceeded", 23))
        if self.completion_deadline and now >= self.completion_deadline:
            raise self.fail(Failure("TASK_EXIT_TIMEOUT", "Task exit-after cleanup timed out", 27))
        if self.running and now - self.last_input > self.idle:
            raise self.fail(Failure("NO_INPUT_PROGRESS", "Daily routine made no input progress", 25))

    def start(self):
        def monitor():
            while not self.stop.wait(0.5):
                try:
                    self.poll()
                except Exception as error:
                    self.fail(error if isinstance(error, Failure) else Failure("MONITOR_FAILED", str(error)))
                    break
            if self.failure:
                time.sleep(5)
                os._exit(self.failure.code)  # own worker only; no cross-process kills
        threading.Thread(target=monitor, name="GSDesktopWatchdog", daemon=True).start()


from _gs_nte_launcher import patch_input, patch_launcher


class Proof:
    def __init__(self, task, guard):
        self.task, self.guard = task, guard
        self.started = self.returned = self.done = False
        self.result = None
        original = task.do_run
        def run(*args, **kwargs):
            self.started, guard.running = True, True
            guard.last_input = time.monotonic()
            try:
                self.result = original(*args, **kwargs)
                self.returned = True
                return self.result
            finally:
                guard.running = False
        task.do_run = run
    def on_done(self, task, *_):
        if task is self.task:
            self.done = True
            self.guard.completion_deadline = time.monotonic() + 30
    def verify(self):
        self.guard.check()
        status = getattr(self.task, "task_status", None)
        if not isinstance(status, dict) or not all(isinstance(status.get(k), list) for k in ("success", "failed", "skipped", "pending")):
            raise Failure("TASK_RESULT_UNVERIFIABLE", "Daily result schema unavailable", 20)
        if not (self.started and self.returned and self.done and self.result is True):
            raise Failure("TASK_NOT_COMPLETED", "Daily routine did not prove completion", 20)
        if status["failed"] or status["pending"]:
            raise Failure("TASK_ITEMS_FAILED", "Daily items failed or remain pending", 20)
        if not status["success"] and not status["skipped"]:
            raise Failure("EMPTY_ROUTINE", "No daily items were accounted for", 20)
        return status


def wait_detector(services, guard):
    if not getattr(services, "_started", False):
        raise Failure("RUNTIME_SERVICES_NOT_STARTED", "Runtime-ready event did not start services", 23)
    future = getattr(services, "_openvino_model_future", None)
    if future is None:
        raise Failure("RUNTIME_UNSUPPORTED", "OpenVINO readiness Future unavailable", 24)
    while True:
        guard.poll()
        try:
            detector = future.result(timeout=0.25)
            if detector is None:
                raise Failure("DETECTOR_INIT_FAILED", "Detector returned no object", 23)
            return detector
        except FutureTimeout:
            continue
        except Exception as error:
            raise Failure("DETECTOR_INIT_FAILED", str(error), 23)


def load_runtime(guard):
    # Establish identity/headless mode BEFORE imports. No CLI index/auto-dispatch.
    sys.argv = [os.path.join(os.getcwd(), "main.py"), "--headless"]
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    from src.config import config
    from src.patches.startup_patches import install_startup_patches
    install_startup_patches(config)
    from src.interaction.NTEInteraction import NTEInteraction
    from ok.device.intercation import PostMessageInteraction
    restore = patch_input(NTEInteraction, PostMessageInteraction, guard, Failure)
    import ok
    from ok.core.events import communicate
    instance = ok.OK(config)
    guard.instance = instance
    if getattr(instance, "_app", None) is not None:
        raise Failure("GUI_INITIALIZED_UNEXPECTEDLY", "GUI initialized in unattended mode", 24)
    headless = instance.headless_app
    headless.initialize_overlay()
    tasks = [t for t in instance.task_executor.onetime_tasks if type(t).__name__ == "DailyRoutineTask"
             and type(t).__module__ == "src.tasks.daily.DailyRoutineTask"]
    if len(tasks) != 1 or not callable(getattr(tasks[0], "do_run", None)):
        raise Failure("DAILY_TASK_UNAVAILABLE", "Expected exactly one compatible DailyRoutineTask", 24)
    communicate.start_success.emit()
    wait_detector(getattr(getattr(ok.og, "my_app", None), "_runtime_services", None), guard)
    guard.init_deadline = None
    print("GS_OK_NTE_RUNTIME_READY=1", flush=True)
    emit("RUNTIME_READY", version=config.get("version"), task_class=type(tasks[0]).__name__)
    return instance, tasks[0], communicate, restore


def execute(desktop_factory=Desktop, runtime_loader=load_runtime):
    guard = instance = proof = restore = None
    code, outcome = 24, {"ok": False, "reason": "NOT_STARTED", "status": None}
    try:
        requested_mode = os.environ.get("GS_OK_NTE_INPUT_MODE", "cursor-compatible").strip().lower()
        aliases = {"unattended-desktop": "cursor-compatible",
                   "strict": "strict-no-mouse",
                   "compat": "cursor-compatible"}
        mode = aliases.get(requested_mode, requested_mode)
        if mode not in ("auto", "strict-no-mouse", "cursor-compatible"):
            raise Failure("INPUT_MODE_UNSUPPORTED",
                          "Expected auto, strict-no-mouse, or cursor-compatible", 24)
        with desktop_factory() as desktop:
            guard = Guard(desktop, mode=mode, requested_mode=requested_mode)
            emit("INPUT_MODE_REQUESTED", requested=requested_mode, selected=mode)
            if os.environ.get("GS_OK_NTE_DOCTOR") == "1":
                outcome.update(reason="DESKTOP_CHECK_ONLY", desktop_ready=True)
                return 28, outcome  # Diagnostic is never daily-task success.
            if runtime_loader is load_runtime and not is_elevated():
                raise Failure(
                    "ADMIN_REQUIRED",
                    "ok-nte PC LauncherTask requires administrator rights. "
                    "For a manual trial, restart Game Scheduler elevated; "
                    "for unattended logon, use Setup-Startup.cmd.",
                    30)
            guard.start()
            try:
                instance, task, events, restore = runtime_loader(guard)
                if runtime_loader is load_runtime:
                    launchers = [t for t in instance.task_executor.onetime_tasks
                                 if type(t).__name__ == "LauncherTask" and type(t).__module__ == "src.tasks.LauncherTask"]
                    if len(launchers) != 1:
                        raise Failure("LAUNCHER_INTERFACE_UNSUPPORTED", "Expected exactly one LauncherTask", 24)
                    restore_input = restore
                    restore_launcher = patch_launcher(launchers[0], guard, Failure, emit)
                    def restore():
                        restore_launcher()
                        if restore_input:
                            restore_input()
                proof = Proof(task, guard)
                events.task_done.connect(proof.on_done)
                emit("TASK_DISPATCH", input_mode=guard.mode, requested_input_mode=requested_mode,
                     task_class=type(task).__name__)
                guard.launch_deadline = time.monotonic() + guard.launch_budget
                instance.run_onetime_task(task, exit_after=True)
                status = proof.verify()
                audit = guard.verify_input_contract()
                outcome.update(ok=True, reason="TASK_COMPLETED", status=status,
                               input_mode=guard.mode, requested_input_mode=requested_mode,
                               input_audit=audit)
                emit("INPUT_AUDIT", input_mode=guard.mode, audit=audit)
                code = 0
            finally:
                guard.stop.set()
                if proof:
                    outcome["status"] = getattr(proof.task, "task_status", None)
                if guard:
                    outcome["input_mode"] = guard.mode
                    outcome["requested_input_mode"] = guard.requested_mode
                    outcome["input_audit"] = guard.audit_snapshot()
                instance = instance or guard.instance
                if instance:
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
                if restore:
                    restore()
    except Failure as error:
        code = error.code
        outcome.update(ok=False, reason=error.reason, message=str(error))
    except BaseException as error:
        code = 22
        outcome.update(ok=False, reason="WORKER_EXCEPTION", message=type(error).__name__ + ": " + str(error))
    if guard and guard.failure:
        code = guard.failure.code
        outcome.update(ok=False, reason=guard.failure.reason, message=str(guard.failure))
    return code, outcome


def main():
    # python._pth may ignore environment settings; configure actual pipes too.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    global _EVENT_FILE
    directory = os.environ.get("GS_OK_NTE_EVENT_DIR")
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
            filename = os.path.join(directory, "nte-%d-%d.jsonl" % (time.time_ns(), os.getpid()))
            _EVENT_FILE = open(filename, "x", encoding="utf-8", buffering=1)
            print("GS_OK_NTE_EVENT_LOG=" + filename, flush=True)
        except OSError as error:
            print("GS_OK_NTE_EVENT_LOG_ERROR=" + str(error), file=sys.stderr, flush=True)
    emit("WORKER_STARTED", pid=os.getpid())
    code, outcome = execute()
    emit("WORKER_RESULT", code=code, outcome=outcome)
    print("GS_OK_NTE_STATUS=" + json.dumps(outcome, ensure_ascii=True, default=str), flush=True)
    if _EVENT_FILE is not None:
        _EVENT_FILE.close()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # No orphan non-daemon Python thread can hold the chain open.


if __name__ == "__main__":
    main()
