"""Fake handle tests and owned-child Windows CI tests, never a game process."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from sims4foundry_cli import cli, process_exit as exits
from sims4foundry_cli.errors import FoundryError
from test_process_lease import identity


class ExitObservationTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.open.return_value = 'owned'
        self.api.identity.return_value = (identity()['executable'], 123)
        self.api.wait.return_value = exits.WAIT_OBJECT_0

    def observe(self, value=None, seconds=0):
        with patch.object(exits, '_WindowsApi', return_value=self.api), \
                patch.object(exits.launch, '_source_stat', return_value={}), \
                patch.object(exits.launch, '_snapshot', return_value=('a' * 64, None, False)):
            return exits.observe_exit(identity() if value is None else value, seconds)

    def assert_code(self, code, callback):
        with self.assertRaises(FoundryError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def test_retained_process_signal_is_only_termination_proof(self):
        result = self.observe()
        self.assertTrue(result['ok'])
        self.assertTrue(result['process_exit_verified'])
        for field in ('normal_exit_verified', 'save_verified', 'input_sent', 'quit_requested',
                      'termination_requested', 'profile_accessed', 'apex_required'):
            self.assertIs(False, result[field])
        self.api.open.assert_called_once_with(42)
        self.api.identity.assert_called_once_with('owned')
        self.api.wait.assert_called_once_with('owned', 0)
        self.api.close.assert_called_once_with('owned')

    def test_timeout_is_unresolved_without_quit_or_reopen(self):
        self.api.wait.return_value = exits.WAIT_TIMEOUT
        result = self.observe()
        self.assertFalse(result['ok'])
        self.assertFalse(result['process_exit_verified'])
        self.assertEqual('unresolved', result['outcome'])
        self.api.open.assert_called_once()
        self.api.close.assert_called_once()

    def test_waits_share_one_deadline_and_one_handle(self):
        self.api.wait.side_effect = [exits.WAIT_TIMEOUT, exits.WAIT_TIMEOUT]
        clock = iter([10.0, 10.0, 10.25, 10.25, 10.5])
        result = exits._wait_retained(42, identity()['executable'], 123, .5,
                                      self.api, lambda: next(clock))
        self.assertEqual((False, .5, 2), result)
        self.assertEqual([('owned', 250), ('owned', 250)], [call.args for call in self.api.wait.call_args_list])
        self.api.open.assert_called_once()

    def test_signal_on_second_wait_does_not_query_recycled_pid(self):
        self.api.wait.side_effect = [exits.WAIT_TIMEOUT, exits.WAIT_OBJECT_0]
        clock = iter([10.0, 10.0, 10.25, 10.25, 10.3])
        ended, _, count = exits._wait_retained(42, identity()['executable'], 123, 1,
                                               self.api, lambda: next(clock))
        self.assertTrue(ended)
        self.assertEqual(2, count)
        self.api.identity.assert_called_once()
        self.api.open.assert_called_once()

    def test_changed_path_or_creation_refuses_before_wait(self):
        for observed in ((r'C:\Other\TS4_x64.exe', 123), (identity()['executable'], 124),
                         (identity()['executable'], True), (None, 123)):
            with self.subTest(observed=observed):
                self.api.reset_mock()
                self.api.identity.return_value = observed
                self.assert_code('process_lifetime_changed', self.observe)
                self.api.wait.assert_not_called()
                self.api.close.assert_called_once_with('owned')

    def test_case_equivalent_image_is_accepted(self):
        self.api.identity.return_value = (identity()['executable'].upper(), 123)
        self.assertTrue(self.observe()['process_exit_verified'])

    def test_unicode_casefold_collision_is_rejected_before_wait(self):
        for expected, observed in ((r'C:\strasse\TS4_x64.exe', r'C:\straße\TS4_x64.exe'),
                                   (r'C:\office\TS4_x64.exe', r'C:\oﬃce\TS4_x64.exe'),
                                   (r'C:\same\TS4_x64.exe', r'C:\same\Tſ4_x64.exe')):
            with self.subTest(expected=expected, observed=observed):
                self.api.reset_mock()
                self.api.identity.return_value = (observed, 123)
                self.assert_code('process_lifetime_changed', lambda: self.observe(dict(identity(), executable=expected)))
                self.api.wait.assert_not_called()

    def test_confusable_selected_basename_refuses_before_native_access(self):
        with patch.object(exits, '_WindowsApi') as factory:
            self.assert_code('wrong_executable', lambda: exits.observe_exit(
                dict(identity(), executable=r'C:\same\Tſ4_x64.exe')))
            factory.assert_not_called()

    def test_open_denial_never_becomes_exit_success_or_fallback(self):
        self.api.open.side_effect = FoundryError('exit_observation_unavailable', 'Denied')
        self.assert_code('exit_observation_unavailable', self.observe)
        self.api.identity.assert_not_called()
        self.api.wait.assert_not_called()
        self.api.close.assert_not_called()

    def test_query_wait_and_cleanup_failures_do_not_hide_uncertainty(self):
        for method in ('identity', 'wait', 'close'):
            with self.subTest(method=method):
                self.api.reset_mock(side_effect=True)
                self.api.identity.return_value = (identity()['executable'], 123)
                self.api.wait.return_value = exits.WAIT_OBJECT_0
                getattr(self.api, method).side_effect = FoundryError('exit_observation_unavailable', 'failed')
                self.assert_code('exit_observation_unavailable', self.observe)
                self.api.close.assert_called_once_with('owned')

    def test_unexpected_wait_result_is_not_exit(self):
        for value in (exits.WAIT_FAILED, 0x80, -1, None, True, False):
            with self.subTest(value=value):
                self.api.wait.return_value = value
                self.assert_code('exit_observation_unavailable', self.observe)

    def test_invalid_time_or_pid_stops_before_native_access(self):
        for seconds in (-1, 60.01, float('nan'), float('inf'), True, '1'):
            with self.subTest(seconds=seconds), patch.object(exits, '_WindowsApi') as factory:
                self.assert_code('invalid_timeout', lambda: exits.observe_exit(identity(), seconds))
                factory.assert_not_called()
        for pid in (True, 0, 2 ** 32):
            with self.subTest(pid=pid), patch.object(exits, '_WindowsApi') as factory:
                self.assert_code('invalid_identity', lambda: exits.observe_exit(dict(identity(), pid=pid)))
                factory.assert_not_called()

    def test_hash_mismatch_stops_before_open(self):
        with patch.object(exits, '_WindowsApi', return_value=self.api), \
                patch.object(exits.launch, '_source_stat', return_value={}), \
                patch.object(exits.launch, '_snapshot', return_value=('b' * 64, None, False)):
            self.assert_code('wrong_executable', lambda: exits.observe_exit(identity()))
        self.api.open.assert_not_called()

    def test_caller_alias_does_not_change_captured_target(self):
        original = identity()
        def changed(*args, **kwargs):
            original.update(pid=444, creation_filetime=555, executable=r'C:\Other\TS4_x64.exe')
            return ('a' * 64, None, False)
        with patch.object(exits, '_WindowsApi', return_value=self.api), \
                patch.object(exits.launch, '_source_stat', return_value={}), \
                patch.object(exits.launch, '_snapshot', side_effect=changed):
            result = exits.observe_exit(original, 0)
        self.api.open.assert_called_once_with(42)
        self.assertEqual(123, result['creation_filetime'])

    def test_cli_unresolved_exit_three_and_new_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            identity_path, output_path = Path(directory) / 'identity.json', Path(directory) / 'result.json'
            expected = identity()
            identity_path.write_text(json.dumps(expected))
            value = {'ok': False, 'outcome': 'unresolved', 'process_exit_verified': False}
            with patch.object(exits, 'observe_exit', return_value=value) as observe, redirect_stdout(io.StringIO()):
                self.assertEqual(3, cli.main(['observe-exit', '--identity', str(identity_path),
                                              '--seconds', '0', '--output', str(output_path)]))
            self.assertEqual(value, json.loads(output_path.read_text()))
            observe.assert_called_once_with(expected, 0.0)
            with patch.object(exits, 'observe_exit') as observe, redirect_stdout(io.StringIO()):
                self.assertEqual(2, cli.main(['observe-exit', '--identity', str(identity_path),
                                              '--output', str(output_path)]))
            observe.assert_not_called()


_NATIVE_CI = (os.name == 'nt' and os.environ.get('GITHUB_ACTIONS') == 'true'
              and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')


@unittest.skipUnless(_NATIVE_CI, 'Only disposable GitHub-hosted Windows child-process fixtures')
class NativeExitFixtureTests(unittest.TestCase):
    def test_new_owned_python_child_exit_and_timeout(self):
        # No discover/name scan, Sims process, desktop, or game installation.
        child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c',
                                  'import sys; sys.stdin.buffer.read(1)'], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        api = exits._WindowsApi()
        handle = api.open(child.pid)
        try:
            image, created = api.identity(handle)
        finally:
            api.close(handle)
        try:
            ended, _, _ = exits._wait_retained(child.pid, image, created, 0, api, time.monotonic)
            self.assertFalse(ended)
            def finish():
                time.sleep(.1)
                child.stdin.write(b'x')
                child.stdin.flush()
            worker = threading.Thread(target=finish)
            worker.start()
            try:
                ended, _, _ = exits._wait_retained(child.pid, image, created, 5, api, time.monotonic)
                self.assertTrue(ended)
            finally:
                worker.join(5)
            self.assertEqual(0, child.wait(timeout=5))
        finally:
            child.stdin.close()
            child.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
