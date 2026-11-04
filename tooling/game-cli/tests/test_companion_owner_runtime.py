"""Owned fabricated cores/services and localhost only; no game qualification."""
from pathlib import Path
from contextlib import contextmanager
import socket
import struct
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion import owner_runtime as runtime, wire, live_wire
from foundry_companion.native import DenyAdmission, HostModeDeclaration
from foundry_companion.queue import (ProcessEpoch, QueueBusy, QueueError,
    LiveQueueRequest, LiveQueueArguments, LiveQueueExpectedContext)
from foundry_companion.transport import Binding, DenyHostPolicy

SESSION, EPOCH = '2' * 32, ProcessEpoch(123, 456)


def module(name, **attributes):
    value = ModuleType(name)
    value.__dict__.update(attributes)
    return value


class FixtureTransport:
    """Lifecycle fixture only, with no authorization or production role."""
    def __init__(self):
        self.binding = Binding(SESSION, EPOCH)
        self.address = ('127.0.0.1', 1)
        self.start = Mock(return_value=self.address)
        self.shutdown = Mock(return_value=True)
        self.owner_tick = Mock()


class LifecycleTests(unittest.TestCase):
    def make(self, original=None, **changes):
        original = original if original is not None else Mock(return_value=object())
        core = module('sims4.core_services', on_tick=original)
        services = module('services')
        loader = Mock(side_effect=lambda name: {'sims4.core_services': core, 'services': services}[name])
        transport = FixtureTransport()
        with patch.object(runtime, 'LoopbackTransport', return_value=transport):
            owner = runtime.OwnerRuntime(SESSION, EPOCH, module_loader=loader, **changes)
        self.addCleanup(owner.close)
        return owner, core, original, loader, transport

    def assert_code(self, code, action, *args):
        with self.assertRaises(runtime.OwnerRuntimeError) as error:
            action(*args)
        self.assertEqual(code, error.exception.code)

    def test_import_and_construction_do_not_import_game_start_thread_or_open_socket(self):
        code = """
import builtins, socket, threading, sys
sys.path.insert(0, sys.argv[1])
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'services' or name.startswith(('sims4', 'server_commands', 'apex')):
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
def forbidden(*args, **kwargs):
    raise AssertionError('unexpected I/O or thread')
socket.socket = forbidden
threading.Thread.start = forbidden
from foundry_companion.owner_runtime import OwnerRuntime
from foundry_companion.queue import ProcessEpoch
owner = OwnerRuntime('2' * 32, ProcessEpoch(123, 456))
assert owner.address is None and owner.status.state == 'new'
assert owner.close()
"""
        subprocess.run([sys.executable, '-c', code, str(SOURCE)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_original_first_exact_arguments_return_and_no_dispatch_during_start(self):
        events, result, value = [], object(), object()
        def original(*args, **kwargs):
            events.append(('original', args, kwargs))
            return result
        owner, core, _, loader, transport = self.make(original)
        transport.start.side_effect = lambda port: core.on_tick('startup')
        owner.install(0)
        transport.owner_tick.assert_not_called()
        transport.owner_tick.side_effect = lambda execute: events.append(('drain',))
        self.assertIs(result, core.on_tick(value, b= value))
        self.assertEqual([('original', ('startup',), {}),
                          ('original', (value,), {'b': value}), ('drain',)], events)
        self.assertEqual(['sims4.core_services', 'services'],
                         [call[0][0] for call in loader.call_args_list])
        self.assertIs(original, core.on_tick.__wrapped__)

    def test_original_exceptions_are_exact_and_never_dispatch(self):
        for error in (ValueError('original'), KeyboardInterrupt(), SystemExit(7)):
            def original(*args, **kwargs):
                raise error
            owner, core, _, _, transport = self.make(original)
            owner.install()
            with self.assertRaises(type(error)) as caught:
                core.on_tick(object(), sample=object())
            self.assertIs(error, caught.exception)
            self.assertEqual(0, owner._tick_depth)
            self.assertTrue(owner.status.enabled)
            transport.owner_tick.assert_not_called()

    def test_foreign_tick_only_calls_original_and_foreign_lifecycle_refuses(self):
        owner, core, original, _, transport = self.make()
        owner.install()
        results = []
        def foreign():
            results.append(core.on_tick('foreign'))
            for action in (owner.install, owner.close):
                try:
                    action()
                except runtime.OwnerRuntimeError as error:
                    results.append(error.code)
        worker = threading.Thread(target=foreign)
        worker.start(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([original.return_value, 'wrong_thread', 'wrong_thread'], results)
        transport.owner_tick.assert_not_called()
        self.assertTrue(owner.status.enabled)
        self.assertEqual(0, owner._tick_depth)

    def test_nested_original_and_executor_ticks_dispatch_only_once(self):
        seen, references = [], {}
        def original(depth=0):
            seen.append(depth)
            if depth == 0:
                references['core'].on_tick(1)
            return depth
        owner, core, _, _, transport = self.make(original)
        references['core'] = core
        owner.install()
        transport.owner_tick.side_effect = lambda execute: core.on_tick(2)
        self.assertEqual(0, core.on_tick())
        self.assertEqual([0, 1, 2], seen)
        transport.owner_tick.assert_called_once()
        self.assertEqual(0, owner._tick_depth)

    def test_queue_busy_is_one_nonblocking_attempt_per_tick(self):
        owner, core, original, _, transport = self.make()
        owner.install()
        transport.owner_tick.side_effect = QueueBusy()
        for expected in (1, 2):
            self.assertIs(original.return_value, core.on_tick())
            self.assertEqual(expected, transport.owner_tick.call_count)
            self.assertTrue(owner.status.enabled)
            self.assertEqual('queue_busy', owner.status.last_tick)
        transport.shutdown.assert_not_called()

    def test_dispatch_faults_preserve_original_return_and_latch_off_without_io(self):
        for error in (RuntimeError('private'), KeyboardInterrupt(), SystemExit(2)):
            owner, core, original, _, transport = self.make()
            owner.install()
            transport.owner_tick.side_effect = error
            self.assertIs(original.return_value, core.on_tick())
            self.assertIs(original.return_value, core.on_tick())
            transport.owner_tick.assert_called_once()
            transport.shutdown.assert_not_called()
            self.assertEqual('dispatch_fault', owner.status.state)
            self.assertFalse(owner.status.enabled)

    def test_later_wrapper_is_preserved_and_ours_is_inert_in_its_chain(self):
        owner, core, original, _, transport = self.make()
        owner.install()
        installed = core.on_tick
        def later(*args, **kwargs):
            return installed(*args, **kwargs)
        core.on_tick = later
        self.assertIs(original.return_value, core.on_tick())
        self.assertEqual('hook_replaced', owner.status.state)
        transport.owner_tick.assert_not_called()
        self.assertTrue(owner.close())
        self.assertIs(later, core.on_tick)
        self.assertIs(original.return_value, later())
        transport.owner_tick.assert_not_called()

    def test_replacement_by_original_blocks_same_tick_and_never_rearms(self):
        references = {}
        replacement = Mock()
        def original():
            references['core'].on_tick = replacement
        owner, core, _, _, transport = self.make(original)
        references['core'] = core
        owner.install()
        installed = core.on_tick
        installed()
        transport.owner_tick.assert_not_called()
        core.on_tick = installed
        installed()
        self.assertFalse(owner.status.enabled)
        transport.owner_tick.assert_not_called()

    def test_preexisting_other_hook_is_restored_by_identity(self):
        earlier = Mock(return_value=object())
        owner, core, _, _, transport = self.make(earlier)
        owner.install()
        installed = core.on_tick
        self.assertTrue(owner.close())
        self.assertIs(earlier, core.on_tick)
        self.assertIs(earlier.return_value, installed())
        transport.owner_tick.assert_not_called()
        self.assert_code('lifecycle_closed', owner.install)

    def test_changed_hook_during_module_load_is_never_overwritten(self):
        owner, core, _, loader, transport = self.make()
        later = Mock()
        def load(name):
            if name == 'sims4.core_services':
                return core
            core.on_tick = later
            return module('services')
        loader.side_effect = load
        self.assert_code('hook_changed_during_install', owner.install)
        self.assertIs(later, core.on_tick)
        transport.start.assert_not_called()
        transport.shutdown.assert_called_once()

    def test_changed_hook_during_start_is_preserved_and_listener_closed(self):
        owner, core, _, _, transport = self.make()
        later = Mock()
        transport.start.side_effect = lambda port: setattr(core, 'on_tick', later)
        self.assert_code('installation_interrupted', owner.install)
        self.assertIs(later, core.on_tick)
        transport.shutdown.assert_called_once()
        self.assertFalse(owner.status.enabled)

    def test_start_failure_preserves_exception_and_prevents_retry_or_second_instance(self):
        owner, core, original, loader, transport = self.make()
        error = KeyboardInterrupt()
        transport.start.side_effect = error
        transport.shutdown.return_value = False
        with self.assertRaises(KeyboardInterrupt) as caught:
            owner.install()
        self.assertIs(error, caught.exception)
        self.assertIs(original, core.on_tick)
        self.assertFalse(owner.status.cleanup_complete)
        self.assert_code('lifecycle_closed', owner.install)
        with patch.object(runtime, 'LoopbackTransport', return_value=FixtureTransport()):
            second = runtime.OwnerRuntime(SESSION, EPOCH, module_loader=loader)
        self.addCleanup(second.close)
        self.assert_code('core_already_claimed', second.install)
        transport.start.assert_called_once()

    def test_clean_close_also_does_not_release_core_claim_for_rebinding(self):
        owner, core, original, loader, transport = self.make()
        owner.install(); owner.close()
        with patch.object(runtime, 'LoopbackTransport', return_value=FixtureTransport()):
            second = runtime.OwnerRuntime(SESSION, EPOCH, module_loader=loader)
        self.addCleanup(second.close)
        self.assert_code('core_already_claimed', second.install)
        self.assertIs(original, core.on_tick)

    def test_close_in_tick_only_disables_then_cleanup_can_run_outside_tick(self):
        reference, failures = {}, []
        def original():
            try:
                reference['owner'].close()
            except runtime.OwnerRuntimeError as error:
                failures.append(error.code)
            return 9
        owner, core, _, _, transport = self.make(original)
        reference['owner'] = owner
        owner.install()
        self.assertEqual(9, core.on_tick())
        self.assertEqual(['close_inside_tick'], failures)
        transport.owner_tick.assert_not_called()
        transport.shutdown.assert_not_called()
        self.assertEqual('cleanup_pending', owner.status.state)
        self.assertTrue(owner.close())
        transport.shutdown.assert_called_once()

    def test_close_failure_retains_pending_and_only_explicit_cleanup_can_continue(self):
        owner, core, original, _, transport = self.make()
        owner.install()
        transport.shutdown.side_effect = [False, RuntimeError('cleanup'), True]
        self.assertFalse(owner.close(0))
        self.assertEqual('cleanup_pending', owner.status.state)
        self.assertIs(original, core.on_tick)
        with self.assertRaises(RuntimeError):
            owner.close(0)
        self.assertFalse(owner.status.enabled)
        self.assertTrue(owner.close(0))
        self.assertEqual('closed', owner.status.state)
        transport.shutdown.side_effect = None
        self.assert_code('lifecycle_closed', owner.install)

    def test_shutdown_exception_does_not_replace_startup_exception(self):
        owner, core, original, _, transport = self.make()
        start_error = ValueError('start')
        transport.start.side_effect = start_error
        transport.shutdown.side_effect = RuntimeError('close')
        with self.assertRaises(ValueError) as caught:
            owner.install()
        self.assertIs(start_error, caught.exception)
        self.assertIs(original, core.on_tick)
        self.assertFalse(owner.status.cleanup_complete)
        transport.shutdown.side_effect = None

    def test_missing_loader_invalid_modules_and_invalid_ports_are_one_shot(self):
        for name, value, expected in (
                ('sims4.core_services', NS(on_tick=lambda: None), 'invalid_core_module'),
                ('sims4.core_services', module('core', on_tick=1), 'invalid_original_tick'),
                ('services', NS(), 'invalid_services_module')):
            owner, core, _, loader, transport = self.make()
            loader.side_effect = lambda requested, name=name, value=value: (
                value if requested == name else core)
            self.assert_code(expected, owner.install)
            self.assert_code('lifecycle_closed', owner.install)
            transport.start.assert_not_called()
        for port in (True, -1, 65536, 1.0):
            owner, _, _, loader, transport = self.make()
            self.assert_code('invalid_port', owner.install, port)
            loader.assert_not_called()
            self.assert_code('lifecycle_closed', owner.install)
        owner = runtime.OwnerRuntime(SESSION, EPOCH)
        self.addCleanup(owner.close)
        self.assert_code('module_loader_required', owner.install)

    def test_invalid_injections_are_rejected_without_starting_or_importing(self):
        for arguments, code in (
                ({'module_loader': 7}, 'invalid_module_loader'),
                ({'command_modules': []}, 'invalid_command_modules'),
                ({'command_modules': {'os': module('os')}}, 'invalid_command_modules'),
                ({'command_modules': {'sims4.commands': NS()}}, 'invalid_command_modules'),
                ({'live_readers': object()}, 'invalid_live_readers'),
                ({'live_readers': runtime.LiveReaders(None, lambda: None, lambda: None)},
                 'invalid_live_readers')):
            with self.subTest(code=code):
                self.assert_code(code, lambda: runtime.OwnerRuntime(SESSION, EPOCH, **arguments))

    def test_busy_claim_lock_does_not_block_or_change_core(self):
        owner, core, original, _, transport = self.make()
        runtime._CLAIM_LOCK.acquire()
        try:
            self.assert_code('lifecycle_busy', owner.install)
        finally:
            runtime._CLAIM_LOCK.release()
        self.assertIs(original, core.on_tick)
        transport.start.assert_not_called()
        self.assert_code('lifecycle_closed', owner.install)

    def test_second_runtime_cannot_overwrite_an_active_first_hook(self):
        owner, core, _, loader, transport = self.make()
        owner.install()
        installed = core.on_tick
        with patch.object(runtime, 'LoopbackTransport', return_value=FixtureTransport()):
            second = runtime.OwnerRuntime(SESSION, EPOCH, module_loader=loader)
        self.addCleanup(second.close)
        self.assert_code('core_already_claimed', second.install)
        self.assertIs(installed, core.on_tick)
        core.on_tick()
        transport.owner_tick.assert_called_once()

    def test_close_before_install_starts_nothing_and_invalid_close_does_not_disable(self):
        owner, _, _, loader, transport = self.make()
        self.assertTrue(owner.close())
        loader.assert_not_called()
        transport.start.assert_not_called()
        self.assert_code('lifecycle_closed', owner.install)
        active, _, _, _, _ = self.make()
        active.install()
        for timeout in (True, -1, 6, float('nan'), float('inf')):
            self.assert_code('invalid_timeout', active.close, timeout)
            self.assertTrue(active.status.enabled)

    def test_reentrant_close_during_start_never_enables_or_reinstalls(self):
        owner, core, original, _, transport = self.make()
        transport.start.side_effect = lambda port: owner.close()
        self.assert_code('installation_interrupted', owner.install)
        self.assertIs(original, core.on_tick)
        self.assertFalse(owner.status.enabled)
        self.assert_code('lifecycle_closed', owner.install)


class FixturePolicy(DenyHostPolicy):
    """Permissive fabricated test policy ONLY. It authenticates no real peer."""
    def __init__(self):
        self.worker_threads, self.dispatch_threads = [], []
        self.dispatch_hook = None
    def authorize_listener(self, binding):
        return True
    def authorize_peer(self, connection, peer, binding, deadline):
        self.worker_threads.append(threading.current_thread())
        return True
    def authorize_submit(self, submission, binding, deadline):
        self.worker_threads.append(threading.current_thread())
        return True
    def authorize_dispatch(self, submission, binding):
        self.dispatch_threads.append(threading.current_thread())
        if self.dispatch_hook:
            self.dispatch_hook()
        return True


class FixtureAdmission(DenyAdmission):
    def require_read(self, request):
        return True
    def require_mutation(self, request, observation):
        return True
    def observe_mode(self, request, observation, sample_token):
        return HostModeDeclaration(request, observation['client_id'], sample_token, 'live')


class IntegratedOwnerTests(unittest.TestCase):
    def setUp(self):
        self.owner_thread = threading.current_thread()
        self.read_threads, self.commands, self.loads = [], [], []
        self.clock = NS(clock_speed=1, now=lambda: NS(absolute_ticks=lambda: 200))
        self.instance = object()
        self.sim = NS(id=30, household_id=20, get_sim_instance=lambda: self.instance)
        self.core = module('sims4.core_services', on_tick=Mock(return_value=object()))
        values = dict(current_zone=lambda: NS(id=10, is_in_build_buy=False, is_zone_running=True),
            active_household=lambda: NS(id=20),
            client_manager=lambda: NS(get_first_client=lambda: NS(id=5)),
            get_persistence_service=lambda: NS(get_save_slot_proto_buff=lambda: NS(slot_id=4),
                                               get_save_slot_proto_guid=lambda: 99),
            game_clock_service=lambda: self.clock,
            sim_info_manager=lambda: NS(get=lambda sid: self.sim if sid == 30 else None),
            time_service=lambda: NS(sim_now=NS(absolute_ticks=lambda: 100)))
        def record(function):
            def read():
                self.read_threads.append(threading.current_thread())
                return function()
            return read
        self.services = module('services', **{key: record(value) for key, value in values.items()})
        self.policy = FixturePolicy()
        self.admission = FixtureAdmission()

    def make(self, **changes):
        def loader(name):
            self.loads.append(name)
            return {'sims4.core_services': self.core, 'services': self.services}[name]
        settings = dict(module_loader=loader, host_policy=self.policy, native_admission=self.admission)
        settings.update(changes)
        owner = runtime.OwnerRuntime(SESSION, EPOCH, **settings)
        self.addCleanup(owner.close)
        return owner

    def submit(self, number=1, operation='observe'):
        arguments = {'observe': {}, 'live.set_speed': {'speed': 2},
                     'cas.enter': {'sim_id': '30'}, 'game.request_exit_menu': {}}[operation]
        return dict(schema=2, kind='submit', ttl_seconds=5.0,
            request=dict(schema=1, kind='request', request_id='{:032x}'.format(number),
                session_id=SESSION, process_epoch=wire.epoch_wire(EPOCH), operation=operation,
                arguments=arguments),
            expected_context=None if operation == 'observe' else
                dict(zone_id='10', household_id='20', save_slot=4, save_guid='99'))

    def exchange(self, owner, value):
        with socket.create_connection(owner.address, timeout=2) as connection:
            connection.sendall(wire.encode_frame(value))
            connection.shutdown(socket.SHUT_WR)
            result = bytearray()
            while True:
                part = connection.recv(65540)
                if not part:
                    break
                result.extend(part)
        self.assertEqual(len(result) - 4, struct.unpack('>I', result[:4])[0])
        return wire.decode_payload(bytes(result[4:]))

    def receipt(self, owner, submitted):
        parser = live_wire.parse_submit if submitted['schema'] == 3 else wire.parse_submit
        parsed = parser(submitted, SESSION, EPOCH)
        epoch = (dict(pid=EPOCH.pid, creation_filetime=EPOCH.creation_filetime)
                 if submitted['schema'] == 3 else wire.epoch_wire(EPOCH))
        return self.exchange(owner, dict(schema=submitted['schema'], kind='receipt_read',
            request_id=parsed.request.request_id, session_id=SESSION,
            process_epoch=epoch, request_sha256=parsed.request_sha256))

    def tick_without_io(self):
        with patch('socket.socket', side_effect=AssertionError('socket on tick')), \
                patch('builtins.open', side_effect=AssertionError('file I/O on tick')), \
                patch('threading.Thread.start', side_effect=AssertionError('thread on tick')):
            return self.core.on_tick()

    def test_real_transport_defaults_denied_and_original_hook_is_restored(self):
        original = self.core.on_tick
        owner = self.make(host_policy=None)
        with self.assertRaises(QueueError) as error:
            owner.install()
        self.assertEqual('host_policy_required', error.exception.code)
        self.assertIs(original, self.core.on_tick)
        self.assertIsNone(owner.address)
        self.assertFalse(owner.status.enabled)
        self.assertTrue(owner.status.cleanup_complete)
        self.assertEqual([], self.read_threads)

    def test_real_socket_to_native_observe_is_one_command_per_owner_tick(self):
        owner = self.make()
        owner.install()
        first, second = self.submit(), self.submit(2)
        self.assertEqual('accepted', self.exchange(owner, first)['state'])
        self.assertEqual('accepted', self.exchange(owner, second)['state'])
        self.assertEqual([], self.read_threads)
        result = self.tick_without_io()
        self.assertIs(self.core.on_tick.__wrapped__.return_value, result)
        done = self.receipt(owner, first)
        self.assertEqual('adapter_returned', done['state'])
        self.assertFalse(done['independent_evidence'])
        self.assertFalse(done['adapter_result']['game_qualified'])
        self.assertEqual('accepted', self.receipt(owner, second)['state'])
        self.tick_without_io()
        self.assertEqual('adapter_returned', self.receipt(owner, second)['state'])
        self.assertTrue(self.read_threads)
        self.assertTrue(all(thread is self.owner_thread for thread in self.read_threads))
        self.assertTrue(all(thread is self.owner_thread for thread in self.policy.dispatch_threads))
        self.assertTrue(all(thread is not self.owner_thread for thread in self.policy.worker_threads))
        self.assertEqual(['sims4.core_services', 'services'], self.loads)

    def test_real_native_admission_is_independently_default_denied(self):
        owner = self.make(native_admission=None)
        owner.install()
        submitted = self.submit()
        self.exchange(owner, submitted)
        self.tick_without_io()
        self.assertEqual('unknown', self.receipt(owner, submitted)['state'])
        self.assertEqual([], self.read_threads)

    def test_real_native_speed_uses_only_supplied_cache_and_exact_binding(self):
        def speed(value, source, _connection):
            self.commands.append((value, source, _connection, threading.current_thread()))
            self.clock.clock_speed = ('paused', 'one', 'two', 'three').index(value)
        modules = {'server_commands.clock_commands': module('clock', set_speed=speed)}
        owner = self.make(command_modules=modules)
        modules.clear()
        owner.install()
        original_binding = owner.binding
        object.__setattr__(original_binding.process_epoch, 'pid', 999)
        submitted = self.submit(operation='live.set_speed')
        self.exchange(owner, submitted)
        self.tick_without_io()
        result = self.receipt(owner, submitted)
        self.assertEqual('adapter_returned', result['state'])
        self.assertEqual('verified', result['adapter_result']['outcome'])
        self.assertEqual([('two', 'foundry.cli', 5, self.owner_thread)], self.commands)
        self.assertEqual(['sims4.core_services', 'services'], self.loads)
        self.assertEqual(123, owner.binding.process_epoch.pid)

    def test_missing_cached_command_never_imports_or_dispatches(self):
        owner = self.make()
        owner.install()
        submitted = self.submit(operation='live.set_speed')
        self.exchange(owner, submitted)
        self.tick_without_io()
        self.assertEqual('unknown', self.receipt(owner, submitted)['state'])
        self.assertEqual([], self.commands)
        self.assertEqual(['sims4.core_services', 'services'], self.loads)

    def test_real_quit_menu_request_is_narrow_and_does_not_claim_game_exit(self):
        commands = module('sims4.commands', client_cheat=lambda *args: self.commands.append(args))
        owner = self.make(command_modules={'sims4.commands': commands})
        owner.install()
        submitted = self.submit(operation='game.request_exit_menu')
        self.exchange(owner, submitted)
        self.tick_without_io()
        result = self.receipt(owner, submitted)
        self.assertEqual([('quit', 5)], self.commands)
        self.assertEqual('adapter_returned', result['state'])
        self.assertFalse(result['adapter_result']['game_exit_verified'])
        self.assertFalse(result['adapter_result']['save_choice_made'])
        self.assertFalse(result['adapter_result']['game_qualified'])

    def test_real_cas_request_retains_explicit_preservation_scope(self):
        scopes = []
        class CasAdmission(FixtureAdmission):
            @contextmanager
            def cas_scope(self, request, sim, client):
                scopes.append('enter')
                try:
                    yield
                finally:
                    scopes.append('exit')
        def cas(target, _connection):
            self.assertEqual(['enter'], scopes)
            self.commands.append((target, _connection))
        owner = self.make(native_admission=CasAdmission(), command_modules={
            'server_commands.cas_commands': module('cas', modify_in_cas=cas),
            'server_commands.argument_helpers': module('args', OptionalTargetParam=lambda value: ('target', value))})
        owner.install()
        submitted = self.submit(operation='cas.enter')
        self.exchange(owner, submitted)
        self.tick_without_io()
        result = self.receipt(owner, submitted)
        self.assertEqual([(('target', '30'), 5)], self.commands)
        self.assertEqual(['enter', 'exit'], scopes)
        self.assertEqual('adapter_returned', result['state'])
        self.assertFalse(result['adapter_result']['cas_visibility_verified'])
        self.assertFalse(result['adapter_result']['game_qualified'])
        self.assertEqual(['sims4.core_services', 'services'], self.loads)

    def test_general_native_admission_does_not_supply_cas_scope(self):
        owner = self.make(command_modules={
            'server_commands.cas_commands': module('cas', modify_in_cas=lambda *args, **kw: self.commands.append(args)),
            'server_commands.argument_helpers': module('args', OptionalTargetParam=lambda value: value)})
        owner.install()
        submitted = self.submit(operation='cas.enter')
        self.exchange(owner, submitted)
        self.tick_without_io()
        self.assertEqual('unknown', self.receipt(owner, submitted)['state'])
        self.assertEqual([], self.commands)

    def test_real_queue_lock_contention_does_not_wait_or_call_native(self):
        owner = self.make()
        owner.install()
        submitted = self.submit()
        self.exchange(owner, submitted)
        held, release = threading.Event(), threading.Event()
        def hold():
            with owner._transport._queue._lock:
                held.set()
                release.wait(2)
        worker = threading.Thread(target=hold)
        worker.start()
        self.assertTrue(held.wait(2))
        try:
            self.tick_without_io()
            self.assertFalse(release.is_set())
            self.assertEqual('queue_busy', owner.status.last_tick)
            self.assertEqual([], self.read_threads)
        finally:
            release.set(); worker.join(2)
        self.tick_without_io()
        self.assertEqual('adapter_returned', self.receipt(owner, submitted)['state'])

    def test_hook_replacement_during_dispatch_admission_blocks_native(self):
        owner = self.make()
        owner.install()
        later = Mock()
        self.policy.dispatch_hook = lambda: setattr(self.core, 'on_tick', later)
        submitted = self.submit()
        self.exchange(owner, submitted)
        self.tick_without_io()
        self.assertEqual('unknown', self.receipt(owner, submitted)['state'])
        self.assertEqual([], self.read_threads)
        self.assertFalse(owner.status.enabled)
        self.assertTrue(owner.close())
        self.assertIs(later, self.core.on_tick)

    def test_optional_real_live_executor_has_typed_route_and_no_game_qualification(self):
        import test_companion_live_observation as live_fixtures
        harness = live_fixtures.Harness()
        owner = self.make(live_readers=runtime.LiveReaders(harness.read_context,
            harness.read_info, harness.read_instance, harness.admission))
        owner.install()
        request = LiveQueueRequest('3' * 32, SESSION, EPOCH, LiveQueueArguments('30'),
                                  LiveQueueExpectedContext('10', '20', '5', 4, '99', 1))
        submitted = live_wire.request_wire(request)
        self.assertEqual('accepted', self.exchange(owner, submitted)['state'])
        self.assertEqual([], harness.calls)
        self.tick_without_io()
        result = self.receipt(owner, submitted)
        self.assertEqual('adapter_returned', result['state'])
        self.assertEqual('observed', result['adapter_result']['read_status'])
        self.assertFalse(result['adapter_result']['game_qualified'])
        self.assertFalse(result['independent_evidence'])
        self.assertTrue(harness.calls)
        self.assertEqual([], self.read_threads)
        self.assertEqual(['sims4.core_services', 'services'], self.loads)

    def test_unconfigured_live_route_does_not_fall_back_to_legacy_native(self):
        owner = self.make()
        owner.install()
        request = LiveQueueRequest('4' * 32, SESSION, EPOCH, LiveQueueArguments('30'),
                                  LiveQueueExpectedContext('10', '20', '5', 4, '99', 1))
        submitted = live_wire.request_wire(request)
        self.exchange(owner, submitted)
        self.tick_without_io()
        self.assertEqual('unknown', self.receipt(owner, submitted)['state'])
        self.assertEqual([], self.read_threads)

    def test_live_readers_without_separate_admission_remain_denied(self):
        import test_companion_live_observation as live_fixtures
        harness = live_fixtures.Harness()
        owner = self.make(live_readers=runtime.LiveReaders(harness.read_context,
            harness.read_info, harness.read_instance))
        owner.install()
        request = LiveQueueRequest('5' * 32, SESSION, EPOCH, LiveQueueArguments('30'),
                                  LiveQueueExpectedContext('10', '20', '5', 4, '99', 1))
        submitted = live_wire.request_wire(request)
        self.exchange(owner, submitted)
        self.tick_without_io()
        self.assertEqual('unknown', self.receipt(owner, submitted)['state'])
        self.assertEqual([], harness.calls)

    def test_real_close_stops_listener_and_never_dispatches_queued_work(self):
        original = self.core.on_tick
        owner = self.make()
        owner.install()
        installed, address = self.core.on_tick, owner.address
        submitted = self.submit()
        self.exchange(owner, submitted)
        self.assertTrue(owner.close())
        self.assertEqual('closed', owner.status.state)
        self.assertIs(original, self.core.on_tick)
        self.assertIs(original.return_value, installed())
        self.assertEqual([], self.read_threads)
        with self.assertRaises(OSError):
            socket.create_connection(address, timeout=0.2)
        snapshot = owner._transport._queue.status(submitted['request']['request_id'])
        self.assertIsNotNone(snapshot)
        self.assertEqual('pending', snapshot.state)


if __name__ == '__main__':
    unittest.main()
