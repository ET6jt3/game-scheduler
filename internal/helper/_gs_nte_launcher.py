"""Native NTE launcher input, isolated from the in-game NTEInteraction backend.

Only an existing recognized Start/Update/close control is clicked. A sent input
is never game-readiness proof; the real game HWND and capture must appear.
"""
import ctypes
import inspect
import time
import types
from ctypes import wintypes as W


class MouseInput(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_int32), ('dy', ctypes.c_int32),
                ('mouseData', ctypes.c_uint32), ('dwFlags', ctypes.c_uint32),
                ('time', ctypes.c_uint32), ('dwExtraInfo', ctypes.c_size_t)]


class InputUnion(ctypes.Union):
    _fields_ = [('mi', MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [('type', ctypes.c_uint32), ('data', InputUnion)]


def desktop_click(desktop, hwnd, point, Failure):
    """Click the recognized launcher control through checked foreground input."""
    desktop.position(hwnd, point)  # session, foreground, bounds and cursor checks
    u = desktop.u
    u.WindowFromPoint.argtypes, u.WindowFromPoint.restype = [W.POINT], W.HWND
    u.IsChild.argtypes, u.IsChild.restype = [W.HWND, W.HWND], W.BOOL
    u.SendInput.argtypes = [W.UINT, ctypes.POINTER(Input), ctypes.c_int]
    u.SendInput.restype = W.UINT
    hit = u.WindowFromPoint(W.POINT(*map(round, point)))
    # Same process is not enough: a modal dialog may belong to it, too.
    if not hit or not (hit == hwnd or u.IsChild(hwnd, hit)):
        raise Failure('LAUNCHER_CLICK_OBSTRUCTED', 'A different window covers the detected launcher control', 29)
    desktop.check()
    if u.GetForegroundWindow() != (u.GetAncestor(hwnd, 2) or hwnd):
        raise Failure('FOREGROUND_DENIED', 'Launcher lost foreground before click', 26)
    event = Input()
    event.type, event.data.mi.dwFlags = 0, 0x0002  # left down
    if u.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
        raise Failure('LAUNCHER_INPUT_REJECTED', 'Windows rejected launcher mouse-down', 29)
    try:
        time.sleep(0.08)
    finally:
        # Always release the button this call pressed, even if focus changes.
        event.data.mi.dwFlags = 0x0004
        if u.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise Failure('LAUNCHER_INPUT_REJECTED', 'Windows rejected launcher mouse-up', 29)


def patch_launcher(task, guard, Failure, emit, click_driver=desktop_click):
    """Wrap only the loaded LauncherTask instance, without editing helper files."""
    names = ('run', 'click', '_launcher_button_state')
    original = {name: getattr(task, name, None) for name in names}
    if not all(callable(fn) for fn in original.values()):
        raise Failure('LAUNCHER_INTERFACE_UNSUPPORTED', 'LauncherTask interface changed', 24)
    try:
        launcher_exes = task.capture_config.LAUNCHER_CAPTURE_CONFIG['windows']['exe']
        game_exes = task.capture_config.GAME_CAPTURE_CONFIG['windows']['exe']
        signature = inspect.signature(original['click'])
        if 'x' not in signature.parameters:
            raise ValueError('click has no x parameter')
    except (KeyError, AttributeError, TypeError, ValueError) as error:
        raise Failure('LAUNCHER_INTERFACE_UNSUPPORTED', str(error), 24)
    owned = {name: name in vars(task) for name in names}
    attempts, last_click = {}, {}
    state_seen = None

    def button_state(_self, *args, **kwargs):
        nonlocal state_seen
        result = original['_launcher_button_state'](*args, **kwargs)
        ready, box = result
        name = str(getattr(box, 'name', 'not-found')).rsplit('.', 1)[-1]
        state = (bool(ready), name)
        if state != state_seen:
            state_seen = state
            emit('LAUNCHER_CONTROL', control=name, ready=bool(ready))
        if (name == 'launcher_update' and not ready and
                not guard.update_extension and guard.launch_deadline):
            # Visual state, not a downloaded-byte counter. Extend only ONCE.
            guard.update_extension = True
            guard.launch_deadline += guard.update_budget
            emit('LAUNCHER_UPDATE_WAIT', extra_budget_seconds=guard.update_budget,
                 progress_verified=False)
        return result

    def click(_self, *args, **kwargs):
        try:
            guard.poll()
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            box = bound.arguments.get('x')
            name = str(getattr(box, 'name', bound.arguments.get('name', ''))).rsplit('.', 1)[-1]
            if name not in ('launcher_start', 'launcher_update', 'launcher_popup_close'):
                raise Failure('LAUNCHER_CONTROL_UNSUPPORTED', 'Refusing an unrecognized launcher click', 29)
            if task._find_process(game_exes):
                emit('LAUNCHER_STALE_CLICK_SKIPPED', control=name)
                return False
            now = time.monotonic()
            if now - last_click.get(name, float('-inf')) < 10:
                return False
            if attempts.get(name, 0) >= 3:
                raise Failure('LAUNCHER_NO_TRANSITION', name + ' remained after three click attempts', 29)
            proc, hwnd = task._find_process_window(launcher_exes, require_title=True)
            interaction = task.executor.interaction
            if not proc or not hwnd or interaction.hwnd_window.hwnd != hwnd:
                raise Failure('LAUNCHER_CAPTURE_MISMATCH', 'Capture is not attached to the native launcher', 29)
            if (not all(hasattr(box, attr) for attr in ('x', 'y', 'width', 'height')) or
                    box.width <= 0 or box.height <= 0):
                raise Failure('LAUNCHER_CONTROL_UNSUPPORTED', 'Matched control rectangle is unavailable', 29)
            guard.desktop.foreground(hwnd)
            point = interaction.capture.get_abs_cords(box.x + box.width / 2,
                                                       box.y + box.height / 2)
            emit('LAUNCHER_CLICK_ATTEMPT', control=name, hwnd=int(hwnd),
                 attempt=attempts.get(name, 0) + 1)
            click_driver(guard.desktop, hwnd, point, Failure)
            attempts[name] = attempts.get(name, 0) + 1
            last_click[name] = guard.last_input = time.monotonic()
            emit('LAUNCHER_CLICK_SENT', control=name, process_started=False)
            delay = bound.arguments.get('after_sleep', 0) or 0
            if delay:
                task.sleep(delay)
            return True
        except Exception as error:
            failure = error if isinstance(error, Failure) else Failure('LAUNCHER_CLICK_FAILED', str(error), 29)
            raise guard.fail(failure)

    def run(_self, *args, **kwargs):
        if guard.launch_deadline is None:
            guard.launch_deadline = time.monotonic() + guard.launch_budget
        guard.update_extension = False
        emit('LAUNCHER_BEGIN', timeout_seconds=guard.launch_budget)
        try:
            value = original['run'](*args, **kwargs)
            guard.poll()
            proc, hwnd = task._find_process_window(game_exes)
            capture_hwnd = task.executor.interaction.hwnd_window.hwnd
            if not proc or not hwnd or capture_hwnd != hwnd or not task.executor.connected():
                raise Failure('GAME_NOT_READY', 'Launcher returned without game window/capture readiness', 29)
            emit('GAME_READY', hwnd=int(hwnd))
            return value
        except BaseException as error:
            failure = error if isinstance(error, Failure) else Failure('LAUNCHER_FAILED', str(error), 29)
            raise guard.fail(failure)
        finally:
            guard.launch_deadline = None

    task.run = types.MethodType(run, task)
    task.click = types.MethodType(click, task)
    task._launcher_button_state = types.MethodType(button_state, task)
    def restore():
        for name, method in original.items():
            if owned[name]:
                setattr(task, name, method)
            elif name in vars(task):
                delattr(task, name)
    return restore
