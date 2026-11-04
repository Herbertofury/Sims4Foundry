"""Exact immutable schema-4 Build requests. No native imports or authority."""
from dataclasses import dataclass
import hashlib
from .queue import ProcessEpoch
from . import wire
from . import build_schema as schema
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
