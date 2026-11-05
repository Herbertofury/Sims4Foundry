"""Shared registry/journal integration with synthetic providers only."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import input_journal, live_input, registered_input
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import Point, ClientBounds, encode_click
from sims4foundry_cli.session_registry import SessionRegistry
from test_guarded_input import FakeBackend, identity
from test_session_registry import FakeAnchor
from test_live_input import FakeApi, FakeLease, FakeRecorder


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
        self.backend = FakeBackend()
        self.packets = encode_click(Point(20,30), ClientBounds(500,500))
    def execute(self):
        return registered_input.execute_once(self.registry,self.journal,'2'*32,self.packets,self.backend,
                                             expected_observation={'mode':'cas'})
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
    def test_wrong_journal_binding_never_dispatches(self):
        # The same session metadata in a different directory is still foreign.
        other = input_journal.InputJournal.create(Path(self.temp.name)/'other', '1'*32, identity())
        with self.assertRaises(FoundryError):
            registered_input.execute_once(self.registry,other,'2'*32,self.packets,self.backend,
                                          expected_observation={'mode':'cas'})
        self.assertEqual([], self.backend.posts)
    def test_registry_journal_and_native_adapter_join_in_one_owned_context(self):
        api = FakeApi()
        with (mock.patch.object(live_input, '_NativeApi', return_value=api),
              mock.patch.object(live_input.process_lease, 'process_lease', return_value=FakeLease()),
              mock.patch.object(live_input, 'WgcRecorder', FakeRecorder),
              mock.patch.object(live_input, 'os', SimpleNamespace(name='nt'))):
            with live_input.LiveInputBackend(identity(),self.packets,'ffmpeg.exe','a'*64) as backend:
                result = registered_input.execute_once(self.registry,self.journal,'2'*32,self.packets,backend,
                                                       expected_observation={'mode':'cas'})
                self.assertEqual('awaiting_readback', result['state'])
                self.assertFalse(result['result']['semantic_success'])
        self.assertEqual(2, len(api.posts))
        self.assertEqual('attempt', self.registry.inspect()['events'][-1]['kind'])


if __name__ == '__main__': unittest.main()
