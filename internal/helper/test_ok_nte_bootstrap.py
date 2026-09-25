"""Behavioral fixtures. No game, GPU model, physical input, or external packages."""
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import threading
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("gsnte", Path(__file__).with_name("ok_nte_bootstrap.py"))
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)


class FakeDesktop:
    def __init__(self):
        self.problem, self.closed, self.cursor = None, False, (-400, -400)
        self.messages = []
    def check(self):
        if self.problem:
            raise self.problem
    def foreground(self, hwnd):
        self.check()
        if not hwnd:
            raise b.Failure("GAME_WINDOW_UNAVAILABLE", "fixture", 26)
    def position(self, hwnd, point=None):
        self.foreground(hwnd)
        self.cursor = point or (960, 540)
    def post(self, hwnd, message, wp, lp):
        self.check()
        self.messages.append((hwnd, message, wp, lp))
    def __enter__(self):
        self.check()
        return self
    def __exit__(self, *_):
        self.closed = True


class Signal:
    def __init__(self):
        self.handlers = []
    def connect(self, handler):
        self.handlers.append(handler)
    def emit(self, *args):
        for handler in self.handlers:
            handler(*args)


def inputs():
    class Base:
        def post(self, *args, **kwargs):
            raise AssertionError("Original upstream PostMessage can swallow failures")
        def move(self, x, y, down_btn=0):
            lparam = (int(x) & 0xffff) | ((int(y) & 0xffff) << 16)
            self.post(0x200, down_btn, lparam)
            return lparam
    class NTE(Base):
        def __init__(self):
            self.calls = []
            self._input_lock = threading.RLock()
            self._cursor_sync = types.SimpleNamespace(stop=lambda: self.calls.append("sync_stop"), _thread=None)
            self.hwnd_window = types.SimpleNamespace(hwnd=99)
            self.hwnd = 99
            self.capture = types.SimpleNamespace(width=1920, height=1080, get_abs_cords=lambda x, y: (x+100, y+100))
        def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.01, move=True, key="left"):
            self.calls.append(("click", x, y, move_back, move, key))
            self.post(0x201, 1, 0)
            self.post(0x202, 0, 0)
        def operate(self, fun, block=False, restore_cursor=True):
            self.calls.append(("operate", block, restore_cursor))
            return fun()
        def scroll(self, x, y, scroll_amount):
            self.post(0x20A, scroll_amount, 0)
        def send_key(self, key, down_time=0.01):
            self.send_key_down(key)
            self.send_key_up(key)
        def send_key_down(self, key, activate=True):
            self.post(0x100, 70, 0)
        def send_key_up(self, key):
            self.post(0x101, 70, 0)
        def move_mouse_relative(self, dx, dy):
            return self.send_result
        def _restore_cursor(self):
            self.calls.append("restore")
        def block_input(self):
            self.calls.append("block")
        def unblock_input(self):
            self.calls.append("unblock")
    NTE.send_result = 1
    return NTE, Base


def daily(result=True, status=None):
    class DailyRoutineTask:
        def __init__(self):
            self.task_status = status if status is not None else {"success": ["a"], "failed": [], "skipped": [], "pending": []}
        def do_run(self):
            return result
    DailyRoutineTask.__module__ = "src.tasks.daily.DailyRoutineTask"
    return DailyRoutineTask()


class Tests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
    def tearDown(self):
        self.stack.close()
    def state(self, **change):
        values = dict(session=1, active=True, visible=True, desktop="Default", input_desktop="Default", monitors=1, cursor=True)
        values.update(change)
        return b.State(**values)
    def test_no_physical_mouse_or_user_activity_condition(self):
        b.validate(self.state())
        self.assertNotIn("last_input", b.State.__dataclass_fields__)
        self.assertNotIn("mouse_present", b.State.__dataclass_fields__)
    def test_reject_session_zero(self):
        with self.assertRaises(b.Failure): b.validate(self.state(session=0))
    def test_reject_hidden_station(self):
        with self.assertRaises(b.Failure): b.validate(self.state(visible=False))
    def test_reject_disconnected_session(self):
        with self.assertRaises(b.Failure): b.validate(self.state(active=False))
    def test_reject_lock_and_secure_desktop(self):
        for change in ({"input_desktop": "Winlogon"}, {"desktop": "Winlogon"}, {"input_desktop": ""}):
            with self.subTest(change=change), self.assertRaises(b.Failure): b.validate(self.state(**change))
    def test_reject_missing_display(self):
        with self.assertRaises(b.Failure): b.validate(self.state(monitors=0))
    def test_cursor_api_is_not_required_for_strict_capable_session(self):
        b.validate(self.state(cursor=False))
    def test_command_line_under_windows_limit(self):
        code = Path(b.__file__).read_text(encoding="utf-8")
        command = subprocess.list2cmdline(["C:/" + "long-path/" * 50 + "python.exe", "-c", code])
        self.assertLess(len(command.encode("utf-16-le")) // 2, 30000)
    def test_cursor_outside_screen_is_positioned_without_human(self):
        cls, base = inputs(); d = FakeDesktop(); restore = b.patch_input(cls, base, b.Guard(d), b.Failure)
        try:
            obj = cls(); obj.click(400, 300, move_back=True)
            self.assertEqual(d.cursor, (500, 400))
            self.assertEqual(obj.calls, ["sync_stop", ("click", 400, 300, False, False, "left")])
            self.assertEqual(len(d.messages), 2)
        finally: restore()
    def test_strict_click_uses_virtual_hover_without_global_cursor(self):
        cls, base = inputs(); d = FakeDesktop(); guard = b.Guard(d, mode="strict-no-mouse")
        restore = b.patch_input(cls, base, guard, b.Failure)
        try:
            obj = cls(); obj.click(400, 300, move_back=True)
            self.assertEqual(d.cursor, (-400, -400))
            self.assertEqual(obj.calls, ["sync_stop", ("click", 400, 300, False, False, "left")])
            self.assertEqual([m[1] for m in d.messages], [0x200, 0x201, 0x202])
            audit = guard.verify_input_contract()
            self.assertEqual(audit["cursor_positions"], 0)
            self.assertEqual(audit["send_input"], 0)
            self.assertEqual(audit["virtual_moves"], 1)
        finally: restore()

    def test_positional_click_arguments_normalized(self):
        cls, base = inputs(); restore = b.patch_input(cls, base, b.Guard(FakeDesktop()), b.Failure)
        try:
            obj = cls(); obj.click(4, 5, True, None, 0.01, True, "right")
            self.assertIn(("click", 4, 5, False, False, "right"), obj.calls)
        finally: restore()
    def test_operate_preserves_result_but_not_global_block_or_restore(self):
        cls, base = inputs(); restore = b.patch_input(cls, base, b.Guard(FakeDesktop()), b.Failure)
        try:
            obj = cls(); self.assertEqual(obj.operate(lambda: 17, True, True), 17)
            obj.block_input(); obj.unblock_input(); obj._restore_cursor()
            self.assertEqual(obj.calls, ["sync_stop", ("operate", False, False)])
        finally: restore()
    def test_sendinput_zero_is_error(self):
        cls, base = inputs(); restore = b.patch_input(cls, base, b.Guard(FakeDesktop()), b.Failure)
        try:
            obj = cls(); self.assertEqual(obj.move_mouse_relative(5, 1), 1); obj.send_result = 0
            with self.assertRaisesRegex(b.Failure, "SendInput"): obj.move_mouse_relative(5, 1)
        finally: restore()
    def test_strict_relative_mouse_fails_closed_before_sendinput(self):
        cls, base = inputs(); guard = b.Guard(FakeDesktop(), mode="strict-no-mouse")
        restore = b.patch_input(cls, base, guard, b.Failure)
        try:
            obj = cls()
            with self.assertRaises(b.Failure) as caught:
                obj.move_mouse_relative(5, 1)
            self.assertEqual(caught.exception.reason, "STRICT_RELATIVE_MOUSE_UNSUPPORTED")
            self.assertEqual(guard.audit_snapshot()["send_input"], 0)
        finally: restore()

    def test_lock_mid_run_blocks_next_input(self):
        cls, base = inputs(); d = FakeDesktop(); restore = b.patch_input(cls, base, b.Guard(d), b.Failure)
        try:
            obj = cls(); obj.send_key("f1"); self.assertEqual(len(d.messages), 2)
            d.problem = b.Failure("DESKTOP_UNAVAILABLE", "locked")
            with self.assertRaisesRegex(b.Failure, "locked"): obj.send_key("f1")
            self.assertEqual(len(d.messages), 2)
        finally: restore()
    def test_foreground_denial_blocks_click(self):
        cls, base = inputs(); d = FakeDesktop()
        def deny(hwnd): raise b.Failure("FOREGROUND_DENIED", "denied")
        d.foreground = deny; restore = b.patch_input(cls, base, b.Guard(d), b.Failure)
        try:
            obj = cls()
            with self.assertRaises(b.Failure): obj.click(1, 2)
            self.assertEqual(d.messages, [])
        finally: restore()
    def test_postmessage_failure_cannot_be_swallowed(self):
        cls, base = inputs(); d = FakeDesktop()
        def reject(*args): raise b.Failure("POSTMESSAGE_FAILED", "rejected")
        d.post = reject; guard = b.Guard(d); restore = b.patch_input(cls, base, guard, b.Failure)
        try:
            with self.assertRaisesRegex(b.Failure, "rejected"): cls().send_key("f1")
            self.assertEqual(guard.failure.reason, "POSTMESSAGE_FAILED")
        finally: restore()
    def test_activation_is_not_progress(self):
        cls, base = inputs(); guard = b.Guard(FakeDesktop()); restore = b.patch_input(cls, base, guard, b.Failure)
        try:
            obj = cls(); guard.last_input = 0; obj.post(6, 1, 0)
            self.assertEqual(guard.last_input, 0)
            obj.post(0x100, 70, 0); self.assertGreater(guard.last_input, 0)
        finally: restore()
    def test_unsupported_input_interface_fails(self):
        with self.assertRaises(b.Failure): b.patch_input(type("Bad", (), {}), object, b.Guard(FakeDesktop()), b.Failure)
    def test_signature_drift_fails(self):
        cls, base = inputs(); cls.click = lambda self, anything: None
        with self.assertRaises(b.Failure): b.patch_input(cls, base, b.Guard(FakeDesktop()), b.Failure)
    def test_future_ready(self):
        f = Future(); value = object(); f.set_result(value)
        self.assertIs(b.wait_detector(types.SimpleNamespace(_started=True, _openvino_model_future=f), b.Guard(FakeDesktop())), value)
    def test_future_failure(self):
        f = Future(); f.set_exception(ValueError("model error"))
        with self.assertRaisesRegex(b.Failure, "model error"):
            b.wait_detector(types.SimpleNamespace(_started=True, _openvino_model_future=f), b.Guard(FakeDesktop()))
    def test_future_cancelled(self):
        f = Future(); f.cancel()
        with self.assertRaises(b.Failure):
            b.wait_detector(types.SimpleNamespace(_started=True, _openvino_model_future=f), b.Guard(FakeDesktop()))
    def test_future_pending_does_not_wait_forever(self):
        g = b.Guard(FakeDesktop()); g.init_deadline = 1
        with self.assertRaises(b.Failure):
            b.wait_detector(types.SimpleNamespace(_started=True, _openvino_model_future=Future()), g)
    def test_unstarted_runtime_rejected(self):
        with self.assertRaises(b.Failure): b.wait_detector(types.SimpleNamespace(_started=False), b.Guard(FakeDesktop()))
    def test_idle_deadline_only_for_daily_not_launcher(self):
        g = b.Guard(FakeDesktop()); g.init_deadline = None; g.last_input = 0; g.poll(10000); g.running = True
        with self.assertRaises(b.Failure): g.poll(10000)
    def test_completion_exit_deadline(self):
        g = b.Guard(FakeDesktop()); g.init_deadline = None; g.completion_deadline = 1
        with self.assertRaises(b.Failure): g.poll(2)
    def proof(self, result=True, status=None):
        task = daily(result, status); proof = b.Proof(task, b.Guard(FakeDesktop())); task.do_run(); proof.on_done(task)
        return proof
    def test_proved_success(self): self.assertEqual(self.proof().verify()["success"], ["a"])
    def test_unstarted_zero_exit_not_success(self):
        with self.assertRaises(b.Failure): b.Proof(daily(), b.Guard(FakeDesktop())).verify()
    def test_swallowed_false_result_not_success(self):
        with self.assertRaises(b.Failure): self.proof(False).verify()
    def test_missing_bool_not_success(self):
        with self.assertRaises(b.Failure): self.proof(None).verify()
    def test_failed_pending_empty_invalid_status_not_success(self):
        for status in ({}, {"success": [], "failed": [], "skipped": [], "pending": []},
                       {"success": [], "failed": ["a"], "skipped": [], "pending": []},
                       {"success": ["a"], "failed": [], "skipped": [], "pending": ["b"]}):
            with self.subTest(status=status), self.assertRaises(b.Failure): self.proof(status=status).verify()
    def test_other_task_done_does_not_count(self):
        task = daily(); p = b.Proof(task, b.Guard(FakeDesktop())); task.do_run(); p.on_done(daily())
        with self.assertRaises(b.Failure): p.verify()
    def test_invalid_input_mode_rejected_before_desktop(self):
        with patch.dict(os.environ, {"GS_OK_NTE_INPUT_MODE": "background-only"}):
            code, out = b.execute(lambda: self.fail("should not open desktop"))
        self.assertEqual(code, 24); self.assertFalse(out["ok"])

    def test_legacy_mode_alias_maps_to_compatibility(self):
        d, task = FakeDesktop(), daily(); events = types.SimpleNamespace(task_done=Signal())
        class Instance:
            def run_onetime_task(self, t, exit_after):
                t.do_run(); events.task_done.emit(t)
            def quit(self): pass
        def loader(g):
            g.init_deadline = None; g.instance = Instance()
            return g.instance, task, events, lambda: None
        with patch.dict(os.environ, {"GS_OK_NTE_INPUT_MODE": "unattended-desktop"}),              patch.object(b.Guard, "start", lambda g: None):
            code, out = b.execute(lambda: d, loader)
        self.assertEqual(code, 0)
        self.assertEqual(out["input_mode"], "cursor-compatible")

    def test_strict_contract_rejects_forbidden_global_input_audit(self):
        g = b.Guard(FakeDesktop(), mode="strict-no-mouse")
        g.count("send_input")
        with self.assertRaises(b.Failure) as caught:
            g.verify_input_contract()
        self.assertEqual(caught.exception.reason, "STRICT_INPUT_CONTRACT_BREACH")
    def test_bad_deadline_policy(self):
        for value in ("nan", "-1", "0", "inf", "bad"):
            with patch.dict(os.environ, {"GS_OK_NTE_INIT_TIMEOUT": value}), self.assertRaises(b.Failure): b.Guard(FakeDesktop())
    def test_real_runtime_requires_admin_before_import(self):
        d = FakeDesktop()
        with patch.object(b, "is_elevated", return_value=False),              patch.object(b.Guard, "start", lambda g: self.fail("watchdog should not start")):
            code, out = b.execute(lambda: d, b.load_runtime)
        self.assertEqual(code, 30)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "ADMIN_REQUIRED")
        self.assertTrue(d.closed)

    def test_doctor_does_not_import_runtime_or_count_as_task_success(self):
        d = FakeDesktop()
        with patch.dict(os.environ, {"GS_OK_NTE_DOCTOR": "1"}):
            code, out = b.execute(lambda: d, lambda g: self.fail("no game imports"))
        self.assertEqual(code, 28); self.assertFalse(out["ok"]); self.assertTrue(d.closed)
    def test_headless_argv_services_and_stable_class_selection(self):
        task, future, calls = daily(), Future(), []
        services = types.SimpleNamespace(_started=False, _openvino_model_future=future)
        events = types.SimpleNamespace(start_success=Signal(), task_done=Signal())
        def ready():
            calls.append("services"); services._started = True; future.set_result(object())
        events.start_success.connect(ready)
        class OK:
            def __init__(self, config):
                calls.append("init"); self.argv = list(sys.argv)
                assert "--headless" in self.argv
                self._app = None
                self.headless_app = types.SimpleNamespace(initialize_overlay=lambda: calls.append("overlay"))
                self.task_executor = types.SimpleNamespace(onetime_tasks=[object(), object(), task])
        cls, base = inputs()
        modules = {n: types.ModuleType(n) for n in ("src", "src.config", "src.patches", "src.patches.startup_patches",
                   "src.interaction", "src.interaction.NTEInteraction", "ok", "ok.core", "ok.core.events", "ok.device", "ok.device.intercation")}
        modules["src.config"].config = {"version": "fixture"}
        modules["src.patches.startup_patches"].install_startup_patches = lambda cfg: calls.append("patches")
        modules["src.interaction.NTEInteraction"].NTEInteraction = cls
        modules["ok.device.intercation"].PostMessageInteraction = base
        modules["ok"].OK = OK; modules["ok"].og = types.SimpleNamespace(my_app=types.SimpleNamespace(_runtime_services=services))
        modules["ok.core.events"].communicate = events
        argv, path = list(sys.argv), list(sys.path)
        try:
            with patch.dict(sys.modules, modules):
                instance, selected, _, restore = b.load_runtime(b.Guard(FakeDesktop()))
                self.assertIs(selected, task); self.assertEqual(calls, ["patches", "init", "overlay", "services"])
                self.assertEqual(instance.argv, [os.path.join(os.getcwd(), "main.py"), "--headless"])
                restore()
        finally: sys.argv[:], sys.path[:] = argv, path
    def test_execute_success_and_failure_release_resources(self):
        for result, expected in ((True, 0), (False, 20)):
            d, task = FakeDesktop(), daily(result); events = types.SimpleNamespace(task_done=Signal())
            class Instance:
                closed = False
                def run_onetime_task(self, t, exit_after):
                    self.exit_after = exit_after; t.do_run(); events.task_done.emit(t)
                def quit(self): self.closed = True
            instance = Instance()
            def loader(g):
                g.init_deadline = None; g.instance = instance
                return instance, task, events, lambda: None
            with patch.object(b.Guard, "start", lambda g: None): code, out = b.execute(lambda: d, loader)
            self.assertEqual(code, expected); self.assertEqual(out["ok"], expected == 0)
            self.assertTrue(d.closed and instance.closed and instance.exit_after)
    def test_failed_import_releases_desktop(self):
        d = FakeDesktop()
        def fail(g): raise ValueError("broken import")
        with patch.object(b.Guard, "start", lambda g: None): code, out = b.execute(lambda: d, fail)
        self.assertNotEqual(code, 0); self.assertTrue(d.closed); self.assertIn("broken import", out["message"])
    @unittest.skipUnless(os.name == "nt", "Win32 API ABI probe requires Windows")
    def test_win32_read_only_probe(self):
        self.assertIsInstance(b.Desktop().snapshot(), b.State)


if __name__ == "__main__": unittest.main(verbosity=2)
