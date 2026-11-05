"""Prepare, activate and recover an explicit mod-neutral test profile on Windows.

Original and test directories are moved, never deleted. A stable OS-held lock
serializes cooperating lifecycle/launch commands, not external filesystem actors
or game launches. Process and pathname identity checks remain snapshots.
"""
from contextlib import contextmanager
import os
import stat
import uuid

from .errors import FoundryError
from .io import MAX_JSON, digest, external_path, loads, new_json, plain_path
from .neutral_testmods import _no_game_candidates
from .neutral_profile_lock import profile_lock as _profile_lock

MAX_OPTIONS_BYTES = 4 * 1024 * 1024
# Module-local fixture seams. Tests must not change global os.name or os.rename.
_WINDOWS = os.name == 'nt'
_rename_directory = os.rename
_MISSING = object()


def _require_windows():
    if not _WINDOWS:
        raise FoundryError('windows_required', 'Profile mutation currently requires Windows.')


def _lstat_optional(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _exists(path):
    return _lstat_optional(path) is not None


def _regular(info):
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, 'st_file_attributes', 0) & 0x400)


def _file_snapshot(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


def _read_regular(path, limit, code='invalid_profile_record'):
    """Bounded, no-follow descriptor read; access errors never trigger a fallback."""
    path = plain_path(path)
    before = path.lstat()
    if not _regular(before) or not 0 <= before.st_size <= limit:
        raise FoundryError(code, 'Expected a bounded regular file with no links.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not _regular(opened) or _file_snapshot(opened) != _file_snapshot(before):
            raise FoundryError(code, 'File identity changed before reading.')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        current = plain_path(path).lstat()
        if (len(raw) != before.st_size or len(raw) > limit or not _regular(current)
                or _file_snapshot(before) != _file_snapshot(after)
                or _file_snapshot(before) != _file_snapshot(current)):
            raise FoundryError(code, 'File changed while being read.')
        return raw
    finally:
        os.close(fd)


def _read_json(path):
    return loads(_read_regular(path, MAX_JSON))


def _optional_json(path):
    if _lstat_optional(path) is None:
        return _MISSING
    return _read_json(path)



def _identity(path):
    path = plain_path(path)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise FoundryError('linked_path', 'Linked profile directories are not accepted.')
    if not stat.S_ISDIR(info.st_mode):
        raise FoundryError('profile_missing', 'Expected an existing unlinked directory.')
    if not info.st_dev or not info.st_ino:
        raise FoundryError('identity_unavailable', 'Directory identity is not available.')
    return {'device': info.st_dev, 'inode': info.st_ino}


def _record(session):
    value = _read_json(session / 'activation-intent.json')
    keys = {'schema', 'kind', 'session_id', 'profile', 'original_identity', 'record_sha256'}
    if (not isinstance(value, dict) or set(value) != keys or type(value['schema']) is not int
            or value['schema'] != 1 or value['kind'] != 'foundry_profile_activation'
            or type(value['session_id']) is not str or len(value['session_id']) != 32
            or any(c not in '0123456789abcdef' for c in value['session_id'])
            or type(value['profile']) is not str
            or digest({k: v for k, v in value.items() if k != 'record_sha256'}) != value['record_sha256']):
        raise FoundryError('invalid_profile_record', 'Profile activation intent is invalid.')
    _valid_identity(value['original_identity'])
    return value


def _valid_identity(value):
    if (not isinstance(value, dict) or set(value) != {'device', 'inode'}
            or any(type(value[k]) is not int or value[k] <= 0 for k in value)):
        raise FoundryError('invalid_profile_record', 'Directory identity is invalid.')


def _locations(record):
    profile = plain_path(record['profile'])
    if profile.name.casefold() != 'the sims 4' or str(profile) != record['profile']:
        raise FoundryError('invalid_profile_record', 'Profile path is not an absolute The Sims 4 path.')
    suffix = record['session_id']
    return (profile, plain_path(profile.parent / ('.foundry-original-' + suffix)),
            plain_path(profile.parent / ('.foundry-prepared-' + suffix)),
            plain_path(profile.parent / ('.foundry-test-' + suffix)))


def _prepared(session, record):
    value = _read_json(session / 'activation-prepared.json')
    if (not isinstance(value, dict) or set(value) != {'record_sha256', 'test_identity'}
            or value['record_sha256'] != record['record_sha256']):
        raise FoundryError('invalid_profile_record', 'Prepared profile record is invalid.')
    _valid_identity(value['test_identity'])
    return value


def _records(session, record):
    """Validate all present evidence, including contradictory terminal records."""
    if _record(session) != record:
        raise FoundryError('invalid_profile_record', 'Activation intent changed.')
    prepared = _prepared(session, record) if _exists(session / 'activation-prepared.json') else None
    complete = _optional_json(session / 'activation-complete.json')
    aborted = _optional_json(session / 'activation-aborted.json')
    intent = _optional_json(session / 'restore-intent.json')
    restored = _optional_json(session / 'restore-complete.json')
    if complete is not _MISSING and (prepared is None or complete != prepared):
        raise FoundryError('invalid_profile_record', 'Activation completion does not match preparation.')
    if aborted is not _MISSING:
        if (aborted != {'record_sha256': record['record_sha256']} or complete is not _MISSING
                or intent is not _MISSING or restored is not _MISSING):
            raise FoundryError('invalid_profile_record', 'Activation terminal records conflict.')
    if intent is not _MISSING and (prepared is None or intent != prepared or complete is _MISSING):
        raise FoundryError('invalid_profile_record', 'Restore intent does not match a completed activation.')
    if restored is not _MISSING and (intent is _MISSING or restored != {
            'record_sha256': record['record_sha256'], 'test_recovery': str(_locations(record)[3])}):
        raise FoundryError('invalid_profile_record', 'Restore completion does not match intent.')
    return prepared, complete is not _MISSING, aborted is not _MISSING, intent is not _MISSING, restored is not _MISSING


@contextmanager
def _session_guard(session_directory):
    session = external_path(session_directory)
    record = _record(session)
    with _profile_lock(_locations(record)[0]):
        if _record(session) != record:
            raise FoundryError('invalid_profile_record', 'Activation intent changed while locking.')
        yield session, record


def _no_launch_reservation(session):
    # A handed-off launcher can start later even when a process snapshot is empty.
    # Neither process absence nor a failed acknowledgment proves cancellation.
    from .neutral_launch_recovery import RESOLUTION_NAME, read_resolution
    if (_exists(session / 'launch-reservation.json')
            and not _exists(session / RESOLUTION_NAME)):
        raise FoundryError('launch_pending', 'A durable launch reservation blocks profile mutation; process absence is not cancellation.')
    record = _record(session)
    reservation = _launch_reservation(session, record)
    resolution = read_resolution(session, record, reservation)
    if reservation is not None and resolution is None:
        raise FoundryError('launch_pending', 'A durable launch reservation blocks profile mutation; process absence is not cancellation.')


def _launch_reservation(session, record):
    value = _optional_json(session / 'launch-reservation.json')
    if value is _MISSING:
        return None
    fields = {'schema', 'kind', 'session_id', 'activation_record_sha256',
              'launch_session', 'request_id', 'state', 'record_sha256'}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value['schema']) is not int or value['schema'] != 1
            or value['kind'] != 'foundry_profile_launch_reservation'
            or value['session_id'] != record['session_id']
            or value['activation_record_sha256'] != record['record_sha256']
            or value['state'] != 'pending' or type(value['launch_session']) is not str
            or type(value['request_id']) is not str or len(value['request_id']) != 32
            or any(c not in '0123456789abcdef' for c in value['request_id'])
            or digest({k: v for k, v in value.items() if k != 'record_sha256'}) != value['record_sha256']):
        raise FoundryError('invalid_profile_record', 'Launch reservation is invalid or belongs to another activation.')
    if str(external_path(value['launch_session'])) != value['launch_session']:
        raise FoundryError('invalid_profile_record', 'Launch reservation path is not absolute.')
    return value


def reserve_launch(session_directory, launch_session, request_id):
    """Create sticky admission evidence. Caller must hold active_profile_guard."""
    _require_windows()
    session = external_path(session_directory)
    record = _record(session)
    _no_launch_reservation(session)
    _require_state(_status(session, record)['state'] == 'activation_recorded',
                   'Launch reservation requires the exact active test profile.')
    if (type(request_id) is not str or len(request_id) != 32
            or any(c not in '0123456789abcdef' for c in request_id)):
        raise FoundryError('invalid_profile_record', 'Expected a bounded launch request identifier.')
    value = {'schema': 1, 'kind': 'foundry_profile_launch_reservation',
             'session_id': record['session_id'], 'activation_record_sha256': record['record_sha256'],
             'launch_session': str(external_path(launch_session)), 'request_id': request_id, 'state': 'pending'}
    value['record_sha256'] = digest(value)
    new_json(session / 'launch-reservation.json', value)
    return value


def validate_launch_reservation(session_directory, launch_session, request_id):
    """Validate the exact pending request while the caller holds the guard."""
    _require_windows()
    session = external_path(session_directory)
    from .neutral_launch_recovery import read_resolution
    record = _record(session)
    value = _launch_reservation(session, record)
    if (value is None or value['launch_session'] != str(external_path(launch_session))
            or value['request_id'] != request_id):
        raise FoundryError('launch_pending', 'Launch reservation does not match this exact request.')
    if read_resolution(session, record, value) is not None:
        raise FoundryError('launch_resolved', 'A resolved launch cannot be replayed or receive consent.')
    return value


def _is_original(path, record):
    return _exists(path) and _identity(path) == record['original_identity']


def _is_test(path, record, prepared):
    if not _exists(path) or _identity(path) != prepared['test_identity']:
        return False
    marker = path / '.foundry-test-profile.json'
    return (_exists(marker) and _read_json(marker) == {
        'session_id': record['session_id'], 'record_sha256': record['record_sha256']})


def _copy_options(profile, staging):
    source = plain_path(profile / 'Options.ini')
    if not _exists(source):
        return False
    raw = _read_regular(source, MAX_OPTIONS_BYTES, 'options_invalid')
    with (staging / 'Options.ini').open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return True


def _unresolved(record, error, message):
    return {'ok': False, 'outcome': 'unresolved', 'session_id': record['session_id'],
            'message': message, 'error_type': type(error).__name__}


def _require_state(condition, message, code='profile_changed'):
    if not condition:
        raise FoundryError(code, message)


def _active_locations(record, prepared):
    profile, backup, staging, recovery = _locations(record)
    return (_is_original(backup, record) and _is_test(profile, record, prepared)
            and not _exists(staging) and not _exists(recovery))


def _restored_locations(record, prepared):
    profile, backup, staging, recovery = _locations(record)
    return (_is_original(profile, record) and not _exists(backup)
            and not _exists(staging) and _is_test(recovery, record, prepared))


def activate(profile_path, session_directory, *, process_check=None):
    _require_windows()
    profile = plain_path(profile_path)
    if profile.name.casefold() != 'the sims 4':
        raise FoundryError('profile', 'Select the existing The Sims 4 profile.')
    with _profile_lock(profile):
        return _activate(profile, session_directory, process_check or _no_game_candidates)


def _activate(profile, session_directory, check):
    original = _identity(profile)
    if _exists(profile / '.foundry-test-profile.json'):
        raise FoundryError('profile_in_use', 'The selected profile already has a Foundry test marker.')
    session = external_path(session_directory)
    if _exists(session):
        raise FoundryError('session_exists', 'Use a new external session directory.')
    check()
    base = {'schema': 1, 'kind': 'foundry_profile_activation',
            'session_id': uuid.uuid4().hex, 'profile': str(profile), 'original_identity': original}
    record = dict(base, record_sha256=digest(base))
    _, backup, staging, recovery = _locations(record)
    if any(_exists(p) for p in (backup, staging, recovery)):
        raise FoundryError('profile_conflict', 'A profile preservation path already exists.')
    session.mkdir(parents=True, exist_ok=False)
    new_json(session / 'activation-intent.json', record)
    staging.mkdir(exist_ok=False)
    (staging / 'Mods').mkdir()
    (staging / 'saves').mkdir()
    (staging / 'Tray').mkdir()
    (staging / 'Mods' / 'Resource.cfg').write_text(
        'Priority 500\nPackedFile *.package\nPackedFile */*.package\n', encoding='ascii')
    options_copied = _copy_options(profile, staging)
    new_json(staging / '.foundry-test-profile.json',
             {'session_id': record['session_id'], 'record_sha256': record['record_sha256']})
    prepared = {'record_sha256': record['record_sha256'], 'test_identity': _identity(staging)}
    new_json(session / 'activation-prepared.json', prepared)
    check()
    _require_state(_is_original(profile, record) and _is_test(staging, record, prepared)
                   and not _exists(backup) and not _exists(recovery),
                   'Original or prepared profile changed before activation.')
    try:
        _rename_directory(profile, backup)
    except Exception as error:
        return _unresolved(record, error, 'Original profile move could not be confirmed; inspect status.')
    try:
        check()
        _require_state(_is_original(backup, record) and _is_test(staging, record, prepared)
                       and not _exists(profile) and not _exists(recovery),
                       'Profile locations changed during activation.')
        _rename_directory(staging, profile)
    except Exception as error:
        return _unresolved(record, error, 'Original was preserved but activation is unresolved; inspect status.')
    try:
        _require_state(_records(session, record) == (prepared, False, False, False, False)
                       and _active_locations(record, prepared), 'Activation evidence or identities changed.')
        new_json(session / 'activation-complete.json', prepared)
    except Exception as error:
        return _unresolved(record, error, 'Profiles were moved but activation completion is unresolved; inspect status.')
    return {'ok': True, 'activated': True, 'session_id': record['session_id'],
            'profile': str(profile), 'original_preserved': str(backup),
            'options_copied': options_copied, 'game_launched': False, 'mods_installed': False}


def _status(session, record):
    from .neutral_launch_recovery import read_resolution
    profile, backup, staging, recovery = _locations(record)
    prepared, completed, aborted, restore_intent, restore_complete = _records(session, record)
    original_at_profile = _is_original(profile, record)
    original_at_backup = _is_original(backup, record)
    test_at_profile = _is_test(profile, record, prepared) if prepared else False
    test_at_staging = _is_test(staging, record, prepared) if prepared else False
    test_at_recovery = _is_test(recovery, record, prepared) if prepared else False
    reservation = _launch_reservation(session, record)
    resolution = read_resolution(session, record, reservation)
    if aborted:
        state = 'activation_aborted' if original_at_profile and not _exists(backup) else 'profile_changed'
    elif restore_complete:
        state = 'restored_recorded' if _restored_locations(record, prepared) else 'profile_changed'
    elif restore_intent:
        state = 'restore_unresolved'
    elif completed:
        state = 'activation_recorded' if _active_locations(record, prepared) else 'profile_changed'
    else:
        state = 'activation_unresolved'
    return {'ok': True, 'state': state, 'session_id': record['session_id'],
            'record_sha256': record['record_sha256'],
            'activation_record_sha256': record['record_sha256'],
            'original_identity': dict(record['original_identity']),
            'test_identity': dict(prepared['test_identity']) if prepared else None,
            'profile': str(profile), 'original_backup': str(backup),
            'test_staging': str(staging), 'test_recovery': str(recovery),
            'original_at_profile': original_at_profile, 'original_at_backup': original_at_backup,
            'test_at_profile': test_at_profile, 'test_at_staging': test_at_staging,
            'test_at_recovery': test_at_recovery,
            'launch_reservation_present': reservation is not None,
            'launch_state': ('resolved_before_dispatch' if resolution is not None else
                             'pending' if reservation is not None else 'not_reserved'),
            'launch_resolution': resolution,
            'launch_reservation': reservation, 'read_only': True}


def status(session_directory):
    session = external_path(session_directory)
    return _status(session, _record(session))


@contextmanager
def active_profile_guard(session_directory):
    """Hold the lifecycle lock through the caller's final checks and dispatch.

    The caller must create a durable launch reservation before handing off a
    launch. This guard does not cancel or resolve any existing reservation.
    """
    _require_windows()
    with _session_guard(session_directory) as (session, record):
        state = _status(session, record)
        _require_state(state['state'] == 'activation_recorded',
                       'Launch requires an unchanged, completed test-profile activation.')
        yield state


def test_mod_binding(profile_path, session_directory):
    """Admit test-mod mutation under the caller-held shared profile lock.

    This is not a lock-acquiring entry point. A stored binding must still name
    the exact completed activation, marker, directory identities and resolved
    launch state; missing evidence never falls back to plain-profile admission.
    """
    _require_windows()
    session = external_path(session_directory)
    record = _record(session)
    if _locations(record)[0] != plain_path(profile_path):
        raise FoundryError('profile_unverified', 'The profile session names a different profile.')
    _no_launch_reservation(session)
    state = _status(session, record)
    _require_state(state['state'] == 'activation_recorded',
                   'Test-mod mutation requires the exact active test-profile marker and activation.',
                   'profile_unverified')
    fields = ('profile', 'session_id', 'activation_record_sha256', 'original_identity', 'test_identity')
    return dict({key: state[key] for key in fields}, profile_session=str(session))


def restore(session_directory, *, process_check=None):
    _require_windows()
    with _session_guard(session_directory) as (session, record):
        _no_launch_reservation(session)
        prepared, completed, aborted, intent, restored = _records(session, record)
        _require_state(completed and not aborted and not intent and not restored,
                       'Restoration requires an active session without pending restoration; inspect status or recover.')
        _require_state(_active_locations(record, prepared), 'Original or test profile identity does not match the session.')
        check = process_check or _no_game_candidates
        check()
        new_json(session / 'restore-intent.json', prepared)
        check()
        _require_state(_active_locations(record, prepared), 'Profile identity changed before restoration.')
        profile, _, _, recovery = _locations(record)
        try:
            _rename_directory(profile, recovery)
        except Exception as error:
            return _unresolved(record, error, 'Test profile move could not be confirmed; inspect status.')
        return _finish_restore(session, record, prepared, check)


def _finish_restore(session, record, prepared, check):
    profile, backup, staging, recovery = _locations(record)
    try:
        check()
        _require_state(_is_original(backup, record) and _is_test(recovery, record, prepared)
                       and not _exists(profile) and not _exists(staging),
                       'Original or preserved test profile changed before restoration.')
        _rename_directory(backup, profile)
    except Exception as error:
        return _unresolved(record, error, 'Original profile restoration could not be confirmed; inspect status.')
    return _record_restored(session, record, prepared)


def _record_restored(session, record, prepared):
    profile, _, _, recovery = _locations(record)
    try:
        _require_state(_records(session, record) == (prepared, True, False, True, False)
                       and _restored_locations(record, prepared), 'Restoration evidence or identities changed.')
        new_json(session / 'restore-complete.json',
                 {'record_sha256': record['record_sha256'], 'test_recovery': str(recovery)})
    except Exception as error:
        return _unresolved(record, error, 'The original may be present but restoration completion is unresolved.')
    return {'ok': True, 'restored': True, 'original_profile': str(profile),
            'test_profile_preserved': str(recovery), 'deleted': False}


def recover(session_directory, *, process_check=None):
    """Reconcile exact observable restore states; never blindly replay a move."""
    _require_windows()
    with _session_guard(session_directory) as (session, record):
        _no_launch_reservation(session)
        prepared, completed, aborted, intent, restored = _records(session, record)
        if restored:
            raise FoundryError('already_restored', 'A restoration completion already exists.')
        _require_state(completed and not aborted and intent, 'No pending restoration is recorded.', 'recovery_unavailable')
        profile, backup, staging, recovery = _locations(record)
        check = process_check or _no_game_candidates
        check()
        if _active_locations(record, prepared):
            # Intent was durable but the first move never happened. Exact source
            # identity and absent destination justify this still-pending move.
            try:
                _rename_directory(profile, recovery)
            except Exception as error:
                return _unresolved(record, error, 'Test profile preservation could not be confirmed; inspect status.')
        elif _restored_locations(record, prepared):
            return _record_restored(session, record, prepared)
        else:
            _require_state(_is_original(backup, record) and not _exists(profile)
                           and not _exists(staging) and _is_test(recovery, record, prepared),
                           'Current profile locations are ambiguous.', 'recovery_unavailable')
        return _finish_restore(session, record, prepared, check)


def reconcile_restored_receipt(session_directory):
    """Inspect an existing completion; never move profiles or publish receipts."""
    _require_windows()
    with _session_guard(session_directory) as (session, record):
        _no_launch_reservation(session)
        prepared, completed, aborted, intent, restored = _records(session, record)
        _require_state(prepared is not None and completed and not aborted and intent and restored,
                       'A completed restoration receipt is required.', 'recovery_unavailable')
        # The guard may create or initialize its sibling lock file. Ordinary
        # dict equality equates True and 1; canonical JSON digests preserve types.
        profile, _, _, recovery = _locations(record)
        def evidence_matches():
            if _records(session, record) != (prepared, True, False, True, True):
                return False
            expected_digest = digest(prepared)
            return (digest(_read_json(session / 'activation-complete.json')) == expected_digest
                    and digest(_read_json(session / 'restore-intent.json')) == expected_digest)
        for _ in range(2):
            _require_state(evidence_matches() and _restored_locations(record, prepared),
                           'Restoration evidence or identities differ.', 'recovery_unavailable')
        return {'ok': True, 'outcome': 'restoration_receipt_observed',
                'move_performed': False, 'profile_data_mutation_attempted': False,
                'lock_file_may_be_created_or_initialized': True,
                'original_profile': str(profile), 'test_profile_preserved': str(recovery),
                'saved_data_integrity_verified': False, 'launch_authorized': False}


def _preserved_test(record, prepared):
    _, _, staging, recovery = _locations(record)
    return (prepared is not None and
            ((_is_test(staging, record, prepared) and not _exists(recovery))
             or (_is_test(recovery, record, prepared) and not _exists(staging))))


def recover_activation(session_directory, *, process_check=None):
    """Restore the original only when interrupted activation is unambiguous."""
    _require_windows()
    with _session_guard(session_directory) as (session, record):
        _no_launch_reservation(session)
        prepared, completed, aborted, intent, restored = _records(session, record)
        _require_state(not any((completed, aborted, intent, restored)),
                       'Activation already has a terminal record.', 'recovery_unavailable')
        profile, backup, staging, recovery = _locations(record)
        check = process_check or _no_game_candidates
        check()
        if prepared and _active_locations(record, prepared):
            try:
                _rename_directory(profile, recovery)
            except Exception as error:
                return _unresolved(record, error, 'Test profile preservation could not be confirmed; inspect status.')
        if not _exists(profile) and _is_original(backup, record):
            try:
                check()
                _require_state(not _exists(profile) and _is_original(backup, record)
                               and _preserved_test(record, prepared),
                               'Original or preserved test changed before recovery.', 'recovery_unavailable')
                _rename_directory(backup, profile)
            except Exception as error:
                return _unresolved(record, error, 'Original profile recovery could not be confirmed; inspect status.')
        elif not _is_original(profile, record) or _exists(backup):
            raise FoundryError('recovery_unavailable', 'Activation left ambiguous profile locations.')
        try:
            _require_state(_is_original(profile, record) and not _exists(backup)
                           and (prepared is None or _preserved_test(record, prepared))
                           and _records(session, record) == (prepared, False, False, False, False),
                           'Activation evidence or preserved identities changed.', 'recovery_unavailable')
            new_json(session / 'activation-aborted.json', {'record_sha256': record['record_sha256']})
        except Exception as error:
            return _unresolved(record, error, 'Original may be present but activation recovery completion is unresolved.')
        return {'ok': True, 'activation_aborted': True, 'original_profile': str(profile),
                'test_staging_preserved': str(staging) if _exists(staging) else None,
                'test_profile_preserved': str(recovery) if _exists(recovery) else None, 'deleted': False}
