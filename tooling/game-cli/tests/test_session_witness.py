"""Synthetic registry and same-owner retained witness composition."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import digest, new_json
from sims4foundry_cli.process_exit import WAIT_TIMEOUT
from sims4foundry_cli.retained_process_witness import RetainedProcessRegistry
from sims4foundry_cli.session_registry import SessionRegistry
from sims4foundry_cli.session_witness import SessionWitness
from test_guarded_input import identity
from test_session_registry import FakeAnchor


class FakeApi:
    def __init__(self): self.opens = 0; self.state = WAIT_TIMEOUT
    def open(self, pid): self.opens += 1; return 'fixture-handle'
    def identity(self, handle):
        value = identity()
        return value['executable'], value['creation_filetime']
    def wait(self, handle, timeout): return self.state
    def close(self, handle): pass


class SessionWitnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.api = FakeApi()
        self.provider = RetainedProcessRegistry(self.api, lambda view: True)
        self.anchor = FakeAnchor()
        self.registry = SessionRegistry(Path(self.temp.name), identity(), b'k'*32, self.anchor)
        self.registry.open_session('1'*32, 'a'*64)
        self.process = {key:identity()[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
        self.witness = SessionWitness(self.registry,self.provider,self.process,'2'*32)
    def test_acquire_and_recover_use_one_original_handle(self):
        first = self.witness.acquire()
        self.assertTrue(first.is_running())
        recovered = self.witness.recover()
        self.assertEqual(self.process, recovered.snapshot())
        self.assertEqual(1, self.api.opens)
        self.assertEqual(['session','attempt','resolved'], [event['kind'] for event in self.registry.inspect()['events']])
    def test_replacement_witness_is_denied_after_acquisition_and_restart(self):
        self.witness.acquire()
        other_api = FakeApi()
        other_provider = RetainedProcessRegistry(other_api,lambda view:True)
        reopened = SessionRegistry(Path(self.temp.name),identity(),b'k'*32,self.anchor)
        replacement = SessionWitness(reopened,other_provider,self.process,'3'*32)
        with self.assertRaises(FoundryError): replacement.acquire()
        with self.assertRaises(FoundryError): replacement.recover()
        self.assertEqual(0,other_api.opens)
        self.assertEqual(self.process,self.witness.recover().snapshot())
        self.assertEqual(1,self.api.opens)
    def test_lost_acquisition_reply_recovers_same_provider_handle(self):
        original = self.provider.acquire
        def lost(*args):
            original(*args)
            raise OSError('lost caller reply')
        with mock.patch.object(self.provider, 'acquire', side_effect=lost):
            with self.assertRaises(OSError): self.witness.acquire()
        replacement = SessionWitness(self.registry,RetainedProcessRegistry(FakeApi(),lambda view:True),self.process,'3'*32)
        with self.assertRaises(FoundryError): replacement.acquire()
        with self.assertRaises(FoundryError): self.registry.begin('3'*32, 'exit', 'c'*64)
        self.assertEqual(self.process, self.witness.recover().snapshot())
        self.assertEqual(1, self.api.opens)
    def test_unknown_raw_handle_never_reopens_or_releases_gate(self):
        def lost(pid): self.api.opens += 1; raise OSError('open reply lost')
        self.api.open = lost
        with self.assertRaises(OSError): self.witness.acquire()
        with self.assertRaises(FoundryError): self.witness.recover()
        with self.assertRaises(FoundryError): self.witness.acquire()
        self.assertEqual(1, self.api.opens)
        self.assertEqual('attempt', self.registry.inspect()['events'][-1]['kind'])
    def test_lost_resolution_reply_recovers_without_second_open_or_resolve(self):
        original = self.provider.acquire
        def lose_resolution(*args):
            lease = original(*args)
            self.anchor.fail = 'after'
            return lease
        with mock.patch.object(self.provider, 'acquire', side_effect=lose_resolution):
            with self.assertRaises(FoundryError): self.witness.acquire()
        self.anchor.fail = None
        self.assertEqual(3, self.anchor.cas_calls)
        self.assertEqual(self.process, self.witness.recover().snapshot())
        self.assertEqual(3, self.anchor.cas_calls)
        self.assertEqual(1, self.api.opens)
        replacement = SessionWitness(self.registry,RetainedProcessRegistry(FakeApi(),lambda view:True),self.process,'3'*32)
        with self.assertRaises(FoundryError): replacement.acquire()

    def test_older_multi_acquisition_history_remains_readable(self):
        self.witness.acquire()
        # Construct a valid legacy chain directly: new acquire attempts are
        # refused, but historic authenticated records remain inspectable.
        def legacy_append(event):
            with self.registry._hold_lock():
                records,previous = self.registry._verify()
                self.registry._apply(records,event)
                body = {'schema':1,'scope':self.registry._scope,'index':len(records),
                        'previous':previous,'event':event}
                record = dict(body,mac=self.registry._mac(body))
                new_json(self.registry._directory/('record-%06d.json' % len(records)),record)
                self.assertTrue(self.anchor.compare_and_swap(self.registry._scope,previous,digest(record)))
        legacy_append({'kind':'attempt','request_id':'3'*32,'operation':'acquire','intent_sha256':'b'*64})
        legacy_append({'kind':'resolved','request_id':'3'*32,'intent_sha256':'b'*64,
                       'receipt_sha256':'c'*64,'outcome':'acknowledged'})
        reopened = SessionRegistry(Path(self.temp.name),identity(),b'k'*32,self.anchor)
        self.assertEqual(5,len(reopened.inspect()['events']))
        self.assertEqual(self.process,self.witness.recover().snapshot())
        other_api = FakeApi()
        other = SessionWitness(reopened,RetainedProcessRegistry(other_api,lambda view:True),self.process,'4'*32)
        with self.assertRaises(FoundryError): other.acquire()
        self.assertEqual(0,other_api.opens)
    def test_reentrant_provider_access_is_refused_during_resolution(self):
        original = self.anchor.compare_and_swap
        def attempt_reentry(*args):
            if self.api.opens:
                with self.assertRaises(FoundryError):
                    self.provider.recover('2'*32, self.process)
            return original(*args)
        with mock.patch.object(self.anchor, 'compare_and_swap', side_effect=attempt_reentry):
            self.assertTrue(self.witness.acquire().is_running())
        self.assertEqual(1, self.api.opens)
    def test_foreign_lifetime_cannot_be_bound(self):
        foreign = dict(self.process, creation_filetime=self.process['creation_filetime']+1)
        with self.assertRaises(FoundryError): SessionWitness(self.registry,self.provider,foreign,'2'*32)
        self.assertEqual(0, self.api.opens)


if __name__ == '__main__': unittest.main()
