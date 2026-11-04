"""Offline-only queue fixtures; no native imports, install, or game evidence."""

import ast
from dataclasses import FrozenInstanceError, replace
import importlib.util
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch


# Load by a dedicated source path, without adding companion-src to the CLI's
# sys.path or replacing either its package namespace or stdlib queue.
SOURCE = Path(__file__).resolve().parents[1] / "companion-src" / "foundry_companion" / "queue.py"
SPEC = importlib.util.spec_from_file_location("_foundry_companion_queue_fixture", SOURCE)
queue = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = queue
SPEC.loader.exec_module(queue)

SESSION = "a" * 32


class Clock:
    def __init__(self, now=100.0):
        self.now = now
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.now


def request(number=1, operation="live.set_speed", arguments=None, **changes):
    if arguments is None:
        arguments = {
            "observe": queue.ObserveArguments(),
            "live.set_speed": queue.SetSpeedArguments(1),
            "cas.enter": queue.EnterCasArguments("18446744073709551615"),
            "game.request_exit_menu": queue.RequestExitMenuArguments(),
        }[operation]
    values = dict(request_id="{:032x}".format(number), session_id=SESSION,
                  operation=operation, arguments=arguments)
    if operation != "observe":
        values.update(expected_zone_id="42", expected_household_id="84")
    values.update(changes)
    return queue.Request(**values)


def verified(_request):
    return {"outcome": "verified", "evidence": {"fixture_only": True}}


class QueueAssertions(unittest.TestCase):
    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(queue.QueueError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def run_thread(self, function):
        outcomes = []

        def run():
            try:
                outcomes.append((True, function()))
            except BaseException as error:
                outcomes.append((False, error))
        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive(), "Fixture worker did not finish.")
        self.assertEqual(1, len(outcomes))
        return outcomes[0]


class RequestTests(QueueAssertions):
    def test_all_four_typed_shapes(self):
        expected = [("observe", {}), ("live.set_speed", {"speed": 1}),
                    ("cas.enter", {"sim_id": str(queue.MAX_UINT64)}),
                    ("game.request_exit_menu", {})]
        for operation, arguments in expected:
            with self.subTest(operation=operation):
                self.assertEqual(arguments, request(operation=operation).arguments_dict())

    def test_requests_and_arguments_are_frozen(self):
        req = request()
        for instance, name, value in [(req, "operation", "observe"),
                                      (req.arguments, "speed", 3)]:
            with self.assertRaises(FrozenInstanceError):
                setattr(instance, name, value)
        detached = req.arguments_dict()
        detached["speed"] = 3
        self.assertEqual(1, req.arguments.speed)

    def test_rejects_malformed_request_and_session_ids(self):
        for value in [None, 1, True, "A" * 32, "a" * 31, "a" * 33, "a" * 31 + "g",
                      "a" * 32 + "\n", " " + "a" * 32]:
            for field in ("request_id", "session_id"):
                with self.subTest(value=value, field=field):
                    self.assert_code("invalid_id", request, **{field: value})

    def test_rejects_operation_not_exact_allowlisted_string(self):
        class Text(str):
            pass
        for value in [None, [], {}, 1, "execute", "Live.set_speed", "live.set_speed\n",
                      Text("live.set_speed")]:
            with self.subTest(value=value):
                self.assert_code("invalid_operation", replace, request(), operation=value)

    def test_rejects_wrong_or_mutable_argument_shapes(self):
        class Speed(queue.SetSpeedArguments):
            pass
        for value in [{"speed": 1}, None, queue.ObserveArguments(), Speed(1)]:
            with self.subTest(value=value):
                self.assert_code("invalid_arguments", replace, request(), arguments=value)
        with self.assertRaises(TypeError):
            queue.SetSpeedArguments(speed=1, extra="not allowed")

    def test_speed_is_exact_integer_zero_through_three(self):
        for value in [0, 1, 2, 3]:
            self.assertEqual(value, queue.SetSpeedArguments(value).speed)
        for value in [-1, 4, True, False, 1.0, "1", None]:
            self.assert_code("invalid_arguments", queue.SetSpeedArguments, value)

    def test_sim_id_is_exact_positive_uint64_decimal(self):
        for value in ["1", "9007199254740993", str(queue.MAX_UINT64)]:
            self.assertEqual(value, queue.EnterCasArguments(value).sim_id)
        for value in [1, True, 1.0, None, "", "0", "01", "+1", "-1", "1.0",
                      "1e3", " 1", "1 ", "1\n", "١", str(queue.MAX_UINT64 + 1),
                      "9" * 2000]:
            self.assert_code("invalid_arguments", queue.EnterCasArguments, value)

    def test_each_mutation_requires_both_exact_context_ids(self):
        for operation in ["live.set_speed", "cas.enter", "game.request_exit_menu"]:
            for field in ["expected_zone_id", "expected_household_id"]:
                for value in [None, 42, True, "0", "042", str(queue.MAX_UINT64 + 1)]:
                    with self.subTest(operation=operation, field=field, value=value):
                        self.assert_code("invalid_context", request, operation=operation,
                                         **{field: value})

    def test_observation_cannot_carry_mutation_context(self):
        self.assert_code("invalid_context", request, operation="observe", expected_zone_id="1")

    def test_ttl_is_finite_positive_and_bounded(self):
        for value in [0.001, 1, 30.0]:
            self.assertEqual(value, request(ttl_seconds=value).ttl_seconds)
        for value in [0, -1, 30.001, float("inf"), float("-inf"), float("nan"),
                      True, "5", None, 10 ** 1000]:
            self.assert_code("invalid_ttl", request, ttl_seconds=value)


class QueueLifecycleTests(QueueAssertions):
    def setUp(self):
        self.clock = Clock()
        self.queue = queue.CompanionQueue(SESSION, clock=self.clock)

    def test_constructor_configuration_is_bounded_and_read_only(self):
        for kwargs in [dict(pending_capacity=0), dict(pending_capacity=49),
                       dict(pending_capacity=True), dict(history_capacity=129),
                       dict(history_capacity=47), dict(history_capacity=True)]:
            self.assert_code("invalid_capacity", queue.CompanionQueue, SESSION, **kwargs)
        self.assert_code("invalid_id", queue.CompanionQueue, "wrong")
        self.assert_code("invalid_clock", queue.CompanionQueue, SESSION, clock=None)
        self.assertEqual(threading.current_thread().ident, self.queue.owner_thread_id)
        with self.assertRaises(AttributeError):
            self.queue.session_id = "b" * 32
        self.assertFalse(any(hasattr(self.queue, name) for name in ["reset", "rebind", "cancel", "wait"]))

    def test_submit_never_executes_and_status_is_an_immutable_snapshot(self):
        before = self.queue.submit(request())
        self.assertEqual("pending", before.state)
        self.assertEqual(100.0, before.admitted_at)
        self.assertEqual(105.0, before.expires_at)
        _snapshot_view = self.queue.status(request().request_id)
        self.assertEqual(before, _snapshot_view)
        self.assertIsNot(before, _snapshot_view)
        self.assertIsNone(self.queue.status("0" * 32))
        with self.assertRaises(FrozenInstanceError):
            before.state = "verified"
        final = self.queue.drain(verified)
        self.assertEqual("verified", final.state)
        self.assertEqual("pending", before.state)
        _snapshot_view = self.queue.status(request().request_id)
        self.assertEqual(final, _snapshot_view)
        self.assertIsNot(final, _snapshot_view)

    def test_submit_checks_request_type_and_session(self):
        self.assert_code("invalid_request", self.queue.submit, {})
        self.assert_code("session_mismatch", self.queue.submit, request(session_id="b" * 32))
        self.assertEqual(0, self.queue.queue_status().retained)

    def test_one_callback_per_drain_in_fifo_order(self):
        requests = [request(i) for i in range(1, 4)]
        for req in requests:
            self.queue.submit(req)
        calls = []
        for index in range(3):
            final = self.queue.drain(lambda req: calls.append(req) or verified(req))
            self.assertEqual(requests[index].request_id, final.request_id)
            self.assertEqual(index + 1, len(calls))
        self.assertEqual(requests, calls)
        self.assertIsNone(self.queue.drain(verified))

    def test_identical_replays_before_and_after_finish_do_not_execute_twice(self):
        req = request()
        first = self.queue.submit(req)
        _snapshot_view = self.queue.submit(replace(req))
        self.assertEqual(first, _snapshot_view)
        self.assertIsNot(first, _snapshot_view)
        calls = Mock(side_effect=verified)
        final = self.queue.drain(calls)
        self.clock.now = 1000000
        for _ in range(5):
            _snapshot_view = self.queue.submit(req)
            self.assertEqual(final, _snapshot_view)
            self.assertIsNot(final, _snapshot_view)
            self.assertIsNone(self.queue.drain(calls))
        self.assertEqual(1, calls.call_count)

    def test_every_payload_change_conflicts_before_and_after_completion(self):
        req = request()
        changes = [dict(arguments=queue.SetSpeedArguments(2)), dict(ttl_seconds=6),
                   dict(expected_zone_id="43"), dict(expected_household_id="85"),
                   dict(operation="game.request_exit_menu", arguments=queue.RequestExitMenuArguments())]
        self.queue.submit(req)
        for completed in [False, True]:
            if completed:
                self.queue.drain(verified)
            for change in changes:
                self.assert_code("request_conflict", self.queue.submit, replace(req, **change))

    def test_exact_ttl_boundary_refuses_without_callback(self):
        req = request()
        self.queue.submit(req)
        self.clock.now = 105.0
        execute = Mock()
        final = self.queue.drain(execute)
        self.assertEqual(("refused", "expired"), (final.state, final.reason))
        execute.assert_not_called()
        _snapshot_view = self.queue.submit(req)
        self.assertEqual(final, _snapshot_view)
        self.assertIsNot(final, _snapshot_view)

    def test_just_before_ttl_can_start_but_expiry_does_not_cancel_started_work(self):
        self.queue.submit(request())
        self.clock.now = 104.999

        def execute(req):
            self.clock.now = 1000
            self.assertEqual("started", self.queue.status(req.request_id).state)
            return verified(req)
        final = self.queue.drain(execute)
        self.assertEqual("verified", final.state)
        self.assertEqual(104.999, final.started_at)

    def test_status_and_duplicates_neither_refresh_ttl_nor_expire_work(self):
        req = request()
        first = self.queue.submit(req)
        calls = self.clock.calls
        self.clock.now = 1000
        _snapshot_view = self.queue.status(req.request_id)
        self.assertEqual(first, _snapshot_view)
        self.assertIsNot(first, _snapshot_view)
        _snapshot_view = self.queue.submit(req)
        self.assertEqual(first, _snapshot_view)
        self.assertIsNot(first, _snapshot_view)
        self.assertEqual(calls, self.clock.calls)
        self.assertEqual("pending", self.queue.status(req.request_id).state)
        self.assertEqual("expired", self.queue.drain(verified).reason)

    def test_expired_head_consumes_tick_without_dispatching_next_entry(self):
        self.queue.submit(request(1, ttl_seconds=1))
        self.queue.submit(request(2, ttl_seconds=10))
        self.clock.now += 2
        calls = Mock(side_effect=verified)
        self.assertEqual("expired", self.queue.drain(calls).reason)
        calls.assert_not_called()
        self.assertEqual("verified", self.queue.drain(calls).state)
        self.assertEqual(1, calls.call_count)

    def test_pending_full_refusal_is_retained_and_never_reconsidered(self):
        small = queue.CompanionQueue(SESSION, pending_capacity=1, history_capacity=3, clock=self.clock)
        small.submit(request(1))
        refused = small.submit(request(2))
        self.assertEqual("pending_full", refused.reason)
        small.drain(verified)
        _snapshot_view = small.submit(request(2))
        self.assertEqual(refused, _snapshot_view)
        self.assertIsNot(refused, _snapshot_view)
        self.assertIsNone(small.drain(verified))
        self.assertEqual("pending", small.submit(request(3)).state)
        self.assert_code("request_conflict", small.submit, request(2, ttl_seconds=6))

    def test_defaults_bound_pending_at_48_and_history_at_128(self):
        snapshots = [self.queue.submit(request(i)) for i in range(1, 131)]
        self.assertEqual(48, sum(item.state == "pending" for item in snapshots))
        self.assertEqual(80, sum(item.reason == "pending_full" for item in snapshots))
        self.assertEqual(2, sum(item.reason == "history_full" for item in snapshots))
        self.assertEqual(128, self.queue.queue_status().retained)

    def test_full_history_keeps_old_ids_forever_and_refuses_all_new_work(self):
        calls = Mock(side_effect=verified)
        for index in range(1, 129):
            self.queue.submit(request(index))
            self.queue.drain(calls)
        oldest = self.queue.status(request(1).request_id)
        self.clock.now += 100000
        for index in range(129, 300):
            self.assertEqual("history_full", self.queue.submit(request(index)).reason)
        _snapshot_view = self.queue.submit(request(1))
        self.assertEqual(oldest, _snapshot_view)
        self.assertIsNot(oldest, _snapshot_view)
        self.assert_code("request_conflict", self.queue.submit, request(1, ttl_seconds=6))
        self.assertIsNone(self.queue.drain(calls))
        self.assertEqual(128, calls.call_count)
        self.assertEqual(128, self.queue.queue_status().retained)
        self.assertTrue(self.queue.queue_status().history_exhausted)

    def test_empty_ticks_have_no_clock_executor_or_io_activity(self):
        clock = Mock(side_effect=AssertionError("empty tick touched clock"))
        empty = queue.CompanionQueue(SESSION, clock=clock)
        execute = Mock(side_effect=AssertionError("empty tick executed"))
        with patch("builtins.open", side_effect=AssertionError("I/O")), \
             patch("builtins.print", side_effect=AssertionError("I/O")):
            for _ in range(100):
                self.assertIsNone(empty.drain(execute))
        clock.assert_not_called()
        execute.assert_not_called()

    def test_clock_backward_nonfinite_or_exception_refuses_pending(self):
        for value in [99, float("nan"), float("inf"), -1, True, "100"]:
            clock = Clock()
            local = queue.CompanionQueue(SESSION, clock=clock)
            local.submit(request())
            clock.now = value
            execute = Mock()
            self.assertEqual("invalid_clock", local.drain(execute).reason)
            execute.assert_not_called()
            self.assertIsNone(local.drain(execute))
        local = queue.CompanionQueue(SESSION, clock=Mock(side_effect=[100, RuntimeError("clock")]))
        local.submit(request())
        self.assertEqual("invalid_clock", local.drain(verified).reason)

    def test_invalid_submission_clock_does_not_admit(self):
        for value in [float("nan"), float("inf"), -1, None, True, 1e100, 10 ** 1000]:
            local = queue.CompanionQueue(SESSION, clock=Clock(value))
            self.assert_code("invalid_clock", local.submit, request())
            self.assertEqual(0, local.queue_status().retained)

    def test_clock_interrupt_before_start_leaves_request_pending_without_execution(self):
        local = queue.CompanionQueue(SESSION, clock=Mock(side_effect=[100, KeyboardInterrupt(), 101, 101]))
        local.submit(request())
        callback = Mock(side_effect=verified)
        with self.assertRaises(KeyboardInterrupt):
            local.drain(callback)
        callback.assert_not_called()
        self.assertEqual("pending", local.status(request().request_id).state)
        self.assertEqual(1, local.queue_status().pending)
        self.assertEqual("verified", local.drain(callback).state)
        callback.assert_called_once()


class ExecutionContractTests(QueueAssertions):
    def make_queue(self):
        local = queue.CompanionQueue(SESSION, clock=Clock())
        local.submit(request())
        return local

    def test_verified_and_unverified_are_both_terminal(self):
        for outcome in ["verified", "unverified"]:
            local = self.make_queue()
            final = local.drain(lambda req: {"outcome": outcome, "mutation_may_have_occurred": True})
            self.assertEqual(outcome, final.state)
            _snapshot_view = local.submit(request())
            self.assertEqual(final, _snapshot_view)
            self.assertIsNot(final, _snapshot_view)
            self.assertIsNone(local.drain(verified))

    def test_result_is_detached_and_deeply_immutable(self):
        original = {"outcome": "verified", "detail": {"items": [1, {"yes": True}]}}
        local = self.make_queue()
        final = local.drain(lambda req: original)
        original["detail"]["items"][1]["yes"] = False
        self.assertTrue(final.result["detail"]["items"][1]["yes"])
        with self.assertRaises(TypeError):
            final.result["outcome"] = "unverified"
        with self.assertRaises(TypeError):
            final.result["detail"]["items"][1]["yes"] = False
        self.assertIsInstance(final.result["detail"]["items"], tuple)

    def test_bad_results_become_unknown_and_are_never_requeued(self):
        cyclic = []
        cyclic.append(cyclic)
        class DictSubclass(dict):
            pass
        malformed = [None, True, "verified", {}, {"success": True}, {"outcome": "started"},
                     {"outcome": "refused"}, {"outcome": True}, DictSubclass(outcome="verified"),
                     {"outcome": "verified", "detail": object()},
                     {"outcome": "verified", "detail": b"bytes"},
                     {"outcome": "verified", "detail": (1,)},
                     {"outcome": "verified", "detail": float("nan")},
                     {"outcome": "verified", "detail": 2**64},
                     {"outcome": "verified", 1: "non-string-key"},
                     {"outcome": "verified", "detail": cyclic},
                     {"outcome": "verified", "detail": "x" * (queue.MAX_RESULT_TEXT + 1)},
                     {"outcome": "verified", "detail": [1] * queue.MAX_RESULT_NODES}]
        for index, raw in enumerate(malformed):
            with self.subTest(kind=type(raw), index=index):
                local = self.make_queue()
                execute = Mock(return_value=raw)
                final = local.drain(execute)
                self.assertEqual(("unknown", "invalid_execution_result"), (final.state, final.reason))
                self.assertIsNone(final.result)
                _snapshot_view = local.submit(request())
                self.assertEqual(final, _snapshot_view)
                self.assertIsNot(final, _snapshot_view)
                self.assertIsNone(local.drain(execute))
                execute.assert_called_once()

    def test_callback_exception_is_unknown_without_retaining_exception_text(self):
        local = self.make_queue()
        final = local.drain(Mock(side_effect=RuntimeError("private diagnostic should not persist")))
        self.assertEqual(("unknown", "execution_exception"), (final.state, final.reason))
        self.assertNotIn("private", repr(final))
        _snapshot_view = local.submit(request())
        self.assertEqual(final, _snapshot_view)
        self.assertIsNot(final, _snapshot_view)
        self.assertIsNone(local.drain(verified))

    def test_process_control_exceptions_record_unknown_then_propagate(self):
        for error in [KeyboardInterrupt(), SystemExit(1)]:
            local = self.make_queue()
            with self.assertRaises(type(error)):
                local.drain(Mock(side_effect=error))
            self.assertEqual("unknown", local.status(request().request_id).state)
            self.assertIsNone(local.drain(verified))

    def test_started_duplicate_and_status_are_observational(self):
        local = self.make_queue()

        def execute(req):
            snapshot = local.status(req.request_id)
            self.assertEqual("started", snapshot.state)
            _snapshot_view = local.submit(req)
            self.assertEqual(snapshot, _snapshot_view)
            self.assertIsNot(snapshot, _snapshot_view)
            self.assert_code("request_conflict", local.submit, replace(req, ttl_seconds=10))
            return verified(req)
        self.assertEqual("verified", local.drain(execute).state)

    def test_reentrant_drain_cannot_dispatch_second_request(self):
        local = self.make_queue()
        local.submit(request(2))
        nested = Mock(side_effect=verified)

        def execute(req):
            self.assert_code("reentrant_drain", local.drain, nested)
            return verified(req)
        self.assertEqual("verified", local.drain(execute).state)
        nested.assert_not_called()
        self.assertEqual("pending", local.status(request(2).request_id).state)


class OwnerPreflightTests(QueueAssertions):
    def setUp(self):
        self.clock = Clock()
        self.queue = queue.CompanionQueue(SESSION, clock=self.clock)
        self.queue.submit(request())

    def test_preflight_runs_unlocked_before_started_and_false_is_known_refusal(self):
        def admission(req):
            self.assertTrue(self.queue._lock.acquire(False))
            self.queue._lock.release()
            self.assertEqual("pending", self.queue.status(req.request_id).state)
            return False
        native = Mock()
        final = self.queue.drain(native, admit=admission)
        self.assertEqual(("refused", "host_dispatch_denied", None),
                         (final.state, final.reason, final.started_at))
        native.assert_not_called()
        self.assertIsNone(self.queue.drain(native))

    def test_preflight_renewal_context_change_and_nontrue_never_dispatch(self):
        for action, reason in [(lambda: setattr(self.clock, "now", 110), "expired"),
                               (lambda: self.queue.block_mutations("context_changed"), "context_changed"),
                               (lambda: self.queue.close(), "host_shutdown")]:
            self.setUp()
            def admission(req):
                action()
                return True
            native = Mock()
            final = self.queue.drain(native, admit=admission)
            self.assertEqual(("refused", reason, None), (final.state, final.reason, final.started_at))
            native.assert_not_called()
        self.setUp()
        self.assertEqual("refused", self.queue.drain(Mock(), admit=lambda _: 1).state)

    def test_preflight_queue_lock_contention_refuses_without_requeue_or_owner_wait(self):
        release, held = threading.Event(), threading.Event()
        def lock_reader():
            with self.queue._lock:
                held.set()
                release.wait(3)
        worker = threading.Thread(target=lock_reader)
        def admit(_):
            worker.start()
            self.assertTrue(held.wait(1))
            return True
        native = Mock()
        try:
            final = self.queue.drain(native, admit=admit)
            self.assertEqual(("refused", "queue_busy_before_start"), (final.state, final.reason))
            self.assertTrue(worker.is_alive())
        finally:
            release.set(); worker.join(1)
        _snapshot_view = self.queue.status(request().request_id)
        self.assertEqual(final, _snapshot_view)
        self.assertIsNot(final, _snapshot_view)
        self.assertIsNone(self.queue.drain(native))
        native.assert_not_called()

    def test_preflight_exception_and_interrupt_record_only_prestart_refusal(self):
        for error in (RuntimeError("fixture"), KeyboardInterrupt()):
            self.setUp()
            native = Mock()
            if isinstance(error, Exception):
                final = self.queue.drain(native, admit=Mock(side_effect=error))
            else:
                with self.assertRaises(KeyboardInterrupt):
                    self.queue.drain(native, admit=Mock(side_effect=error))
                final = self.queue.status(request().request_id)
            self.assertEqual(("refused", None), (final.state, final.started_at))
            native.assert_not_called()
            self.assertIsNone(self.queue.drain(native))

    def test_second_clock_interrupt_before_start_has_no_requeue(self):
        self.queue._clock = Mock(side_effect=[101, KeyboardInterrupt()])
        native = Mock()
        with self.assertRaises(KeyboardInterrupt):
            self.queue.drain(native, admit=lambda _: True)
        final = self.queue.status(request().request_id)
        self.assertEqual(("refused", "invalid_clock", None), (final.state, final.reason, final.started_at))
        self.assertIsNone(self.queue.drain(native))
        native.assert_not_called()

    def test_process_and_save_context_participate_in_exact_payload_deduplication(self):
        local = queue.CompanionQueue(SESSION)
        base = request(process_epoch=queue.ProcessEpoch(100, 200), expected_save_slot=4, expected_save_guid="99")
        local.submit(base)
        for changes in (dict(process_epoch=queue.ProcessEpoch(100, 201)),
                        dict(expected_save_slot=5), dict(expected_save_guid="100")):
            self.assert_code("request_conflict", local.submit, replace(base, **changes))
        self.assertEqual("request_conflict", local.drain(Mock()).reason)

    def test_unknown_and_unverified_latch_blocks_new_and_already_pending_mutations(self):
        for callback in (Mock(side_effect=RuntimeError()), lambda _: {"outcome": "unverified"}):
            self.setUp()
            self.queue.submit(request(2))
            self.queue.drain(callback)
            self.assertEqual("unresolved_mutation", self.queue.submit(request(3)).reason)
            native = Mock()
            self.assertEqual("unresolved_mutation", self.queue.drain(native).reason)
            native.assert_not_called()
            self.assertEqual("pending", self.queue.submit(request(4, operation="observe")).state)
            self.queue.drain(verified)
            self.assertEqual("unresolved_mutation", self.queue.submit(request(5)).reason)


class ThreadingTests(QueueAssertions):
    def test_worker_submit_is_allowed_but_worker_drain_never_calls_native(self):
        local = queue.CompanionQueue(SESSION)
        ok, result = self.run_thread(lambda: local.submit(request()))
        self.assertTrue(ok)
        self.assertEqual("pending", result.state)
        callback = Mock(side_effect=verified)
        ok, error = self.run_thread(lambda: local.drain(callback))
        self.assertFalse(ok)
        self.assertEqual("wrong_thread", error.code)
        callback.assert_not_called()
        self.assertEqual("verified", local.drain(callback).state)

    def test_recycled_ident_does_not_replace_captured_thread_object(self):
        local = queue.CompanionQueue(SESSION)
        impostor = Mock(ident=local.owner_thread_id)
        with patch.object(queue.threading, "current_thread", return_value=impostor):
            self.assert_code("wrong_thread", local.drain, verified)

    def test_owner_never_waits_on_queue_lock_for_any_operation(self):
        for operation in [lambda q: q.submit(request()), lambda q: q.status(request().request_id),
                          lambda q: q.queue_status(), lambda q: q.drain(verified)]:
            local = queue.CompanionQueue(SESSION)
            lock = Mock()
            lock.acquire.return_value = False
            local._lock = lock
            self.assert_code("queue_busy", operation, local)
            lock.acquire.assert_called_once_with(blocking=False)

    def test_many_identical_concurrent_submissions_admit_and_dispatch_once(self):
        local = queue.CompanionQueue(SESSION)
        gate = threading.Barrier(25)
        outcomes = []

        def submit():
            gate.wait(timeout=3)
            outcomes.append(local.submit(request()))
        workers = [threading.Thread(target=submit) for _ in range(24)]
        for worker in workers:
            worker.start()
        gate.wait(timeout=3)
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(24, len(outcomes))
        self.assertEqual(1, local.queue_status().retained)
        self.assertTrue(all(item == outcomes[0] for item in outcomes))
        self.assertEqual(len(outcomes), len({id(item) for item in outcomes}))
        execute = Mock(side_effect=verified)
        local.drain(execute)
        self.assertIsNone(local.drain(execute))
        execute.assert_called_once()

    def test_conflicting_concurrent_payloads_retain_one_intent_but_block_dispatch(self):
        local = queue.CompanionQueue(SESSION)
        gate = threading.Barrier(3)
        accepted, rejected = [], []

        def submit(speed):
            gate.wait(timeout=3)
            try:
                accepted.append((speed, local.submit(request(arguments=queue.SetSpeedArguments(speed)))))
            except queue.QueueError as error:
                rejected.append(error.code)
        workers = [threading.Thread(target=submit, args=(speed,)) for speed in [1, 2]]
        for worker in workers:
            worker.start()
        gate.wait(timeout=3)
        for worker in workers:
            worker.join(timeout=3)
        self.assertEqual(1, len(accepted))
        self.assertEqual(["request_conflict"], rejected)
        seen = []
        local.drain(lambda req: seen.append(req.arguments.speed) or verified(req))
        self.assertEqual([], seen)
        self.assertEqual("request_conflict", local.status(request().request_id).reason)
        self.assertEqual("request_conflict", local.queue_status().mutation_blocked_reason)

    def test_concurrent_unique_submissions_cannot_overflow_bounds(self):
        local = queue.CompanionQueue(SESSION, pending_capacity=4, history_capacity=8)
        gate = threading.Barrier(33)
        outcomes = []

        def submit(index):
            gate.wait(timeout=3)
            outcomes.append(local.submit(request(index)))
        workers = [threading.Thread(target=submit, args=(index,)) for index in range(1, 33)]
        for worker in workers:
            worker.start()
        gate.wait(timeout=3)
        for worker in workers:
            worker.join(timeout=3)
        self.assertEqual(32, len(outcomes))
        self.assertEqual(4, local.queue_status().pending)
        self.assertEqual(8, local.queue_status().retained)
        self.assertEqual(24, sum(item.reason == "history_full" for item in outcomes))

    def test_workers_can_read_and_submit_while_callback_is_running(self):
        local = queue.CompanionQueue(SESSION)
        local.submit(request())

        def execute(req):
            def read_and_submit():
                before = local.status(req.request_id)
                duplicate = local.submit(req)
                later = local.submit(request(2))
                return before, duplicate, later
            ok, values = self.run_thread(read_and_submit)
            self.assertTrue(ok, values)
            self.assertEqual(["started", "started", "pending"], [item.state for item in values])
            return verified(req)
        self.assertEqual("verified", local.drain(execute).state)
        self.assertEqual("pending", local.status(request(2).request_id).state)

    def test_finishing_owner_does_not_wait_for_reader_lock_and_completion_survives(self):
        local = queue.CompanionQueue(SESSION)
        local.submit(request())
        locked, release = threading.Event(), threading.Event()

        def hold_lock():
            with local._lock:
                locked.set()
                release.wait(timeout=3)

        worker = threading.Thread(target=hold_lock)

        def execute(req):
            worker.start()
            self.assertTrue(locked.wait(timeout=3))
            return verified(req)
        try:
            final = local.drain(execute)
            self.assertEqual("verified", final.state)
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join(timeout=3)
        _snapshot_view = local.status(request().request_id)
        self.assertEqual(final, _snapshot_view)
        self.assertIsNot(final, _snapshot_view)
        _snapshot_view = local.submit(request())
        self.assertEqual(final, _snapshot_view)
        self.assertIsNot(final, _snapshot_view)
        self.assertIsNone(local.drain(verified))


class PackagingTests(unittest.TestCase):
    def test_source_parses_as_python_37_not_a_runtime_qualification(self):
        if sys.version_info[:2] == (3, 7):
            ast.parse(SOURCE.read_text(), filename=str(SOURCE))
        else:
            ast.parse(SOURCE.read_text(), filename=str(SOURCE), feature_version=(3, 7))

    def test_dedicated_import_has_no_cli_or_native_namespace_side_effect(self):
        script = """
import importlib.util, pathlib, sys
before = list(sys.path)
spec = importlib.util.spec_from_file_location('_isolated_companion_queue', sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert before == sys.path
for name in ('sims4foundry_cli', 'services', 'sims4', 'server_commands', 'foundry_companion.native'):
    assert name not in sys.modules, name
"""
        result = subprocess.run([sys.executable, "-I", "-c", script, str(SOURCE)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_source_declares_volatile_external_gates_and_no_reset(self):
        text = SOURCE.read_text()
        for phrase in ["external durable", "authentication", "profile lease", "current-build",
                       "Never replay", "History never evicts", "Python 3.7 syntax"]:
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
