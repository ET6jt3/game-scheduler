"""Native NTE launcher input for dual unattended input modes.

The launcher control must first be recognized by upstream ok-nte. Strict mode
tries Windows UI Automation and then a targeted PostMessage click without moving
the global cursor. Compatibility mode retains the checked foreground SendInput
path from v2. No backend treats an input attempt as proof that the game started.
"""
import ctypes
import inspect
import os
import shutil
import subprocess
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


_UIA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $hwnd = [Int64]$env:GS_NTE_UIA_HWND
    $x = [Double]::Parse($env:GS_NTE_UIA_X, [Globalization.CultureInfo]::InvariantCulture)
    $y = [Double]::Parse($env:GS_NTE_UIA_Y, [Globalization.CultureInfo]::InvariantCulture)
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$hwnd)
    if ($null -eq $root) { Write-Output 'UNAVAILABLE'; exit 3 }
    $all = $root.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition)
    $best = $null
    $bestArea = [Double]::PositiveInfinity
    foreach ($element in $all) {
        try {
            $rect = $element.Current.BoundingRectangle
            if (-not $element.Current.IsEnabled) { continue }
            if ($x -lt $rect.Left -or $x -ge $rect.Right -or
                $y -lt $rect.Top -or $y -ge $rect.Bottom) { continue }
            $pattern = $null
            if ($element.TryGetCurrentPattern(
                    [System.Windows.Automation.InvokePattern]::Pattern,
                    [ref]$pattern)) {
                $area = [Math]::Max(1.0, $rect.Width * $rect.Height)
                if ($area -lt $bestArea) {
                    $best = $pattern
                    $bestArea = $area
                }
            }
        } catch {}
    }
    if ($null -eq $best) { Write-Output 'UNAVAILABLE'; exit 3 }
    $best.Invoke()
    Write-Output 'INVOKED'
    exit 0
} catch {
    Write-Error $_
    exit 4
}
"""


def uia_invoke(hwnd, point, Failure, timeout=5):
    """Invoke the smallest enabled UIA control containing the recognized point."""
    powershell = shutil.which('powershell.exe') or shutil.which('powershell')
    if not powershell:
        return False
    env = os.environ.copy()
    env['GS_NTE_UIA_HWND'] = str(int(hwnd))
    env['GS_NTE_UIA_X'] = format(float(point[0]), '.6f')
    env['GS_NTE_UIA_Y'] = format(float(point[1]), '.6f')
    try:
        result = subprocess.run(
            [powershell, '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-Command', _UIA_SCRIPT],
            env=env, capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired as error:
        raise Failure('LAUNCHER_UIA_TIMEOUT', 'UI Automation invoke timed out', 29) from error
    output = (result.stdout or '').strip()
    if result.returncode == 0 and 'INVOKED' in output:
        return True
    if result.returncode == 3 and 'UNAVAILABLE' in output:
        return False
    message = (result.stderr or output or ('exit ' + str(result.returncode))).strip()
    raise Failure('LAUNCHER_UIA_FAILED', message[-1000:], 29)


def postmessage_click(desktop, hwnd, point, Failure):
    """Send a virtual hover/click to the launcher child under the recognized point."""
    desktop.target(hwnd)
    u = desktop.u
    u.WindowFromPoint.argtypes, u.WindowFromPoint.restype = [W.POINT], W.HWND
    u.IsChild.argtypes, u.IsChild.restype = [W.HWND, W.HWND], W.BOOL
    u.ScreenToClient.argtypes, u.ScreenToClient.restype = [W.HWND, ctypes.POINTER(W.POINT)], W.BOOL
    screen = W.POINT(*map(round, point))
    hit = u.WindowFromPoint(screen)
    if not hit or not (hit == hwnd or u.IsChild(hwnd, hit)):
        raise Failure('LAUNCHER_CLICK_OBSTRUCTED',
                      'A different window covers the detected launcher control', 29)
    local = W.POINT(screen.x, screen.y)
    if not u.ScreenToClient(hit, ctypes.byref(local)):
        raise Failure('LAUNCHER_COORDINATE_FAILURE',
                      'Cannot resolve launcher child coordinates', 29)
    lparam = (local.x & 0xffff) | ((local.y & 0xffff) << 16)
    desktop.post(hit, 0x0200, 0, lparam)       # WM_MOUSEMOVE
    desktop.post(hit, 0x0201, 0x0001, lparam)  # WM_LBUTTONDOWN / MK_LBUTTON
    time.sleep(0.05)
    desktop.post(hit, 0x0202, 0, lparam)       # WM_LBUTTONUP
    return True


def desktop_click(desktop, hwnd, point, Failure):
    """Compatibility click through checked foreground input."""
    desktop.position(hwnd, point)
    u = desktop.u
    u.WindowFromPoint.argtypes, u.WindowFromPoint.restype = [W.POINT], W.HWND
    u.IsChild.argtypes, u.IsChild.restype = [W.HWND, W.HWND], W.BOOL
    u.SendInput.argtypes = [W.UINT, ctypes.POINTER(Input), ctypes.c_int]
    u.SendInput.restype = W.UINT
    hit = u.WindowFromPoint(W.POINT(*map(round, point)))
    if not hit or not (hit == hwnd or u.IsChild(hwnd, hit)):
        raise Failure('LAUNCHER_CLICK_OBSTRUCTED',
                      'A different window covers the detected launcher control', 29)
    desktop.check()
    if u.GetForegroundWindow() != (u.GetAncestor(hwnd, 2) or hwnd):
        raise Failure('FOREGROUND_DENIED', 'Launcher lost foreground before click', 26)
    event = Input()
    event.type, event.data.mi.dwFlags = 0, 0x0002
    if u.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
        raise Failure('LAUNCHER_INPUT_REJECTED',
                      'Windows rejected launcher mouse-down', 29)
    try:
        time.sleep(0.08)
    finally:
        event.data.mi.dwFlags = 0x0004
        if u.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise Failure('LAUNCHER_INPUT_REJECTED',
                          'Windows rejected launcher mouse-up', 29)
    return True


def patch_launcher(task, guard, Failure, emit, compat_driver=desktop_click,
                   uia_driver=uia_invoke, post_driver=postmessage_click):
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

    def count(name, amount=1):
        fn = getattr(guard, 'count', None)
        if callable(fn):
            fn(name, amount)

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
            guard.update_extension = True
            guard.launch_deadline += guard.update_budget
            emit('LAUNCHER_UPDATE_WAIT', extra_budget_seconds=guard.update_budget,
                 progress_verified=False)
        return result

    def dispatch(hwnd, point):
        mode = getattr(guard, 'mode', 'cursor-compatible')
        if mode == 'cursor-compatible':
            count('cursor_positions')
            count('send_input', 2)
            count('compat_clicks')
            compat_driver(guard.desktop, hwnd, point, Failure)
            return 'compat-sendinput', True
        invoked = uia_driver(hwnd, point, Failure)
        if invoked:
            count('uia_invokes')
            if mode == 'auto':
                guard.mode = 'strict-no-mouse'
                emit('INPUT_MODE_SELECTED', requested='auto',
                     selected='strict-no-mouse', reason='launcher-uia-available')
            return 'uia-invoke', False
        if mode == 'auto':
            guard.mode = 'cursor-compatible'
            emit('INPUT_MODE_SELECTED', requested='auto',
                 selected='cursor-compatible', reason='launcher-uia-unavailable')
            count('cursor_positions')
            count('send_input', 2)
            count('compat_clicks')
            compat_driver(guard.desktop, hwnd, point, Failure)
            return 'compat-sendinput', True
        count('post_messages', 3)
        count('virtual_moves')
        count('launcher_post_clicks')
        post_driver(guard.desktop, hwnd, point, Failure)
        return 'postmessage', False

    def click(_self, *args, **kwargs):
        try:
            guard.poll()
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            box = bound.arguments.get('x')
            name = str(getattr(box, 'name',
                               bound.arguments.get('name', ''))).rsplit('.', 1)[-1]
            if name not in ('launcher_start', 'launcher_update', 'launcher_popup_close'):
                raise Failure('LAUNCHER_CONTROL_UNSUPPORTED',
                              'Refusing an unrecognized launcher click', 29)
            if task._find_process(game_exes):
                emit('LAUNCHER_STALE_CLICK_SKIPPED', control=name)
                return False
            now = time.monotonic()
            if now - last_click.get(name, float('-inf')) < 10:
                return False
            if attempts.get(name, 0) >= 3:
                raise Failure('LAUNCHER_NO_TRANSITION',
                              name + ' remained after three click attempts', 29)
            proc, hwnd = task._find_process_window(launcher_exes, require_title=True)
            interaction = task.executor.interaction
            if not proc or not hwnd or interaction.hwnd_window.hwnd != hwnd:
                raise Failure('LAUNCHER_CAPTURE_MISMATCH',
                              'Capture is not attached to the native launcher', 29)
            if (not all(hasattr(box, attr) for attr in ('x', 'y', 'width', 'height')) or
                    box.width <= 0 or box.height <= 0):
                raise Failure('LAUNCHER_CONTROL_UNSUPPORTED',
                              'Matched control rectangle is unavailable', 29)
            point = interaction.capture.get_abs_cords(
                box.x + box.width / 2, box.y + box.height / 2)
            emit('LAUNCHER_CLICK_ATTEMPT', control=name, hwnd=int(hwnd),
                 attempt=attempts.get(name, 0) + 1,
                 requested_mode=getattr(guard, 'mode', 'cursor-compatible'))
            backend, global_mouse = dispatch(hwnd, point)
            attempts[name] = attempts.get(name, 0) + 1
            last_click[name] = guard.last_input = time.monotonic()
            emit('LAUNCHER_CLICK_SENT', control=name, process_started=False,
                 backend=backend, global_mouse=global_mouse)
            delay = bound.arguments.get('after_sleep', 0) or 0
            if delay:
                task.sleep(delay)
            return True
        except Exception as error:
            failure = error if isinstance(error, Failure) else Failure(
                'LAUNCHER_CLICK_FAILED', str(error), 29)
            raise guard.fail(failure)

    def run(_self, *args, **kwargs):
        if guard.launch_deadline is None:
            guard.launch_deadline = time.monotonic() + guard.launch_budget
        guard.update_extension = False
        emit('LAUNCHER_BEGIN', timeout_seconds=guard.launch_budget,
             input_mode=getattr(guard, 'mode', 'cursor-compatible'))
        try:
            value = original['run'](*args, **kwargs)
            guard.poll()
            proc, hwnd = task._find_process_window(game_exes)
            capture_hwnd = task.executor.interaction.hwnd_window.hwnd
            if not proc or not hwnd or capture_hwnd != hwnd or not task.executor.connected():
                raise Failure('GAME_NOT_READY',
                              'Launcher returned without game window/capture readiness', 29)
            emit('GAME_READY', hwnd=int(hwnd))
            return value
        except BaseException as error:
            failure = error if isinstance(error, Failure) else Failure(
                'LAUNCHER_FAILED', str(error), 29)
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
