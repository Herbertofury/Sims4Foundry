"""Explicit, evidence-gated recovery of interrupted test-mod retirement.

Coordinates trusted writers through the shared profile lock. This does not
exclude a concurrent external game launch or hostile filesystem writer. Never
clear intent, delete files, roll back, or replay a move from an acknowledgment.
"""
import os

from . import neutral_testmods as testmods
from .errors import FoundryError
from .io import canonical, external_path, new_json, plain_path
from .neutral_profile_lock import directory_identity, profile_lock
from .neutral_records import read_json

_WINDOWS = os.name == 'nt'


def _rename_directory(source, destination):
    # Windows rename refuses an existing destination; no replace fallback.
    os.rename(source, destination)


def _present(path):
    try:
        plain_path(path).lstat()
    except FileNotFoundError:
        return False
    return True


def _completion(record, retired):
    return {'schema': 1, 'kind': 'foundry_test_retirement',
            'session_id': record['session_id'], 'recovered': str(retired),
            'record_sha256': record['record_sha256']}


def _evidence(session, record, identities, session_identity, completion):
    if directory_identity(session) != session_identity:
        raise FoundryError('invalid_record', 'The external session directory changed.')
    for name in ('install-intent.json', 'install-complete.json', 'retire-intent.json'):
        current = testmods._validate_record(read_json(session / name))
        if canonical(current) != canonical(record):
            raise FoundryError('invalid_record', 'Exact matching installation and retirement receipts are required.')
    if canonical(testmods._install_identities(session, record)) != canonical(identities):
        raise FoundryError('invalid_record', 'Stored directory identities or profile binding changed.')
    present, current = testmods._optional_record(session / 'retire-complete.json')
    if present != (completion is not None) or (present and canonical(current) != canonical(completion)):
        raise FoundryError('invalid_record', 'Retirement completion evidence changed or contradicts this recovery.')


def _location(root, mod_root, target, retired, staging, identities, record):
    testmods._require_directories(root, mod_root, identities)
    if _present(staging):
        raise FoundryError('retirement_recovery_refused', 'Unexpected staging evidence exists; preserve and inspect it.')
    original, moved = _present(target), _present(retired)
    if original == moved:
        raise FoundryError('retirement_recovery_refused', 'Exactly one original or retired test directory must exist.')
    location = target if original else retired
    testmods._require_directories(root, mod_root, identities, location)
    testmods._validate_files(location, record['files'])
    testmods._require_directories(root, mod_root, identities, location)
    return location


def recover_retirement(session_directory, *, process_check=None):
    """Finish one proven pending retirement; preserve every existing receipt."""
    if not _WINDOWS:
        raise FoundryError('windows_required', 'Test-mod retirement recovery currently requires Windows.')
    session = external_path(session_directory)
    session_identity = directory_identity(session)
    record = testmods._validate_record(read_json(session / 'install-complete.json'))
    root = plain_path(record['profile'])
    if str(root) != record['profile']:
        raise FoundryError('invalid_record', 'The stored profile path must be absolute and canonical.')
    with profile_lock(root):
        root, mod_root = testmods._profile(root)
        target = mod_root / 'FoundryTest'
        retired = root / ('.foundry-retired-' + record['session_id'])
        staging = root / ('.foundry-stage-' + record['session_id'])
        identities = testmods._install_identities(session, record)
        expected = _completion(record, retired)
        present, completion = testmods._optional_record(session / 'retire-complete.json')
        if present and canonical(completion) != canonical(expected):
            raise FoundryError('invalid_record', 'Retirement completion does not match this exact installation.')

        def verify(previous=None, receipt=completion):
            _evidence(session, record, identities, session_identity, receipt)
            location = _location(root, mod_root, target, retired, staging, identities, record)
            _evidence(session, record, identities, session_identity, receipt)
            if previous is not None and location != previous:
                raise FoundryError('retirement_recovery_refused', 'Test directory location changed during recovery.')
            if receipt is not None and location != retired:
                raise FoundryError('retirement_recovery_refused', 'Completion contradicts the current test directory location.')
            return location

        location = verify()
        check = process_check or testmods._no_game_candidates
        check()
        verify(location)
        check()
        verify(location)
        if present:
            return {'ok': True, 'retired': True, 'recovered': str(retired),
                    'deleted': False, 'already_recorded': True, 'move_performed': False}
        moved = False
        if location == target:
            try:
                _rename_directory(target, retired)
                moved = True
            except OSError as error:
                return _unresolved(record, error)
        # Acknowledged rename still needs fresh checks and durable evidence.
        # On ANY uncertainty retain intent and bytes for another explicit recovery.
        try:
            check()
            verify(retired)
            new_json(session / 'retire-complete.json', expected)
            verify(retired, receipt=expected)
        except Exception as error:
            return _unresolved(record, error)
        return {'ok': True, 'retired': True, 'recovered': str(retired),
                'deleted': False, 'already_recorded': False, 'move_performed': moved}


def _unresolved(record, error):
    return {'ok': False, 'outcome': 'unresolved', 'session_id': record['session_id'],
            'error_type': type(error).__name__,
            'message': 'Retirement recovery could not be confirmed. Preserve all evidence and inspect status before explicit recovery.'}
