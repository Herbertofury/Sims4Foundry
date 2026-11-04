"""Pure schema-3 Live read validation, mirrored verbatim across runtimes.

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
            and type(value['mode']) is str and value['mode'] == 'live',
            'invalid_context', 'Exact Live context and generation are required.')


def submission_fields(value, require):
    object_fields(value, {'schema', 'kind', 'request_id', 'session_id', 'process_epoch',
                         'operation', 'arguments', 'ttl_seconds', 'expected_context'},
                  require, 'invalid_envelope')
    require(type(value['schema']) is int and value['schema'] == 3
            and type(value['kind']) is str and value['kind'] == 'submit',
            'invalid_envelope', 'Expected explicit schema 3 submit.')
    require(token(value['request_id']) and token(value['session_id']),
            'invalid_identity', 'Expected exact request and session IDs.')
    epoch_fields(value['process_epoch'], require)
    require(type(value['operation']) is str and value['operation'] == 'live.observe_queue',
            'invalid_operation', 'Only the distinct Live queue read is supported.')
    object_fields(value['arguments'], {'actor_sim_id'}, require, 'invalid_arguments')
    require(decimal(value['arguments']['actor_sim_id']), 'invalid_arguments',
            'Actor ID must be canonical positive uint64 decimal text.')
    ttl = value['ttl_seconds']
    require(type(ttl) in (int, float) and 0 < ttl <= 30 and math.isfinite(ttl),
            'invalid_ttl', 'TTL must be finite, positive and at most thirty seconds.')
    context_fields(value['expected_context'], require)


def query_fields(value, require):
    object_fields(value, {'schema', 'kind', 'request_id', 'session_id', 'process_epoch',
                         'request_sha256'}, require, 'invalid_envelope')
    require(type(value['schema']) is int and value['schema'] == 3
            and type(value['kind']) is str and value['kind'] == 'receipt_read',
            'invalid_envelope', 'Expected explicit schema 3 receipt_read.')
    require(token(value['request_id']) and token(value['session_id'])
            and type(value['request_sha256']) is str
            and _HASH.fullmatch(value['request_sha256']) is not None,
            'invalid_identity', 'Invalid receipt lookup identity.')
    epoch_fields(value['process_epoch'], require)


def result_fields(value, submission, require):
    object_fields(value, {'schema_version', 'outcome', 'game_qualified', 'read_status',
                         'observation', 'reason'}, require)
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and type(value['outcome']) is str and value['outcome'] == 'unverified'
            and value['game_qualified'] is False and type(value['read_status']) is str,
            'invalid_result', 'A source read cannot claim game qualification or verification.')
    if value['read_status'] == 'unavailable':
        require(value['observation'] is None and type(value['reason']) is str
                and value['reason'] in ('row_limit_exceeded', 'result_budget_exceeded'),
                'invalid_result', 'Whole-read unavailable must not contain partial observations.')
        return
    require(value['read_status'] == 'observed' and value['reason'] is None,
            'invalid_result', 'Expected observed or bounded whole-read unavailable.')
    dto = value['observation']
    flags = {'schema_version': 1, 'source': 'source_only_injected_readers',
             'source_only': True, 'read_only': True, 'live_qualified': False,
             'independent_evidence': False, 'atomic_snapshot': False,
             'context_source': 'injected_host_declaration', 'identity_scope': 'this_call_only',
             'selection_authority': False, 'cancellation_authority': False,
             'action_outcome': 'unknown'}
    object_fields(dto, set(flags) | {'request_id', 'context', 'actor_sim_id',
                                    'actor', 'queued', 'running'}, require)
    require(all(type(dto[key]) is type(expected) and dto[key] == expected
                for key, expected in flags.items()), 'invalid_result',
            'Source observation authority flags are fixed.')
    require(type(dto['request_id']) is str and dto['request_id'] == submission['request_id']
            and type(dto['actor_sim_id']) is str
            and dto['actor_sim_id'] == submission['arguments']['actor_sim_id'],
            'response_mismatch', 'Observation must bind the exact request and actor.')
    expected = dict(submission['expected_context'])
    del expected['mode']
    expected['save_slot'] = str(expected['save_slot'])
    expected['zone_generation'] = str(expected['zone_generation'])
    expected['session_id'] = submission['session_id']
    expected['process_epoch'] = {key: str(item) for key, item in submission['process_epoch'].items()}
    object_fields(dto['context'], expected, require)
    object_fields(dto['context']['process_epoch'], {'pid', 'creation_filetime'}, require)
    require(dto['context'] == expected and
            all(type(dto['context'][key]) is str for key in expected if key != 'process_epoch')
            and all(type(item) is str for item in dto['context']['process_epoch'].values()),
            'response_mismatch', 'Observation must bind the full declared context.')
    actor = dto['actor']
    require(type(actor) is dict and type(actor.get('status')) is str,
            'invalid_result', 'Actor availability must be explicit.')
    actor_ok = actor['status'] == 'observed'
    if actor_ok:
        object_fields(actor, {'status'}, require)
    else:
        object_fields(actor, {'status', 'reason'}, require)
        require(actor['status'] == 'unavailable' and type(actor['reason']) is str
                and actor['reason'] in ('not_found', 'not_instanced', 'reader_exception',
                                        'identity_mismatch_or_unavailable'),
                'invalid_result', 'Invalid unavailable actor reason.')
    total = 0
    for channel_name in ('queued', 'running'):
        channel = dto[channel_name]
        require(type(channel) is dict and type(channel.get('status')) is str,
                'invalid_result', 'Channel availability must be explicit.')
        if channel['status'] == 'unavailable':
            object_fields(channel, {'status', 'rows', 'reason'}, require)
            reason = channel['reason']
            require(channel['rows'] is None and type(reason) is str
                    and reason in ('actor_unavailable', 'reader_exception', 'missing_field',
                                   'null_value', 'not_iterable')
                    and (reason != 'actor_unavailable' if actor_ok else reason == 'actor_unavailable'),
                    'invalid_result', 'An unavailable channel is never an empty queue.')
            continue
        object_fields(channel, {'status', 'rows'}, require)
        require(actor_ok and channel['status'] == 'observed' and type(channel['rows']) is list,
                'invalid_result', 'Observed rows require an observed actor.')
        total += len(channel['rows'])
        require(total <= MAX_ROWS, 'invalid_result', 'Combined rows exceed the fixed integration bound.')
        for index, row in enumerate(channel['rows']):
            object_fields(row, {'index', 'tuning_guid64', 'runtime_instance_id'}, require)
            require(type(row['index']) is int and row['index'] == index,
                    'invalid_result', 'Channel indices must be contiguous and ordered.')
            for name in ('tuning_guid64', 'runtime_instance_id'):
                field = row[name]
                require(type(field) is dict and type(field.get('status')) is str,
                        'invalid_result', 'Field availability must be explicit.')
                if field['status'] == 'available':
                    object_fields(field, {'status', 'value'}, require)
                    require(decimal(field['value']), 'invalid_result', 'Invalid exact field ID.')
                else:
                    object_fields(field, {'status', 'value', 'reason'}, require)
                    require(field['status'] == 'unavailable' and field['value'] is None
                            and type(field['reason']) is str
                            and field['reason'] in ('reader_exception', 'missing_field',
                                                   'null_value', 'invalid_value'),
                            'invalid_result', 'Unavailable field must retain a fixed reason and null value.')


def receipt_fields(value, submission, digest, require):
    object_fields(value, {'schema', 'kind', 'request_id', 'session_id', 'process_epoch',
                         'request_sha256', 'state', 'reason', 'adapter_result',
                         'independent_evidence'}, require, 'invalid_response')
    require(type(value['schema']) is int and value['schema'] == 3
            and type(value['kind']) is str and value['kind'] == 'receipt'
            and value['independent_evidence'] is False,
            'invalid_response', 'Expected an unqualified schema 3 receipt.')
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
        result_fields(result, submission, require)
    elif state in ('refused', 'unknown', 'not_found'):
        valid = (result is None and type(reason) is str and _REASON.fullmatch(reason) is not None
                 and (state != 'not_found' or reason == 'history_missing'))
    else:
        valid = False
    require(valid, 'invalid_response', 'Invalid receipt chronology or result shape.')
