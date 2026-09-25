import importlib.util
import pathlib
import time
import types
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "gs_ok_nte_native_lifecycle",
    HERE / "ok_nte_native_lifecycle.py",
)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


class Signal:
    def __init__(self):
        self.handlers = []

    def connect(self, fn):
        self.handlers.append(fn)

    def disconnect(self, fn):
        if fn in self.handlers:
            self.handlers.remove(fn)

    def emit(self, *args):
        for fn in list(self.handlers):
            fn(*args)


class DailyRoutineTask:
    def __init__(self, result=True, status=None):
        self.result = result
        self.task_status = status or {
            "success": ["daily"],
            "failed": [],
            "skipped": [],
            "pending": [],
        }

    def do_run(self):
        return self.result


DailyRoutineTask.__module__ = "src.tasks.daily.DailyRoutineTask"


class LauncherTask:
    enable_after_start = True


LauncherTask.__module__ = "src.tasks.LauncherTask"


class FakeFrame(bytearray):
    shape = (1, 1, 3)


class FakeCapture:
    def __init__(self, payload=b"abc"):
        self.frame = FakeFrame(payload)
        self.calls = 0

    def connected(self):
        return True

    def get_frame(self):
        self.calls += 1
        return self.frame


class WindowsGraphicsCaptureMethod(FakeCapture):
    pass


class SavedConfig(dict):
    config_file = r"C:\ok-nte\working\configs\devices.json"


class FakeHwndWindow:
    hwnd = 123
    top_hwnd = 123
    exists = True
    visible = True
    pos_valid = True
    x = 10
    y = 20
    width = 1920
    height = 1080
    window_width = 1920
    window_height = 1080
    client_width = 1920
    client_height = 1080
    real_x_offset = 0
    real_y_offset = 0
    real_width = 1920
    real_height = 1080
    capture_target_signature = (123, 123, 1920, 1080)


class FakeInstance:
    def __init__(self, daily=None, launcher=None, signal=None):
        self.daily = daily or DailyRoutineTask()
        self.launcher = launcher or LauncherTask()
        self.signal = signal or Signal()
        self.task_executor = types.SimpleNamespace(
            onetime_tasks=[self.launcher, self.daily],
            current_task=None,
            device_manager=None,
        )
        self.quit_called = False
        self.run_called = False

    def run_onetime_task(self, task, exit_after=False):
        self.run_called = True
        result = task.do_run()
        self.signal.emit(task)
        return result

    def quit(self):
        self.quit_called = True


class Tests(unittest.TestCase):
    def test_prefer_wgc_allowed_preserves_native_fallback(self):
        config = {
            "windows": {
                "capture_method": ["WGC", "BitBlt_RenderFull"],
            }
        }
        previous, allowed = native.prefer_wgc_allowed(config)
        self.assertEqual(previous, ["WGC", "BitBlt_RenderFull"])
        self.assertEqual(config["windows"]["capture_method"], ["WGC", "BitBlt_RenderFull"])
        self.assertEqual(allowed, ["WGC", "BitBlt_RenderFull"])

    def test_prefer_wgc_selected_persists_runtime_preference(self):
        device_config = SavedConfig(capture="BitBlt_RenderFull")
        device_manager = types.SimpleNamespace(
            windows_capture_config={
                "capture_method": ["WGC", "BitBlt_RenderFull"],
            },
            config=device_config,
        )
        instance = types.SimpleNamespace(device_manager=device_manager)
        previous, path, allowed = native.prefer_wgc_selected(instance)
        self.assertEqual(previous, "BitBlt_RenderFull")
        self.assertEqual(device_config["capture"], "WGC")
        self.assertEqual(
            device_manager.windows_capture_config["capture_method"],
            ["WGC", "BitBlt_RenderFull"],
        )
        self.assertEqual(allowed, ["WGC", "BitBlt_RenderFull"])
        self.assertEqual(path, SavedConfig.config_file)

    def test_selects_native_daily_and_launcher(self):
        instance = FakeInstance()
        daily, launcher = native.select_native_tasks(instance)
        self.assertIs(daily, instance.daily)
        self.assertIs(launcher, instance.launcher)

    def test_requires_enable_after_start_launcher(self):
        launcher = LauncherTask()
        launcher.enable_after_start = False
        with self.assertRaises(native.Failure) as caught:
            native.select_native_tasks(FakeInstance(launcher=launcher))
        self.assertEqual(caught.exception.reason, "NATIVE_LAUNCHER_UNAVAILABLE")

    def test_proof_accepts_truthful_success(self):
        task = DailyRoutineTask()
        proof = native.Proof(task)
        self.assertTrue(task.do_run())
        proof.on_done(task)
        status = proof.verify()
        self.assertEqual(status["success"], ["daily"])
        proof.restore()

    def test_proof_rejects_failed_items(self):
        task = DailyRoutineTask(status={
            "success": [],
            "failed": ["coffee"],
            "skipped": [],
            "pending": [],
        })
        proof = native.Proof(task)
        task.do_run()
        proof.on_done(task)
        with self.assertRaises(native.Failure) as caught:
            proof.verify()
        self.assertEqual(caught.exception.reason, "TASK_ITEMS_FAILED")
        proof.restore()

    def test_launcher_capture_observer_reports_target_and_stale_frame(self):
        instance = FakeInstance()
        capture = WindowsGraphicsCaptureMethod()
        instance.task_executor.current_task = instance.launcher
        instance.launcher.frame = capture.frame
        instance.task_executor.device_manager = types.SimpleNamespace(
            capture_method=capture,
            hwnd_window=FakeHwndWindow(),
        )
        observer = native.LauncherCaptureObserver(instance, instance.launcher)
        events = []
        original_emit = native.emit
        try:
            native.emit = lambda event, **data: events.append((event, data))
            observer._sample()
            self.assertEqual(capture.calls, 0)
            self.assertTrue(any(event == "LAUNCHER_CAPTURE_TARGET" for event, _ in events))
            self.assertIsNotNone(observer.last_hash)

            observer._sample()
            observer.same_hash_since = time.monotonic() - 20
            observer.last_stale_emit = 0
            observer._sample()
            self.assertTrue(any(event == "LAUNCHER_CAPTURE_STALE" for event, _ in events))

            instance.launcher.frame = FakeFrame(b"xyz")
            observer.same_hash_since = time.monotonic() - 20
            observer._sample()
            self.assertTrue(any(event == "LAUNCHER_CAPTURE_RECOVERED" for event, _ in events))
        finally:
            native.emit = original_emit

    def test_launcher_capture_observer_records_native_fallback_backend(self):
        instance = FakeInstance()
        capture = FakeCapture()
        instance.task_executor.current_task = instance.launcher
        instance.launcher.frame = capture.frame
        instance.task_executor.device_manager = types.SimpleNamespace(
            capture_method=capture,
            hwnd_window=FakeHwndWindow(),
        )
        observer = native.LauncherCaptureObserver(instance, instance.launcher)
        events = []
        original_emit = native.emit
        try:
            native.emit = lambda event, **data: events.append((event, data))
            observer._sample()
            self.assertEqual(observer.last_capture_method, "FakeCapture")
            self.assertTrue(any(
                event == "LAUNCHER_CAPTURE_BACKEND"
                for event, _ in events
            ))
        finally:
            native.emit = original_emit

    def test_cta_watchdog_clicks_only_after_stale_frame_grace(self):
        instance = FakeInstance()
        instance.task_executor.current_task = instance.launcher
        now = [100.0]
        observer = types.SimpleNamespace(
            last_ready_percentage=0.0,
            same_hash_since=80.0,
            unavailable_since=None,
            last_hash="abc",
        )
        sent = []
        state = {
            "valid": True,
            "game_started": False,
            "hwnd": 123,
            "pid": 456,
            "class_name": "Qt51517QWindowOwnDC",
            "title": "NTE",
            "window_rect": [0, 0, 1920, 1080],
            "client_size": [1920, 1080],
        }
        watchdog = native.LauncherPrimaryCTAWatchdog(
            instance,
            instance.launcher,
            observer,
            probe=lambda: state,
            sender=lambda s: (sent.append(s) or True, {"reason": "posted"}),
            clock=lambda: now[0],
        )
        events = []
        original_emit = native.emit
        try:
            native.emit = lambda event, **data: events.append((event, data))
            watchdog._tick()
            self.assertEqual(sent, [])
            now[0] = 121.0
            watchdog._tick()
            self.assertEqual(len(sent), 1)
            self.assertTrue(any(
                event == "LAUNCHER_CTA_WATCHDOG_ATTEMPT"
                for event, _ in events
            ))
        finally:
            native.emit = original_emit

    def test_cta_watchdog_defers_to_native_ready_button(self):
        instance = FakeInstance()
        instance.task_executor.current_task = instance.launcher
        now = [100.0]
        observer = types.SimpleNamespace(
            last_ready_percentage=1.0,
            same_hash_since=70.0,
            unavailable_since=None,
            last_hash="abc",
        )
        sent = []
        watchdog = native.LauncherPrimaryCTAWatchdog(
            instance,
            instance.launcher,
            observer,
            probe=lambda: {
                "valid": True,
                "game_started": False,
                "hwnd": 123,
                "pid": 456,
                "class_name": "Qt51517QWindowOwnDC",
                "title": "NTE",
                "window_rect": [0, 0, 1920, 1080],
                "client_size": [1920, 1080],
            },
            sender=lambda s: (sent.append(s) or True, {"reason": "posted"}),
            clock=lambda: now[0],
        )
        watchdog._tick()
        now[0] = 130.0
        watchdog._tick()
        self.assertEqual(sent, [])

    def test_cta_watchdog_clicks_after_capture_unavailable(self):
        instance = FakeInstance()
        instance.task_executor.current_task = instance.launcher
        now = [100.0]
        observer = types.SimpleNamespace(
            last_ready_percentage=None,
            same_hash_since=None,
            unavailable_since=100.0,
            last_hash=None,
        )
        sent = []
        watchdog = native.LauncherPrimaryCTAWatchdog(
            instance,
            instance.launcher,
            observer,
            probe=lambda: {
                "valid": True,
                "game_started": False,
                "hwnd": 123,
                "pid": 456,
                "class_name": "Qt51517QWindowOwnDC",
                "title": "NTE",
                "window_rect": [0, 0, 1920, 1080],
                "client_size": [1920, 1080],
            },
            sender=lambda s: (sent.append(s) or True, {"reason": "posted"}),
            clock=lambda: now[0],
        )
        watchdog._tick()
        now[0] = 121.0
        watchdog._tick()
        self.assertEqual(len(sent), 1)

    def test_cta_watchdog_never_clicks_after_game_process_appears(self):
        instance = FakeInstance()
        instance.task_executor.current_task = instance.launcher
        now = [100.0]
        observer = types.SimpleNamespace(
            last_ready_percentage=0.0,
            same_hash_since=60.0,
            unavailable_since=None,
            last_hash="abc",
        )
        sent = []
        watchdog = native.LauncherPrimaryCTAWatchdog(
            instance,
            instance.launcher,
            observer,
            probe=lambda: {"valid": True, "game_started": True},
            sender=lambda s: (sent.append(s) or True, {"reason": "posted"}),
            clock=lambda: now[0],
        )
        watchdog._tick()
        now[0] = 130.0
        watchdog._tick()
        self.assertEqual(sent, [])

    def test_execute_uses_native_instance_without_patching_launcher(self):
        signal = Signal()
        instance = FakeInstance(signal=signal)

        def loader():
            return instance, instance.daily, instance.launcher, types.SimpleNamespace(
                task_done=signal
            )

        code, outcome = native.execute(
            runtime_loader=loader,
            elevation_check=lambda: True,
        )
        self.assertEqual(code, 0)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["lifecycle"], "native")
        self.assertEqual(outcome["launcher_owned_by"], "ok-nte")
        self.assertTrue(instance.run_called)
        self.assertTrue(instance.quit_called)

    def test_execute_propagates_unverifiable_daily_failure(self):
        signal = Signal()
        daily = DailyRoutineTask(result=False, status={
            "success": [],
            "failed": [],
            "skipped": [],
            "pending": ["daily"],
        })
        instance = FakeInstance(daily=daily, signal=signal)

        def loader():
            return instance, instance.daily, instance.launcher, types.SimpleNamespace(
                task_done=signal
            )

        code, outcome = native.execute(
            runtime_loader=loader,
            elevation_check=lambda: True,
        )
        self.assertEqual(code, 20)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["reason"], "TASK_NOT_COMPLETED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
