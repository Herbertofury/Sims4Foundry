"""Frame-stream recorder tests; all processes and target windows are synthetic."""
from contextlib import contextmanager
from copy import deepcopy
import base64
import io
import inspect
import struct
import subprocess
import sys
import threading
import unittest
from pathlib import Path
import tempfile
from unittest import mock

from sims4foundry_cli import wgc_recorder as recorder
from sims4foundry_cli.errors import FoundryError
from test_windows_cli import identity, png


class Pipe:
    def __init__(self):
        self.lock = threading.Condition()
        self.data = bytearray()
        self.closed = False
    def feed(self, data):
        with self.lock:
            self.data.extend(data)
            self.lock.notify_all()
    def read(self, count):
        with self.lock:
            while not self.data and not self.closed:
                self.lock.wait()
            value = bytes(self.data[:count])
            del self.data[:count]
            return value
    def close(self):
        with self.lock:
            self.closed = True
            self.lock.notify_all()


class FakeProcess:
    def __init__(self):
        self.stdout = Pipe()
        self.code = None
        self.terminated = 0
    def poll(self): return self.code
    def terminate(self):
        self.terminated += 1
        self.code = 0
        self.stdout.close()
    def kill(self): self.terminate()
    def wait(self, timeout=None): return self.code


class FrameTests(unittest.TestCase):
    def test_reads_two_concatenated_frames_without_losing_boundaries(self):
        raw = png()
        stream = io.BytesIO(raw + raw)
        self.assertEqual((raw,(2,2)),recorder._read_frame(stream))
        self.assertEqual((raw,(2,2)),recorder._read_frame(stream))
    def test_malformed_truncated_and_oversized_frames_fail(self):
        raw = png()
        bad = [raw[:12], b'not png!', raw[:40]+b'X'+raw[41:],
               raw[:8]+struct.pack('>I',recorder.MAX_FRAME_BYTES)+b'IHDR']
        for value in bad:
            with self.subTest(length=len(value)), self.assertRaises(FoundryError):
                recorder._read_frame(io.BytesIO(value))


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name)/'ffmpeg.exe'
        self.image.write_bytes(b'fixture')
        self.proc = FakeProcess()
        self.pinned = False
        self.target = identity()
        self.commands = []
        self.now = 1.0
        def popen(command, **kwargs):
            self.assertTrue(self.pinned)
            self.commands.append((command,kwargs))
            return self.proc
        @contextmanager
        def pin(path, expected):
            self.pinned = True
            try: yield
            finally: self.pinned = False
        self.recorder = recorder.WgcRecorder(self.target, self.image, 'a'*64,
                     observer=lambda *args:deepcopy(self.target), popen=popen, pin=pin,
                     clock=lambda:self.now)
        self.addCleanup(self.recorder.close)
    def wait_for_frame(self, sequence):
        for _ in range(200):
            try:
                frame = self.recorder.sample()
                if frame.sequence >= sequence:
                    return frame
            except FoundryError:
                pass
            threading.Event().wait(.005)
        self.fail('fixture frame was not observed')
    def test_pinned_stream_emits_fresh_frames_and_closes_owned_child(self):
        self.recorder.start()
        self.assertTrue(self.pinned)
        self.assertEqual(1,len(self.commands))
        self.assertIn('pipe:1',self.commands[0][0])
        self.assertFalse(self.commands[0][1].get('shell',False))
        self.proc.stdout.feed(png())
        first = self.wait_for_frame(1)
        self.assertEqual((2,2),(first.width,first.height))
        self.assertEqual(png(),first.png)
        self.proc.stdout.feed(png())
        second = self.wait_for_frame(2)
        self.assertIs(first.epoch,second.epoch)
        self.assertGreater(second.sequence,first.sequence)
        self.assertTrue(self.recorder.close())
        self.assertEqual(1,self.proc.terminated)
        self.assertFalse(self.pinned)
        self.assertTrue(self.recorder.close())
        with self.assertRaises(FoundryError): self.recorder.start()
    def test_stale_frame_or_changed_identity_refuses(self):
        self.recorder.start()
        self.proc.stdout.feed(png())
        self.wait_for_frame(1)
        self.now = 3.0
        with self.assertRaises(FoundryError): self.recorder.sample()
        self.now = 1.0
        self.target['hwnd'] = 999
        with self.assertRaises(FoundryError): self.recorder.sample()
    def test_frame_that_ages_during_identity_check_is_refused(self):
        self.recorder.start()
        self.proc.stdout.feed(png())
        self.wait_for_frame(1)
        calls=[0]
        def slow_observation(*args):
            calls[0] += 1
            if calls[0] == 2: self.now = 3.0
            return deepcopy(self.target)
        self.recorder._observer = slow_observation
        with self.assertRaises(FoundryError): self.recorder.sample()
    def test_malformed_stream_latches_failure(self):
        self.recorder.start()
        self.proc.stdout.feed(b'bad data')
        self.recorder._worker.join(timeout=1)
        self.assertFalse(self.recorder._worker.is_alive())
        with self.assertRaises(FoundryError): self.recorder.sample()
    def test_unconfirmed_spawn_retains_pin_without_replay(self):
        self.recorder._popen = mock.Mock(side_effect=OSError('fixture failure'))
        with self.assertRaises(FoundryError) as error: self.recorder.start()
        self.assertEqual('recording_cleanup_unresolved', error.exception.code)
        self.assertTrue(self.pinned)
        with self.assertRaises(FoundryError): self.recorder.start()
        self.assertEqual(1,self.recorder._popen.call_count)
        with recorder._QUARANTINE_LOCK:
            self.assertIn(self.recorder,recorder._QUARANTINE)
            recorder._QUARANTINE.discard(self.recorder)
        # The test knows this fake never created a process; production does not.
        self.recorder._pin.__exit__(None,None,None)
        self.recorder._pin_active = False
    def test_empty_spawn_result_is_unresolved_and_retains_pin(self):
        self.recorder._popen = mock.Mock(return_value=None)
        with self.assertRaises(FoundryError) as error: self.recorder.start()
        self.assertEqual('recording_cleanup_unresolved', error.exception.code)
        self.assertTrue(self.pinned)
        self.assertFalse(self.recorder.close())
        self.assertEqual(1,self.recorder._popen.call_count)
        with recorder._QUARANTINE_LOCK:
            self.assertIn(self.recorder,recorder._QUARANTINE)
            recorder._QUARANTINE.discard(self.recorder)
        self.recorder._pin.__exit__(None,None,None)  # known synthetic provider
        self.recorder._pin_active = False
    def test_late_spawn_is_retired_after_start_timeout(self):
        gate=threading.Event()
        def late(*args,**kwargs):
            gate.wait()
            return self.proc
        self.recorder._popen = late
        with mock.patch.object(recorder, 'START_TIMEOUT', .01), mock.patch.object(recorder, 'CLEANUP_TIMEOUT', .02):
            with self.assertRaises(FoundryError) as error: self.recorder.start()
        self.assertEqual('recording_cleanup_unresolved', error.exception.code)
        self.assertTrue(self.pinned)
        gate.set()
        self.recorder._starter.join(timeout=1)
        self.assertFalse(self.recorder._starter.is_alive())
        self.assertEqual(1,self.proc.terminated)
        self.assertFalse(self.pinned)
        self.assertTrue(self.recorder.close())
    def test_late_cleanup_cannot_be_overwritten_by_failed_start_state(self):
        gate = threading.Event()
        def late(*args, **kwargs):
            gate.wait()
            return self.proc
        self.recorder._popen = late
        flag = threading.Event()
        owner = self.recorder
        class CancelAndJoin:
            def set(self):
                flag.set()
                gate.set()
                owner._starter.join(timeout=1)
            def is_set(self): return flag.is_set()
        self.recorder._cancel = CancelAndJoin()
        with mock.patch.object(recorder, 'START_TIMEOUT', .01):
            with self.assertRaises(FoundryError) as error: self.recorder.start()
        self.assertEqual('recording_start_failed', error.exception.code)
        self.assertEqual('closed', self.recorder._state)
        self.assertEqual(1, self.proc.terminated)
        self.assertFalse(self.pinned)
        with recorder._QUARANTINE_LOCK:
            self.assertNotIn(self.recorder, recorder._QUARANTINE)
    def test_failed_pin_cleanup_is_never_relabelled_clean(self):
        calls=[]
        @contextmanager
        def failing(path, expected):
            self.pinned = True
            yield
            calls.append(1)
            self.pinned = False
            raise OSError('fixture pin cleanup')
        self.recorder._pin_factory = failing
        self.recorder.start()
        self.assertFalse(self.recorder.close())
        self.assertFalse(self.recorder.close())
        self.assertEqual([1],calls)
        with recorder._QUARANTINE_LOCK:
            recorder._QUARANTINE.discard(self.recorder)  # synthetic fixture only
    def test_uncertain_child_cleanup_is_retained_and_not_repeated(self):
        calls=[]
        def uncertain():
            calls.append(1)
            raise OSError('lost child termination acknowledgement')
        self.proc.terminate = uncertain
        self.recorder.start()
        self.assertFalse(self.recorder.close())
        self.assertFalse(self.recorder.close())
        self.assertEqual([1], calls)
        with recorder._QUARANTINE_LOCK:
            self.assertIn(self.recorder, recorder._QUARANTINE)
        # Synthetic process/pipe only: retire fixture resources for the test.
        self.proc.code = 0
        self.proc.stdout.close()
        self.recorder._worker.join(timeout=1)
        with recorder._QUARANTINE_LOCK:
            recorder._QUARANTINE.discard(self.recorder)
    def test_termination_timeout_does_not_send_second_termination(self):
        calls=[]
        self.proc.terminate = lambda:calls.append('terminate')
        self.proc.kill = lambda:calls.append('kill')
        self.proc.wait = mock.Mock(side_effect=subprocess.TimeoutExpired('fixture',1))
        self.recorder.start()
        self.assertFalse(self.recorder.close())
        self.assertFalse(self.recorder.close())
        self.assertEqual(['terminate'],calls)
        self.assertEqual(1,self.proc.wait.call_count)
        self.proc.code = 0
        self.proc.stdout.close()
        self.recorder._worker.join(timeout=1)
        with recorder._QUARANTINE_LOCK:
            recorder._QUARANTINE.discard(self.recorder)
    def test_pin_entry_cleanup_failure_remains_unresolved(self):
        class FailedPin:
            def __enter__(self):
                raise FoundryError('capture_pin_cleanup_failed','fixture')
            def __exit__(self,*args):
                raise AssertionError('pin entry was never completed')
        self.recorder._pin_factory = lambda *args:FailedPin()
        with self.assertRaises(FoundryError) as error: self.recorder.start()
        self.assertEqual('recording_cleanup_unresolved',error.exception.code)
        self.assertTrue(self.recorder._pin_cleanup_failed)
        self.assertFalse(self.recorder.close())
        with recorder._QUARANTINE_LOCK:
            self.assertIn(self.recorder,recorder._QUARANTINE)
            recorder._QUARANTINE.discard(self.recorder)
    def test_interruption_after_pin_enter_retains_unknown_ownership(self):
        lines, first = inspect.getsourcelines(recorder.WgcRecorder.start)
        target = first + next(index for index,line in enumerate(lines) if 'self._pin_active = True' in line)
        def trace(frame, event, arg):
            if frame.f_code is recorder.WgcRecorder.start.__code__ and event == 'line' and frame.f_lineno == target:
                raise KeyboardInterrupt('synthetic post-enter interruption')
            return trace
        sys.settrace(trace)
        try:
            with self.assertRaises(FoundryError) as error: self.recorder.start()
        finally:
            sys.settrace(None)
        self.assertEqual('recording_cleanup_unresolved',error.exception.code)
        self.assertTrue(self.pinned)
        with recorder._QUARANTINE_LOCK:
            self.assertIn(self.recorder,recorder._QUARANTINE)
            recorder._QUARANTINE.discard(self.recorder)
        self.recorder._pin.__exit__(None,None,None)  # synthetic pin only
    def test_busy_cleanup_lock_returns_unresolved_without_blocking(self):
        ready, release = threading.Event(), threading.Event()
        def hold():
            with self.recorder._cleanup_lock:
                ready.set()
                release.wait(1)
        worker = threading.Thread(target=hold)
        worker.start()
        try:
            self.assertTrue(ready.wait(1))
            with mock.patch.object(recorder, 'CLEANUP_TIMEOUT', .01):
                self.assertFalse(self.recorder.close())
            with recorder._QUARANTINE_LOCK:
                self.assertIn(self.recorder,recorder._QUARANTINE)
        finally:
            release.set()
            worker.join(timeout=1)
        self.assertTrue(self.recorder.close())
    def test_wrong_thread_does_not_start_helper(self):
        errors=[]
        def other():
            try: self.recorder.start()
            except Exception as error: errors.append(error)
        worker=threading.Thread(target=other)
        worker.start(); worker.join()
        self.assertEqual([FoundryError],[type(error) for error in errors])
        self.assertEqual([],self.commands)
    def test_owned_python_child_streams_fixture_and_is_reaped(self):
        encoded = base64.b64encode(png()).decode('ascii')
        source = ("import base64,sys,time; "
                  "sys.stdout.buffer.write(base64.b64decode(%r)); "
                  "sys.stdout.buffer.flush(); time.sleep(30)" % encoded)
        child=[]
        def spawn(command, **kwargs):
            process = subprocess.Popen([sys.executable,'-c',source], **kwargs)
            child.append(process)
            return process
        self.recorder._popen = spawn
        self.recorder.start()
        self.assertEqual(png(),self.wait_for_frame(1).png)
        self.assertTrue(self.recorder.close())
        self.assertEqual(1,len(child))
        self.assertIsNotNone(child[0].poll())
        self.assertFalse(self.recorder._worker.is_alive())
        self.assertFalse(self.pinned)
