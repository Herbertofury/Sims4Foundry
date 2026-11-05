"""Synthetic authenticated registry and durable-anchor fault cases."""
from pathlib import Path
import json
import tempfile
import unittest

from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.session_registry import SessionRegistry
from test_guarded_input import identity


class FakeAnchor:
    def __init__(self):
        self.heads = {}
        self.cas_calls = 0
        self.fail = None
    def read(self, scope): return self.heads.get(scope)
    def compare_and_swap(self, scope, previous, next_head):
        self.cas_calls += 1
        if self.fail == 'before': raise OSError('lost before update')
        if self.heads.get(scope) != previous: return False
        self.heads[scope] = next_head
        if self.fail == 'after': raise OSError('lost after update')
        return True


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.anchor = FakeAnchor()
        self.registry = SessionRegistry(self.root, identity(), b'k'*32, self.anchor)
    def start(self): return self.registry.open_session('1'*32, 'a'*64)
    def test_input_and_exit_share_one_pending_gate(self):
        self.start()
        self.registry.begin('2'*32, 'input', 'b'*64)
        with self.assertRaises(FoundryError): self.registry.begin('3'*32, 'exit', 'c'*64)
        with self.assertRaises(FoundryError): self.registry.resolve('3'*32, 'b'*64, 'd'*64, 'acknowledged')
        self.registry.resolve('2'*32, 'b'*64, 'd'*64, 'acknowledged')
        self.registry.begin('3'*32, 'exit', 'c'*64)
        self.assertEqual(['session','attempt','resolved','attempt'], [x['kind'] for x in self.registry.inspect()['events']])
        with self.assertRaises(FoundryError): self.registry.begin('2'*32, 'input', 'b'*64)
    def test_other_controller_with_same_key_and_anchor_observes_pending(self):
        self.start()
        self.registry.begin('2'*32, 'input', 'b'*64)
        other = SessionRegistry(self.root, identity(), b'k'*32, self.anchor)
        self.assertEqual('2'*32, other.inspect()['events'][-1]['request_id'])
        with self.assertRaises(FoundryError): other.begin('3'*32, 'exit', 'c'*64)
    def test_changed_record_or_wrong_key_is_rejected(self):
        self.start()
        wrong = SessionRegistry(self.root, identity(), b'x'*32, self.anchor)
        with self.assertRaises(FoundryError): wrong.inspect()
        path = next(self.root.glob('*/record-000000.json'))
        data = json.loads(path.read_text())
        data['event']['session_id'] = '2'*32
        path.write_text(json.dumps(data))
        with self.assertRaises(FoundryError): self.registry.inspect()
    def test_truncated_history_is_rejected_by_anchor(self):
        self.start()
        self.registry.begin('2'*32, 'input', 'b'*64)
        next(self.root.glob('*/record-000001.json')).unlink()
        with self.assertRaises(FoundryError): self.registry.inspect()
    def test_lost_ack_after_anchor_update_never_retries_and_can_inspect(self):
        self.start()
        self.anchor.fail = 'after'
        with self.assertRaises(FoundryError): self.registry.begin('2'*32, 'input', 'b'*64)
        self.assertEqual(2, self.anchor.cas_calls)
        self.anchor.fail = None
        self.assertEqual('2'*32, self.registry.inspect()['events'][-1]['request_id'])
        with self.assertRaises(FoundryError): self.registry.begin('2'*32, 'input', 'b'*64)
        self.assertEqual(2, self.anchor.cas_calls)
    def test_lost_ack_before_anchor_update_latches_mismatch(self):
        self.start()
        self.anchor.fail = 'before'
        with self.assertRaises(FoundryError): self.registry.begin('2'*32, 'input', 'b'*64)
        self.anchor.fail = None
        with self.assertRaises(FoundryError): self.registry.inspect()
        with self.assertRaises(FoundryError): self.registry.begin('3'*32, 'input', 'c'*64)
        self.assertEqual(2, self.anchor.cas_calls)
    def test_different_process_lifetime_has_separate_scope(self):
        first = self.start()
        other = identity()
        other['creation_filetime'] += 1
        second = SessionRegistry(self.root, other, b'k'*32, self.anchor).open_session('4'*32, 'a'*64)
        self.assertNotEqual(first['scope'], second['scope'])
    def test_first_event_and_duplicate_session_are_rejected(self):
        with self.assertRaises(FoundryError): self.registry.begin('2'*32, 'input', 'b'*64)
        self.start()
        with self.assertRaises(FoundryError): self.registry.open_session('5'*32, 'a'*64)


if __name__ == '__main__': unittest.main()
