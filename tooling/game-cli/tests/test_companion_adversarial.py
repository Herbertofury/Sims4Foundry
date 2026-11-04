"""Deterministic cross-boundary faults with fabricated I/O, clock and history.

No real credentials, sockets, game APIs, profile, desktop or production policy.
Late I/O simulates a scheduler resuming after the original absolute deadline;
socket timeouts alone cannot establish that completion happened before it.
The server tests also run with the actual isolated Python 3.7 interpreter.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "companion-src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from foundry_companion import transport, wire
from foundry_companion.queue import ProcessEpoch

SESSION = "a" * 32
EPOCH = ProcessEpoch(1234, 99)


def submission(number=1):
    return {"schema": 2, "kind": "submit", "ttl_seconds": 5.0,
            "request": {"schema": 1, "kind": "request", "request_id": "{:032x}".format(number),
                        "session_id": SESSION, "process_epoch": wire.epoch_wire(EPOCH),
                        "operation": "live.set_speed", "arguments": {"speed": 2}},
            "expected_context": {"zone_id": "10", "household_id": "20",
                                 "save_slot": 4, "save_guid": "99"}}


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def expire(self):
        self.now = 103.0  # Original deadline is 102.0; it is never renewed.


class FabricatedSocket:
    def __init__(self, incoming, clock, late=None):
        self.incoming = incoming
        self.clock, self.late = clock, late
        self.closed = False
        self.sent, self.connects, self.shutdowns, self.reads = [], [], [], []

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def close(self):
        self.closed = True

    def settimeout(self, value):
        if not 0 < value <= 5:
            raise AssertionError("The original bounded deadline was not used.")

    def _complete(self, stage):
        if self.late == stage:
            self.clock.expire()

    def connect(self, address):
        if address != ("127.0.0.1", 12345):
            raise AssertionError("Fixture cannot connect to a different destination.")
        self.connects.append(address)
        self._complete("connect")

    def sendall(self, value):
        self.sent.append(value)
        self._complete("send")

    def shutdown(self, mode):
        self.shutdowns.append(mode)
        self._complete("shutdown")

    def recv(self, count):
        stages = ("header", "payload", "eof")
        if len(self.reads) >= len(stages):
            raise AssertionError("Fixture received an unexpected retry or extra read.")
        stage = stages[len(self.reads)]
        self.reads.append(stage)
        result, self.incoming = self.incoming[:count], self.incoming[count:]
        self._complete(stage)
        return result


class FabricatedPolicy(transport.DenyHostPolicy):
    def __init__(self):
        self.peers, self.submits, self.dispatches = [], [], []

    def authorize_peer(self, connection, peer, binding, deadline):
        self.peers.append(peer)
        return True

    def authorize_submit(self, item, binding, deadline):
        self.submits.append(item)
        return True

    def authorize_dispatch(self, item, binding):
        self.dispatches.append(item)
        return True


class ServerDeadlineTests(unittest.TestCase):
    def test_late_read_or_decode_never_reaches_submission_admission(self):
        for stage in ("header", "payload", "eof", "decode", "submit_parse"):
            with self.subTest(stage=stage):
                clock, policy = Clock(), FabricatedPolicy()
                server = transport.LoopbackTransport(SESSION, EPOCH, policy=policy)
                self.addCleanup(lambda server=server: self.assertTrue(server.shutdown()))
                connection = FabricatedSocket(wire.encode_frame(submission()), clock, stage)
                target = "parse_submit" if stage == "submit_parse" else "decode_payload"
                original = getattr(transport, target)

                def parse(*args):
                    value = original(*args)
                    if stage in ("decode", "submit_parse"):
                        clock.expire()
                    return value

                with patch.object(transport, "monotonic", clock), patch.object(transport, target, parse):
                    with self.assertRaises(wire.ProtocolError) as caught:
                        if stage == "submit_parse":
                            server._handle(submission(), 102.0)
                        else:
                            transport.read_one(connection, 102.0)
                    self.assertEqual("connection_deadline", caught.exception.code)
                    connection.close()
                    self.assertTrue(connection.closed)
                    # Exercise the real service error/cleanup path as well.
                    clock.now = 100.0
                    connection = FabricatedSocket(wire.encode_frame(submission()), clock, stage)
                    server._serve(connection, ("127.0.0.1", 43210), 102.0)
                self.assertTrue(connection.closed)
                self.assertEqual([], connection.sent)
                self.assertEqual([], policy.submits)
                self.assertEqual([], policy.dispatches)
                self.assertEqual({}, server._records)
                self.assertEqual(0, server.queue_status().retained)
                execute = Mock()
                self.assertIsNone(server.owner_tick(execute))
                execute.assert_not_called()
                self.assertTrue(server.shutdown())

    def test_late_send_retains_receipt_and_blocks_new_or_pending_dispatch(self):
        clock, policy = Clock(), FabricatedPolicy()
        server = transport.LoopbackTransport(SESSION, EPOCH, policy=policy)
        connection = FabricatedSocket(wire.encode_frame(submission()), clock, "send")
        try:
            with patch.object(transport, "monotonic", clock):
                server._serve(connection, ("127.0.0.1", 43210), 102.0)
            self.assertTrue(connection.closed)
            self.assertEqual(1, len(connection.sent))
            self.assertEqual("accepted", wire.decode_payload(connection.sent[0][4:])["state"])
            self.assertEqual(1, len(policy.submits))
            self.assertEqual("response_lost", server.queue_status().mutation_blocked_reason)
            original = server._records[submission()["request"]["request_id"]].submission
            with patch.object(transport, "monotonic", clock):
                response, admitted = server._handle(submission(2), 104.0)
            self.assertEqual(("refused", "response_lost", False),
                             (response["state"], response["reason"], admitted))
            self.assertEqual(1, len(policy.submits))  # No extra host admission.
            execute = Mock()
            result = server.owner_tick(execute)
            self.assertEqual(("refused", "response_lost"), (result.state, result.reason))
            execute.assert_not_called()
            self.assertEqual([], policy.dispatches)
            self.assertIs(original, server._records[original.request.request_id].submission)
            self.assertEqual(2, len(server._records))  # Both original intents retained.
        finally:
            self.assertTrue(server.shutdown())

    def test_send_helper_reports_exact_late_completion_error(self):
        clock = Clock()
        connection = FabricatedSocket(b"", clock, "send")
        with patch.object(transport, "monotonic", clock):
            with self.assertRaises(wire.ProtocolError) as caught:
                transport._send_one(connection, {"fixture": True}, 102.0)
        self.assertEqual("connection_deadline", caught.exception.code)
        self.assertEqual(1, len(connection.sent))
        connection.close()
        self.assertTrue(connection.closed)


@unittest.skipIf(sys.version_info < (3, 11), "Current CLI requires Python 3.11+.")
class ClientJournalDeadlineTests(unittest.TestCase):
    def setUp(self):
        from sims4foundry_cli import companion_journal, companion_protocol, companion_transport
        from sims4foundry_cli.errors import FoundryError
        self.ledger, self.protocol, self.client = companion_journal, companion_protocol, companion_transport
        self.FoundryError = FoundryError
        self.item = self.ledger._submission(submission())
        parsed = wire.parse_submit(submission(), SESSION, EPOCH)
        self.reply = wire.receipt(parsed.request.request_id, parsed.request_sha256, SESSION, EPOCH,
                                  transport.Snapshot(parsed.request.request_id, SESSION, "pending"))

    def test_each_late_io_completion_is_uncertain_and_never_retries(self):
        for stage in ("connect", "send", "shutdown", "header", "payload", "eof"):
            with self.subTest(stage=stage):
                clock = Clock()
                connection = FabricatedSocket(wire.encode_frame(self.reply), clock, stage)
                authorize = Mock(return_value=True)
                with patch.object(self.client, "monotonic", clock), \
                        patch.object(self.client.socket, "socket", return_value=connection):
                    with self.assertRaises(self.protocol.ProtocolError) as caught:
                        self.client.exchange_once(12345, self.item, authorize_peer=authorize)
                self.assertEqual("connection_deadline", caught.exception.code)
                self.assertTrue(connection.closed)
                self.assertEqual(1, len(connection.connects))
                self.assertEqual(0 if stage == "connect" else 1, len(connection.sent))
                self.assertEqual(0 if stage == "connect" else 1, authorize.call_count)

    def test_receipt_validation_cannot_return_success_after_original_deadline(self):
        clock = Clock()
        connection = FabricatedSocket(wire.encode_frame(self.reply), clock)
        original = self.client.parse_receipt

        def parse(*args):
            result = original(*args)
            clock.expire()
            return result

        with patch.object(self.client, "monotonic", clock), \
                patch.object(self.client.socket, "socket", return_value=connection), \
                patch.object(self.client, "parse_receipt", parse):
            with self.assertRaises(self.protocol.ProtocolError) as caught:
                self.client.exchange_once(12345, self.item, authorize_peer=lambda *args: True)
        self.assertEqual("connection_deadline", caught.exception.code)
        self.assertTrue(connection.closed)
        self.assertEqual(1, len(connection.sent))

    def test_late_receipt_keeps_durable_uncertainty_and_prevents_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.ledger.create_journal(Path(directory) / "journal")
            clock = Clock()
            connection = FabricatedSocket(wire.encode_frame(self.reply), clock, "eof")
            admission, peer = Mock(return_value=True), Mock(return_value=True)

            def exchange(message, ticket):
                return self.client.exchange_once(12345, message, authorize_peer=peer)

            callback = Mock(side_effect=exchange)
            with patch.object(self.client, "monotonic", clock), \
                    patch.object(self.client.socket, "socket", return_value=connection):
                with self.assertRaises(self.FoundryError) as caught:
                    journal.submit_once(self.item, authorize=admission, exchange=callback)
            self.assertEqual("exchange_uncertain", caught.exception.code)
            self.assertTrue(connection.closed)
            self.assertEqual(1, len(connection.sent))
            reopened = self.ledger.Journal(journal.directory)
            status = reopened.status(self.item.request.request_id)
            self.assertEqual(("unknown", "possible", 1),
                             (status.state, status.transmission, status.attempt_count))
            self.assertTrue(status.uncertainty_latched)
            self.assertTrue(status.unresolved)
            self.assertIsNone(status.receipt)
            self.assertEqual(status, reopened.submit_once(self.item, authorize=admission, exchange=callback))
            with self.assertRaises(self.FoundryError) as caught:
                reopened.submit_once(self.ledger._submission(submission(2)),
                                     authorize=admission, exchange=callback)
            self.assertEqual("mutation_unresolved", caught.exception.code)
            self.assertEqual((1, 1, 1), (admission.call_count, callback.call_count, peer.call_count))
            self.assertEqual(1, len(connection.sent))
            self.assertEqual(status, reopened.status(self.item.request.request_id))


if __name__ == "__main__":
    unittest.main()
