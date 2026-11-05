"""Fixture-only native services adapter coverage; no game is imported or run."""
import ast
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace as NS

SOURCE = Path(__file__).resolve().parents[1] / 'companion-src'
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE.parent / 'build-adapter-src'))
from foundry_build_adapter.build_native_readers import BuildNativeReaders
from foundry_companion.build_observation import BuildContext, BuildObserver
from foundry_companion.native import DenyAdmission, NativeRefusal
from foundry_companion.queue import ObserveArguments, ProcessEpoch, Request


class Manager:
    def __init__(self):
        self.calls = []
        self.values = {}
        self.callback = None
    def get(self, object_id):
        self.calls.append(object_id)
        if self.callback:
            self.callback()
        return self.values.get(object_id)
    def __iter__(self):
        raise AssertionError('manager must not be scanned')


class Services:
    def __init__(self):
        self.zone = NS(id=10, is_zone_running=True, is_in_build_buy=True)
        self.household = NS(id=20)
        self.client = NS(id=5)
        self.persistence = NS(get_save_slot_proto_buff=lambda: NS(slot_id=4),
                              get_save_slot_proto_guid=lambda: 99)
        self.manager = Manager()
        self.zone_callback = None
    def current_zone(self):
        if self.zone_callback: self.zone_callback()
        return self.zone
    def active_household(self): return self.household
    def client_manager(self): return NS(get_first_client=lambda: self.client)
    def get_persistence_service(self): return self.persistence
    def object_manager(self): return self.manager


class Admission(DenyAdmission):
    def require_build_read(self, request, expected, selected): return True


class NativeReadersTests(unittest.TestCase):
    def setUp(self):
        self.services = Services()
        self.generation = 1
        self.readers = BuildNativeReaders(self.services, lambda: self.generation,
                                          '2'*32, ProcessEpoch(123, 456))
        self.expected = BuildContext('2'*32, ProcessEpoch(123, 456),
                                     '10', '20', '5', 4, '99', 1)

    def observer(self, admission=None):
        return BuildObserver(self.readers.context, self.readers.object,
                admission=admission, session_id='2'*32,
                process_epoch=ProcessEpoch(123, 456))

    def request(self):
        return Request('1'*32, '2'*32, 'observe', ObserveArguments(),
                       process_epoch=ProcessEpoch(123, 456))

    def test_selected_object_observed_through_services_and_admission(self):
        obj = NS(id=30, definition=NS(id=40), position=NS(x=1,y=2,z=3),
                 orientation=NS(x=0,y=0,z=0,w=1),
                 routing_surface=NS(primary_id=10,secondary_id=0,type=0))
        self.services.manager.values[30] = obj
        result = self.observer(Admission()).observe(self.request(), self.expected, ['30'])
        self.assertTrue(result['mode']['value'])
        self.assertEqual('observed', result['objects'][0]['status'])
        self.assertEqual('40', result['objects'][0]['fields']['definition_id']['value'])
        self.assertEqual([30, 30], self.services.manager.calls)
        self.assertFalse(result['live_qualified'])

    def test_default_denial_does_not_read_services(self):
        self.services.zone_callback = lambda: self.fail('unexpected zone read')
        with self.assertRaises(NativeRefusal):
            self.observer().observe(self.request(), self.expected, ['30'])
        self.assertEqual([], self.services.manager.calls)

    def test_context_reads_exact_native_fields_and_allows_zero_save(self):
        self.services.persistence = NS(get_save_slot_proto_buff=lambda: NS(slot_id=0),
                                      get_save_slot_proto_guid=lambda: 0)
        value = self.readers.context()
        self.assertEqual(0, value['context'].save_slot)
        self.assertEqual('0', value['context'].save_guid)
        self.assertEqual('10', value['context'].zone_id)
        self.assertEqual('20', value['context'].household_id)
        self.assertEqual('5', value['context'].client_id)

    def test_missing_or_nonboolean_mode_is_unavailable(self):
        for value in (None, 1, 'false'):
            self.services.zone.is_in_build_buy = value
            self.assertIsNone(self.readers.context()['in_build_buy'])
        del self.services.zone.is_in_build_buy
        self.assertIsNone(self.readers.context()['in_build_buy'])

    def test_invalid_context_never_invents_identifiers(self):
        for attr, value in (('id', 0), ('id', True), ('id', '10'),
                            ('is_zone_running', 1)):
            with self.subTest(attr=attr, value=value):
                self.services.zone = NS(id=10, is_zone_running=True, is_in_build_buy=True)
                setattr(self.services.zone, attr, value)
                with self.assertRaises(NativeRefusal): self.readers.context()
        self.services.zone = None
        with self.assertRaises(NativeRefusal): self.readers.context()

    def test_zone_or_generation_change_discards_context(self):
        counts = [0]
        def change():
            counts[0] += 1
            if counts[0] == 2:
                self.services.zone = NS(id=10, is_zone_running=True, is_in_build_buy=True)
        self.services.zone_callback = change
        with self.assertRaises(NativeRefusal): self.readers.context()
        self.services.zone_callback = None
        values = iter([1, True])
        self.readers._generation = lambda: next(values)
        with self.assertRaises(NativeRefusal): self.readers.context()

    def test_exact_object_selection_no_iteration_or_coercion(self):
        for value in (True, 0, -1, '30', 2**64):
            with self.assertRaises(NativeRefusal): self.readers.object(value)
        self.assertIsNone(self.readers.object(30))
        self.assertEqual([30], self.services.manager.calls)

    def test_zone_manager_and_generation_changes_discard_resolution(self):
        for kind in ('zone', 'manager', 'generation'):
            self.setUp()
            def mutate():
                if kind == 'zone':
                    self.services.zone = NS(id=10, is_zone_running=True, is_in_build_buy=True)
                elif kind == 'manager': self.services.manager = Manager()
                else: self.generation = 2
            self.services.manager.callback = mutate
            with self.subTest(kind=kind):
                with self.assertRaises(NativeRefusal): self.readers.object(30)

    def test_reentry_and_foreign_thread_do_not_reach_services(self):
        errors = []
        def call():
            for method, args in ((self.readers.context, ()), (self.readers.object, (30,))):
                try: method(*args)
                except Exception as error: errors.append(error)
        worker = threading.Thread(target=call)
        worker.start(); worker.join()
        self.assertEqual([NativeRefusal, NativeRefusal], [type(e) for e in errors])
        self.assertEqual([], self.services.manager.calls)
        self.services.zone_callback = lambda: self.readers.object(30)
        with self.assertRaises(NativeRefusal): self.readers.context()
        self.assertEqual([], self.services.manager.calls)

    def test_errors_are_static_and_do_not_expose_exception_text(self):
        def fail(): raise RuntimeError('PRIVATE CONTENT')
        self.services.zone_callback = fail
        with self.assertRaises(NativeRefusal) as error: self.readers.context()
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_module_is_python37_and_has_no_game_or_side_effect_imports(self):
        source = (SOURCE.parent / 'build-adapter-src' / 'foundry_build_adapter' / 'build_native_readers.py').read_text()
        tree = ast.parse(source, feature_version=7) if sys.version_info >= (3, 8) else ast.parse(source)
        roots = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): roots.extend(item.name.split('.')[0] for item in node.names)
            if isinstance(node, ast.ImportFrom) and not node.level: roots.append(node.module.split('.')[0])
        self.assertEqual({'threading', 'foundry_companion'}, set(roots))
