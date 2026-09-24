import importlib.util
import pathlib
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


class FakeInstance:
    def __init__(self, daily=None, launcher=None, signal=None):
        self.daily = daily or DailyRoutineTask()
        self.launcher = launcher or LauncherTask()
        self.signal = signal or Signal()
        self.task_executor = types.SimpleNamespace(
            onetime_tasks=[self.launcher, self.daily]
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
