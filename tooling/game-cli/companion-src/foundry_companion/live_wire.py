"""Strict Python-3.7 schema-3 Live read contract, with no sender or game access.

This module is separate from the unchanged v2 wire. Full canonical digests are
correlation only. Queue/executor acceptance checks the complete frozen candidate
and encoded receipt against the original request, never an adapter-selected actor.
"""
from dataclasses import dataclass
import hashlib
from types import MappingProxyType

from . import queue as q
from . import wire
from . import live_schema as schema

TRANSPORT_VERSION = 3
_require = wire._require


class ResultBudgetExceeded(wire.ProtocolError):
    """Only an otherwise schema-valid result exceeded a fixed queue/wire bound."""
    def __init__(self):
        super().__init__('result_budget_exceeded', 'Complete Live result/receipt exceeds its fixed bound.')


def request_wire(request):
    """Capture exact typed fields without invoking caller-shadowed methods."""
    _require(type(request) is q.LiveQueueRequest, 'invalid_request', 'Expected exact Live queue request.')
    _require(type(request.process_epoch) is q.ProcessEpoch, 'invalid_epoch', 'Expected exact process epoch.')
    _require(type(request.arguments) is q.LiveQueueArguments, 'invalid_arguments', 'Expected exact Live arguments.')
    _require(type(request.expected_context) is q.LiveQueueExpectedContext,
             'invalid_context', 'Expected exact Live context.')
    context = request.expected_context
    value = dict(schema=3, kind='submit', request_id=request.request_id, session_id=request.session_id,
                 process_epoch=dict(pid=request.process_epoch.pid,
                                    creation_filetime=request.process_epoch.creation_filetime),
                 operation=request.operation, arguments=dict(actor_sim_id=request.arguments.actor_sim_id),
                 ttl_seconds=request.ttl_seconds,
                 expected_context=dict(zone_id=context.zone_id, household_id=context.household_id,
                                       client_id=context.client_id, save_slot=context.save_slot,
                                       save_guid=context.save_guid, zone_generation=context.zone_generation,
                                       mode=context.mode))
    schema.submission_fields(value, _require)
    return value


def _request_from_wire(value):
    try:
        return q.LiveQueueRequest(value['request_id'], value['session_id'],
                                  q.ProcessEpoch(**value['process_epoch']),
                                  q.LiveQueueArguments(**value['arguments']),
                                  q.LiveQueueExpectedContext(**value['expected_context']),
                                  value['ttl_seconds'])
    except q.QueueError as error:
        raise wire.ProtocolError(error.code, 'Invalid exact Live queue request.') from None


def canonical_payload(request):
    return wire.encode_frame(request_wire(request))[4:]


def request_sha256(request):
    return hashlib.sha256(canonical_payload(request)).hexdigest()


@dataclass(frozen=True)
class Submission:
    request: object
    request_sha256: str
    canonical_payload: bytes

    def __post_init__(self):
        _require(type(self) is Submission, 'invalid_request', 'Expected exact Live submission.')
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
    schema.result_fields(result, request_wire(request), _require)
    wire.encode_frame(result)
    return result


def _thaw(value):
    if type(value) in (dict, MappingProxyType):
        return {key: _thaw(child) for key, child in value.items()}
    if type(value) in (tuple, list):
        return [_thaw(child) for child in value]
    return value


def receipt(request_id, digest, session_id, process_epoch, snapshot=None):
    """Build only the exact schema-3 envelope; terminal binding is checked below."""
    _require(type(process_epoch) is q.ProcessEpoch, 'invalid_epoch', 'Expected exact process epoch.')
    query = dict(schema=3, kind='receipt_read', request_id=request_id, session_id=session_id,
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
                 'invalid_response', 'Live reads cannot produce verified terminal state.')
        state, reason, result = states[snapshot.state], snapshot.reason, _thaw(snapshot.result)
    return dict(schema=3, kind='receipt', request_id=request_id, session_id=session_id,
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
    schema.result_fields(result, original, _require)
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
