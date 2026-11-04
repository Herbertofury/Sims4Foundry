"""Strict schema-4 Build protocol. Digests bind declarations, not authentication.

Capture results are source assertions only; no game success, transaction, or undo
is inferred. This module performs no I/O, sender action, or native dispatch.
"""
from dataclasses import dataclass
import hashlib
from . import companion_protocol as wire
from .companion_protocol import ProcessEpoch
from . import companion_build_schema as schema
TRANSPORT_VERSION = 4
_require = wire._require

@dataclass(frozen=True)
class BuildSurface:
    primary: str
    level: int
    type: int

    def __post_init__(self):
        _require(type(self) is BuildSurface, 'invalid_arguments', 'Expected exact Build surface.')
        schema.surface_fields(surface_wire(self), _require)


def surface_wire(value):
    _require(type(value) is BuildSurface, 'invalid_arguments', 'Expected exact Build surface.')
    return dict(primary=value.primary, level=value.level, type=value.type)


@dataclass(frozen=True)
class BuildCaptureArguments:
    object_id: str
    definition_id: str
    before_position: tuple
    before_orientation: tuple
    before_surface: BuildSurface

    def __post_init__(self):
        arguments_wire(self)


@dataclass(frozen=True)
class BuildTransformArguments(BuildCaptureArguments):
    capture_request_id: str
    capture_request_sha256: str
    destination: tuple
    yaw: float
    level: int


def arguments_wire(args):
    _require(type(args) in (BuildCaptureArguments, BuildTransformArguments),
             'invalid_arguments', 'Expected exact Build arguments.')
    _require(type(args.before_position) is tuple and type(args.before_orientation) is tuple,
             'invalid_arguments', 'Frozen vectors require exact tuples.')
    value = dict(object_id=args.object_id, definition_id=args.definition_id,
                 before_position=list(args.before_position), before_orientation=list(args.before_orientation),
                 before_surface=surface_wire(args.before_surface))
    operation = 'build.capture_transform'
    if type(args) is BuildTransformArguments:
        _require(type(args.destination) is tuple, 'invalid_arguments', 'Destination requires exact tuple.')
        value.update(capture_request_id=args.capture_request_id,
                     capture_request_sha256=args.capture_request_sha256,
                     destination=list(args.destination), yaw=args.yaw, level=args.level)
        operation = 'build.transform_selected'
    schema.arguments_fields(value, operation, _require)
    return value


@dataclass(frozen=True)
class BuildExpectedContext:
    zone_id: str
    household_id: str
    client_id: str
    save_slot: int
    save_guid: str
    zone_generation: int
    mode: str = 'build_buy'

    def __post_init__(self):
        context_wire(self)

    def to_wire(self):
        return context_wire(self)


def context_wire(context):
    _require(type(context) is BuildExpectedContext, 'invalid_context', 'Expected exact Build context.')
    value = dict(zone_id=context.zone_id, household_id=context.household_id,
                 client_id=context.client_id, save_slot=context.save_slot,
                 save_guid=context.save_guid, zone_generation=context.zone_generation, mode=context.mode)
    schema.context_fields(value, _require)
    return value


@dataclass(frozen=True)
class BuildRequest:
    request_id: str
    session_id: str
    process_epoch: ProcessEpoch
    arguments: BuildCaptureArguments
    expected_context: BuildExpectedContext
    deployment_sha256: str
    ttl_seconds: float = 5.0
    operation: str = 'build.capture_transform'

    def __post_init__(self):
        value = request_wire(self)
        object.__setattr__(self, 'process_epoch', ProcessEpoch(**value['process_epoch']))
        object.__setattr__(self, 'arguments', arguments_from_wire(value['arguments'], value['operation']))
        object.__setattr__(self, 'expected_context', BuildExpectedContext(**value['expected_context']))

    def validate(self):
        request_wire(self)

    def to_wire(self):
        return request_wire(self)


def arguments_from_wire(value, operation):
    args = dict(value)
    args['before_position'] = tuple(args['before_position'])
    args['before_orientation'] = tuple(args['before_orientation'])
    args['before_surface'] = BuildSurface(**args['before_surface'])
    if operation == 'build.transform_selected':
        args['destination'] = tuple(args['destination'])
        return BuildTransformArguments(**args)
    return BuildCaptureArguments(**args)


def request_wire(request):
    _require(type(request) is BuildRequest, 'invalid_request', 'Expected exact Build request.')
    _require(type(request.process_epoch) is ProcessEpoch, 'invalid_epoch', 'Expected exact process epoch.')
    expected = BuildCaptureArguments if request.operation == 'build.capture_transform' else BuildTransformArguments
    _require(type(request.arguments) is expected, 'invalid_arguments', 'Arguments must match operation.')
    value = dict(schema=4, kind='submit', request_id=request.request_id, session_id=request.session_id,
                 process_epoch=dict(pid=request.process_epoch.pid, creation_filetime=request.process_epoch.creation_filetime),
                 arguments=arguments_wire(request.arguments), expected_context=context_wire(request.expected_context),
                 deployment_sha256=request.deployment_sha256, ttl_seconds=request.ttl_seconds,
                 operation=request.operation)
    schema.submission_fields(value, _require)
    return value


def _request_from_wire(value):
    schema.submission_fields(value, _require)
    return BuildRequest(value['request_id'], value['session_id'], ProcessEpoch(**value['process_epoch']),
                        arguments_from_wire(value['arguments'], value['operation']),
                        BuildExpectedContext(**value['expected_context']), value['deployment_sha256'],
                        value['ttl_seconds'], value['operation'])


def canonical_payload(request):
    return wire.encode_frame(request_wire(request))[4:]


def request_sha256(request):
    return hashlib.sha256(canonical_payload(request)).hexdigest()


def request_key(request):
    return canonical_payload(request)


def request_from_key(key):
    _require(type(key) is bytes, 'invalid_request', 'Expected immutable canonical baseline.')
    value = wire.decode_payload(key)
    request = _request_from_wire(value)
    _require(canonical_payload(request) == key, 'invalid_request', 'Baseline must be canonical.')
    return request


@dataclass(frozen=True)
class BuildSubmission:
    request: BuildRequest

    def __post_init__(self):
        _require(type(self) is BuildSubmission, 'invalid_request', 'Expected exact Build submission.')
        object.__setattr__(self, 'request', _request_from_wire(request_wire(self.request)))

    def to_wire(self):
        _require(type(self) is BuildSubmission, 'invalid_request', 'Expected exact Build submission.')
        return request_wire(self.request)

    @property
    def canonical_payload(self):
        return wire.encode_frame(BuildSubmission.to_wire(self))[4:]

    @property
    def request_sha256(self):
        return hashlib.sha256(BuildSubmission.canonical_payload.fget(self)).hexdigest()


@dataclass(frozen=True)
class BuildReceiptRead:
    submission: BuildSubmission

    def __post_init__(self):
        _require(type(self) is BuildReceiptRead and type(self.submission) is BuildSubmission,
                 'invalid_request', 'Expected exact original Build submission.')
        object.__setattr__(self, 'submission', BuildSubmission(self.submission.request))

    def to_wire(self):
        _require(type(self) is BuildReceiptRead and type(self.submission) is BuildSubmission,
                 'invalid_request', 'Expected exact original Build submission.')
        original = BuildSubmission.to_wire(self.submission)
        return dict(schema=4, kind='receipt_read', request_id=original['request_id'],
                    session_id=original['session_id'], process_epoch=original['process_epoch'],
                    request_sha256=hashlib.sha256(wire.encode_frame(original)[4:]).hexdigest())


def _binding(value, session_id, process_epoch):
    _require(schema.token(session_id) and type(process_epoch) is wire.ProcessEpoch,
             'invalid_identity', 'Expected exact captured session and process epoch.')
    epoch = dict(pid=process_epoch.pid, creation_filetime=process_epoch.creation_filetime)
    schema.epoch_fields(epoch, _require)
    _require(value['session_id'] == session_id and value['process_epoch'] == epoch,
             'context_mismatch', 'Wrong session or process lifetime.')


def parse_build_submit(value, session_id, process_epoch):
    wire.encode_frame(value)
    schema.submission_fields(value, _require)
    _binding(value, session_id, process_epoch)
    return BuildSubmission(_request_from_wire(value))


def parse_build_receipt_read(value, session_id, process_epoch):
    wire.encode_frame(value)
    schema.query_fields(value, _require)
    _binding(value, session_id, process_epoch)
    return value['request_id'], value['request_sha256']


def validate_result(result, request):
    value = request_wire(request)
    wire.encode_frame(result)
    schema.result_fields(result, value, _require, request_sha256(request))
    return result


def parse_build_receipt(value, submission):
    """Return adapter assertions only; an old-peer error never becomes fallback."""
    _require(type(submission) is BuildSubmission, 'invalid_request', 'Expected exact Build submission.')
    original = BuildSubmission.to_wire(submission)
    payload = wire.encode_frame(original)[4:]
    wire.encode_frame(value)
    if value.get('kind') == 'error':
        schema.object_fields(value, {'schema', 'kind', 'code', 'outcome_known'}, _require, 'invalid_response')
        _require(type(value['schema']) is int and value['schema'] in (2, 3, 4)
                 and type(value['kind']) is str and value['outcome_known'] is False
                 and type(value['code']) is str and schema._REASON.fullmatch(value['code']) is not None,
                 'invalid_response', 'Malformed uncertain exchange error.')
        raise wire.ProtocolError(value['code'], 'Exchange is uncertain; no fallback or resubmission is permitted.')
    schema.receipt_fields(value, original, hashlib.sha256(payload).hexdigest(), _require)
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


def capture_ready(value, submission):
    """Project known capture intent only after strict captured receipt validation.

    This is correlation for a subsequent owner-thread lookup, not a capability or
    proof of native success. It conveys no arbitrary adapter-selected object.
    """
    parsed = parse_build_receipt(value, submission)
    original = BuildSubmission.to_wire(submission)
    _require(original['operation'] == 'build.capture_transform'
             and parsed['state'] == 'adapter_returned'
             and parsed['adapter_result']['action_status'] == 'captured',
             'capture_not_ready', 'Only an exact captured receipt exposes a reference.')
    args = dict(original['arguments'])
    args.update(capture_request_id=original['request_id'],
                capture_request_sha256=hashlib.sha256(wire.encode_frame(original)[4:]).hexdigest())
    return args
