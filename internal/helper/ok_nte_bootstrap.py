"""Unattended *interactive-desktop* adapter. No human mouse activity is needed.

Embedded by Game Scheduler; never installed into the external helper/game.
Not a cursor-free, locked-desktop, or Session-0 backend. No lock-policy changes.
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

VERSION = "unattended-desktop-v1"


class Failure(RuntimeError):
    def __init__(self, reason, message, code=21):
        super().__init__(message)
        self.reason, self.code = reason, code


def emit(event, **data):
    print("GS_OK_NTE_EVENT=" + json.dumps(dict(event=event, adapter=VERSION, **data),
          ensure_ascii=True), flush=True)


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
    if not state.cursor:
        raise Failure("CURSOR_UNAVAILABLE", "Windows cursor APIs are unavailable")


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
    def __init__(self, desktop):
        self.desktop, self.failure, self.instance = desktop, None, None
        self.stop, self.lock = threading.Event(), threading.Lock()
        self.last_input, self.running, self.completion_deadline = time.monotonic(), False, None
        self.init_deadline = time.monotonic() + deadline_option("GS_OK_NTE_INIT_TIMEOUT", 180, 5, 1800)
        self.idle = deadline_option("GS_OK_NTE_NO_INPUT_TIMEOUT", 600, 30, 21600)

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


def patch_input(cls, base, guard):
    names = ("__init__", "click", "operate", "scroll", "send_key", "send_key_down",
             "send_key_up", "move_mouse_relative", "_restore_cursor", "block_input", "unblock_input")
    original = {name: getattr(cls, name, None) for name in names}
    if not all(callable(fn) for fn in original.values()) or not callable(getattr(base, "post", None)):
        raise Failure("INPUT_BACKEND_UNSUPPORTED", "Required NTE input interface missing", 24)
    for name, keys in (("click", ("x", "y", "move", "move_back")), ("operate", ("block", "restore_cursor"))):
        if not all(key in inspect.signature(original[name]).parameters for key in keys):
            raise Failure("INPUT_BACKEND_UNSUPPORTED", "NTE signature changed: " + name, 24)
    original_post = base.post
    def post(obj, message, wParam=0, lParam=0, hwnd=None):
        try:
            guard.check()
            guard.desktop.post(obj.hwnd if hwnd is None else hwnd, message, wParam, lParam)
            # Count actual input messages, not synthetic activation polling.
            if 0x100 <= message <= 0x109 or 0x200 <= message <= 0x20E:
                guard.last_input = time.monotonic()
        except Failure as error:
            raise guard.fail(error)
    def init(obj, *args, **kwargs):
        original["__init__"](obj, *args, **kwargs)
        sync = getattr(obj, "_cursor_sync", None)
        if not callable(getattr(sync, "stop", None)):
            raise guard.fail(Failure("INPUT_BACKEND_UNSUPPORTED", "CursorSync interface missing", 24))
        sync.stop()
        thread = getattr(sync, "_thread", None)
        if thread and thread is not threading.current_thread():
            thread.join(1)
            if thread.is_alive():
                raise guard.fail(Failure("CURSOR_SYNC_STOP_FAILED", "Cursor synchronization did not stop", 26))
    def wrap(name):
        function, signature = original[name], inspect.signature(original[name])
        @functools.wraps(function)
        def call(obj, *args, **kwargs):
            with obj._input_lock:
                try:
                    guard.check()
                    hwnd = obj.hwnd_window.hwnd
                    guard.desktop.foreground(hwnd)
                    bound = signature.bind(obj, *args, **kwargs)
                    bound.apply_defaults()
                    if name == "click":
                        x, y = bound.arguments["x"], bound.arguments["y"]
                        if x < 0 or y < 0:
                            x, y = round(obj.capture.width/2), round(obj.capture.height/2)
                        guard.desktop.position(hwnd, obj.capture.get_abs_cords(x, y))
                        bound.arguments.update(x=x, y=y, move=False, move_back=False)
                    elif name == "operate":
                        bound.arguments.update(block=False, restore_cursor=False)
                    else:
                        guard.desktop.position(hwnd)
                    result = function(*bound.args, **bound.kwargs)
                    if name == "move_mouse_relative":
                        if result != 1:
                            raise Failure("INPUT_REJECTED", "SendInput did not insert its event", 26)
                        guard.last_input = time.monotonic()
                    guard.check()
                    return result
                except Failure as error:
                    raise guard.fail(error)
                except Exception as error:
                    raise guard.fail(Failure("INPUT_OPERATION_FAILED", name + ": " + str(error), 26))
        return call
    base.post, cls.__init__ = post, init
    for name in ("_restore_cursor", "block_input", "unblock_input"):
        setattr(cls, name, lambda self: None)
    for name in names[1:8]:
        setattr(cls, name, wrap(name))
    def restore():
        base.post = original_post
        for name, fn in original.items():
            setattr(cls, name, fn)
    return restore


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
    restore = patch_input(NTEInteraction, PostMessageInteraction, guard)
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
        mode = os.environ.get("GS_OK_NTE_INPUT_MODE", "unattended-desktop")
        if mode != "unattended-desktop":
            raise Failure("INPUT_MODE_UNSUPPORTED", "Cursor-free/locked-desktop mode is not supported", 24)
        with desktop_factory() as desktop:
            guard = Guard(desktop)
            if os.environ.get("GS_OK_NTE_DOCTOR") == "1":
                outcome.update(reason="DESKTOP_CHECK_ONLY", desktop_ready=True)
                return 28, outcome  # Diagnostic is never daily-task success.
            guard.start()
            try:
                instance, task, events, restore = runtime_loader(guard)
                proof = Proof(task, guard)
                events.task_done.connect(proof.on_done)
                emit("TASK_DISPATCH", input_mode=mode, task_class=type(task).__name__)
                instance.run_onetime_task(task, exit_after=True)
                outcome.update(ok=True, reason="TASK_COMPLETED", status=proof.verify())
                code = 0
            finally:
                guard.stop.set()
                if proof:
                    outcome["status"] = getattr(proof.task, "task_status", None)
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
    code, outcome = execute()
    print("GS_OK_NTE_STATUS=" + json.dumps(outcome, ensure_ascii=True, default=str), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # No orphan non-daemon Python thread can hold the chain open.


if __name__ == "__main__":
    main()
