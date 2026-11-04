"""Fabricated offline temp files/processes only; no game, secrets or sockets."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
from unittest.mock import Mock, patch

from sims4foundry_cli import companion_journal as ledger
from sims4foundry_cli import companion_protocol as wire
from sims4foundry_cli.companion_transport import ExpectedContext, ReceiptRead, Submission
from sims4foundry_cli.errors import FoundryError
from sims4foundry_cli.io import read_json, replace_json


def submission(number=1, *, observe=False, ttl=5.0, session="a" * 32, epoch=None):
    return Submission(wire.Request(f"{number:032x}", session, epoch or wire.ProcessEpoch(1234, 133000000000000000),
                      wire.Operation.OBSERVE if observe else wire.Operation.SET_SPEED,
                      wire.ObserveArguments() if observe else wire.SetSpeedArguments(2)),
                      ttl, None if observe else ExpectedContext("10", "20", 4, "99"))


def receipt(item, state="accepted", *, reason=None, result=None):
    item = item.submission if type(item) is ReceiptRead else item
    request = item.request
    if state in ("refused", "unknown") and reason is None:
        reason = "fixture_refusal" if state == "refused" else "fixture_unknown"
    if state == "not_found":
        reason = "history_missing"
    if state == "adapter_returned" and result is None:
        result = {"outcome": "verified", "fixture_only": True}
    return {"schema": 2, "kind": "receipt", "request_id": request.request_id,
            "session_id": request.session_id, "process_epoch": request.process_epoch.to_wire(),
            "request_sha256": item.request_sha256, "state": state, "reason": reason,
            "adapter_result": result, "independent_evidence": False}


def admit(_message, _ticket):
    """Fabricated unit-test policy, never a real host admission policy."""
    return True


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ledger"
        self.journal = ledger.create_journal(self.root)
        self.item = submission()

    def code(self, code, callback):
        with self.assertRaises(FoundryError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def submit(self, state="accepted", **kwargs):
        return self.journal.submit_once(self.item, authorize=admit,
                                       exchange=lambda message, _: receipt(message, state, **kwargs))

    def reconcile(self, state="accepted", **kwargs):
        return self.journal.reconcile(self.item.request.request_id, authorize=admit,
                                     exchange=lambda message, _: receipt(message, state, **kwargs))

    def records(self):
        return sorted((self.root / "records").glob("*.json"))

    def test_create_exclusive_default_dispatch_denied(self):
        with self.assertRaises(FileExistsError):
            ledger.create_journal(self.root)
        for kwargs in ({}, {"authorize": True, "exchange": True}):
            self.code("host_policy_required", lambda: self.journal.submit_once(self.item, **kwargs))
        self.assertEqual((), self.journal.status())
        self.assertEqual([], self.records())

    def test_exact_intent_before_admission_attempt_before_callback(self):
        order = []
        def authorize(message, ticket):
            order.append("authorize")
            events = [read_json(path) for path in self.records()]
            self.assertEqual(["intent"], [row["kind"] for row in events])
            self.assertEqual(self.item.to_wire(), events[0]["data"]["submission"])
            self.assertEqual(self.item.request_sha256, events[0]["data"]["request_sha256"])
            self.assertEqual(1, ticket.sequence)
            return True
        def exchange(message, ticket):
            order.append("exchange")
            events = [read_json(path) for path in self.records()]
            self.assertEqual(["intent", "attempt"], [row["kind"] for row in events])
            self.assertEqual(ticket.to_wire(), events[1]["data"])
            self.assertEqual("submit", ticket.kind)
            self.assertIs(type(message), Submission)
            return receipt(message)
        status = self.journal.submit_once(self.item, authorize=authorize, exchange=exchange)
        self.assertEqual(["authorize", "exchange"], order)
        self.assertEqual("accepted", status.state)
        self.assertTrue(status.unresolved)
        self.assertFalse(status.independent_evidence)
        self.assertFalse(status.live_qualified)

    def test_intent_only_never_enters_transport_later(self):
        first = self.journal.record_intent(self.item)
        self.assertEqual("intent_recorded", first.state)
        self.assertEqual("not_attempted", first.transmission)
        self.assertTrue(first.unresolved)
        callback = Mock(side_effect=AssertionError("resubmission"))
        self.assertEqual(first, ledger.Journal(self.root).submit_once(self.item, authorize=callback, exchange=callback))
        callback.assert_not_called()

    def test_identical_id_returns_status_without_hooks(self):
        first = self.submit()
        callback = Mock(side_effect=AssertionError("resubmission"))
        self.assertEqual(first, ledger.Journal(self.root).submit_once(self.item, authorize=callback, exchange=callback))
        callback.assert_not_called()
        self.assertEqual(3, len(self.records()))

    def test_id_conflict_every_immutable_binding_including_numeric_ttl(self):
        self.journal.record_intent(self.item)
        variants = [replace(self.item, ttl_seconds=6.0), replace(self.item, ttl_seconds=5),
                    replace(self.item, expected_context=ExpectedContext("11", "20", 4, "99")),
                    replace(self.item, expected_context=ExpectedContext("10", "21", 4, "99")),
                    replace(self.item, expected_context=ExpectedContext("10", "20", 5, "99")),
                    replace(self.item, expected_context=ExpectedContext("10", "20", 4, "100")),
                    submission(session="b" * 32), submission(epoch=wire.ProcessEpoch(1234, 133000000000000001)),
                    submission(epoch=wire.ProcessEpoch(1235, 133000000000000000)),
                    replace(self.item, request=replace(self.item.request, arguments=wire.SetSpeedArguments(3))),
                    submission(observe=True)]
        for item in variants:
            with self.subTest(item=item):
                self.code("intent_conflict", lambda: self.journal.submit_once(item))
        self.assertEqual(1, len(self.records()))

    def test_unresolved_mutation_blocks_new_id_session_process_but_not_observe(self):
        self.submit()
        for item in (submission(2), submission(2, session="b" * 32), submission(2, epoch=wire.ProcessEpoch(999, 100))):
            self.code("mutation_unresolved", lambda: self.journal.record_intent(item))
        observe = self.journal.record_intent(submission(3, observe=True))
        self.assertFalse(observe.mutation)
        self.assertTrue(observe.unresolved)

    def test_unresolved_observation_does_not_block_mutation(self):
        self.journal.record_intent(submission(2, observe=True))
        self.assertTrue(self.submit().mutation)

    def test_local_false_is_known_not_dispatched(self):
        exchange = Mock(side_effect=AssertionError("must not run"))
        result = self.journal.submit_once(self.item, authorize=lambda *_: False, exchange=exchange)
        self.assertEqual("not_dispatched", result.state)
        self.assertFalse(result.unresolved)
        self.assertEqual(0, result.attempt_count)
        exchange.assert_not_called()
        self.assertEqual(result, ledger.Journal(self.root).reconcile(self.item.request.request_id))
        self.journal.record_intent(submission(2))

    def test_nonexact_admission_and_exception_leave_unresolved_intent(self):
        exchange = Mock()
        self.code("host_admission_refused", lambda: self.journal.submit_once(
            self.item, authorize=lambda *_: 1, exchange=exchange))
        exchange.assert_not_called()
        self.assertEqual("intent_recorded", self.journal.status()[0].state)
        other = ledger.create_journal(Path(self.temp.name) / "exception")
        self.code("host_admission_failed", lambda: other.submit_once(
            self.item, authorize=Mock(side_effect=RuntimeError("fixture")), exchange=exchange))
        exchange.assert_not_called()
        self.assertEqual("intent_recorded", other.status()[0].state)

    def test_admission_cannot_change_submission_or_attempt(self):
        for target in ("submission", "ticket"):
            with self.subTest(target=target):
                other = ledger.create_journal(Path(self.temp.name) / target)
                def changed(message, ticket):
                    object.__setattr__(message if target == "submission" else ticket,
                                       "ttl_seconds" if target == "submission" else "attempt_id",
                                       6.0 if target == "submission" else "b" * 32)
                    return True
                exchange = Mock()
                self.code("admission_changed", lambda: other.submit_once(self.item, authorize=changed, exchange=exchange))
                exchange.assert_not_called()
                self.assertEqual(self.item.request_sha256, other.status()[0].request_sha256)

    def test_first_submit_refusal_is_terminal_without_game_evidence(self):
        result = self.submit("refused")
        self.assertFalse(result.unresolved)
        self.assertFalse(result.independent_evidence)
        callback = Mock(side_effect=AssertionError("terminal"))
        self.assertEqual(result, self.journal.reconcile(self.item.request.request_id, authorize=callback, exchange=callback))
        callback.assert_not_called()
        self.journal.record_intent(submission(2))

    def test_accepted_can_progress_by_read_only_to_refused(self):
        self.submit()
        result = self.reconcile("refused")
        self.assertFalse(result.unresolved)
        self.assertEqual(2, result.attempt_count)

    def test_reconcile_exact_original_no_ttl_renewal(self):
        self.submit()
        tickets = []
        def exchange(message, ticket):
            self.assertIs(type(message), ReceiptRead)
            self.assertEqual(ReceiptRead(self.item).to_wire(), message.to_wire())
            self.assertEqual(self.item.to_wire(), message.submission.to_wire())
            self.assertEqual("receipt_read", ticket.kind)
            tickets.append(ticket)
            return receipt(message, "dispatched")
        result = ledger.Journal(self.root).reconcile(self.item.request.request_id, authorize=admit, exchange=exchange)
        self.assertEqual(2, tickets[0].sequence)
        self.assertEqual("dispatched", result.state)
        self.assertTrue(result.unresolved)
        self.assertNotEqual(read_json(self.records()[1])["data"]["attempt_id"], tickets[0].attempt_id)

    def test_adapter_verified_never_resolves_game_outcome(self):
        self.submit()
        self.reconcile("dispatched")
        result = self.reconcile("adapter_returned")
        self.assertEqual("verified", result.receipt.adapter_outcome)
        self.assertTrue(result.unresolved)
        self.assertFalse(result.independent_evidence)
        self.assertFalse(result.live_qualified)
        self.code("mutation_unresolved", lambda: self.journal.record_intent(submission(2)))
        self.assertEqual(result, ledger.Journal(self.root).status()[0])

    def test_missing_transport_history_is_unresolved_not_noop(self):
        result = self.submit("not_found")
        self.assertEqual("history_missing", result.receipt.reason)
        self.assertTrue(result.unresolved)
        self.assertTrue(result.uncertainty_latched)
        self.code("mutation_unresolved", lambda: self.journal.record_intent(submission(2)))

    def test_first_receipt_read_cannot_resolve_staged_intent(self):
        self.journal.record_intent(self.item)
        self.code("receipt_uncertain", lambda: self.reconcile("refused"))
        self.assertTrue(self.journal.status()[0].unresolved)

    def test_callback_failure_latches_uncertainty_never_resubmits(self):
        self.code("exchange_uncertain", lambda: self.journal.submit_once(
            self.item, authorize=admit, exchange=Mock(side_effect=TimeoutError("do not save this detail"))))
        result = ledger.Journal(self.root).submit_once(self.item)
        self.assertTrue(result.unresolved)
        self.assertTrue(result.uncertainty_latched)
        self.assertEqual("possible", result.transmission)
        self.assertNotIn("do not save this detail", "".join(path.read_text() for path in self.records()))
        self.code("receipt_uncertain", lambda: self.reconcile("refused"))
        self.assertTrue(self.reconcile("adapter_returned").unresolved)

    def test_terminal_receipt_immutable_including_unstored_details(self):
        self.submit("adapter_returned")
        before = self.journal.status()[0].receipt
        self.reconcile("adapter_returned")
        for state, kwargs in (("accepted", {}), ("refused", {}),
                              ("adapter_returned", {"result": {"outcome": "verified", "fixture_only": False}})):
            self.code("receipt_uncertain", lambda: self.reconcile(state, **kwargs))
            self.assertEqual(before, self.journal.status()[0].receipt)

    def test_dispatched_cannot_regress_to_refused_or_accepted(self):
        self.submit("dispatched")
        self.code("receipt_uncertain", lambda: self.reconcile("refused"))
        self.code("receipt_uncertain", lambda: self.reconcile("accepted"))
        self.assertEqual("dispatched", self.journal.status()[0].receipt.state)

    def test_unknown_terminal_claim_cannot_regress(self):
        self.submit("unknown")
        self.code("receipt_uncertain", lambda: self.reconcile("refused"))
        self.code("receipt_uncertain", lambda: self.reconcile("adapter_returned"))

    def test_bad_first_receipt_binding_and_shapes_fail_closed(self):
        for number, bad in enumerate((None, {}, dict(receipt(self.item), request_sha256="0" * 64),
                {"schema": 2, "kind": "error", "code": "fixture_error", "outcome_known": False},
                dict(receipt(self.item), independent_evidence=True)), 1):
            other = ledger.create_journal(Path(self.temp.name) / f"invalid{number}")
            self.code("receipt_uncertain", lambda: other.submit_once(self.item, authorize=admit, exchange=lambda *_: bad))
            self.assertTrue(other.status()[0].uncertainty_latched)

    def test_raw_adapter_details_are_never_persisted(self):
        result = self.submit("adapter_returned", result={"outcome": "verified", "fake_secret_detail": "not-persisted"})
        self.assertTrue(result.unresolved)
        raw = "".join(path.read_text() for path in self.records())
        self.assertNotIn("fake_secret_detail", raw)
        self.assertNotIn("not-persisted", raw)

    def test_mutated_exchange_message_or_ticket_becomes_unknown(self):
        for target in ("message", "ticket"):
            other = ledger.create_journal(Path(self.temp.name) / target)
            def exchange(message, ticket):
                original = receipt(message)
                object.__setattr__(message if target == "message" else ticket,
                                   "ttl_seconds" if target == "message" else "attempt_id",
                                   7 if target == "message" else "c" * 32)
                return original
            self.code("receipt_uncertain", lambda: other.submit_once(self.item, authorize=admit, exchange=exchange))
            self.assertEqual(self.item.request_sha256, other.status()[0].request_sha256)
            self.assertTrue(other.status()[0].uncertainty_latched)

    def test_unknown_id_never_creates_intent_or_queries(self):
        callback = Mock()
        self.code("intent_missing", lambda: self.journal.reconcile("b" * 32, authorize=admit, exchange=callback))
        self.code("intent_missing", lambda: self.journal.status("b" * 32))
        callback.assert_not_called()
        self.assertEqual([], self.records())

    def test_lock_stable_inode_and_missing_lock_not_recreated(self):
        path = self.root / "operation.lock"
        inode = path.stat().st_ino
        self.journal.record_intent(self.item)
        self.journal.status()
        self.assertEqual(inode, path.stat().st_ino)
        self.assertEqual(b"\0", path.read_bytes())
        path.unlink()
        self.code("journal_corrupt", self.journal.status)
        self.assertFalse(path.exists())

    def test_callback_reentrancy_refuses_same_and_distinct_instances(self):
        nested = Mock(side_effect=AssertionError("nested exchange must not run"))
        stages = []
        def check_locked(stage):
            stages.append(stage)
            for other in (self.journal, ledger.Journal(self.root)):
                self.code("busy", other.status)
                self.code("busy", lambda: other.submit_once(
                    self.item, authorize=admit, exchange=nested))
                self.code("busy", lambda: other.submit_once(
                    submission(2, observe=True), authorize=admit, exchange=nested))
                self.code("busy", lambda: other.reconcile(
                    self.item.request.request_id, authorize=admit, exchange=nested))
        def authorize(message, ticket):
            check_locked("authorize")
            return True
        def exchange(message, ticket):
            check_locked("exchange")
            return receipt(message)
        result = self.journal.submit_once(self.item, authorize=authorize, exchange=exchange)
        self.assertEqual(["authorize", "exchange"], stages)
        nested.assert_not_called()
        self.assertEqual("accepted", result.state)
        self.assertEqual(1, result.attempt_count)
        self.assertEqual(["intent", "attempt", "receipt"],
                         [read_json(path)["kind"] for path in self.records()])
        self.assertEqual((result,), ledger.Journal(self.root).status())

    def test_concurrent_instance_cannot_enter_held_callback_or_replay(self):
        entered, release = threading.Event(), threading.Event()
        results, failures = [], []
        def exchange(message, ticket):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture release timed out")
            return receipt(message)
        def run():
            try:
                results.append(ledger.Journal(self.root).submit_once(
                    self.item, authorize=admit, exchange=exchange))
            except BaseException as error:
                failures.append(error)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        nested = Mock(side_effect=AssertionError("concurrent exchange must not run"))
        try:
            self.assertTrue(entered.wait(5), "fixture callback did not start")
            self.code("busy", self.journal.status)
            self.code("busy", lambda: self.journal.submit_once(
                self.item, authorize=admit, exchange=nested))
            self.code("busy", lambda: self.journal.submit_once(
                submission(2, observe=True), authorize=admit, exchange=nested))
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive(), "fixture worker did not finish")
        self.assertEqual([], failures)
        self.assertEqual(1, len(results))
        self.assertEqual(results[0], self.journal.submit_once(
            self.item, authorize=admit, exchange=nested))
        nested.assert_not_called()
        self.assertEqual(["intent", "attempt", "receipt"],
                         [read_json(path)["kind"] for path in self.records()])

    def test_missing_head_metadata_intent_attempt_receipt_fail_closed(self):
        for missing in ("head.json", "journal.json", "records/000001.json", "records/000002.json", "records/000003.json"):
            other = ledger.create_journal(Path(self.temp.name) / missing.replace("/", "_"))
            other.submit_once(self.item, authorize=admit, exchange=lambda msg, _: receipt(msg))
            (other.directory / missing).unlink()
            self.code("journal_corrupt", other.status)
            self.code("journal_corrupt", lambda: other.record_intent(submission(2)))

    def test_corrupt_truncated_duplicate_deep_nonfinite_json(self):
        self.journal.record_intent(self.item)
        path = self.records()[0]
        original = path.read_bytes()
        for raw in (b"", b"{", b'{"schema":1,"schema":1}',
                    b'{"x":' + b'[' * 40 + b'0' + b']' * 40 + b'}', b'{"x":NaN}', original[:-5]):
            path.write_bytes(raw)
            with self.assertRaises(FoundryError):
                self.journal.status()
        path.write_bytes(original)
        self.assertEqual(1, len(self.journal.status()))

    def test_pending_file_and_nonregular_record_fail_closed(self):
        self.journal.record_intent(self.item)
        path = self.root / "records" / ".000002.json.crash.pending"
        path.write_bytes(b"partial")
        self.code("journal_corrupt", self.journal.status)
        path.unlink()
        record = self.records()[0]
        record.unlink()
        record.mkdir()
        self.code("journal_corrupt", self.journal.status)

    def test_oversized_file_directory_scan_bounded(self):
        (self.root / "head.json").write_bytes(b" " * (ledger.MAX_RECORD_BYTES + 1))
        self.code("journal_bound", self.journal.status)
        (self.root / "unexpected").write_text("x")
        self.code("journal_bound", self.journal.status)

    def test_record_chain_tamper_fails_closed(self):
        self.submit()
        path = self.records()[1]
        value = read_json(path)
        value["previous_sha256"] = "0" * 64
        replace_json(path, value)
        self.code("journal_corrupt", self.journal.status)

    def test_hardlinked_record_rejected(self):
        self.journal.record_intent(self.item)
        os.link(self.records()[0], Path(self.temp.name) / "alias")
        self.code("journal_corrupt", self.journal.status)

    def test_symlink_and_protected_paths_refused(self):
        self.code("protected_path", lambda: ledger.create_journal(Path(self.temp.name) / "The Sims 4"))
        link = Path(self.temp.name) / "linked"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable on this runner")
        self.code("linked_path", lambda: ledger.Journal(link))

    def publication_failure(self, kind, *, head=False, after=False):
        method = "replace_json" if head else "new_json"
        original = getattr(ledger, method)
        sequences = {"intent": 1, "attempt": 2, "receipt": 3, "exchange_failed": 3}
        def fail(path, value):
            hit = value.get("sequence") == sequences[kind] if head else value.get("kind") == kind
            if hit:
                if after:
                    original(path, value)
                raise OSError("fabricated storage failure")
            return original(path, value)
        return patch.object(ledger, method, side_effect=fail)

    def test_intent_publication_failure_zero_callbacks(self):
        callback = Mock()
        with self.publication_failure("intent"):
            self.code("journal_corrupt", lambda: self.journal.submit_once(self.item, authorize=callback, exchange=callback))
        callback.assert_not_called()
        self.assertEqual((), self.journal.status())

    def test_intent_head_failure_blocks_orphan(self):
        callback = Mock()
        with self.publication_failure("intent", head=True):
            self.code("journal_corrupt", lambda: self.journal.submit_once(self.item, authorize=callback, exchange=callback))
        callback.assert_not_called()
        self.code("journal_corrupt", ledger.Journal(self.root).status)

    def test_attempt_publication_failure_no_exchange_no_retry(self):
        callback = Mock()
        with self.publication_failure("attempt"):
            self.code("journal_corrupt", lambda: self.journal.submit_once(self.item, authorize=admit, exchange=callback))
        callback.assert_not_called()
        self.assertEqual("intent_recorded", self.journal.submit_once(self.item).state)
        self.code("mutation_unresolved", lambda: self.journal.record_intent(submission(2)))

    def test_attempt_head_failure_orphan_zero_transmission(self):
        callback = Mock()
        with self.publication_failure("attempt", head=True):
            self.code("journal_corrupt", lambda: self.journal.submit_once(self.item, authorize=admit, exchange=callback))
        callback.assert_not_called()
        self.code("journal_corrupt", self.journal.status)

    def test_post_attempt_head_failure_unknown_even_without_callback(self):
        callback = Mock()
        with self.publication_failure("attempt", head=True, after=True):
            self.code("journal_corrupt", lambda: self.journal.submit_once(self.item, authorize=admit, exchange=callback))
        callback.assert_not_called()
        self.assertEqual("unknown", ledger.Journal(self.root).submit_once(self.item).state)
        self.code("receipt_uncertain", lambda: self.reconcile("refused"))

    def test_receipt_publication_failure_possible_transmission(self):
        with self.publication_failure("receipt"):
            self.code("journal_corrupt", lambda: self.submit("refused"))
        result = ledger.Journal(self.root).submit_once(self.item)
        self.assertEqual("unknown", result.state)
        self.assertTrue(result.unresolved)
        self.code("receipt_uncertain", lambda: self.reconcile("refused"))

    def test_receipt_head_failure_never_accepts_partial_receipt(self):
        with self.publication_failure("receipt", head=True):
            self.code("journal_corrupt", lambda: self.submit("refused"))
        self.code("journal_corrupt", self.journal.status)

    def test_post_receipt_head_failure_retains_exact_published_receipt(self):
        with self.publication_failure("receipt", head=True, after=True):
            self.code("journal_corrupt", lambda: self.submit("refused"))
        result = ledger.Journal(self.root).submit_once(self.item)
        self.assertEqual("refused", result.state)
        self.assertFalse(result.unresolved)

    def test_error_record_failure_unanswered_attempt(self):
        with self.publication_failure("exchange_failed"):
            self.code("journal_corrupt", lambda: self.journal.submit_once(
                self.item, authorize=admit, exchange=Mock(side_effect=TimeoutError())))
        self.assertTrue(ledger.Journal(self.root).status()[0].uncertainty_latched)

    def test_attempt_bound_no_eviction(self):
        self.submit()
        with patch.object(ledger, "MAX_ATTEMPTS", 2):
            self.reconcile()
            callback = Mock()
            self.code("journal_full", lambda: self.journal.reconcile(self.item.request.request_id, authorize=admit, exchange=callback))
            callback.assert_not_called()
            self.assertEqual(2, self.journal.status()[0].attempt_count)

    def test_request_record_capacity_bounded(self):
        self.journal.record_intent(submission(2, observe=True))
        with patch.object(ledger, "MAX_REQUESTS", 1):
            self.code("journal_full", lambda: self.journal.record_intent(submission(3, observe=True)))
        with patch.object(ledger, "MAX_RECORDS", 1):
            self.code("journal_full", lambda: self.journal.record_intent(submission(3, observe=True)))
            self.assertEqual(1, len(self.journal.status()))

    def child(self, body):
        source = str(Path(ledger.__file__).resolve().parents[1])
        code = "import os, sys, time\nfrom pathlib import Path\nfrom sims4foundry_cli.companion_journal import Journal\n" + body
        env = dict(os.environ, PYTHONPATH=source)
        return subprocess.Popen([sys.executable, "-B", "-c", code, str(self.root)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_process_crash_releases_lock_no_resubmit(self):
        raw = json.dumps(self.item.to_wire())
        body = ("from sims4foundry_cli.companion_journal import _submission\nimport json\n"
                f"item = _submission(json.loads({raw!r}))\n"
                "Journal(sys.argv[1]).submit_once(item, authorize=lambda *_: True, exchange=lambda *_: os._exit(73))\n")
        child = self.child(body)
        stdout, stderr = child.communicate(timeout=15)
        self.assertEqual(73, child.returncode, (stdout, stderr))
        result = ledger.Journal(self.root).submit_once(self.item)
        self.assertEqual("unknown", result.state)
        self.assertEqual(1, result.attempt_count)
        self.code("receipt_uncertain", lambda: self.reconcile("refused"))
        self.assertTrue(self.reconcile("adapter_returned").unresolved)

    def test_real_competing_process_lock_busy_exit_releases(self):
        # The child waits on stdin, so its lifecycle is controlled, not a timed
        # sleep. The parent always terminates this owned fabricated process.
        source = str(Path(ledger.__file__).resolve().parents[1])
        child = subprocess.Popen([sys.executable, "-B", "-c",
            "import sys\nfrom sims4foundry_cli.companion_journal import Journal\n"
            "with Journal(sys.argv[1])._locked():\n print('locked', flush=True)\n sys.stdin.read(1)\n", str(self.root)],
            env=dict(os.environ, PYTHONPATH=source), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual("locked", child.stdout.readline().strip())
            self.code("busy", self.journal.status)
        finally:
            child.terminate()
            child.communicate(timeout=15)
        self.assertEqual((), self.journal.status())

    def test_real_crash_during_record_publication_fail_closed(self):
        raw = json.dumps(self.item.to_wire())
        body = ("import json\nfrom sims4foundry_cli import companion_journal as module\n"
                f"item = module._submission(json.loads({raw!r}))\n"
                "def crash(path, value):\n"
                "    path.with_name('.interrupted.pending').write_text('{')\n"
                "    os._exit(74)\n"
                "module.new_json = crash\n"
                "Journal(sys.argv[1]).record_intent(item)\n")
        child = self.child(body)
        stdout, stderr = child.communicate(timeout=15)
        self.assertEqual(74, child.returncode, (stdout, stderr))
        self.code("journal_corrupt", ledger.Journal(self.root).status)


    def append_raw(self, kind, request_id, data):
        # Fabricate a hash-consistent, semantically impossible record. Hashes
        # are consistency checks, not authentication; chronology still validates.
        with self.journal._locked():
            head = ledger._read(self.root / "head.json")
            self.journal._append(head, kind, request_id, data)

    def test_semantic_rejection_of_attempt_after_resolved_refusal(self):
        self.submit("refused")
        ticket = read_json(self.records()[1])["data"]
        ticket.update(attempt_id="d" * 32, sequence=2, kind="receipt_read")
        self.append_raw("attempt", self.item.request.request_id, ticket)
        self.code("journal_corrupt", self.journal.status)
        self.code("journal_corrupt", lambda: self.journal.record_intent(submission(2)))

    def test_semantic_rejection_of_delayed_submit_and_refusal(self):
        self.journal.record_intent(self.item)
        self.journal.record_intent(submission(2, observe=True))
        head = read_json(self.root / "head.json")
        ticket = ledger.Attempt(head["journal_id"], self.item.request.request_id,
                                self.item.request_sha256, "d" * 32, 1, "submit")
        self.append_raw("attempt", self.item.request.request_id, ticket.to_wire())
        self.code("journal_corrupt", self.journal.status)

    def test_receipt_bound_to_exact_attempt_id_and_sequence(self):
        self.submit()
        original = read_json(self.records()[-1])
        for field, value in (("attempt_id", "e" * 32), ("attempt_sequence", 2), ("attempt_sequence", True)):
            altered = json.loads(json.dumps(original))
            altered["data"][field] = value
            replace_json(self.records()[-1], altered)
            head = read_json(self.root / "head.json")
            head["record_sha256"] = ledger._sha(altered)
            replace_json(self.root / "head.json", head)
            self.code("journal_corrupt", self.journal.status)
        replace_json(self.records()[-1], original)
        head["record_sha256"] = ledger._sha(original)
        replace_json(self.root / "head.json", head)
        self.assertEqual("accepted", self.journal.status()[0].state)

    def test_duplicate_attempt_id_and_nonsequential_attempt_rejected(self):
        self.submit()
        original = read_json(self.records()[1])["data"]
        self.append_raw("attempt", self.item.request.request_id, dict(original, kind="receipt_read", sequence=2))
        self.code("journal_corrupt", self.journal.status)

    def test_duplicate_receipt_rejected_not_overwritten(self):
        self.submit()
        original = read_json(self.records()[-1])
        self.append_raw("receipt", self.item.request.request_id, original["data"])
        self.code("journal_corrupt", self.journal.status)

    def test_receipt_read_claims_cannot_invent_submit_chronology(self):
        self.journal.record_intent(self.item)
        self.reconcile("accepted")
        self.code("receipt_uncertain", lambda: self.reconcile("refused"))
        self.assertTrue(self.journal.status()[0].unresolved)

    def test_token_shaped_reason_redacted_everywhere_but_digest(self):
        token = "abcdef0123456789" * 4
        result = self.submit("unknown", reason=token)
        self.assertEqual("adapter_unknown", result.receipt.reason)
        self.assertNotIn(token, repr(result))
        raw = "".join(path.read_text() for path in self.root.rglob("*.json"))
        self.assertNotIn(token, raw)
        self.reconcile("unknown", reason=token)
        self.code("receipt_uncertain", lambda: self.reconcile("unknown", reason="different_private_reason"))

    def test_public_reason_codes_preserved(self):
        self.assertEqual("expired", self.submit("refused", reason="expired").receipt.reason)

    def test_admission_exception_redacted_without_dispatch(self):
        token = "PRIVATE_ADMISSION_FIXTURE"
        exchange = Mock()
        error = self.code("host_admission_failed", lambda: self.journal.submit_once(
            self.item, authorize=Mock(side_effect=RuntimeError(token)), exchange=exchange))
        self.assertNotIn(token, str(error))
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)
        self.assertNotIn(token, "".join(path.read_text() for path in self.root.rglob("*.json")))
        self.assertTrue(self.journal.status()[0].unresolved)
        exchange.assert_not_called()

    def test_receipt_query_denied_does_not_reclassify_or_send(self):
        before = self.submit()
        exchange = Mock()
        self.code("host_admission_refused", lambda: self.journal.reconcile(
            self.item.request.request_id, authorize=lambda *_: False, exchange=exchange))
        self.assertEqual(before, self.journal.status()[0])
        exchange.assert_not_called()

    def test_cross_workspace_does_not_claim_global_history(self):
        self.submit()
        other = ledger.create_journal(Path(self.temp.name) / "independent")
        # Offline storage can be created, but carries no live qualification and
        # has no executor. Global authority is explicitly absent, not inferred.
        self.assertFalse(other.record_intent(self.item).live_qualified)
        self.code("host_policy_required", lambda: other.submit_once(submission(2, observe=True)))



    def test_corrupt_schema_and_storage_exception_tracebacks_redacted(self):
        token = "PRIVATE_MALFORMED_FIELD_FIXTURE"
        self.journal.record_intent(self.item)
        path = self.records()[0]
        original = read_json(path)
        value = json.loads(json.dumps(original))
        value["data"]["submission"]["request"]["process_epoch"][token] = 1
        replace_json(path, value)
        error = self.code("journal_corrupt", self.journal.status)
        self.assertNotIn(token, "".join(traceback.format_exception(error)))
        replace_json(path, original)
        for exception in (TypeError(token), OSError(token), ValueError(token)):
            with patch.object(ledger, "_read", side_effect=exception):
                error = self.code("journal_corrupt", self.journal.status)
            self.assertNotIn(token, "".join(traceback.format_exception(error)))
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)
        other = ledger.create_journal(Path(self.temp.name) / "bad_callback_receipt")
        bad = receipt(self.item)
        bad["process_epoch"][token] = 1
        error = self.code("receipt_uncertain", lambda: other.submit_once(
            self.item, authorize=admit, exchange=lambda *_: bad))
        self.assertNotIn(token, "".join(traceback.format_exception(error)))
        self.assertNotIn(token, "".join(path.read_text() for path in other.directory.rglob("*.json")))


if __name__ == "__main__":
    unittest.main()
