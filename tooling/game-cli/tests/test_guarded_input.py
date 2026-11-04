from copy import deepcopy
from dataclasses import replace
import unittest
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.guarded_input import run_packets
from sims4foundry_cli.input_packets import (Point, ClientBounds, Message, encode_click, encode_drag,
                                          NavigationKey, ScanCode, encode_key_press, encode_wheel)


def identity():
    return {"schema": 1, "kind": "foundry_windows_identity", "pid": 42, "creation_filetime": 123,
            "executable": "C:\\fixture\\TS4_x64.exe", "executable_sha256": "a" * 64,
            "hwnd": 100, "thread_id": 200, "window_class": "Canvas-Fixture", "client_width": 500,
            "client_height": 500, "client_origin": [0, 0], "dpi": 96, "integrity": 8192}


class FakeBackend:
    def __init__(self):
        self.state = identity(); self.time = 0; self.posts = []; self.failure = None
        self.levels = (8192, 8192); self.focus = 12; self.recording = True; self.owner = True
    def observe(self): return deepcopy(self.state)
    def observe_owned(self): return deepcopy(self.state) if self.owner else {}
    def integrities(self): return self.levels
    def foreground(self): return self.focus
    def monotonic(self): return self.time
    def verify_recording(self):
        if not self.recording: raise RuntimeError("recording stopped")
        return True
    def verify_input_context(self, packet): return True
    def sleep(self, seconds): self.time += seconds
    def post(self, hwnd, packet):
        self.posts.append((hwnd, packet))
        if self.failure: return self.failure(packet)
        return True


class GuardedInputTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.click = encode_click(Point(20, 30), ClientBounds(500, 500))
        self.journal = []

    def run_sequence(self, packets=None, journal=None):
        return run_packets(identity(), packets or self.click, self.backend, journal or self.journal.append)

    def test_queue_only_not_semantic_success(self):
        result = self.run_sequence()
        self.assertTrue(result["queue_accepted"])
        self.assertFalse(result["semantic_success"])
        self.assertFalse(result["game_qualified"])
        self.assertEqual([Message.LEFT_DOWN, Message.LEFT_UP], [p.message for _,p in self.backend.posts])

    def test_integrity_refusal_sends_nothing(self):
        self.backend.levels = (8192, 12288)
        with self.assertRaises(FoundryError): self.run_sequence()
        self.assertEqual([], self.backend.posts)

    def test_false_or_missing_recording_verdict_sends_nothing(self):
        for verdict in (False, None):
            self.backend.verify_recording = lambda: verdict
            with self.assertRaises(FoundryError): self.run_sequence()
            self.assertEqual([], self.backend.posts)

    def test_input_context_requires_explicit_true(self):
        for verdict in (False, None):
            self.backend.verify_input_context = lambda packet: verdict
            with self.assertRaises(FoundryError): self.run_sequence()
            self.assertEqual([], self.backend.posts)

    def test_unknown_foreground_refuses(self):
        self.backend.focus = None
        with self.assertRaises(FoundryError): self.run_sequence()
        self.assertEqual([], self.backend.posts)

    def test_intent_persistence_failure_sends_nothing(self):
        with self.assertRaises(OSError):
            self.run_sequence(journal=lambda _: (_ for _ in ()).throw(OSError("disk full")))
        self.assertEqual([], self.backend.posts)

    def test_lost_down_result_still_releases_once(self):
        def fail(packet):
            if packet.message is Message.LEFT_DOWN: raise OSError("response lost")
            return True
        self.backend.failure = fail
        result = self.run_sequence()
        self.assertEqual("unresolved", result["outcome"])
        self.assertEqual([Message.LEFT_DOWN, Message.LEFT_UP], [p.message for _,p in self.backend.posts])

    def test_recorder_and_viewport_failure_does_not_block_release(self):
        def fail(packet):
            if packet.message is Message.LEFT_DOWN:
                self.backend.recording = False
                self.backend.state["client_width"] = 10
            return True
        self.backend.failure = fail
        result = self.run_sequence()
        self.assertEqual("unresolved", result["outcome"])
        self.assertEqual(Message.LEFT_UP, self.backend.posts[-1][1].message)

    def test_new_owner_never_receives_release(self):
        def fail(packet):
            if packet.message is Message.LEFT_DOWN: self.backend.owner = False
            return True
        self.backend.failure = fail
        result = self.run_sequence()
        self.assertIsNotNone(result["release_error"])
        self.assertEqual(1, len(self.backend.posts))

    def test_explicit_down_refusal_not_retried(self):
        self.backend.failure = lambda _: False
        result = self.run_sequence()
        self.assertEqual("unresolved", result["outcome"])
        self.assertEqual(1, len(self.backend.posts))

    def test_failed_drag_releases_at_last_attempted_point(self):
        path = (Point(10,10), Point(20,20), Point(30,30))
        packets = encode_drag(path, ClientBounds(500,500), duration_ms=200)
        def fail(packet):
            if packet.message is Message.MOUSE_MOVE: raise OSError("lost move")
            return True
        self.backend.failure = fail
        result = self.run_sequence(packets)
        self.assertEqual("unresolved", result["outcome"])
        up = self.backend.posts[-1][1]
        self.assertEqual(Message.LEFT_UP, up.message)
        self.assertEqual(20 | 20 << 16, up.lparam)

    def test_evidence_failure_after_down_releases_before_more_logs(self):
        trace = []
        def log(value):
            trace.append(value["state"])
            if value["state"] == "post_result":
                self.assertEqual(Message.LEFT_UP, self.backend.posts[-1][1].message)
                self.assertTrue(value["persisted_after_release"])
                raise OSError("disk failed after release")
        result = self.run_sequence(journal=log)
        self.assertEqual(Message.LEFT_UP, self.backend.posts[-1][1].message)
        self.assertEqual("unresolved", result["outcome"])

    def test_no_journal_call_can_stall_between_down_and_up(self):
        packets = encode_drag((Point(10,10),Point(20,20),Point(30,30)),ClientBounds(500,500),duration_ms=100)
        intents = []
        def log(value):
            if self.backend.posts:
                self.assertEqual(Message.LEFT_UP,self.backend.posts[-1][1].message)
            if value["state"] == "dispatch_intent": intents.append(value)
        self.assertTrue(self.run_sequence(packets,journal=log)["queue_accepted"])
        self.assertEqual(len(packets),len(intents[0]["packets"]))

    def test_navigation_key_release_is_paired(self):
        result = self.run_sequence(encode_key_press(NavigationKey.ESCAPE, ScanCode(1,False)))
        self.assertTrue(result["queue_accepted"])
        self.assertEqual(Message.KEY_UP, self.backend.posts[-1][1].message)

    def test_unpaired_sequence_refuses_before_any_dispatch(self):
        with self.assertRaises(FoundryError): self.run_sequence((self.click[0],))
        self.assertEqual([], self.backend.posts)

    def test_changed_guards_during_journal_prevent_first_post(self):
        for field, value in (("focus", 99), ("recording", False)):
            self.backend = FakeBackend()
            def log(record):
                if record["state"] == "post_attempt": setattr(self.backend, field, value)
            result = self.run_sequence(journal=log)
            self.assertEqual("not_dispatched", result["outcome"])
            self.assertEqual([], self.backend.posts)

    def test_wheel_point_is_bound_to_viewport(self):
        for point in (Point(-1, 20), Point(500,20), Point(20,500)):
            with self.assertRaises(FoundryError): self.run_sequence((encode_wheel(point,120),))
        self.assertEqual([], self.backend.posts)
        self.assertTrue(self.run_sequence((encode_wheel(Point(20,20),120),))["queue_accepted"])

    def test_signed_wheel_coordinates_work_on_negative_monitor(self):
        value = identity(); value["client_origin"] = [-500,-300]
        self.backend.state = value
        packet = encode_wheel(Point(-200,-100),-120)
        self.assertTrue(run_packets(value,(packet,),self.backend,self.journal.append)["queue_accepted"])

    def test_direct_packets_cannot_bypass_hold_or_move_timing(self):
        drag = encode_drag((Point(10,10),Point(20,20)),ClientBounds(500,500),duration_ms=100)
        for packets in ((self.click[0],replace(self.click[1],offset_ms=0)),
                        (drag[0],replace(drag[1],offset_ms=0),drag[2])):
            with self.assertRaises(FoundryError): self.run_sequence(packets)
        self.assertEqual([], self.backend.posts)

    def test_direct_packets_cannot_bypass_total_drag_distance(self):
        drag = encode_drag((Point(0,0),Point(499,499)),ClientBounds(500,500),duration_ms=100)
        moves = tuple(replace(drag[1],lparam=(499 | 499 << 16) if i % 2 else 0,offset_ms=i)
                      for i in range(1,31))
        packets = (drag[0],) + moves + (replace(drag[-1],lparam=0,offset_ms=30),)
        with self.assertRaises(FoundryError): self.run_sequence(packets)
        self.assertEqual([], self.backend.posts)
