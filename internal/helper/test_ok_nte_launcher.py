"""Native launcher regressions. No games or physical Windows input executed."""
import contextlib
import importlib.util
import io
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('gsnte_launcher_test', Path(__file__).with_name('ok_nte_bootstrap.py'))
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)
import _gs_nte_launcher as launcher


class Desktop:
    def __init__(self): self.clicks = []; self.problem = None
    def check(self):
        if self.problem: raise self.problem
    def foreground(self, hwnd): self.check()
    def click(self, hwnd, point):
        self.check(); self.clicks.append((hwnd, point))


class NativeLauncher:
    def __init__(self):
        self.game = False; self.connected = False; self.hwnd = 11
        self.capture_config = types.SimpleNamespace(
            LAUNCHER_CAPTURE_CONFIG={'windows': {'exe': ['NTEGlobalGame.exe']}},
            GAME_CAPTURE_CONFIG={'windows': {'exe': 'HTGame.exe'}})
        self.executor = types.SimpleNamespace(
            connected=lambda: self.connected,
            interaction=types.SimpleNamespace(
                hwnd_window=types.SimpleNamespace(hwnd=11),
                capture=types.SimpleNamespace(get_abs_cords=lambda x,y:(x+100,y+50))))
        self.box = types.SimpleNamespace(name='Labels.launcher_start',x=800,y=600,width=100,height=40)
        self.ready = True
        self.after_click = lambda: None
    def _find_process(self, exe): return {'pid':22} if self.game else None
    def _find_process_window(self, exe, require_title=False):
        if exe == 'HTGame.exe': return ({'pid':22},22) if self.game else (None,0)
        return {'pid':11}, self.hwnd
    def _launcher_button_state(self): return self.ready, self.box
    def click(self,x=-1,y=-1,move_back=None,name=None,after_sleep=0):
        raise AssertionError('The ineffective original PostMessage click must not run')
    def sleep(self, delay): pass
    def run(self):
        self._launcher_button_state()
        self.click(self.box, after_sleep=1)
        self.after_click()
    def enter_game(self):
        self.game=self.connected=True; self.executor.interaction.hwnd_window.hwnd=22


class Tests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack()
        self.output=self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.dict(os.environ,{},clear=True))
        self.desktop=Desktop(); self.guard=b.Guard(self.desktop); self.guard.init_deadline=None
        self.task=NativeLauncher()
        self.restore=b.patch_launcher(self.task,self.guard,b.Failure,b.emit,
                click_driver=lambda d,h,p,f:d.click(h,p))
    def tearDown(self): self.restore(); self.stack.close()
    def test_launcher_not_just_game_gets_foreground_click(self):
        self.task.after_click=self.task.enter_game
        self.task.run()
        self.assertEqual(self.desktop.clicks,[(11,(950,670))])
        self.assertIn('GAME_READY',self.output.getvalue())
    def test_no_transition_is_failure_not_success(self):
        with self.assertRaises(b.Failure) as caught: self.task.run()
        self.assertEqual(caught.exception.reason,'GAME_NOT_READY')
        self.assertIsNotNone(self.guard.failure)
    def test_launcher_return_does_not_prove_game_start(self):
        with self.assertRaises(b.Failure): self.task.run()
        self.assertNotIn('"event": "GAME_READY"',self.output.getvalue())
    def test_wrong_capture_rejects_click(self):
        self.task.executor.interaction.hwnd_window.hwnd=44
        with self.assertRaises(b.Failure) as caught: self.task.click(self.task.box)
        self.assertEqual(caught.exception.reason,'LAUNCHER_CAPTURE_MISMATCH')
        self.assertEqual(self.desktop.clicks,[])
    def test_game_already_present_prevents_stale_launcher_click(self):
        self.task.enter_game()
        self.assertFalse(self.task.click(self.task.box)); self.assertEqual(self.desktop.clicks,[])
    def test_rejected_input_does_not_get_success_event(self):
        self.desktop.problem=b.Failure('LAUNCHER_INPUT_REJECTED','denied',29)
        with self.assertRaises(b.Failure): self.task.click(self.task.box)
        self.assertNotIn('LAUNCHER_CLICK_SENT',self.output.getvalue())
    def test_unrecognized_control_never_clicked(self):
        self.task.box.name='Labels.purchase'
        with self.assertRaises(b.Failure): self.task.click(self.task.box)
        self.assertEqual(self.desktop.clicks,[])
    def test_numeric_coordinates_alone_are_rejected(self):
        with self.assertRaises(b.Failure): self.task.click(123,456)
        self.assertEqual(self.desktop.clicks,[])
    def test_click_retry_rate_and_count_are_bounded(self):
        with patch('time.monotonic', return_value=100):
            self.task.click(self.task.box); self.task.click(self.task.box)
        with patch('time.monotonic', return_value=111): self.task.click(self.task.box)
        with patch('time.monotonic', return_value=122): self.task.click(self.task.box)
        with patch('time.monotonic', return_value=133), self.assertRaises(b.Failure): self.task.click(self.task.box)
        self.assertEqual(len(self.desktop.clicks),3)
    def test_update_extension_applies_only_once(self):
        self.guard.launch_deadline=100
        self.task.box.name='Labels.launcher_update'; self.task.ready=False
        self.task._launcher_button_state(); first=self.guard.launch_deadline
        for _ in range(20): self.task._launcher_button_state()
        self.assertEqual(first,100+self.guard.update_budget)
        self.assertEqual(first,self.guard.launch_deadline)
        self.assertIn('"progress_verified": false',self.output.getvalue())
    def test_launcher_deadline_applies_before_daily(self):
        self.guard.running=False; self.guard.launch_deadline=10
        with self.assertRaises(b.Failure) as caught: self.guard.poll(11)
        self.assertEqual(caught.exception.reason,'LAUNCHER_TIMEOUT')
    def test_game_process_without_matching_capture_not_ready(self):
        def stale_capture(): self.task.game=True; self.task.connected=True
        self.task.after_click=stale_capture
        with self.assertRaises(b.Failure) as caught: self.task.run()
        self.assertEqual(caught.exception.reason,'GAME_NOT_READY')
    def test_failure_caught_upstream_remains_in_guard(self):
        try: self.task.click(12,20)
        except Exception: pass
        with self.assertRaises(b.Failure): self.guard.check()
    def test_unknown_signature_fails_before_dispatch(self):
        self.restore(); self.restore=lambda:None
        self.task.click=lambda value:None
        with self.assertRaises(b.Failure): b.patch_launcher(self.task,self.guard,b.Failure,b.emit)
    def test_restoration_does_not_leave_instance_overrides(self):
        self.restore()
        self.assertNotIn('click',vars(self.task)); self.assertNotIn('run',vars(self.task))
    def test_event_log_flushed_while_running(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'events.jsonl'
            with p.open('w',encoding='utf-8') as stream, patch.object(b,'_EVENT_FILE',stream), patch.object(b,'_EVENT_BYTES',0):
                b.emit('LAUNCHER_CLICK_ATTEMPT',hwnd=11)
                self.assertIn('LAUNCHER_CLICK_ATTEMPT',p.read_text())
    def test_input_abi_size(self):
        import ctypes
        self.assertEqual(ctypes.sizeof(launcher.Input),40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28)



class InputAPITests(unittest.TestCase):
    def setUp(self):
        class Function:
            def __init__(self, fn): self.fn=fn
            def __call__(self,*a): return self.fn(*a)
        self.flags=[]; self.foreground=11; self.hit=11; self.accept=[1,1]
        def send(count,pointer,size):
            self.flags.append((pointer._obj.data.mi.dwFlags,size))
            return self.accept.pop(0)
        self.desktop=types.SimpleNamespace(
            position=lambda h,p:None, check=lambda:None,
            u=types.SimpleNamespace(
                WindowFromPoint=Function(lambda p:self.hit),
                IsChild=Function(lambda p,c:False),
                SendInput=Function(send),
                GetForegroundWindow=Function(lambda:self.foreground),
                GetAncestor=Function(lambda h,f:h)))
    def test_checked_down_up_and_native_abi(self):
        launcher.desktop_click(self.desktop,11,(100,100),b.Failure)
        self.assertEqual([f for f,_ in self.flags],[2,4])
        import ctypes
        self.assertTrue(all(size==ctypes.sizeof(launcher.Input) for _,size in self.flags))
    def test_obstruction_is_rejected_without_input(self):
        self.hit=22
        with self.assertRaises(b.Failure) as c: launcher.desktop_click(self.desktop,11,(100,100),b.Failure)
        self.assertEqual(c.exception.reason,'LAUNCHER_CLICK_OBSTRUCTED'); self.assertEqual(self.flags,[])
    def test_lost_foreground_is_rejected_without_input(self):
        self.foreground=22
        with self.assertRaises(b.Failure): launcher.desktop_click(self.desktop,11,(100,100),b.Failure)
        self.assertEqual(self.flags,[])
    def test_rejected_down_never_claims_success(self):
        self.accept=[0]
        with self.assertRaises(b.Failure): launcher.desktop_click(self.desktop,11,(100,100),b.Failure)
        self.assertEqual(len(self.flags),1)
    def test_rejected_release_never_claims_success(self):
        self.accept=[1,0]
        with self.assertRaises(b.Failure): launcher.desktop_click(self.desktop,11,(100,100),b.Failure)
        self.assertEqual([f for f,_ in self.flags],[2,4])
    def test_exception_releases_pressed_button(self):
        with patch('time.sleep',side_effect=RuntimeError('interrupted')), self.assertRaises(RuntimeError):
            launcher.desktop_click(self.desktop,11,(100,100),b.Failure)
        self.assertEqual([f for f,_ in self.flags],[2,4])


if __name__=='__main__': unittest.main(verbosity=2)

