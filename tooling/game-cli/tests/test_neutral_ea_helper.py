"""Owned fake pipes/process objects only; never run PowerShell or native UIA."""
from contextlib import ExitStack
import io
import threading
import time
import unittest
from unittest.mock import patch

from sims4foundry_cli import neutral_ea_consent as consent
from sims4foundry_cli.errors import FoundryError


class Sink(io.StringIO):
    def __init__(self):
        super().__init__()
        self.writes = []
    def write(self, value):
        self.writes.append(value)
        return super().write(value)


class Process:
    def __init__(self):
        self.stdin = Sink()
        self.stdout = io.StringIO('')
        self.exited = False
        self.terminations = 0
    def poll(self): return 0 if self.exited else None
    def terminate(self):
        self.terminations += 1
        self.exited = True
    def kill(self): raise AssertionError('unexpected kill')
    def wait(self, timeout):
        if not self.exited: raise consent.subprocess.TimeoutExpired('fixture', timeout)
        return 0


class HelperBoundsTests(unittest.TestCase):
    def seams(self, popen, thread=None):
        stack = ExitStack()
        stack.enter_context(patch.object(consent, '_WINDOWS', True))
        stack.enter_context(patch.dict(consent.os.environ, {'SystemRoot':'fixture'}))
        stack.enter_context(patch.object(consent, '_performance_counter', return_value=(1000000,1000000)))
        stack.enter_context(patch.object(consent, '_popen', side_effect=popen))
        if thread is not None:
            stack.enter_context(patch.object(consent, '_thread', side_effect=thread))
        self.addCleanup(stack.close)
        return stack
    def config(self, seconds=.5):
        return {'pid':42, 'creation_filetime':'123', 'image':'EA.exe', 'nonce':'fixture',
                'deadline_monotonic':time.monotonic()+seconds}

    def test_actual_windows_guard_stays_in_place(self):
        with patch.object(consent, '_WINDOWS', False), patch.object(consent, '_popen') as process:
            with self.assertRaises(FoundryError) as caught:
                consent._UiHelper(self.config())
            self.assertEqual('windows_required', caught.exception.code)
            process.assert_not_called()

    def test_late_popen_is_retired_without_configuration_or_commit(self):
        release, retired = threading.Event(), threading.Event()
        process = Process()
        terminate = process.terminate
        def stop():
            terminate()
            retired.set()
        process.terminate = stop
        def delayed(*args, **kwargs):
            release.wait(2)
            return process
        self.seams(delayed)
        started = time.monotonic()
        try:
            with self.assertRaises(FoundryError) as caught:
                consent._UiHelper(self.config(.03))
            self.assertEqual('ea_helper_cleanup_uncertain', caught.exception.code)
            self.assertLess(time.monotonic()-started, 1)
        finally:
            release.set()
        self.assertTrue(retired.wait(1))
        # Join owned fixture workers so no late test work remains.
        for thread in threading.enumerate():
            if thread.name == 'FoundryEAConsentStart': thread.join(1)
        self.assertEqual([], process.stdin.writes)
        self.assertTrue(process.stdin.closed)
        self.assertEqual(1, process.terminations)

    def test_thread_start_failure_closes_owned_process(self):
        process = Process()
        class BrokenReader(threading.Thread):
            def start(self): raise RuntimeError('fixture start failure')
        def factory(**kwargs):
            return BrokenReader(**kwargs) if kwargs['name'].endswith('Output') else threading.Thread(**kwargs)
        self.seams(lambda *a,**k:process, factory)
        with self.assertRaises(RuntimeError):
            consent._UiHelper(self.config())
        self.assertTrue(process.exited)
        self.assertEqual(1, process.terminations)
        self.assertEqual([], process.stdin.writes)

    def test_start_can_raise_after_start_without_dispatching(self):
        opened = []
        class UncertainThread(threading.Thread):
            def start(self):
                super().start()
                raise RuntimeError('fixture acknowledgment lost')
        self.seams(lambda *a,**k:opened.append(1), lambda **kw:UncertainThread(**kw))
        with self.assertRaises(RuntimeError):
            consent._UiHelper(self.config())
        self.assertEqual([], opened)

    def test_late_commit_thread_never_writes_and_no_second_commit(self):
        process = Process()
        class SlowCommit(threading.Thread):
            def start(self):
                super().start()
                time.sleep(.08)
        self.seams(lambda *a,**k:process)
        helper = consent._UiHelper(self.config())
        helper.deadline = time.monotonic()+.03
        baseline = list(process.stdin.writes)
        with patch.object(consent, '_thread', side_effect=lambda **kw:SlowCommit(**kw)):
            with self.assertRaises(TimeoutError): helper.commit('fixture')
        self.assertTrue(helper.close())
        self.assertEqual(baseline, process.stdin.writes)
        with self.assertRaises(ValueError): helper.commit('fixture')
        self.assertEqual(1, process.terminations)

    def test_pipe_output_is_bounded_and_script_checks_before_invoke(self):
        process = Process()
        process.stdout = io.StringIO('x'*8193+'\n')
        self.seams(lambda *a,**k:process)
        with self.assertRaises(RuntimeError): consent._UiHelper(self.config())
        self.assertTrue(process.exited)
        self.assertIn("Emit 'invoke-intent'\n    CheckDeadline\n    $pattern.Invoke()", consent.SCRIPT)
        self.assertIn('GetTimestamp() -ge [long]$cfg.deadline_qpc', consent.SCRIPT)
        self.assertNotIn('TreeScope]::Descendants', consent.SCRIPT)


if __name__ == '__main__': unittest.main()
