"""Offline protocol fixtures, not transport, authentication, or game evidence."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import ast
import inspect
import json
import random
import struct
import unittest

from sims4foundry_cli import companion_protocol as protocol
from sims4foundry_cli.companion_protocol import (
    MAX_FEED_BYTES, MAX_FRAME_BYTES, MAX_INTEGER, MAX_JSON_DEPTH, MAX_JSON_NODES,
    EnterCasArguments, EvidenceReference, FrameDecoder, ObserveArguments,
    Operation, ProcessEpoch, ProtocolError, ReplayDisposition, Request,
    RequestExitMenuArguments, Response, ResponseState, SetSpeedArguments,
    classify_replay, decode_payload, encode_frame, parse_request, parse_response,
    request_fingerprint, response_for, validate_context, validate_response_transition,
)


REQUEST_ID = "1" * 32
SESSION_ID = "2" * 32
EPOCH = ProcessEpoch(1234, 133000000000000000)
EVIDENCE = EvidenceReference("3" * 32, "4" * 64)


def request(operation=Operation.SET_SPEED, arguments=None):
    if arguments is None:
        arguments = {
            Operation.OBSERVE: ObserveArguments(),
            Operation.SET_SPEED: SetSpeedArguments(2),
            Operation.ENTER_CAS: EnterCasArguments("1234567890123456789"),
            Operation.REQUEST_EXIT_MENU: RequestExitMenuArguments(),
        }[operation]
    return Request(REQUEST_ID, SESSION_ID, EPOCH, operation, arguments)


def parse(value):
    return parse_request(value, session_id=SESSION_ID, process_epoch=EPOCH)


def frame(payload):
    return struct.pack(">I", len(payload)) + payload


def response(state, req=None):
    return response_for(req or request(), state,
                        evidence=EVIDENCE if state is ResponseState.VERIFIED else None,
                        reason="fixture_reason" if state in (ResponseState.REFUSED, ResponseState.UNKNOWN) else None)


class ProtocolAssertions(unittest.TestCase):
    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(ProtocolError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)


class FramingTests(ProtocolAssertions):
    def test_prefix_is_payload_byte_count_big_endian(self):
        value = {"text": "雪😀"}
        encoded = encode_frame(value)
        self.assertEqual(len(encoded) - 4, struct.unpack(">I", encoded[:4])[0])
        self.assertNotEqual(len(encoded[4:].decode("utf-8")), len(encoded) - 4)
        self.assertEqual(value, decode_payload(encoded[4:]))

    def test_every_single_split_of_request_and_utf8_frame(self):
        for value in (request().to_wire(), {"snow": "雪", "emoji": "😀"}):
            encoded = encode_frame(value)
            for split in range(len(encoded) + 1):
                with self.subTest(split=split):
                    decoder = FrameDecoder()
                    result = decoder.feed(encoded[:split]) + decoder.feed(encoded[split:])
                    self.assertEqual((value,), result)
                    self.assertEqual(0, decoder.buffered_bytes)
                    decoder.finish()

    def test_byte_at_a_time(self):
        value = request(Operation.ENTER_CAS).to_wire()
        decoder = FrameDecoder()
        observed = []
        for byte in encode_frame(value):
            observed.extend(decoder.feed(bytes([byte])))
            self.assertLessEqual(decoder.buffered_bytes, MAX_FRAME_BYTES)
        self.assertEqual([value], observed)
        decoder.finish()

    def test_coalesced_frames_and_partial_next_prefix(self):
        values = [req.to_wire() for req in (request(op) for op in Operation)]
        encoded = b"".join(encode_frame(value) for value in values)
        decoder = FrameDecoder()
        self.assertEqual(tuple(values), decoder.feed(encoded + b"\x00\x00"))
        self.assertEqual(2, decoder.buffered_bytes)
        self.assertEqual(({},), decoder.feed(b"\x00\x02{}"))
        decoder.finish()

    def test_deterministic_random_fragmentation_and_coalescing(self):
        values = [{"index": i, "text": "escaped \\\" [ { 雪"} for i in range(80)]
        wire = b"".join(encode_frame(value) for value in values)
        for seed in range(8):
            rng = random.Random(seed)
            decoder = FrameDecoder()
            observed = []
            offset = 0
            while offset < len(wire):
                width = rng.randint(1, 501)
                observed.extend(decoder.feed(wire[offset:offset + width]))
                offset += width
            self.assertEqual(values, observed)
            decoder.finish()

    def test_exact_maximum_payload_and_one_byte_over(self):
        value = {"x": "x" * (MAX_FRAME_BYTES - 8)}
        encoded = encode_frame(value)
        self.assertEqual(MAX_FEED_BYTES, len(encoded))
        decoder = FrameDecoder()
        self.assertEqual((value,), decoder.feed(encoded))
        decoder.finish()
        self.assert_code("oversized_frame", encode_frame, {"x": "x" * (MAX_FRAME_BYTES - 7)})

    def test_zero_oversized_and_uint32_lengths_poison_before_body(self):
        for length in (0, MAX_FRAME_BYTES + 1, 2**32 - 1):
            with self.subTest(length=length):
                decoder = FrameDecoder()
                self.assert_code("invalid_frame", decoder.feed, struct.pack(">I", length))
                self.assertTrue(decoder.failed)
                self.assertEqual(0, decoder.buffered_bytes)
                self.assert_code("decoder_failed", decoder.feed, encode_frame({}))
                self.assert_code("decoder_failed", decoder.finish)

    def test_header_checked_as_soon_as_final_prefix_byte_arrives(self):
        decoder = FrameDecoder()
        header = struct.pack(">I", MAX_FRAME_BYTES + 1)
        self.assertEqual((), decoder.feed(header[:3]))
        self.assert_code("invalid_frame", decoder.feed, header[3:])

    def test_malformed_frame_poisoning_drops_same_feed_prefix(self):
        decoder = FrameDecoder()
        self.assert_code("invalid_json", decoder.feed,
                         encode_frame({"valid": True}) + frame(b'{"x":NaN}') + encode_frame({}))
        self.assertTrue(decoder.failed)
        self.assert_code("decoder_failed", decoder.feed, encode_frame({}))

    def test_partial_eof_never_infers_a_result(self):
        wire = encode_frame({"x": 1})
        for cut in range(1, len(wire)):
            with self.subTest(cut=cut):
                decoder = FrameDecoder()
                self.assertEqual((), decoder.feed(wire[:cut]))
                self.assert_code("truncated_frame", decoder.finish)
                self.assertTrue(decoder.failed)
                self.assertEqual(0, decoder.buffered_bytes)

    def test_clean_eof_and_empty_feed(self):
        for wire in (b"", encode_frame({})):
            decoder = FrameDecoder()
            decoder.feed(wire)
            self.assertEqual((), decoder.feed(b""))
            decoder.finish()
            self.assert_code("decoder_closed", decoder.feed, b"")
            self.assert_code("decoder_closed", decoder.finish)

    def test_feed_bound_applies_even_to_many_small_coalesced_frames(self):
        decoder = FrameDecoder()
        self.assert_code("oversized_feed", decoder.feed, b"x" * (MAX_FEED_BYTES + 1))
        self.assertEqual(0, decoder.buffered_bytes)
        self.assertTrue(decoder.failed)
        decoder = FrameDecoder()
        count = MAX_FEED_BYTES // 6
        self.assertEqual(count, len(decoder.feed(encode_frame({}) * count)))
        decoder.finish()

    def test_feed_input_types_and_bytearray_snapshot(self):
        value = bytearray(encode_frame({"x": 1}))
        decoder = FrameDecoder()
        self.assertEqual((), decoder.feed(value[:5]))
        self.assertEqual(({"x": 1},), decoder.feed(value[5:]))
        value[:] = b"garbage"
        decoder.finish()
        for bad in ("{}", None, 1, [0, 0, 0, 2, 123, 125], memoryview(b"{}")):
            decoder = FrameDecoder()
            self.assert_code("invalid_frame", decoder.feed, bad)
            self.assertTrue(decoder.failed)

    def test_smaller_local_frame_limit_and_invalid_limits(self):
        decoder = FrameDecoder(max_frame_bytes=2)
        self.assertEqual(({}, {}), decoder.feed(encode_frame({}) * 2))
        with self.assertRaises(AttributeError):
            decoder.max_frame_bytes = MAX_FRAME_BYTES + 1
        self.assert_code("invalid_frame", decoder.feed, encode_frame({"x": 0})[:4])
        for bound in (0, -1, True, False, 2.0, "2", MAX_FRAME_BYTES + 1):
            self.assert_code("invalid_bound", FrameDecoder, max_frame_bytes=bound)


class StrictJsonTests(ProtocolAssertions):
    def test_duplicate_keys_at_every_level_and_escaped_alias(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":{"id":1,"id":2}}',
                    b'{"a":[{"id":1,"id":2}]}', b'{"x":1,"\\u0078":2}'):
            self.assert_code("invalid_json", decode_payload, raw)

    def test_nonfinite_literals_and_exponent_overflow(self):
        for value in (b"NaN", b"Infinity", b"-Infinity", b"1e999", b"-1e999"):
            self.assert_code("invalid_json", decode_payload, b'{"x":' + value + b"}")
        for value in (float("nan"), float("inf"), float("-inf")):
            self.assert_code("invalid_json", encode_frame, {"x": value})
        self.assertEqual({"x": 1e308}, decode_payload(b'{"x":1e308}'))

    def test_integer_limits_and_no_global_python_setting_changes(self):
        for value in (-MAX_INTEGER, MAX_INTEGER):
            self.assertEqual({"x": value}, decode_payload(encode_frame({"x": value})[4:]))
        for value in (MAX_INTEGER + 1, -MAX_INTEGER - 1):
            self.assert_code("invalid_json", encode_frame, {"x": value})
            self.assert_code("invalid_json", decode_payload, ('{"x":%s}' % value).encode())
        self.assert_code("invalid_json", decode_payload, b'{"x":' + b"9" * 5000 + b"}")

    def test_invalid_utf8_bom_utf16_and_surrogates(self):
        for raw in (b'{"x":"\xff"}', b'{"x":"\xc0\xaf"}', b'{"x":"\xed\xa0\x80"}',
                    b'{"x":"\xf0\x9f"}', b"\xef\xbb\xbf{}", "{}".encode("utf-16"),
                    b'{"x":"\\ud800"}', b'{"\\udfff":1}'):
            self.assert_code("invalid_json", decode_payload, raw)
        self.assert_code("invalid_json", encode_frame, {"x": "\ud800"})
        self.assertEqual({"x": "😀"}, decode_payload(b'{"x":"\\ud83d\\ude00"}'))

    def test_only_one_object_and_no_trailing_nonwhitespace(self):
        for raw in (b"null", b"true", b"3", b'"text"', b"[]", b"{}{}", b"{}x", b" ",
                    b'{"x":1,}', b'{"x":01}', b'{"x":"\n"}', b'{/*comment*/}'):
            self.assert_code("invalid_json", decode_payload, raw)
        self.assertEqual({}, decode_payload(b" \n {}\t\r"))

    def test_payload_type_and_byte_bounds(self):
        for raw in ("{}", bytearray(b"{}"), b"", b" " * (MAX_FRAME_BYTES + 1)):
            self.assert_code("invalid_frame", decode_payload, raw)

    def test_encoding_rejects_non_json_keys_containers_and_values(self):
        for value in ([], None, "{}", {1: "x"}, {"x": (1,)}, {"x": {1}}, {"x": b"a"}, {"x": object()}):
            self.assert_code("invalid_json", encode_frame, value)
        class Dictionary(dict):
            pass
        self.assert_code("invalid_json", encode_frame, Dictionary(x=1))

    def test_nesting_bound_and_cycles(self):
        value = 1
        for _ in range(MAX_JSON_DEPTH - 1):
            value = [value]
        self.assertEqual({"x": value}, decode_payload(encode_frame({"x": value})[4:]))
        self.assert_code("invalid_json", encode_frame, {"x": [value]})
        self.assert_code("invalid_json", decode_payload,
                         b'{"x":' + b"[" * MAX_JSON_DEPTH + b"1" + b"]" * MAX_JSON_DEPTH + b"}")
        cyclic = {}
        cyclic["x"] = cyclic
        self.assert_code("invalid_json", encode_frame, cyclic)

    def test_quoted_brackets_do_not_count_as_nesting(self):
        value = {"text": '["{}\\' * 100}
        self.assertEqual(value, decode_payload(encode_frame(value)[4:]))

    def test_node_count_bound_on_encode_and_decode(self):
        # Root object, its key, and the list consume three nodes.
        value = {"x": [0] * (MAX_JSON_NODES - 3)}
        self.assertEqual(value, decode_payload(encode_frame(value)[4:]))
        self.assert_code("invalid_json", encode_frame, {"x": [0] * (MAX_JSON_NODES - 2)})
        self.assert_code("invalid_json", decode_payload, json.dumps({"x": [0] * MAX_JSON_NODES}).encode())

    def test_total_string_and_serialized_escape_bounds(self):
        self.assert_code("oversized_frame", encode_frame, {"x": "a" * (MAX_FRAME_BYTES + 1)})
        self.assert_code("oversized_frame", encode_frame, {"x": ["a" * 40000, "b" * 40000]})
        self.assert_code("oversized_frame", encode_frame, {"x": "\0" * 12000})

    def test_canonical_encoding_is_deterministic(self):
        self.assertEqual(encode_frame({"b": 2, "a": {"d": 4, "c": 3}}),
                         encode_frame({"a": {"c": 3, "d": 4}, "b": 2}))


class RequestTests(ProtocolAssertions):
    def test_every_allowlisted_request_round_trips(self):
        self.assertEqual({"observe", "live.set_speed", "cas.enter", "game.request_exit_menu"},
                         {op.value for op in Operation})
        for op in Operation:
            original = request(op)
            wire, = FrameDecoder().feed(encode_frame(original.to_wire()))
            self.assertEqual(original, parse(wire))

    def test_exact_envelope_fields(self):
        original = request().to_wire()
        for key in original:
            value = deepcopy(original)
            del value[key]
            self.assert_code("invalid_envelope", parse, value)
        for key in ("token", "qualified", "execute", "history_complete", "command", "raw_input"):
            self.assert_code("invalid_envelope", parse, original | {key: True})
        for value in (None, [], "{}"):
            self.assert_code("invalid_envelope", parse, value)

    def test_version_and_kind_are_strict(self):
        original = request().to_wire()
        for schema in (True, False, 0, 2, 1.0, "1", None):
            self.assert_code("invalid_envelope", parse, original | {"schema": schema})
        for kind in ("response", "Request", 1, None):
            self.assert_code("invalid_envelope", parse, original | {"kind": kind})

    def test_request_and_session_ids_are_bounded_lowercase_hex(self):
        original = request().to_wire()
        for name in ("request_id", "session_id"):
            for value in ("", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "0" * 31 + "\n", 123, True):
                self.assert_code("invalid_identity", parse, original | {name: value})

    def test_epoch_type_ranges_and_exact_fields(self):
        for pid in (0, -1, True, 1.0, "1", 2**32):
            self.assert_code("invalid_epoch", ProcessEpoch, pid, 1)
        for created in (0, -1, True, 1.0, "1", MAX_INTEGER + 1):
            self.assert_code("invalid_epoch", ProcessEpoch, 1, created)
        self.assertEqual({"pid": 2**32 - 1, "creation_filetime": MAX_INTEGER},
                         ProcessEpoch(2**32 - 1, MAX_INTEGER).to_wire())
        for epoch in ({"pid": 1}, {"pid": 1, "creation_filetime": 1, "trusted": True}, None, 1):
            self.assert_code("invalid_epoch", parse, request().to_wire() | {"process_epoch": epoch})

    def test_wrong_session_pid_and_creation_epoch_fail(self):
        for replacement in ({"session_id": "f" * 32},
                            {"process_epoch": ProcessEpoch(EPOCH.pid + 1, EPOCH.creation_filetime).to_wire()},
                            {"process_epoch": ProcessEpoch(EPOCH.pid, EPOCH.creation_filetime + 1).to_wire()}):
            self.assert_code("context_mismatch", parse, request().to_wire() | replacement)
        self.assert_code("context_mismatch", validate_context, request(), session_id="f" * 32, process_epoch=EPOCH)
        self.assert_code("invalid_epoch", validate_context, request(), session_id=SESSION_ID, process_epoch=EPOCH.to_wire())

    def test_no_generic_relay_or_catalog_widening(self):
        for op in ("eval", "exec", "shell", "raw_input", "game.exit", "game.save", "build.place",
                   "cas.set_name", "live.set_speed ", "OBSERVE", None, [], True):
            self.assert_code("invalid_operation", parse, request().to_wire() | {"operation": op})

    def test_set_speed_rejects_bool_float_string_and_out_of_range(self):
        for speed in range(4):
            self.assertEqual(speed, parse(request(arguments=SetSpeedArguments(speed)).to_wire()).arguments.speed)
        for speed in (True, False, -1, 4, 1.0, "1", None, [], MAX_INTEGER):
            self.assert_code("invalid_arguments", SetSpeedArguments, speed)
            self.assert_code("invalid_arguments", parse, request().to_wire() | {"arguments": {"speed": speed}})

    def test_cas_identifier_is_canonical_positive_uint64_decimal_text(self):
        for sim_id in ("1", "123", str(MAX_INTEGER)):
            self.assertEqual(sim_id, EnterCasArguments(sim_id).sim_id)
        for sim_id in ("", "0", "01", "-1", "+1", " 1", "1 ", "1.0", "1e1", "١", "１",
                       str(MAX_INTEGER + 1), "9" * 21, "symbolic_sim", "1\n", "a;exec()", True, 123):
            self.assert_code("invalid_arguments", EnterCasArguments, sim_id)

    def test_no_argument_extras_or_hidden_exit_choice(self):
        for op in Operation:
            original = request(op).to_wire()
            self.assert_code("invalid_arguments", parse, original | {"arguments": original["arguments"] | {"execute": True}})
            for arguments in ([], None, "{}"):
                self.assert_code("invalid_arguments", parse, original | {"arguments": arguments})
        original = request(Operation.REQUEST_EXIT_MENU).to_wire()
        for argument in ("save_policy", "discard", "save", "force", "confirm"):
            self.assert_code("invalid_arguments", parse, original | {"arguments": {argument: True}})

    def test_typed_construction_cannot_confuse_arguments_or_operation(self):
        for op in ("live.set_speed", None, 1):
            self.assert_code("invalid_operation", Request, REQUEST_ID, SESSION_ID, EPOCH, op, SetSpeedArguments(2))
        for args in ({"speed": 2}, ObserveArguments(), RequestExitMenuArguments(), None):
            self.assert_code("invalid_arguments", Request, REQUEST_ID, SESSION_ID, EPOCH, Operation.SET_SPEED, args)

    def test_envelopes_are_immutable_and_wire_values_are_detached(self):
        req = request()
        for obj, name, value in ((req, "request_id", "f" * 32), (req.arguments, "speed", 3), (EPOCH, "pid", 42)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, name, value)
        wire = req.to_wire()
        wire["arguments"]["speed"] = 3
        wire["process_epoch"]["pid"] = 42
        self.assertEqual(2, req.arguments.speed)
        self.assertEqual(1234, req.process_epoch.pid)

    def test_fingerprint_binds_full_request_and_is_key_order_independent(self):
        original = request()
        value = original.to_wire()
        reordered = {key: value[key] for key in reversed(value)}
        self.assertEqual(request_fingerprint(original), request_fingerprint(parse(reordered)))
        variants = (replace(original, request_id="a" * 32), replace(original, session_id="a" * 32),
                    replace(original, process_epoch=ProcessEpoch(1235, EPOCH.creation_filetime)),
                    replace(original, process_epoch=ProcessEpoch(1234, EPOCH.creation_filetime + 1)),
                    replace(original, arguments=SetSpeedArguments(3)), request(Operation.OBSERVE))
        for variant in variants:
            self.assertNotEqual(request_fingerprint(original), request_fingerprint(variant))


class ResponseTests(ProtocolAssertions):
    def test_all_response_states_round_trip(self):
        self.assertEqual({"accepted", "dispatched", "verified", "refused", "unknown"},
                         {state.value for state in ResponseState})
        for state in ResponseState:
            original = response(state)
            wire, = FrameDecoder().feed(encode_frame(original.to_wire()))
            self.assertEqual(original, parse_response(wire, request=request()))

    def test_response_requires_exact_fields_and_version(self):
        original = response(ResponseState.ACCEPTED).to_wire()
        for key in original:
            value = deepcopy(original)
            del value[key]
            self.assert_code("invalid_envelope", parse_response, value, request=request())
        for changes in ({"ok": True}, {"live_qualified": True}, {"schema": True}, {"kind": "request"}):
            self.assert_code("invalid_envelope", parse_response, original | changes, request=request())

    def test_response_binding_rejects_wrong_request_context_or_hash(self):
        original = response(ResponseState.ACCEPTED).to_wire()
        for changes in ({"request_id": "a" * 32}, {"session_id": "a" * 32},
                        {"process_epoch": ProcessEpoch(1234, EPOCH.creation_filetime + 1).to_wire()},
                        {"request_sha256": "a" * 64}):
            self.assert_code("response_mismatch", parse_response, original | changes, request=request())
        self.assert_code("response_mismatch", parse_response, original, request=request(arguments=SetSpeedArguments(3)))
        self.assert_code("invalid_response", parse_response, original | {"request_sha256": "A" * 64}, request=request())

    def test_unknown_states_and_non_typed_states_fail(self):
        original = response(ResponseState.ACCEPTED).to_wire()
        for state in ("success", "verified_simulation", "sent", "done", "VERIFIED", [], None, True):
            self.assert_code("invalid_response", parse_response, original | {"state": state}, request=request())
        self.assert_code("invalid_response", response_for, request(), "accepted")

    def test_verified_requires_evidence_but_not_a_success_flag(self):
        self.assert_code("invalid_response", response_for, request(), ResponseState.VERIFIED)
        self.assert_code("invalid_response", response_for, request(), ResponseState.VERIFIED,
                         evidence=EVIDENCE, reason="unqualified")
        for state in (ResponseState.ACCEPTED, ResponseState.DISPATCHED):
            self.assert_code("invalid_response", response_for, request(), state, evidence=EVIDENCE)
            self.assert_code("invalid_response", response_for, request(), state, reason="not_done")
        self.assertNotIn("ok", response(ResponseState.VERIFIED).to_wire())

    def test_refused_unknown_require_reason_not_evidence(self):
        for state in (ResponseState.REFUSED, ResponseState.UNKNOWN):
            for reason in (None, "", "x" * 65, "Bad", "not done", "x\n", True, {}):
                self.assert_code("invalid_response", response_for, request(), state, reason=reason)
            self.assert_code("invalid_response", response_for, request(), state, reason="uncertain", evidence=EVIDENCE)
            self.assertEqual("x" * 64, response_for(request(), state, reason="x" * 64).reason)

    def test_evidence_reference_types_and_fields(self):
        for observation_id, digest in (("a" * 31, "b" * 64), ("a" * 32, "b" * 63),
                                       ("a" * 32, "B" * 64), (True, "b" * 64)):
            self.assert_code("invalid_evidence", EvidenceReference, observation_id, digest)
        original = response(ResponseState.VERIFIED).to_wire()
        for value in ({"observation_id": "a" * 32}, EVIDENCE.to_wire() | {"trusted": True}, [], "evidence"):
            self.assert_code("invalid_evidence", parse_response, original | {"evidence": value}, request=request())
        self.assert_code("invalid_response", parse_response, original | {"evidence": None}, request=request())

    def test_all_allowed_and_forbidden_transitions(self):
        allowed = {("accepted", "dispatched"), ("accepted", "refused"), ("accepted", "unknown"),
                   ("dispatched", "verified"), ("dispatched", "unknown"), ("unknown", "verified")}
        for before in ResponseState:
            for after in ResponseState:
                with self.subTest(before=before, after=after):
                    if before == after or (before.value, after.value) in allowed:
                        validate_response_transition(response(before), response(after))
                    else:
                        self.assert_code("invalid_transition", validate_response_transition,
                                         response(before), response(after))

    def test_terminal_receipts_and_same_state_records_cannot_be_rewritten(self):
        verified = response(ResponseState.VERIFIED)
        changed = replace(verified, evidence=EvidenceReference("5" * 32, "6" * 64))
        self.assert_code("invalid_transition", validate_response_transition, verified, changed)
        for state in (ResponseState.REFUSED, ResponseState.UNKNOWN):
            old = response(state)
            self.assert_code("invalid_transition", validate_response_transition, old, replace(old, reason="different"))
        old = response(ResponseState.ACCEPTED)
        self.assert_code("response_mismatch", validate_response_transition, old,
                         response(ResponseState.DISPATCHED, replace(request(), request_id="a" * 32)))


class ReplayTests(ProtocolAssertions):
    def test_missing_history_defaults_to_read_only_reconciliation(self):
        decision = classify_replay(request())
        self.assertEqual(ReplayDisposition.RECONCILE_ONLY, decision.disposition)
        self.assertIsNone(decision.recorded_response)
        self.assertEqual(ReplayDisposition.RECONCILE_ONLY, classify_replay(request(), history_complete=False).disposition)

    def test_first_seen_requires_explicit_complete_local_history(self):
        self.assertEqual(ReplayDisposition.FIRST_SEEN, classify_replay(request(), history_complete=True).disposition)
        for bad in (1, 0, "true", None, {}):
            self.assert_code("invalid_history", classify_replay, request(), history_complete=bad)

    def test_identical_terminal_record_returns_receipt_only(self):
        for state in (ResponseState.VERIFIED, ResponseState.REFUSED):
            record = response(state)
            for complete in (False, True):
                decision = classify_replay(request(), previous_request=request(),
                                           previous_response=record, history_complete=complete)
                self.assertEqual(ReplayDisposition.RETURN_RECORDED, decision.disposition)
                self.assertIs(record, decision.recorded_response)

    def test_pending_or_unknown_record_never_allows_another_dispatch(self):
        for record in (None, response(ResponseState.ACCEPTED), response(ResponseState.DISPATCHED), response(ResponseState.UNKNOWN)):
            for complete in (False, True):
                decision = classify_replay(request(), previous_request=request(),
                                           previous_response=record, history_complete=complete)
                self.assertEqual(ReplayDisposition.RECONCILE_ONLY, decision.disposition)
                self.assertIs(record, decision.recorded_response)

    def test_same_id_different_arguments_or_context_is_conflict(self):
        variants = (request(arguments=SetSpeedArguments(3)), request(Operation.OBSERVE),
                    replace(request(), session_id="a" * 32), replace(request(), process_epoch=ProcessEpoch(999, 1)),
                    replace(request(), request_id="b" * 32))
        for variant in variants:
            for record in (None, response(ResponseState.VERIFIED), response(ResponseState.UNKNOWN)):
                decision = classify_replay(variant, previous_request=request(), previous_response=record,
                                           history_complete=True)
                self.assertEqual(ReplayDisposition.CONFLICT, decision.disposition)
                self.assertIsNone(decision.recorded_response)

    def test_orphan_or_mismatched_records_are_not_replay_evidence(self):
        self.assert_code("invalid_history", classify_replay, request(), previous_response=response(ResponseState.VERIFIED))
        self.assert_code("response_mismatch", classify_replay, request(), previous_request=request(),
                         previous_response=response(ResponseState.VERIFIED, request(arguments=SetSpeedArguments(3))))
        self.assert_code("invalid_history", classify_replay, request(), previous_request={})
        self.assert_code("invalid_history", classify_replay, request().to_wire())

    def test_no_hidden_memory_claims_after_cache_loss(self):
        first = classify_replay(request(), history_complete=True)
        self.assertEqual(ReplayDisposition.FIRST_SEEN, first.disposition)
        classify_replay(request(), previous_request=request(), previous_response=response(ResponseState.VERIFIED))
        # Supplying no record after an earlier call is still unknown, not an
        # implicit persistent receipt or a promise that this ID was not used.
        self.assertEqual(ReplayDisposition.RECONCILE_ONLY, classify_replay(request()).disposition)


class OfflineBoundaryTests(unittest.TestCase):
    def test_module_imports_only_offline_standard_library_helpers(self):
        tree = ast.parse(inspect.getsource(protocol))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(0, node.level)
                imported.add(node.module)
        self.assertEqual({"dataclasses", "enum", "hashlib", "json", "math", "re", "struct"}, imported)
        calls = [node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        self.assertFalse(set(calls) & {"eval", "exec", "compile", "open", "__import__"})

    def test_no_live_qualification_or_authorization_fields(self):
        for value in (request().to_wire(), response(ResponseState.VERIFIED).to_wire()):
            self.assertFalse(set(value) & {"token", "authorized", "live_qualified", "ok", "execute"})
        self.assertIn("not a signature", inspect.getdoc(request_fingerprint))
        self.assertIn("not represented as an installable Sims script mod", inspect.getdoc(protocol))


if __name__ == "__main__":
    unittest.main()
