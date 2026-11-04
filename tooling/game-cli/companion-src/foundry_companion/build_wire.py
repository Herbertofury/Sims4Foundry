"""Strict Python-3.7 schema-4 Build contract, with no sender or game access.

This module is separate from the unchanged v2 wire. Full canonical digests are
correlation only. Queue/executor acceptance checks the complete frozen candidate
and encoded receipt against the original request, never an adapter-selected actor.
"""
from dataclasses import dataclass
import hashlib
from types import MappingProxyType

from . import queue as q
from . import wire
from . import build_schema as schema

TRANSPORT_VERSION = 4
_require = wire._require


class ResultBudgetExceeded(wire.ProtocolError):
    """Only an otherwise schema-valid result exceeded a fixed queue/wire bound."""
    def __init__(self):
        super().__init__('result_budget_exceeded', 'Complete Build result/receipt exceeds its fixed bound.')


from .build_request import (BuildRequest, request_wire, _request_from_wire,
                            canonical_payload, request_sha256, request_key, request_from_key)


@dataclass(frozen=True)
class Submission:
    request: object
    request_sha256: str
    canonical_payload: bytes

    def __post_init__(self):
        _require(type(self) is Submission, 'invalid_request', 'Expected exact Build submission.')
        original = request_wire(self.request)
        payload = wire.encode_frame(original)[4:]
        _require(type(self.canonical_payload) is bytes and self.canonical_payload == payload
                 and type(self.request_sha256) is str
                 and self.request_sha256 == hashlib.sha256(payload).hexdigest(),
                 'request_conflict', 'Submission must bind the complete canonical request.')
        object.__setattr__(self, 'request', _request_from_wire(original))


def _binding(value, session_id, process_epoch):
    _require(schema.token(session_id) and type(process_epoch) is q.ProcessEpoch,
             'invalid_identity', 'Expected exact captured session and process epoch.')
    epoch = dict(pid=process_epoch.pid, creation_filetime=process_epoch.creation_filetime)
    schema.epoch_fields(epoch, _require)
    _require(value['session_id'] == session_id and value['process_epoch'] == epoch,
             'context_mismatch', 'Wrong session or process lifetime.')


def parse_submit(value, session_id, process_epoch):
    payload = wire.encode_frame(value)[4:]
    schema.submission_fields(value, _require)
    _binding(value, session_id, process_epoch)
    return Submission(_request_from_wire(value), hashlib.sha256(payload).hexdigest(), payload)


def parse_receipt_read(value, session_id, process_epoch):
    wire.encode_frame(value)
    schema.query_fields(value, _require)
    _binding(value, session_id, process_epoch)
    return value['request_id'], value['request_sha256']


def validate_result(result, request):
    schema.result_fields(result, request_wire(request), _require, request_sha256(request))
    wire.encode_frame(result)
    return result


def _thaw(value):
    if type(value) in (dict, MappingProxyType):
        return {key: _thaw(child) for key, child in value.items()}
    if type(value) in (tuple, list):
        return [_thaw(child) for child in value]
    return value


def receipt(request_id, digest, session_id, process_epoch, snapshot=None):
    """Build only the exact schema-4 envelope; terminal binding is checked below."""
    _require(type(process_epoch) is q.ProcessEpoch, 'invalid_epoch', 'Expected exact process epoch.')
    query = dict(schema=4, kind='receipt_read', request_id=request_id, session_id=session_id,
                 process_epoch=dict(pid=process_epoch.pid, creation_filetime=process_epoch.creation_filetime),
                 request_sha256=digest)
    schema.query_fields(query, _require)
    _binding(query, session_id, process_epoch)
    state, reason, result = 'not_found', 'history_missing', None
    if snapshot is not None:
        _require(type(snapshot) is q.Snapshot and snapshot.request_id == request_id
                 and snapshot.session_id == session_id, 'response_mismatch', 'Wrong retained queue snapshot.')
        states = {'pending': 'accepted', 'started': 'dispatched', 'unverified': 'adapter_returned',
                  'refused': 'refused', 'unknown': 'unknown'}
        _require(type(snapshot.state) is str and snapshot.state in states,
                 'invalid_response', 'Build actions cannot produce verified terminal state.')
        state, reason, result = states[snapshot.state], snapshot.reason, _thaw(snapshot.result)
    return dict(schema=4, kind='receipt', request_id=request_id, session_id=session_id,
                process_epoch=query['process_epoch'], request_sha256=digest, state=state,
                reason=reason, adapter_result=result, independent_evidence=False)


def preview_result_receipt(result, request):
    """Validate complete plain candidate and encode its actual terminal receipt.

    Call on a detached thaw of the queue's frozen candidate, against a fresh
    reconstruction of its retained primitive request baseline. Shape/binding
    failures are protocol errors; only a schema-valid budget failure has the
    dedicated ResultBudgetExceeded type. No result/receipt is retained here.
    """
    original = request_wire(request)
    schema.result_fields(result, original, _require, request_sha256(request))
    try:
        frozen = q._freeze_result(result)
    except q.QueueError:
        raise ResultBudgetExceeded() from None
    # Build the receipt from the frozen copy, not the executor's mutable object.
    digest = hashlib.sha256(wire.encode_frame(original)[4:]).hexdigest()
    snapshot = q.Snapshot(request.request_id, request.session_id, 'unverified', result=frozen)
    value = receipt(request.request_id, digest, request.session_id, request.process_epoch, snapshot)
    schema.receipt_fields(value, original, digest, _require)
    try:
        wire.encode_frame(value)
    except wire.ProtocolError as error:
        if error.code in ('oversized_frame', 'invalid_json'):
            raise ResultBudgetExceeded() from None
        raise
    return value


def make_result(request, action_status, reason=None, dispatch_attempted=False, after=None):
    """Create only a narrow validated assertion, detached from callback-owned data."""
    original = request_wire(request)
    value = dict(schema_version=1, outcome='unverified', game_qualified=False,
                 action_status=action_status, request_sha256=request_sha256(request),
                 selection=original['arguments'], dispatch_attempted=dispatch_attempted,
                 may_have_applied=dispatch_attempted, after=after, reason=reason,
                 undo_recorded=False, transaction_verified=False)
    for key in ('request_id', 'session_id', 'process_epoch', 'expected_context',
                'deployment_sha256', 'operation'):
        value[key] = original[key]
    schema.result_fields(value, original, _require, request_sha256(request))
    return wire.decode_payload(wire.encode_frame(value)[4:])
