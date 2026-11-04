"""Pure schema-4 Build validation, mirrored verbatim across runtimes.

No authority, readers, transport, codec, queue or game imports. Callers supply
strict bounded codecs and a require(condition, code, message) error function.
Source parity is tested; plain values remain deliberately unqualified.
"""
import math
import re

MAX_UINT64 = (1 << 64) - 1
MAX_ROWS = 49
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"[1-9][0-9]{0,19}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def decimal(value, zero=False):
    return (type(value) is str and
            ((zero and value == '0') or _DECIMAL.fullmatch(value) is not None)
            and int(value) <= MAX_UINT64)


def token(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def object_fields(value, fields, require, code='invalid_result'):
    require(type(value) is dict and all(type(key) is str for key in value)
            and value.keys() == set(fields), code, 'Expected exactly the declared fields.')


def epoch_fields(value, require):
    object_fields(value, {'pid', 'creation_filetime'}, require, 'invalid_epoch')
    require(type(value['pid']) is int and 1 <= value['pid'] <= (1 << 32) - 1
            and type(value['creation_filetime']) is int
            and 1 <= value['creation_filetime'] <= MAX_UINT64,
            'invalid_epoch', 'Expected exact PID and creation FILETIME.')


def context_fields(value, require):
    object_fields(value, {'zone_id', 'household_id', 'client_id', 'save_slot',
                         'save_guid', 'zone_generation', 'mode'}, require, 'invalid_context')
    require(all(decimal(value[key]) for key in ('zone_id', 'household_id', 'client_id'))
            and type(value['save_slot']) is int and 0 <= value['save_slot'] <= MAX_UINT64
            and decimal(value['save_guid'], zero=True)
            and type(value['zone_generation']) is int
            and 1 <= value['zone_generation'] <= MAX_UINT64
            and type(value['mode']) is str and value['mode'] == 'build_buy',
            'invalid_context', 'Exact Build context and generation are required.')


def hash_value(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def vector(value, length, require):
    require(type(value) is list and len(value) == length
            and all(number(item) and abs(item) <= 1000000 for item in value),
            'invalid_arguments', 'Expected a bounded exact numeric vector.')
    if length == 4:
        require(abs(sum(item * item for item in value) - 1) <= 0.001,
                'invalid_arguments', 'Orientation must be near-unit.')


def surface_fields(value, require):
    object_fields(value, {'primary', 'level', 'type'}, require, 'invalid_arguments')
    require(decimal(value['primary'], zero=True)
            and type(value['level']) is int and -(1 << 31) <= value['level'] < (1 << 31)
            and type(value['type']) is int and 0 <= value['type'] < (1 << 31),
            'invalid_arguments', 'Expected exact routing surface.')


def arguments_fields(value, operation, require):
    fields = {'object_id', 'definition_id', 'before_position', 'before_orientation', 'before_surface'}
    if operation == 'build.transform_selected':
        fields |= {'capture_request_id', 'capture_request_sha256', 'destination', 'yaw', 'level'}
    object_fields(value, fields, require, 'invalid_arguments')
    require(decimal(value['object_id']) and decimal(value['definition_id']),
            'invalid_arguments', 'Object and definition must be positive uint64 text.')
    vector(value['before_position'], 3, require)
    vector(value['before_orientation'], 4, require)
    surface_fields(value['before_surface'], require)
    if operation == 'build.transform_selected':
        require(token(value['capture_request_id']) and hash_value(value['capture_request_sha256']),
                'invalid_arguments', 'Capture reference requires its complete digest.')
        vector(value['destination'], 3, require)
        require(number(value['yaw']) and abs(value['yaw']) <= 2 * math.pi
                and type(value['level']) is int and -32 <= value['level'] <= 32,
                'invalid_arguments', 'Yaw or level is outside the bounded Build range.')


def submission_fields(value, require):
    object_fields(value, {'schema', 'kind', 'request_id', 'session_id', 'process_epoch',
                         'operation', 'arguments', 'ttl_seconds', 'expected_context',
                         'deployment_sha256'}, require, 'invalid_envelope')
    require(type(value['schema']) is int and value['schema'] == 4
            and type(value['kind']) is str and value['kind'] == 'submit',
            'invalid_envelope', 'Expected explicit schema 4 submit.')
    require(token(value['request_id']) and token(value['session_id'])
            and hash_value(value['deployment_sha256']), 'invalid_identity', 'Invalid Build binding.')
    epoch_fields(value['process_epoch'], require)
    require(type(value['operation']) is str and value['operation'] in
            ('build.capture_transform', 'build.transform_selected'),
            'invalid_operation', 'Expected a narrow Build operation.')
    arguments_fields(value['arguments'], value['operation'], require)
    require(number(value['ttl_seconds']) and 0 < value['ttl_seconds'] <= 30,
            'invalid_ttl', 'TTL must be finite, positive and at most thirty seconds.')
    context_fields(value['expected_context'], require)


def query_fields(value, require):
    object_fields(value, {'schema', 'kind', 'request_id', 'session_id', 'process_epoch',
                         'request_sha256'}, require, 'invalid_envelope')
    require(type(value['schema']) is int and value['schema'] == 4
            and type(value['kind']) is str and value['kind'] == 'receipt_read',
            'invalid_envelope', 'Expected explicit schema 4 receipt_read.')
    require(token(value['request_id']) and token(value['session_id'])
            and type(value['request_sha256']) is str
            and _HASH.fullmatch(value['request_sha256']) is not None,
            'invalid_identity', 'Invalid receipt lookup identity.')
    epoch_fields(value['process_epoch'], require)


def result_fields(value, submission, require, digest):
    object_fields(value, {'schema_version', 'outcome', 'game_qualified', 'action_status',
                         'request_id', 'request_sha256', 'session_id', 'process_epoch',
                         'expected_context', 'deployment_sha256', 'operation', 'selection',
                         'dispatch_attempted', 'may_have_applied', 'after', 'reason',
                         'undo_recorded', 'transaction_verified'}, require)
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and type(value['outcome']) is str and value['outcome'] == 'unverified'
            and value['game_qualified'] is False and value['undo_recorded'] is False
            and value['transaction_verified'] is False, 'invalid_result',
            'Build results cannot claim verified success, undo or transaction guarantees.')
    # Type-sensitive structural equality: bool/int/float aliases are never bindings.
    def exact(left, right):
        if type(left) is not type(right):
            return False
        if type(left) is dict:
            return left.keys() == right.keys() and all(exact(left[k], right[k]) for k in left)
        if type(left) is list:
            return len(left) == len(right) and all(exact(a, b) for a, b in zip(left, right))
        if type(left) is float:
            return left.hex() == right.hex()
        return left == right
    require(all(exact(value[k], submission[k]) for k in
                ('request_id', 'session_id', 'process_epoch', 'expected_context',
                 'deployment_sha256', 'operation'))
            and exact(value['selection'], submission['arguments'])
            and type(value['request_sha256']) is str and value['request_sha256'] == digest,
            'response_mismatch', 'Result must bind the complete original Build intent.')
    status = value['action_status']
    require(type(status) is str and status in
            ('captured', 'placement_refused', 'dispatched', 'unresolved')
            and type(value['dispatch_attempted']) is bool and type(value['may_have_applied']) is bool,
            'invalid_result', 'Invalid action status or dispatch evidence.')
    attempted = value['dispatch_attempted']
    require(value['may_have_applied'] is attempted, 'invalid_result',
            'A dispatch attempt remains possibly applied; native readback is not verification.')
    if status == 'captured':
        require(submission['operation'] == 'build.capture_transform' and not attempted
                and value['reason'] is None, 'invalid_result', 'Invalid capture result.')
    elif status == 'dispatched':
        require(submission['operation'] == 'build.transform_selected' and attempted
                and value['reason'] is None, 'invalid_result', 'Invalid dispatch result.')
    else:
        require(type(value['reason']) is str and _REASON.fullmatch(value['reason']) is not None
                and (status != 'placement_refused' or not attempted),
                'invalid_result', 'Refused or unresolved results require a bounded reason.')
    require(submission['operation'] != 'build.capture_transform' or not attempted,
            'invalid_result', 'Capture never dispatches a mutation.')
    if value['after'] is not None:
        object_fields(value['after'], {'position', 'orientation', 'surface'}, require)
        vector(value['after']['position'], 3, require)
        vector(value['after']['orientation'], 4, require)
        surface_fields(value['after']['surface'], require)


def receipt_fields(value, submission, digest, require):
    object_fields(value, {'schema', 'kind', 'request_id', 'session_id', 'process_epoch',
                         'request_sha256', 'state', 'reason', 'adapter_result',
                         'independent_evidence'}, require, 'invalid_response')
    require(type(value['schema']) is int and value['schema'] == 4
            and type(value['kind']) is str and value['kind'] == 'receipt'
            and value['independent_evidence'] is False,
            'invalid_response', 'Expected an unqualified schema 4 receipt.')
    epoch_fields(value['process_epoch'], require)
    require(all(type(value[key]) is str and value[key] == submission[key]
                for key in ('request_id', 'session_id'))
            and value['process_epoch'] == submission['process_epoch']
            and type(value['request_sha256']) is str and value['request_sha256'] == digest,
            'response_mismatch', 'Receipt must bind the complete exact submission.')
    state, reason, result = value['state'], value['reason'], value['adapter_result']
    require(type(state) is str, 'invalid_response', 'Invalid receipt state.')
    if state in ('accepted', 'dispatched'):
        valid = reason is None and result is None
    elif state == 'adapter_returned':
        valid = reason is None
        result_fields(result, submission, require, digest)
    elif state in ('refused', 'unknown', 'not_found'):
        valid = (result is None and type(reason) is str and _REASON.fullmatch(reason) is not None
                 and (state != 'not_found' or reason == 'history_missing'))
    else:
        valid = False
    require(valid, 'invalid_response', 'Invalid receipt chronology or result shape.')
