"""Exercise live integration with synthetic Win32 and recorder providers only."""
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from sims4foundry_cli import input_journal, live_input
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.input_packets import Point, ClientBounds, Message, encode_click
from sims4foundry_cli.wgc_recorder import Frame
from test_guarded_input import identity


class FakeLease:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def _check_context(self): pass


class FakeApi:
    def __init__(self):
        self.posts = []
        self.packets = []
        self.on_post = None
        self.owned = True
        self.focus = 12
    def pin_image(self, expected): return 'image'
    def close(self, handle): pass
    def open_target(self, pid): return 'target'
    def current_security_context(self): return 'security'
    def foreground(self): return self.focus
    def layout(self, thread): return 123
    def ownership(self, process, expected, security):
        return {key: expected[key] for key in live_input.OWNED_KEYS} if self.owned else {}
    def geometry(self, expected):
        return {key: expected[key] for key in ('client_width', 'client_height', 'client_origin', 'dpi')}
    def integrity(self, process): return 8192
    def _current_process(self): return 'controller'
    def input_observable(self, *args): pass
    def key_down(self, key): return False
    def window_owner(self, expected): pass
    def post(self, hwnd, packet):
        self.posts.append((hwnd, packet.message))
        self.packets.append(packet)
        return self.on_post(packet) if self.on_post else True


class FakeRecorder:
    def __init__(self, *args, **kwargs):
        self.epoch = object()
        self.sequence = 0
        self.closed = False
        self.fail = False
    def start(self): pass
    def sample(self):
        if self.fail: raise FoundryError('recording_unavailable', 'fixture')
        self.sequence += 1
        return Frame(self.epoch, self.sequence, time.monotonic(), 'a'*64, 1, 1, b'fixture')
    def close(self):
        self.closed = True
        return True


class LiveInputTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.packets = encode_click(Point(20, 30), ClientBounds(500, 500))
        for patcher in (mock.patch.object(live_input, '_NativeApi', return_value=self.api),
                        mock.patch.object(live_input.process_lease, 'process_lease', return_value=FakeLease()),
                        mock.patch.object(live_input, 'WgcRecorder', FakeRecorder),
                        mock.patch.object(live_input, 'os', SimpleNamespace(name='nt'))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = input_journal.InputJournal.create(Path(self.temp.name)/'history', '2'*32, identity())

    def backend(self):
        return live_input.LiveInputBackend(identity(), self.packets, 'ffmpeg.exe', 'a'*64)

    def run_once(self, backend):
        return self.journal.execute_once('1'*32, self.packets, backend, expected_observation={'mode':'cas'})

    def test_synthetic_click_is_journaled_once_and_needs_readback(self):
        with self.backend() as backend:
            self.assertEqual([], self.api.posts)
            result = self.run_once(backend)
            self.assertEqual('awaiting_readback', result['state'])
            self.assertFalse(result['result']['semantic_success'])
            with self.assertRaises(FoundryError):
                self.journal.execute_once('3'*32, self.packets, backend, expected_observation={'mode':'cas'})
        self.assertEqual([(100, Message.LEFT_DOWN), (100, Message.LEFT_UP)], self.api.posts)
        self.assertTrue(backend._recorder.closed)

    def test_lost_down_reply_releases_once_and_blocks_replay(self):
        def post(packet):
            if packet.message is Message.LEFT_DOWN: raise OSError('lost reply')
            return True
        self.api.on_post = post
        with self.backend() as backend:
            result = self.run_once(backend)
            self.assertEqual('unresolved', result['state'])
            self.assertTrue(backend.unresolved)
        self.assertEqual([(100, Message.LEFT_DOWN), (100, Message.LEFT_UP)], self.api.posts)
        self.assertEqual('unresolved', input_journal.InputJournal(self.journal.root).status('1'*32)['state'])

    def test_recorder_loss_after_down_still_releases_original(self):
        with self.backend() as backend:
            def post(packet):
                if packet.message is Message.LEFT_DOWN: backend._recorder.fail = True
                return True
            self.api.on_post = post
            result = self.run_once(backend)
            self.assertEqual('unresolved', result['state'])
        self.assertEqual([(100, Message.LEFT_DOWN), (100, Message.LEFT_UP)], self.api.posts)

    def test_wrong_window_or_packet_does_not_post(self):
        with self.backend() as backend:
            with self.assertRaises(FoundryError): backend.post(101, self.packets[0])
            with self.assertRaises(FoundryError): backend.post(100, self.packets[-1])
        self.assertEqual([], self.api.posts)

    def test_native_refusal_is_never_retried(self):
        self.api.on_post = lambda packet: False
        with self.backend() as backend:
            self.assertEqual('unresolved', self.run_once(backend)['state'])
            self.assertTrue(backend.unresolved)
        self.assertEqual([(100, Message.LEFT_DOWN)], self.api.posts)

    def test_changed_owner_after_down_never_receives_release(self):
        with self.assertRaises(FoundryError) as error:
            with self.backend() as backend:
                def post(packet):
                    if packet.message is Message.LEFT_DOWN: self.api.owned = False
                    return True
                self.api.on_post = post
                self.assertEqual('unresolved', self.run_once(backend)['state'])
        self.assertEqual('release_unresolved', error.exception.code)
        self.assertEqual([(100, Message.LEFT_DOWN)], self.api.posts)

    def test_foreground_change_before_dispatch_is_unresolved(self):
        with self.backend() as backend:
            self.api.focus = 99
            self.assertEqual('unresolved', self.run_once(backend)['state'])
        self.assertEqual([], self.api.posts)

    def test_delayed_final_foreground_check_prevents_post(self):
        with self.backend() as backend:
            clock = [100.0]
            def foreground():
                clock[0] += .3
                return 12
            with (mock.patch.object(live_input, 'time', SimpleNamespace(monotonic=lambda:clock[0])),
                  mock.patch.object(backend, 'foreground', side_effect=foreground),
                  mock.patch.object(backend, 'verify_recording', return_value=True),
                  mock.patch.object(backend, 'verify_input_context', return_value=True)):
                with self.assertRaises(FoundryError) as error:
                    backend.post(100, self.packets[0])
            self.assertEqual('stale_observation', error.exception.code)
        self.assertEqual([], self.api.posts)

    def test_ownership_change_before_dispatch_does_not_post(self):
        with self.backend() as backend:
            self.api.owned = False
            self.assertEqual('unresolved', self.run_once(backend)['state'])
        self.assertEqual([], self.api.posts)

    def test_constructor_and_entry_never_post(self):
        backend = self.backend()
        self.assertEqual([], self.api.posts)
        with backend:
            self.assertEqual([], self.api.posts)
        with self.assertRaises(FoundryError): backend.__enter__()


if __name__ == '__main__': unittest.main()
