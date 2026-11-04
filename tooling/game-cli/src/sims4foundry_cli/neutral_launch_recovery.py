"""Resolve only a handoff proven never dispatched by this coordinator.

This is not cancellation of EA work. A dispatch exception, timeout, missing
process, or terminated process is insufficient. Immutable receipts and the
profile lock serialize trusted cooperating commands, not hostile file writers.
"""
from .errors import FoundryError
from .io import digest, external_path, new_json
from .neutral_records import read_json

RESOLUTION_NAME = 'launch-resolved-before-dispatch.json'
_MISSING = object()


def _optional(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return _MISSING
    return read_json(path)


def _proof(profile_session, activation, reservation):
    from . import neutral_launch, neutral_profile
    session = external_path(reservation['launch_session'])
    intent = neutral_launch._read_intent(session)
    prepared = neutral_profile._prepared(profile_session, activation)
    expected_binding = {
        'profile': activation['profile'], 'session_id': activation['session_id'],
        'activation_record_sha256': activation['record_sha256'],
        'original_identity': activation['original_identity'],
        'test_identity': prepared['test_identity'] if prepared else None,
    }
    if (intent['schema'] != 2 or intent['request_id'] != reservation['request_id']
            or intent['profile_session'] != str(profile_session)
            or intent['profile_binding'] != expected_binding):
        raise FoundryError('launch_reconciliation_refused', 'The launch does not belong to this exact profile activation.')
    proof = read_json(session / 'launch-not-dispatched.json')
    expected = {'schema': 1, 'kind': 'foundry_ea_launch_not_dispatched',
                'request_id': intent['request_id'], 'dispatch_attempted': False}
    if (proof != expected or type(proof.get('schema')) is not int
            or proof.get('dispatch_attempted') is not False):
        raise FoundryError('launch_reconciliation_refused', 'Exact never-dispatched evidence is required.')
    # Evidence produced after a handoff or consent contradicts the no-dispatch
    # path. Existing malformed or nonregular entries also fail, never disappear.
    for name in ('launch-handoff.json', 'launch-observation.json', 'ea-consent-intent.json', 'ea-consent-result.json'):
        try:
            (session / name).lstat()
        except FileNotFoundError:
            continue
        raise FoundryError('launch_reconciliation_refused', 'Contradictory handoff evidence exists; preserve and inspect it.')
    return intent, proof


def _expected(activation, reservation, intent, proof):
    value = {'schema': 1, 'kind': 'foundry_launch_resolved_before_dispatch',
             'session_id': activation['session_id'],
             'activation_record_sha256': activation['record_sha256'],
             'reservation_sha256': reservation['record_sha256'],
             'launch_session': reservation['launch_session'],
             'request_id': reservation['request_id'],
             'launch_intent_sha256': intent['record_sha256'],
             'not_dispatched_sha256': digest(proof), 'dispatch_attempted': False}
    value['record_sha256'] = digest(value)
    return value


def read_resolution(profile_session, activation, reservation):
    """Return validated resolution or None. Caller holds the lifecycle lock.

Read-only status may also call this without a lock; its result is a snapshot.
Access errors, changed proof, and malformed optional records propagate.
"""
    result = _optional(profile_session / RESOLUTION_NAME)
    if result is _MISSING:
        return None
    if reservation is None:
        raise FoundryError('launch_reconciliation_refused', 'Resolution lacks its preserved launch reservation.')
    intent, proof = _proof(profile_session, activation, reservation)
    expected = _expected(activation, reservation, intent, proof)
    if (result != expected or type(result.get('schema')) is not int
            or result.get('dispatch_attempted') is not False):
        raise FoundryError('launch_reconciliation_refused', 'Launch resolution is malformed or changed.')
    return result


def reconcile_not_dispatched(profile_session, launch_session):
    """Record proven pre-dispatch failure without deleting or replaying anything."""
    from . import neutral_profile
    profile_session = external_path(profile_session)
    launch_session = external_path(launch_session)
    with neutral_profile.active_profile_guard(profile_session):
        activation = neutral_profile._record(profile_session)
        reservation = neutral_profile._launch_reservation(profile_session, activation)
        if reservation is None or reservation['launch_session'] != str(launch_session):
            raise FoundryError('launch_reconciliation_refused', 'The exact launch reservation is required.')
        prior = read_resolution(profile_session, activation, reservation)
        if prior is not None:
            return {'ok': True, 'outcome': 'resolved_before_dispatch',
                    'request_id': reservation['request_id'], 'already_recorded': True,
                    'profile_restored': False, 'dispatch_attempted': False}
        intent, proof = _proof(profile_session, activation, reservation)
        result = _expected(activation, reservation, intent, proof)
        # Check again after all evidence reads, immediately before publication.
        current = neutral_profile._record(profile_session)
        if (current != activation
                or neutral_profile._launch_reservation(profile_session, current) != reservation
                or _proof(profile_session, current, reservation) != (intent, proof)):
            raise FoundryError('launch_reconciliation_refused', 'Launch evidence changed during reconciliation.')
        try:
            new_json(profile_session / RESOLUTION_NAME, result)
            if read_resolution(profile_session, activation, reservation) != result:
                raise FoundryError('launch_reconciliation_refused', 'Cannot confirm the recorded resolution.')
        except Exception as error:
            return {'ok': False, 'outcome': 'unresolved', 'request_id': reservation['request_id'],
                    'error_type': type(error).__name__, 'profile_restored': False,
                    'message': 'Resolution recording is uncertain; inspect status without replaying the launch.'}
        return {'ok': True, 'outcome': 'resolved_before_dispatch',
                'request_id': reservation['request_id'], 'already_recorded': False,
                'profile_restored': False, 'dispatch_attempted': False}
