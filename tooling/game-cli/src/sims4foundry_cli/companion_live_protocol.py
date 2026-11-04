"""Explicit source-only schema-3 Live read values and strict receipt validation.

No sender, CLI route, game provider, capability unlock or authentication change.
The full request digest correlates intent; it neither authenticates nor authorizes.
The existing schema-1/2 types and network sender deliberately reject these types.
"""
from dataclasses import dataclass, field
import hashlib

from . import companion_protocol as wire
from . import companion_live_schema as schema

TRANSPORT_VERSION = 3
OPERATION = 'live.observe_queue'
_require = wire._require


@dataclass(frozen=True)
class LiveQueueArguments:
    actor_sim_id: str

    def __post_init__(self):
        _require(type(self) is LiveQueueArguments and schema.decimal(self.actor_sim_id),
                 'invalid_arguments', 'Expected exact actor uint64 decimal text.')


@dataclass(frozen=True)
class LiveQueueExpectedContext:
    zone_id: str
    household_id: str
    client_id: str
    save_slot: int
    save_guid: str
    zone_generation: int
    mode: str = 'live'

    def __post_init__(self):
        _require(type(self) is LiveQueueExpectedContext, 'invalid_context', 'Expected exact Live context.')
        schema.context_fields(LiveQueueExpectedContext.to_wire(self), _require)

    def to_wire(self):
        _require(type(self) is LiveQueueExpectedContext, 'invalid_context', 'Expected exact Live context.')
        value = dict(zone_id=self.zone_id, household_id=self.household_id, client_id=self.client_id,
                     save_slot=self.save_slot, save_guid=self.save_guid,
                     zone_generation=self.zone_generation, mode=self.mode)
        schema.context_fields(value, _require)
        return value


@dataclass(frozen=True)
class LiveQueueRequest:
    request_id: str
    session_id: str
    process_epoch: wire.ProcessEpoch
    arguments: LiveQueueArguments
    expected_context: LiveQueueExpectedContext
    ttl_seconds: float = 5.0
    operation: str = field(default=OPERATION, init=False)

    def __post_init__(self):
        value = request_wire(self)
        object.__setattr__(self, 'process_epoch', wire.ProcessEpoch(**value['process_epoch']))
        object.__setattr__(self, 'arguments', LiveQueueArguments(**value['arguments']))
        object.__setattr__(self, 'expected_context', LiveQueueExpectedContext(**value['expected_context']))

    def to_wire(self):
        return request_wire(self)


def request_wire(request):
    """Read exact typed fields without calling caller-shadowable instance methods."""
    _require(type(request) is LiveQueueRequest, 'invalid_request', 'Expected exact Live queue request.')
    _require(type(request.process_epoch) is wire.ProcessEpoch, 'invalid_epoch', 'Expected exact process epoch.')
    _require(type(request.arguments) is LiveQueueArguments, 'invalid_arguments', 'Expected exact Live arguments.')
    _require(type(request.expected_context) is LiveQueueExpectedContext,
             'invalid_context', 'Expected exact Live context.')
    value = dict(schema=3, kind='submit', request_id=request.request_id, session_id=request.session_id,
                 process_epoch=dict(pid=request.process_epoch.pid,
                                    creation_filetime=request.process_epoch.creation_filetime),
                 operation=request.operation, arguments=dict(actor_sim_id=request.arguments.actor_sim_id),
                 ttl_seconds=request.ttl_seconds,
                 expected_context=LiveQueueExpectedContext.to_wire(request.expected_context))
    schema.submission_fields(value, _require)
    return value


def _request_from_wire(value):
    return LiveQueueRequest(value['request_id'], value['session_id'],
                            wire.ProcessEpoch(**value['process_epoch']),
                            LiveQueueArguments(**value['arguments']),
                            LiveQueueExpectedContext(**value['expected_context']), value['ttl_seconds'])


def canonical_payload(request):
    return wire.encode_frame(request_wire(request))[4:]


def request_sha256(request):
    return hashlib.sha256(canonical_payload(request)).hexdigest()


@dataclass(frozen=True)
class LiveQueueSubmission:
    request: LiveQueueRequest

    def __post_init__(self):
        _require(type(self) is LiveQueueSubmission, 'invalid_request', 'Expected exact Live submission.')
        object.__setattr__(self, 'request', _request_from_wire(request_wire(self.request)))

    def to_wire(self):
        _require(type(self) is LiveQueueSubmission, 'invalid_request', 'Expected exact Live submission.')
        return request_wire(self.request)

    @property
    def canonical_payload(self):
        return wire.encode_frame(LiveQueueSubmission.to_wire(self))[4:]

    @property
    def request_sha256(self):
        return hashlib.sha256(LiveQueueSubmission.canonical_payload.fget(self)).hexdigest()


@dataclass(frozen=True)
class LiveQueueReceiptRead:
    submission: LiveQueueSubmission

    def __post_init__(self):
        _require(type(self) is LiveQueueReceiptRead and type(self.submission) is LiveQueueSubmission,
                 'invalid_request', 'Expected exact original Live submission.')
        object.__setattr__(self, 'submission', LiveQueueSubmission(self.submission.request))

    def to_wire(self):
        _require(type(self) is LiveQueueReceiptRead and type(self.submission) is LiveQueueSubmission,
                 'invalid_request', 'Expected exact original Live submission.')
        original = LiveQueueSubmission.to_wire(self.submission)
        return dict(schema=3, kind='receipt_read', request_id=original['request_id'],
                    session_id=original['session_id'], process_epoch=original['process_epoch'],
                    request_sha256=hashlib.sha256(wire.encode_frame(original)[4:]).hexdigest())


def _binding(value, session_id, process_epoch):
    _require(schema.token(session_id) and type(process_epoch) is wire.ProcessEpoch,
             'invalid_identity', 'Expected exact captured session and process epoch.')
    epoch = dict(pid=process_epoch.pid, creation_filetime=process_epoch.creation_filetime)
    schema.epoch_fields(epoch, _require)
    _require(value['session_id'] == session_id and value['process_epoch'] == epoch,
             'context_mismatch', 'Wrong session or process lifetime.')


def parse_live_submit(value, session_id, process_epoch):
    wire.encode_frame(value)
    schema.submission_fields(value, _require)
    _binding(value, session_id, process_epoch)
    return LiveQueueSubmission(_request_from_wire(value))


def parse_live_receipt_read(value, session_id, process_epoch):
    wire.encode_frame(value)
    schema.query_fields(value, _require)
    _binding(value, session_id, process_epoch)
    return value['request_id'], value['request_sha256']


def validate_result(result, request):
    value = request_wire(request)
    wire.encode_frame(result)
    schema.result_fields(result, value, _require)
    return result


def parse_live_receipt(value, submission):
    """Return adapter assertions only; an old-peer error never becomes fallback."""
    _require(type(submission) is LiveQueueSubmission, 'invalid_request', 'Expected exact Live submission.')
    original = LiveQueueSubmission.to_wire(submission)
    payload = wire.encode_frame(original)[4:]
    wire.encode_frame(value)
    if value.get('kind') == 'error':
        schema.object_fields(value, {'schema', 'kind', 'code', 'outcome_known'}, _require, 'invalid_response')
        _require(type(value['schema']) is int and value['schema'] in (2, 3)
                 and type(value['kind']) is str and value['outcome_known'] is False
                 and type(value['code']) is str and schema._REASON.fullmatch(value['code']) is not None,
                 'invalid_response', 'Malformed uncertain exchange error.')
        raise wire.ProtocolError(value['code'], 'Exchange is uncertain; no fallback or resubmission is permitted.')
    schema.receipt_fields(value, original, hashlib.sha256(payload).hexdigest(), _require)
    return value
