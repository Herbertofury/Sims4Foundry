"""Shared registry/journal integration with synthetic providers only."""
from pathlib import Path
from contextlib import contextmanager, nullcontext
import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import input_journal, live_input, registered_input
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import Point, ClientBounds, encode_click
from sims4foundry_cli.session_registry import SessionRegistry
from sims4foundry_cli.session_witness import SessionWitness
from sims4foundry_cli.retained_process_witness import RetainedProcessRegistry
from sims4foundry_cli.process_exit import WAIT_OBJECT_0
from test_guarded_input import FakeBackend, identity
from test_session_registry import FakeAnchor
from test_live_input import FakeApi, FakeLease, FakeRecorder
from test_session_witness import FakeApi as FakeWitnessApi


class RegisteredInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root/'registry').mkdir()
        self.journal = input_journal.InputJournal.create(root/'journal', '1'*32, identity())
        self.anchor = FakeAnchor()
        self.registry = SessionRegistry(root/'registry', identity(), b'k'*32, self.anchor)
        self.registry.open_session('1'*32, registered_input.journal_binding(self.journal))
        self.witness_api = FakeWitnessApi()
        self.provider = RetainedProcessRegistry(self.witness_api, lambda view: True)
        process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        self.witness = SessionWitness(self.registry, self.provider, process, '4'*32)
        self.lease = self.witness.acquire()
        self.backend = FakeBackend()
        self.packets = encode_click(Point(20,30), ClientBounds(500,500))
        if os.name != 'nt':
            # Non-Windows exercises orchestration only, not the native pin.
            patcher = mock.patch.object(registered_input, '_retain_record', side_effect=lambda *args:nullcontext())
            patcher.start()
            self.addCleanup(patcher.stop)
    def execute(self):
        return registered_input.execute_once(self.registry,self.journal,'2'*32,self.packets,self.backend,
                                             expected_observation={'mode':'cas'},witness=self.witness)
    def test_shared_gate_holds_until_original_host_acknowledgement(self):
        self.assertEqual('awaiting_readback', self.execute()['state'])
        with self.assertRaises(FoundryError): self.registry.begin('3'*32, 'exit', 'c'*64)
        with self.assertRaises(FoundryError): registered_input.reconcile_acknowledged(self.registry,self.journal,'2'*32)
        self.journal.acknowledge('2'*32, lambda view,target,expected:
                                 dict(view,evidence_sha256='a'*64,observation=expected))
        registered_input.reconcile_acknowledged(self.registry,self.journal,'2'*32)
        self.registry.begin('3'*32, 'exit', 'c'*64)
        self.assertEqual(2, len(self.backend.posts))
    def test_registry_anchor_failure_never_dispatches(self):
        self.anchor.fail = 'before'
        with self.assertRaises(FoundryError): self.execute()
        self.assertEqual([], self.backend.posts)
        with self.assertRaises(FoundryError): self.execute()
        self.assertEqual([], self.backend.posts)
    def test_journal_failure_keeps_shared_gate_pending(self):
        with mock.patch.object(self.journal, 'execute_once', side_effect=OSError('journal unavailable')):
            with self.assertRaises(OSError): self.execute()
        with self.assertRaises(FoundryError): self.registry.begin('3'*32, 'exit', 'c'*64)
        self.assertEqual([], self.backend.posts)
    def test_reentrant_close_during_registry_callback_is_refused(self):
        original = self.anchor.compare_and_swap
        def try_close(*args):
            with self.assertRaises(FoundryError): self.lease.close()
            return original(*args)
        with mock.patch.object(self.anchor, 'compare_and_swap', side_effect=try_close):
            self.assertEqual('awaiting_readback', self.execute()['state'])
        self.assertEqual(2, len(self.backend.posts))
    def test_process_exit_before_packet_prevents_dispatch(self):
        def terminate_before_post(packet):
            self.witness_api.state = WAIT_OBJECT_0
            return True
        self.backend.verify_input_context = terminate_before_post
        result = self.execute()
        self.assertEqual('unresolved', result['state'])
        self.assertEqual([], self.backend.posts)
    def test_failed_witness_preflight_cannot_post_unpaired_release(self):
        calls = []
        def check():
            calls.append(1)
            if len(calls) == 1: raise FoundryError('process_lifetime_unresolved', 'fixture')
            return True
        wrapped = registered_input._WitnessBoundBackend(self.backend, check, 100, self.packets)
        with self.assertRaises(FoundryError): wrapped.post(100, self.packets[0])
        with self.assertRaises(FoundryError): wrapped.post(100, self.packets[-1])
        self.assertEqual([], self.backend.posts)
    def test_lost_down_reply_still_allows_one_original_release(self):
        def lose_down(packet):
            if not packet.release_intent: raise OSError('lost downstream reply')
            return True
        self.backend.failure = lose_down
        wrapped = registered_input._WitnessBoundBackend(self.backend, lambda:True, 100, self.packets)
        with self.assertRaises(OSError): wrapped.post(100, self.packets[0])
        with self.assertRaises(FoundryError): wrapped.post(101, self.packets[-1])
        self.assertTrue(wrapped.post(100, self.packets[-1]))
        with self.assertRaises(FoundryError): wrapped.post(100, self.packets[-1])
        self.assertEqual([self.packets[0],self.packets[-1]], [packet for _,packet in self.backend.posts])
    def test_closed_witness_blocks_dispatch_even_after_anchored_acquisition(self):
        self.lease.close()
        with self.assertRaises(FoundryError): self.execute()
        self.assertEqual([], self.backend.posts)
    def test_wrong_journal_binding_never_dispatches(self):
        # The same session metadata in a different directory is still foreign.
        other = input_journal.InputJournal.create(Path(self.temp.name)/'other', '1'*32, identity())
        with self.assertRaises(FoundryError):
            registered_input.execute_once(self.registry,other,'2'*32,self.packets,self.backend,
                                          expected_observation={'mode':'cas'},witness=self.witness)
        self.assertEqual([], self.backend.posts)
    def test_registry_journal_and_native_adapter_join_in_one_owned_context(self):
        api = FakeApi()
        with (mock.patch.object(live_input, '_NativeApi', return_value=api),
              mock.patch.object(live_input.process_lease, 'process_lease', return_value=FakeLease()),
              mock.patch.object(live_input, 'WgcRecorder', FakeRecorder),
              mock.patch.object(live_input, 'os', SimpleNamespace(name='nt'))):
            with live_input.LiveInputBackend(identity(),self.packets,'ffmpeg.exe','a'*64) as backend:
                result = registered_input.execute_once(self.registry,self.journal,'2'*32,self.packets,backend,
                                                       expected_observation={'mode':'cas'},witness=self.witness)
                self.assertEqual('awaiting_readback', result['state'])
                self.assertFalse(result['result']['semantic_success'])
        self.assertEqual(2, len(api.posts))
        self.assertEqual('attempt', self.registry.inspect()['events'][-1]['kind'])

    @unittest.skipUnless(os.name == 'nt', 'Windows retained-file behavior')
    def test_changed_ack_during_pin_is_refused(self):
        self.execute()
        self.journal.acknowledge('2'*32, lambda view,target,expected:
                                 dict(view,evidence_sha256='a'*64,observation=expected))
        original = registered_input._retain_record
        def replace_before_pin(path, checksum):
            if path.name == 'ack.json':
                data = json.loads(path.read_bytes())
                data['evidence_sha256'] = 'b'*64
                path.write_text(json.dumps(data))
            return original(path, checksum)
        with mock.patch.object(registered_input, '_retain_record', side_effect=replace_before_pin):
            with self.assertRaises(FoundryError):
                registered_input.reconcile_acknowledged(self.registry,self.journal,'2'*32)
        self.assertEqual('attempt', self.registry.inspect()['events'][-1]['kind'])

    @unittest.skipUnless(os.name == 'nt', 'Windows retained-file behavior')
    def test_ack_path_cannot_be_replaced_during_resolution(self):
        self.execute()
        self.journal.acknowledge('2'*32, lambda view,target,expected:
                                 dict(view,evidence_sha256='a'*64,observation=expected))
        path = self.journal.root/'runs'/('2'*32)/'ack.json'
        replacement = path.with_name('replacement.json')
        replacement.write_bytes(path.read_bytes())
        resolve = self.registry.resolve
        def while_pinned(*args):
            with self.assertRaises(OSError): os.replace(replacement, path)
            return resolve(*args)
        with mock.patch.object(self.registry, 'resolve', side_effect=while_pinned):
            registered_input.reconcile_acknowledged(self.registry,self.journal,'2'*32)
        self.assertEqual('resolved', self.registry.inspect()['events'][-1]['kind'])

    def test_lost_cleanup_ack_reconciles_existing_resolution_without_replay(self):
        self.execute()
        self.journal.acknowledge('2'*32, lambda view,target,expected:
                                 dict(view,evidence_sha256='a'*64,observation=expected))
        original = registered_input._retain_record
        @contextmanager
        def fail_cleanup(path, checksum):
            with original(path, checksum):
                yield
            if path.name == 'ack.json': raise OSError('lost cleanup acknowledgement')
        with mock.patch.object(registered_input, '_retain_record', side_effect=fail_cleanup):
            with self.assertRaises(OSError):
                registered_input.reconcile_acknowledged(self.registry,self.journal,'2'*32)
        count = len(self.registry.inspect()['events'])
        result = registered_input.reconcile_acknowledged(self.registry,self.journal,'2'*32)
        self.assertTrue(result['reconciled_existing'])
        self.assertEqual(count, len(self.registry.inspect()['events']))
        self.assertEqual(2, len(self.backend.posts))

    def test_evidence_handle_bound_is_checked_before_pinning(self):
        self.execute()
        self.journal.acknowledge('2'*32, lambda view,target,expected:
                                 dict(view,evidence_sha256='a'*64,observation=expected))
        with mock.patch.object(registered_input, 'MAX_RECONCILE_HANDLES', 0):
            with self.assertRaises(FoundryError) as error:
                registered_input.reconcile_acknowledged(self.registry,self.journal,'2'*32)
        self.assertEqual('registry_bound', error.exception.code)
        self.assertEqual('attempt', self.registry.inspect()['events'][-1]['kind'])


if __name__ == '__main__': unittest.main()
