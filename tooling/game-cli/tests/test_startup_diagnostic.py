"""Fabricated modules, owned temp paths/threads only; never game qualification."""
import builtins
import _thread
import importlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'diagnostic-src'
RUNTIME = SOURCE / 'foundry_diagnostic' / 'runtime.py'


def load_runtime():
    # A fresh owned fixture module stands for a fresh process, never a deployed
    # reload/retry feature. The actual reload prohibition has a subprocess test.
    spec = importlib.util.spec_from_file_location('fixture_diagnostic', str(RUNTIME))
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


class StartupDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'evidence.json'
        self.config = {'run_id': 'a' * 32, 'output_path': str(self.output),
                       'wait_seconds': 1.0}
        self.core = ModuleType('sims4.core_services')
        self.core.on_tick = lambda *args, **kwargs: None
        self.modules = patch.dict(sys.modules, {'sims4.core_services': self.core})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.runs = []
        self.addCleanup(self.finish_workers)

    def finish_workers(self):
        for run in self.runs:
            run._finish_sample('fixture_stopped')
            if run._worker is not None and run._worker.ident is not None:
                run._worker.join(2)
                self.assertFalse(run._worker.is_alive())

    def start(self, **changes):
        config = dict(self.config)
        config.update(changes)
        run = self.runtime.start(config, module_origin='example.ts4script/entry.pyc',
                                 loader_type='FabricatedLoader')
        self.runs.append(run)
        return run

    def evidence(self, run):
        run._worker.join(2)
        self.assertFalse(run._worker.is_alive())
        self.assertEqual('published', run.status['publication'])
        raw = self.output.read_bytes()
        self.assertLessEqual(len(raw), self.runtime.MAX_EVIDENCE_BYTES)
        self.assertEqual([self.output], list(Path(self.temp.name).iterdir()))
        return json.loads(raw.decode('utf-8'))

    def wait_for_run(self, run):
        if run._worker is not None:
            run._worker.join(2)
            self.assertFalse(run._worker.is_alive())

    def test_package_import_is_completely_inert(self):
        code = r'''
import builtins, os, socket, sys, threading
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError('forbidden activation')
os.open = forbidden
socket.socket = forbidden
threading.Thread.start = forbidden
threading.Lock = forbidden
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.startswith(('sims4', 'services', 'foundry_companion')):
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
import foundry_diagnostic
assert 'foundry_diagnostic.runtime' not in sys.modules
'''
        subprocess.run([sys.executable, '-c', code, str(SOURCE)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_runtime_import_does_not_activate(self):
        with patch.object(threading.Thread, 'start', side_effect=AssertionError), \
                patch.object(socket, 'socket', side_effect=AssertionError), \
                patch.object(os, 'open', side_effect=AssertionError):
            load_runtime()

    def test_success_preserves_args_return_and_reports_only_self_report(self):
        marker, argument = object(), object()
        calls = []
        def original(*args, **kwargs):
            calls.append((args, kwargs))
            return marker
        self.core.on_tick = original
        startup = threading.current_thread()
        run = self.start()
        self.assertIs(marker, self.core.on_tick(argument, 2, key=argument))
        self.assertEqual([((argument, 2), {'key': argument})], calls)
        self.assertIs(original, self.core.on_tick)
        data = self.evidence(run)
        self.assertEqual('tick_observed', data['result'])
        self.assertEqual(os.getpid(), data['pid_self_report'])
        self.assertEqual(id(startup), data['startup_thread']['object_id'])
        self.assertEqual(threading.get_ident(), data['startup_thread']['ident'])
        self.assertTrue(data['tick_thread']['same_thread_object_as_startup'])
        self.assertEqual('restored_own_hook', data['hook_cleanup'])
        self.assertEqual(list(sys.version_info[:3]), data['python_self_report']['version_info'])
        for field in ('independent_evidence', 'profile_authority',
                      'os_process_lifetime_proof', 'game_qualified',
                      'native_services_called', 'credentials_used'):
            self.assertIs(False, data[field])
        self.assertEqual('unknown', data['import_outside_tick'])
        self.assertEqual('unknown', data['owner_thread_authority'])

    def test_original_base_exception_identity_is_preserved(self):
        failure = KeyboardInterrupt('fixture')
        def original(*args, **kwargs):
            raise failure
        self.core.on_tick = original
        run = self.start(wait_seconds=0.04)
        hook = self.core.on_tick
        with self.assertRaises(KeyboardInterrupt) as caught:
            hook()
        self.assertIs(failure, caught.exception)
        self.assertIs(hook, self.core.on_tick)
        data = self.evidence(run)
        self.assertEqual('timeout', data['result'])
        self.assertIsNone(data['tick_thread'])
        self.assertEqual('pending', data['hook_cleanup'])

    def test_exception_then_success_records_only_success(self):
        failure = ValueError('fixture')
        calls = []
        def original():
            calls.append(1)
            if len(calls) == 1:
                raise failure
            return 12
        self.core.on_tick = original
        run = self.start()
        with self.assertRaises(ValueError) as caught:
            self.core.on_tick()
        self.assertIs(failure, caught.exception)
        self.assertEqual(12, self.core.on_tick())
        self.assertEqual('tick_observed', self.evidence(run)['result'])

    def test_unavailable_core_is_not_imported(self):
        del sys.modules['sims4.core_services']
        original_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith(('sims4', 'services', 'foundry_companion')):
                raise AssertionError('must not import game module')
            return original_import(name, *args, **kwargs)
        with patch.object(builtins, '__import__', side_effect=guarded):
            run = self.start()
            data = self.evidence(run)
        self.assertEqual('core_unavailable', data['result'])
        self.assertEqual('not_installed', data['hook_cleanup'])

    def test_module_subclass_is_refused_without_attribute_execution(self):
        class NotExact(ModuleType):
            def __getattribute__(self, name):
                raise AssertionError('attribute execution')
        sys.modules['sims4.core_services'] = NotExact('sims4.core_services')
        self.assertEqual('core_unavailable', self.evidence(self.start())['result'])

    def test_missing_tick_is_unavailable(self):
        del self.core.on_tick
        data = self.evidence(self.start())
        self.assertEqual('on_tick_unavailable', data['result'])

    def test_module_getattr_is_not_invoked(self):
        del self.core.on_tick
        def unexpected(name):
            raise AssertionError('dynamic attribute callback')
        self.core.__getattr__ = unexpected
        self.assertEqual('on_tick_unavailable', self.evidence(self.start())['result'])

    def test_noncallable_tick_is_unavailable(self):
        self.core.on_tick = 42
        self.assertEqual('on_tick_unavailable', self.evidence(self.start())['result'])

    def test_duplicate_start_refuses_and_does_not_replace_hook(self):
        run = self.start()
        hook = self.core.on_tick
        with self.assertRaises(self.runtime.DiagnosticError) as caught:
            self.runtime.start(self.config)
        self.assertEqual('already_started', caught.exception.code)
        self.assertIs(hook, self.core.on_tick)
        hook()
        self.evidence(run)

    def test_reload_does_not_reset_one_shot_claim(self):
        code = r'''
import importlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
from foundry_diagnostic import runtime
try:
    runtime.start({})
except runtime.DiagnosticError:
    pass
importlib.reload(runtime)
try:
    runtime.start({})
except runtime.DiagnosticError as error:
    assert error.code == 'already_started', error.code
else:
    raise AssertionError('reload authorized retry')
'''
        subprocess.run([sys.executable, '-c', code, str(SOURCE)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_invalid_config_consumes_attempt_without_thread_or_file(self):
        with patch.object(threading.Thread, 'start', side_effect=AssertionError), \
                patch.object(os, 'open', side_effect=AssertionError):
            with self.assertRaises(self.runtime.DiagnosticError):
                self.runtime.start({})
            with self.assertRaises(self.runtime.DiagnosticError) as caught:
                self.runtime.start(self.config)
        self.assertEqual('already_started', caught.exception.code)

    def test_validation_rejects_nonfinite_bool_bounds_relative_and_profiles(self):
        cases = [({'run_id': 'A' * 32}, 'invalid_run_id'),
                 ({'run_id': 'a' * 31}, 'invalid_run_id'),
                 ({'wait_seconds': True}, 'invalid_wait_seconds'),
                 ({'wait_seconds': float('nan')}, 'invalid_wait_seconds'),
                 ({'wait_seconds': float('inf')}, 'invalid_wait_seconds'),
                 ({'wait_seconds': -0.1}, 'invalid_wait_seconds'),
                 ({'wait_seconds': 30.001}, 'invalid_wait_seconds'),
                 ({'wait_seconds': 10 ** 1000}, 'invalid_wait_seconds'),
                 ({'output_path': 'relative.json'}, 'invalid_output_path'),
                 ({'output_path': str(self.output) + '\x00'}, 'invalid_output_path'),
                 ({'output_path': str(Path(self.temp.name) / '..' / 'out.json')},
                  'invalid_output_path')]
        for folder in ('The Sims 4', 'The Sims 4 Legacy Edition',
                       'The Sims 4 original', 'The Sims 4 DO NOT TOUCH', 'Mods', 'Saves',
                       'Tray', 'example.ts4script'):
            cases.append(({'output_path': str(Path(self.temp.name) / folder / 'out.json')},
                          'output_must_be_external'))
        for changes, expected in cases:
            with self.subTest(changes=changes):
                runtime = load_runtime()
                config = dict(self.config)
                config.update(changes)
                with self.assertRaises(runtime.DiagnosticError) as caught:
                    runtime.start(config)
                self.assertEqual(expected, caught.exception.code)
        self.assertEqual([], list(Path(self.temp.name).iterdir()))

    def test_bounded_loader_self_report_without_string_callbacks(self):
        run = self.runtime.start(self.config, module_origin='\u2603' * 2000,
                                 loader_type='\u2603' * 1000)
        self.runs.append(run)
        self.core.on_tick()
        report = self.evidence(run)['loader_self_report']
        self.assertEqual(1024, len(report['module_origin']))
        self.assertEqual(256, len(report['loader_type']))
        self.assertTrue(report['module_origin_truncated'])
        self.assertTrue(report['loader_type_truncated'])

    def test_astral_loader_fields_are_bounded_by_encoded_json_size(self):
        run = self.runtime.start(self.config, module_origin='\U0001f9ea' * 1024,
                                 loader_type='\U0001f9ea' * 256)
        self.runs.append(run)
        self.core.on_tick()
        data = self.evidence(run)
        report = data['loader_self_report']
        self.assertEqual(512, len(report['module_origin']))
        self.assertEqual(128, len(report['loader_type']))
        self.assertTrue(report['module_origin_truncated'])
        self.assertTrue(report['loader_type_truncated'])
        self.assertEqual('tick_observed', data['result'])

    def test_loader_objects_are_not_stringified(self):
        class Hostile(object):
            def __str__(self):
                raise AssertionError('string callback')
        with self.assertRaises(self.runtime.DiagnosticError) as caught:
            self.runtime.start(self.config, loader_type=Hostile())
        self.assertEqual('invalid_loader_report', caught.exception.code)

    def test_selected_unicode_external_path_is_supported(self):
        self.output = Path(self.temp.name) / '\u68c0\u6d4b-\u00e9.json'
        run = self.start(output_path=str(self.output))
        self.core.on_tick()
        self.assertEqual('tick_observed', self.evidence(run)['result'])

    def test_windows_unsafe_component_names_are_refused_on_all_hosts(self):
        for name in ('CON.json', 'nul.JSON.json', 'com1.extra.json',
                     'NUL .json', 'CON   .json', 'COM1 .json', 'LPT1 .json',
                     'LPT\u00b9.json', 'evidence:stream.json', 'bad?.json',
                     'bad\nname.json', '\ud800.json'):
            with self.subTest(name=repr(name)):
                runtime = load_runtime()
                config = dict(self.config, output_path=str(Path(self.temp.name) / name))
                with self.assertRaises(runtime.DiagnosticError) as caught:
                    runtime.start(config)
                self.assertEqual('invalid_output_path', caught.exception.code)

    def test_startup_inside_known_callable_refuses_before_io_thread_or_hook(self):
        def original():
            run = self.start()
            return run
        self.core.on_tick = original
        with patch.object(os, 'open', side_effect=AssertionError), \
                patch.object(os, 'lstat', side_effect=AssertionError), \
                patch.object(threading.Thread, 'start', side_effect=AssertionError), \
                patch.object(threading, 'current_thread', side_effect=AssertionError):
            run = original()
        self.assertIs(original, self.core.on_tick)
        self.assertIsNone(run._worker)
        self.assertEqual('startup_inside_known_tick', run.status['result'])
        self.assertEqual('not_attempted', run.status['publication'])
        self.assertTrue(run.status['startup_stack_self_report']['exact_original_code_on_stack'])
        self.assertFalse(self.output.exists())
        with self.assertRaises(self.runtime.DiagnosticError) as caught:
            self.runtime.start(self.config)
        self.assertEqual('already_started', caught.exception.code)

    def test_startup_stack_absence_and_callable_objects_remain_unknown(self):
        class CallableTick(object):
            def __call__(self):
                return 8
        self.core.on_tick = CallableTick()
        run = self.start()
        self.assertEqual(8, self.core.on_tick())
        data = self.evidence(run)
        self.assertFalse(data['startup_stack_self_report']['exact_original_code_available'])
        self.assertIsNone(data['startup_stack_self_report']['exact_original_code_on_stack'])
        self.assertEqual('unknown', data['import_outside_tick'])

    def test_startup_stack_bound_does_not_retain_frames(self):
        def recurse(depth):
            return recurse(depth - 1) if depth else self.start()
        run = recurse(80)
        self.core.on_tick()
        report = self.evidence(run)['startup_stack_self_report']
        self.assertEqual(64, report['depth_checked'])
        self.assertTrue(report['truncated'])

    def test_foreign_success_records_mismatch_without_restoring_hook(self):
        original = self.core.on_tick
        run = self.start()
        hook = self.core.on_tick
        thread = threading.Thread(target=hook)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIs(hook, self.core.on_tick)
        data = self.evidence(run)
        self.assertEqual('thread_mismatch', data['result'])
        self.assertFalse(data['tick_thread']['same_thread_object_as_startup'])
        self.assertEqual(id(thread), data['tick_thread']['object_id'])
        self.assertEqual('pending', data['hook_cleanup'])
        before = self.output.read_bytes()
        # A later successful startup-thread callback can safely remove only the
        # still-current wrapper. It never updates/replays the already-published file.
        hook()
        self.assertIs(original, self.core.on_tick)
        self.assertEqual(before, self.output.read_bytes())

    def test_unmanaged_native_thread_does_not_construct_dummy_thread(self):
        run = self.start()
        hook = self.core.on_tick
        done = threading.Event()
        failures = []
        def target():
            try:
                hook()
            except BaseException as error:
                failures.append(error)
            finally:
                done.set()
        with patch.object(threading, '_DummyThread', side_effect=AssertionError('DummyThread')):
            _thread.start_new_thread(target, ())
            self.assertTrue(done.wait(1))
        self.assertEqual([], failures)
        data = self.evidence(run)
        self.assertEqual('tick_thread_unavailable', data['result'])
        self.assertIsNone(data['tick_thread']['object_id'])
        self.assertEqual('pending', data['hook_cleanup'])
        self.assertIs(hook, self.core.on_tick)

    def test_reentrancy_samples_outer_success_only(self):
        calls = []
        def original(depth):
            calls.append(depth)
            if depth:
                return self.core.on_tick(depth - 1) + 1
            return 10
        self.core.on_tick = original
        run = self.start()
        self.assertEqual(13, self.core.on_tick(3))
        self.assertEqual([3, 2, 1, 0], calls)
        self.assertEqual('tick_observed', self.evidence(run)['result'])
        self.assertIs(original, self.core.on_tick)

    def test_inner_success_outer_exception_does_not_sample(self):
        failure = RuntimeError('outer failed')
        def original(depth):
            if depth:
                self.core.on_tick(0)
                raise failure
            return None
        self.core.on_tick = original
        run = self.start(wait_seconds=0.03)
        with self.assertRaises(RuntimeError) as caught:
            self.core.on_tick(1)
        self.assertIs(failure, caught.exception)
        self.assertEqual('timeout', self.evidence(run)['result'])

    def test_concurrent_call_is_passthrough_not_second_sample(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def original(block=False):
            calls.append(block)
            if block:
                entered.set()
                self.assertTrue(release.wait(1))
            return block
        self.core.on_tick = original
        run = self.start()
        hook = self.core.on_tick
        foreign = threading.Thread(target=lambda: hook(True))
        foreign.start()
        self.assertTrue(entered.wait(1))
        self.assertFalse(hook(False))
        self.assertIs(hook, self.core.on_tick)
        release.set()
        foreign.join(1)
        data = self.evidence(run)
        self.assertEqual([True, False], calls)
        self.assertEqual('thread_mismatch', data['result'])

    def test_later_hook_is_never_overwritten_even_when_it_calls_our_wrapper(self):
        run = self.start()
        hook = self.core.on_tick
        later_calls = []
        def later(*args, **kwargs):
            later_calls.append(1)
            return hook(*args, **kwargs)
        self.core.on_tick = later
        later()
        self.assertIs(later, self.core.on_tick)
        data = self.evidence(run)
        self.assertEqual('hook_conflict', data['result'])
        self.assertEqual('not_current_pending', data['hook_cleanup'])
        self.assertEqual([1], later_calls)

    def test_replacement_inside_original_is_not_overwritten(self):
        later = lambda: 19
        def original():
            self.core.on_tick = later
            return 42
        self.core.on_tick = original
        run = self.start()
        self.assertEqual(42, self.core.on_tick())
        self.assertIs(later, self.core.on_tick)
        self.assertEqual('hook_conflict', self.evidence(run)['result'])

    def test_replaced_core_module_is_a_conflict_not_new_authority(self):
        run = self.start()
        hook = self.core.on_tick
        replacement = ModuleType('sims4.core_services')
        replacement.on_tick = lambda: 4
        sys.modules['sims4.core_services'] = replacement
        hook()
        self.assertIs(hook, self.core.on_tick)
        self.assertEqual(4, replacement.on_tick())
        self.assertEqual('hook_conflict', self.evidence(run)['result'])

    def test_timeout_disables_sampling_and_later_success_only_cleans_own_hook(self):
        original = self.core.on_tick
        run = self.start(wait_seconds=0.02)
        hook = self.core.on_tick
        data = self.evidence(run)
        self.assertEqual('timeout', data['result'])
        self.assertEqual('pending', data['hook_cleanup'])
        self.assertIs(hook, self.core.on_tick)
        before = self.output.read_bytes()
        hook()
        self.assertIs(original, self.core.on_tick)
        self.assertEqual('timeout', run.status['result'])
        self.assertEqual(before, self.output.read_bytes())

    def test_zero_wait_never_records_callback(self):
        run = self.start(wait_seconds=0)
        self.core.on_tick()
        self.assertEqual('timeout', self.evidence(run)['result'])

    def test_slow_original_finishing_after_deadline_is_not_collected(self):
        def original():
            time.sleep(0.05)  # Fabricated original behavior, not diagnostic hook work.
            return 73
        self.core.on_tick = original
        run = self.start(wait_seconds=0.01)
        self.assertEqual(73, self.core.on_tick())
        self.assertEqual('timeout', self.evidence(run)['result'])
        self.assertIs(original, self.core.on_tick)

    def test_successful_tick_does_no_io_import_thread_wait_or_lock_creation(self):
        run = self.start()
        owner = threading.current_thread()
        tick_active = [False]
        def checked(original):
            def call(*args, **kwargs):
                if threading.current_thread() is owner and tick_active[0]:
                    raise AssertionError('diagnostic performed forbidden tick action')
                return original(*args, **kwargs)
            return call
        targets = [(builtins, 'open'), (builtins, '__import__'),
                   (os, 'open'), (os, 'link'), (os, 'unlink'),
                   (os, 'stat'), (os, 'lstat'), (os, 'fsync'),
                   (socket, 'socket'), (threading.Thread, 'start'),
                   (threading, 'Lock'), (threading, 'RLock'),
                   (threading.Event, 'wait'), (time, 'sleep')]
        patches = [patch.object(obj, name, checked(getattr(obj, name)))
                   for obj, name in targets]
        for item in patches:
            item.start()
        try:
            tick_active[0] = True
            self.core.on_tick()
            tick_active[0] = False
        finally:
            tick_active[0] = False
            for item in reversed(patches):
                item.stop()
        self.assertEqual('tick_observed', self.evidence(run)['result'])

    def test_tick_fault_does_not_replace_successful_original_result(self):
        marker = object()
        self.core.on_tick = lambda: marker
        run = self.start()
        with patch.object(run, '_after_success', side_effect=SystemExit):
            self.assertIs(marker, self.core.on_tick())
        self.assertEqual('diagnostic_fault', self.evidence(run)['result'])

    def test_worker_start_failure_cleans_current_hook_and_never_retries(self):
        original = self.core.on_tick
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('secret')) as mocked:
            run = self.start()
        self.assertEqual(1, mocked.call_count)
        self.assertIs(original, self.core.on_tick)
        self.assertEqual('worker_start_failed', run.status['result'])
        self.assertEqual('failed', run.status['publication'])
        self.assertEqual('worker_start_failed', run.status['publication_error'])
        self.assertFalse(self.output.exists())

    def test_late_start_exception_cancels_worker_before_resource_claim(self):
        original_work = self.runtime.DiagnosticRun._work
        original_start = threading.Thread.start
        gate = threading.Event()
        def gated_work(run):
            gate.wait(2)
            original_work(run)
        def start_then_raise(worker):
            original_start(worker)
            raise KeyboardInterrupt('owned fixture after actual start')
        original_tick = self.core.on_tick
        with patch.object(self.runtime.DiagnosticRun, '_work', gated_work), \
                patch.object(threading.Thread, 'start', start_then_raise):
            run = self.start()
        self.assertTrue(run._worker.is_alive())
        self.assertTrue(run.status['finished'])
        self.assertEqual('cancelled_before_worker_claim', run.status['worker_launch'])
        self.assertEqual('failed', run.status['publication'])
        self.assertIsNone(run._parent_fd)
        self.assertIs(original_tick, self.core.on_tick)
        gate.set()
        run._worker.join(2)
        self.assertFalse(run._worker.is_alive())
        self.assertFalse(self.output.exists())
        self.assertEqual('failed', run.status['publication'])

    def test_late_start_exception_leaves_resources_with_claimed_worker(self):
        original_start = threading.Thread.start
        original_publish = self.runtime.DiagnosticRun._publish
        claimed, release = threading.Event(), threading.Event()
        def gated_publish(run):
            claimed.set()
            release.wait(2)
            original_publish(run)
        def start_then_raise(worker):
            original_start(worker)
            self.assertTrue(claimed.wait(1))
            raise KeyboardInterrupt('owned fixture after worker claim')
        with patch.object(self.runtime.DiagnosticRun, '_publish', gated_publish), \
                patch.object(threading.Thread, 'start', start_then_raise):
            run = self.start(wait_seconds=0)
            self.assertTrue(run._worker.is_alive())
            self.assertFalse(run.status['finished'])
            self.assertEqual('start_interrupted_worker_owned', run.status['worker_launch'])
            self.assertEqual('pending', run.status['publication'])
            if self.runtime._PIN_DIRECTORY:
                self.assertIsNotNone(run._parent_fd)
            release.set()
            data = self.evidence(run)
        self.assertEqual('timeout', data['result'])
        self.assertIsNone(run._parent_fd)
        self.assertIsNone(run.status['publication_error'])

    def test_late_start_exception_after_completed_worker_preserves_result(self):
        original_start = threading.Thread.start
        def start_then_raise(worker):
            original_start(worker)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            raise KeyboardInterrupt('owned fixture after worker completion')
        with patch.object(threading.Thread, 'start', start_then_raise):
            run = self.start(wait_seconds=0)
        self.assertTrue(run.status['finished'])
        self.assertEqual('published', run.status['publication'])
        self.assertEqual('start_interrupted_worker_owned', run.status['worker_launch'])
        self.assertIsNone(run.status['publication_error'])
        self.assertEqual('timeout', self.evidence(run)['result'])

    def test_existing_output_is_preserved_and_temp_is_cleaned(self):
        self.output.write_bytes(b'existing evidence')
        original = self.core.on_tick
        run = self.start(wait_seconds=0)
        self.wait_for_run(run)
        self.assertIsNone(run._worker)
        self.assertIs(original, self.core.on_tick)
        self.assertEqual('failed', run.status['publication'])
        self.assertEqual(b'existing evidence', self.output.read_bytes())
        self.assertEqual([self.output], list(Path(self.temp.name).iterdir()))
        self.assertEqual('not_created', run.status['temporary_cleanup'])
        with self.assertRaises(self.runtime.DiagnosticError):
            self.runtime.start(self.config)

    def test_temp_collision_is_never_taken_over_or_deleted(self):
        temporary = Path(str(self.output) + '.' + 'a' * 32 + '.tmp')
        temporary.write_bytes(b'preexisting temp')
        run = self.start(wait_seconds=0)
        run._worker.join(2)
        self.assertEqual('failed', run.status['publication'])
        self.assertFalse(self.output.exists())
        self.assertEqual(b'preexisting temp', temporary.read_bytes())

    def test_missing_parent_is_not_created(self):
        target = Path(self.temp.name) / 'not-created' / 'out.json'
        run = self.start(wait_seconds=0, output_path=str(target))
        self.wait_for_run(run)
        self.assertEqual('failed', run.status['publication'])
        self.assertFalse(target.parent.exists())

    def test_complete_exclusive_publication_and_worker_only_output(self):
        link = os.link
        owner = threading.current_thread()
        seen = []
        def checked_link(source, destination, **kwargs):
            self.assertIsNot(owner, threading.current_thread())
            self.assertFalse(os.path.lexists(str(self.output)))
            data = json.loads((Path(self.temp.name) / source).read_text())
            seen.append(data['run_id'])
            return link(source, destination, **kwargs)
        with patch.object(os, 'link', side_effect=checked_link):
            run = self.start()
            self.core.on_tick()
            self.evidence(run)
        self.assertEqual(['a' * 32], seen)
        if os.name != 'nt':
            self.assertEqual(0o600, self.output.stat().st_mode & 0o777)

    def test_publication_failure_cleans_own_temp_and_does_not_fallback(self):
        with patch.object(os, 'link', side_effect=OSError('secret error')) as mocked:
            run = self.start(wait_seconds=0)
            run._worker.join(2)
        self.assertEqual(1, mocked.call_count)
        self.assertEqual('failed', run.status['publication'])
        self.assertEqual('OSError', run.status['publication_error'])
        self.assertEqual('removed', run.status['temporary_cleanup'])
        self.assertEqual([], list(Path(self.temp.name).iterdir()))

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlink unavailable')
    def test_symlink_ancestor_is_refused_before_hook_installation(self):
        real = Path(self.temp.name) / 'real'
        real.mkdir()
        alias = Path(self.temp.name) / 'alias'
        try:
            os.symlink(str(real), str(alias), target_is_directory=True)
        except OSError:
            self.skipTest('symlink creation unavailable')
        original = self.core.on_tick
        run = self.start(output_path=str(alias / 'out.json'))
        self.assertEqual('output_unavailable', run.status['result'])
        self.assertEqual('failed', run.status['publication'])
        self.assertIsNone(run._worker)
        self.assertIs(original, self.core.on_tick)
        self.assertEqual([], list(real.iterdir()))

    @unittest.skipUnless(os.name != 'nt' and hasattr(os, 'symlink'), 'POSIX fixture')
    def test_parent_replaced_with_symlink_during_wait_is_not_followed(self):
        parent = Path(self.temp.name) / 'selected'
        parent.mkdir()
        destination = parent / 'out.json'
        moved = Path(self.temp.name) / 'moved'
        foreign = Path(self.temp.name) / 'foreign'
        foreign.mkdir()
        run = self.start(output_path=str(destination))
        parent.rename(moved)
        os.symlink(str(foreign), str(parent), target_is_directory=True)
        self.core.on_tick()
        self.wait_for_run(run)
        self.assertEqual('failed', run.status['publication'])
        self.assertEqual([], list(foreign.iterdir()))
        self.assertEqual([], list(moved.iterdir()))

    @unittest.skipUnless(os.name != 'nt' and hasattr(os, 'symlink'), 'POSIX fixture')
    def test_replaced_temp_is_never_published_or_deleted(self):
        fsync = os.fsync
        temporary = Path(str(self.output) + '.' + 'a' * 32 + '.tmp')
        original_bytes = Path(self.temp.name) / 'original-temp'
        def substitute(descriptor):
            fsync(descriptor)
            temporary.rename(original_bytes)
            temporary.write_bytes(b'foreign replacement')
        with patch.object(os, 'fsync', side_effect=substitute):
            run = self.start(wait_seconds=0)
            self.wait_for_run(run)
        self.assertEqual('failed', run.status['publication'])
        self.assertFalse(self.output.exists())
        self.assertEqual(b'foreign replacement', temporary.read_bytes())
        self.assertEqual('identity_changed_pending', run.status['temporary_cleanup'])

    def test_reparse_metadata_is_refused_without_following_it(self):
        class Metadata(object):
            st_mode = 0o40700
            st_file_attributes = 1024
            st_dev = 1
            st_ino = 2
        with self.assertRaises(self.runtime.DiagnosticError) as caught:
            self.runtime.DiagnosticRun._directory_identity(Metadata())
        self.assertEqual('output_ancestor_link_or_not_directory', caught.exception.code)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlink unavailable')
    def test_existing_destination_symlink_does_not_touch_target(self):
        target = Path(self.temp.name) / 'target.txt'
        target.write_bytes(b'protected')
        try:
            os.symlink(str(target), str(self.output))
        except OSError:
            self.skipTest('symlink creation unavailable')
        run = self.start(wait_seconds=0)
        self.wait_for_run(run)
        self.assertEqual('failed', run.status['publication'])
        self.assertEqual(b'protected', target.read_bytes())
        self.assertTrue(self.output.is_symlink())


if __name__ == '__main__':
    unittest.main()
