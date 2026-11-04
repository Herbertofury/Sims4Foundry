"""Install and retire explicitly selected test mods without overwriting user mods."""
import os
from pathlib import Path, PurePosixPath
import uuid

from . import discovery, mods
from .errors import FoundryError
from .io import digest, external_path, new_json, plain_path
from .neutral_records import read_json
from .neutral_profile_lock import directory_identity, profile_lock

MAX_TEST_MODS = 64
MAX_TEST_BYTES = 2 * 1024 * 1024 * 1024
GAME_IMAGES = ('TS4_x64.exe', 'TS4_DX9_x64.exe', 'TS4.exe',
               'TS4_DX9.exe', 'TS4_Launcher.exe', 'TS4_Launcher_x64.exe')


def _no_game_candidates():
    # A point-in-time snapshot does not prevent a concurrent game launch.
    for image in GAME_IMAGES:
        if discovery._snapshot_image_pids(image):
            raise FoundryError('game_running', 'A Sims process was observed; close it before changing test mods.')



def _profile(path):
    root = plain_path(path)
    if root.name.casefold() != 'the sims 4' or not root.is_dir():
        raise FoundryError('profile', 'Select the existing Sims 4 user profile.')
    target = plain_path(root / 'Mods')
    if not target.is_dir():
        raise FoundryError('profile', 'The selected profile has no Mods directory.')
    return root, target


def _signed(value):
    return dict(value, record_sha256=digest(value))


def _validate_record(record):
    fields = {'schema', 'kind', 'profile', 'session_id', 'manifest_sha256', 'files', 'record_sha256'}
    if (not isinstance(record, dict) or set(record) != fields
            or type(record['schema']) is not int or record['schema'] != 1
            or record['kind'] != 'foundry_test_install'
            or digest({k:v for k,v in record.items() if k != 'record_sha256'}) != record['record_sha256']):
        raise FoundryError('invalid_record', 'The test installation record is invalid or changed.')
    if (type(record['session_id']) is not str or len(record['session_id']) != 32
            or any(c not in '0123456789abcdef' for c in record['session_id'])
            or not isinstance(record['profile'], str) or not isinstance(record['files'], list)
            or not 1 <= len(record['files']) <= MAX_TEST_MODS):
        raise FoundryError('invalid_record', 'The test installation record is malformed.')
    seen = set()
    for row in record['files']:
        if (not isinstance(row, dict) or set(row) != {'name','bytes','sha256'}
                or not mods._mod_relative(row['name'])
                or PurePosixPath(row['name']).name != row['name']
                or Path(row['name']).suffix.lower() not in ('.package','.ts4script')
                or type(row['bytes']) is not int or not 4 <= row['bytes'] <= mods.MAX_ARTIFACT
                or type(row['sha256']) is not str or len(row['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in row['sha256'])
                or row['name'].casefold() in seen):
            raise FoundryError('invalid_record', 'A test artifact record is malformed.')
        seen.add(row['name'].casefold())
    if sum(row['bytes'] for row in record['files']) > MAX_TEST_BYTES:
        raise FoundryError('invalid_record', 'The test artifact set exceeds its total byte bound.')
    return record


def _check_existing_names(mod_root, names, owned_directory=None):
    present = {p.name.casefold() for p in mods._inventory_files(mod_root)
               if owned_directory is None or p.parent != owned_directory}
    if present.intersection(names):
        raise FoundryError('duplicate_mod_name', 'A selected filename is already present in Mods; no files were changed.')


def _validate_files(directory, files):
    children = []
    with os.scandir(directory) as entries:
        for child in entries:
            children.append(child)
            if len(children) > MAX_TEST_MODS:
                raise FoundryError('directory_changed', 'The test folder exceeds its expected size.')
    expected = {r['name'].casefold():r for r in files}
    if len(children) != len(expected):
        raise FoundryError('directory_changed', 'The test folder contains missing or additional files.')
    for child in children:
        row = expected.get(child.name.casefold())
        if row is None or child.name != row['name'] or not child.is_file(follow_symlinks=False) or child.is_symlink():
            raise FoundryError('directory_changed', 'The test folder contents changed.')
        actual = mods.inspect_artifact(child.path)
        if actual['sha256'] != row['sha256'] or actual['bytes'] != row['bytes']:
            raise FoundryError('directory_changed', 'A test artifact was edited; no files were moved.')


def _profile_admission(root, profile_session):
    present, _marker = _optional_record(root / '.foundry-test-profile.json')
    if profile_session is None:
        if present:
            raise FoundryError('profile_session_required', 'A marked Foundry profile requires its explicit activation session.')
        return None
    # Runtime import avoids the profile module's process-check import cycle.
    from . import neutral_profile
    return neutral_profile.test_mod_binding(root, profile_session)


def _validate_profile_binding(binding, record, profile_identity):
    if binding is None:
        return
    fields = {'profile_session', 'profile', 'session_id', 'activation_record_sha256', 'original_identity', 'test_identity'}
    if (not isinstance(binding, dict) or set(binding) != fields
            or type(binding['profile_session']) is not str
            or binding['profile'] != record['profile']
            or type(binding['session_id']) is not str or len(binding['session_id']) != 32
            or any(c not in '0123456789abcdef' for c in binding['session_id'])
            or type(binding['activation_record_sha256']) is not str or len(binding['activation_record_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in binding['activation_record_sha256'])
            or binding['test_identity'] != profile_identity):
        raise FoundryError('invalid_record', 'The stored profile activation binding is invalid.')
    if str(external_path(binding['profile_session'])) != binding['profile_session']:
        raise FoundryError('invalid_record', 'The stored profile session must be absolute.')
    for name in ('original_identity', 'test_identity'):
        identity = binding[name]
        if (not isinstance(identity, dict) or set(identity) != {'device', 'inode'}
                or any(type(identity[k]) is not int or identity[k] <= 0 for k in identity)):
            raise FoundryError('invalid_record', 'The stored profile activation identity is invalid.')


def _require_directories(root, mod_root, identities, test_directory=None):
    if (directory_identity(root) != identities['profile_identity']
            or directory_identity(mod_root) != identities['mods_identity']
            or (test_directory is not None and directory_identity(test_directory) != identities['test_identity'])):
        raise FoundryError('directory_changed', 'The exact profile, Mods, or test directory was replaced; no further move is authorized.')
    binding = identities['profile_binding']
    current = _profile_admission(root, binding['profile_session'] if binding is not None else None)
    if current != binding:
        raise FoundryError('profile_unverified', 'The stored profile activation binding changed.')


def _install_identities(session, record):
    exists, value = _optional_record(session / 'install-identities.json')
    if not exists:
        raise FoundryError('identity_unavailable', 'This historical install lacks directory identities; retirement is unavailable.')
    fields = {'schema', 'kind', 'record_sha256', 'profile_identity', 'mods_identity', 'test_identity', 'profile_binding'}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value['schema']) is not int or value['schema'] != 2
            or value['kind'] != 'foundry_test_install_identities'
            or value['record_sha256'] != record['record_sha256']):
        raise FoundryError('invalid_record', 'Install directory identities do not match this session.')
    for name in ('profile_identity', 'mods_identity', 'test_identity'):
        identity = value[name]
        if (not isinstance(identity, dict) or set(identity) != {'device', 'inode'}
                or any(type(identity[k]) is not int or identity[k] <= 0 for k in identity)):
            raise FoundryError('invalid_record', 'Install directory identity is invalid.')
    _validate_profile_binding(value['profile_binding'], record, value['profile_identity'])
    return value


def install(manifest, profile, session_directory, *, profile_session=None, process_check=None):
    if os.name != 'nt':
        raise FoundryError('windows_required', 'Test-mod installation currently requires Windows.')
    with profile_lock(plain_path(profile)):
        return _install_locked(manifest, profile, session_directory, profile_session=profile_session, process_check=process_check)


def _install_locked(manifest, profile, session_directory, *, profile_session=None, process_check=None):
    mods._manifest(manifest)
    rows = manifest['artifacts']
    if not 1 <= len(rows) <= MAX_TEST_MODS or sum(r['bytes'] for r in rows) > MAX_TEST_BYTES:
        raise FoundryError('test_mod_bound', 'Select between one and 64 bounded test artifacts.')
    # Script mods are loaded only one folder deep, so keep source basenames in
    # Mods/FoundryTest and require a flat selection with no name collisions.
    names = [r['name'].casefold() for r in rows]
    if any('/' in r['relative'] for r in rows) or len(set(names)) != len(names):
        raise FoundryError('test_mod_layout', 'Test artifact selection must be flat with unique names.')
    root, mod_root = _profile(profile)
    identities = {'schema': 2, 'kind': 'foundry_test_install_identities',
                  'profile_identity': directory_identity(root), 'mods_identity': directory_identity(mod_root),
                  'profile_binding': _profile_admission(root, profile_session)}
    session = external_path(session_directory)
    if session.exists() or session.is_symlink():
        raise FoundryError('session_exists', 'Use a new external session directory.')
    target = mod_root / 'FoundryTest'
    if target.exists() or target.is_symlink():
        raise FoundryError('target_exists', 'A FoundryTest folder already exists; no files were replaced.')
    _check_existing_names(mod_root, names)
    for row in rows:
        actual = mods.inspect_artifact(row['source'])
        if actual['sha256'] != row['sha256'] or actual['bytes'] != row['bytes']:
            raise FoundryError('source_changed', 'A test artifact no longer matches its inventory.')
    check = process_check or _no_game_candidates
    check()
    _require_directories(root, mod_root, identities)
    session_id = uuid.uuid4().hex
    files = [{'name':r['name'], 'bytes':r['bytes'], 'sha256':r['sha256']} for r in rows]
    record = _signed({'schema':1, 'kind':'foundry_test_install', 'profile':str(root),
                      'session_id':session_id, 'manifest_sha256':manifest['manifest_sha256'], 'files':files})
    # Intent is durable before any copy; no attempt overwrites an earlier session.
    session.mkdir(parents=True, exist_ok=False)
    new_json(session / 'install-intent.json', record)
    _require_directories(root, mod_root, identities)
    staging = root / ('.foundry-stage-' + session_id)
    staging.mkdir(exist_ok=False)
    identities.update(record_sha256=record['record_sha256'], test_identity=directory_identity(staging))
    new_json(session / 'install-identities.json', identities)
    for row in rows:
        _require_directories(root, mod_root, identities, staging)
        source = plain_path(row['source'])
        destination = staging / row['name']
        with source.open('rb') as src, destination.open('xb') as dst:
            mods._copy_artifact(src, dst, row['bytes'])
            dst.flush()
            os.fsync(dst.fileno())
        if (destination.stat().st_size != row['bytes']
                or mods._hash_artifact(destination, row['bytes'], 'copy_changed') != row['sha256']):
            raise FoundryError('copy_changed', 'Staging is incomplete; no test folder was published.')
    check()
    _require_directories(root, mod_root, identities, staging)
    _check_existing_names(mod_root, names)
    _validate_files(staging, files)
    check()
    if target.exists() or target.is_symlink():
        raise FoundryError('target_exists', 'Another FoundryTest folder appeared; no replacement occurred.')
    # A native process scan can take time. Check selected paths, collisions and
    # exact staged bytes AFTER the last scan, not only before it.
    _require_directories(root, mod_root, identities, staging)
    _check_existing_names(mod_root, names)
    _validate_files(staging, files)
    _require_directories(root, mod_root, identities, staging)
    # Windows directory rename fails on an existing destination. All files
    # become visible together; the staging directory is outside Mods.
    try:
        os.rename(staging, target)
    except OSError as error:
        return {'ok':False, 'outcome':'unresolved', 'session_id':session_id,
                'message':'Test-folder publication could not be confirmed; inspect session status before any retry.',
                'error_type':type(error).__name__}
    try:
        # Preserve a moved folder on any uncertainty. Never blindly roll back
        # or call a historical receipt proof of current content integrity.
        _require_directories(root, mod_root, identities, target)
        _check_existing_names(mod_root, names, owned_directory=target)
        _validate_files(target, files)
        _require_directories(root, mod_root, identities, target)
        new_json(session / 'install-complete.json', record)
    except Exception as error:
        return {'ok':False, 'outcome':'unresolved', 'installed':True, 'target':str(target),
                'session_id':session_id, 'error_type':type(error).__name__,
                'message':'The test folder moved into Mods, but final verification or its completion receipt failed.'}
    return {'ok':True, 'installed':True, 'target':str(target), 'session_id':session_id,
            'artifact_count':len(files), 'session':str(session), 'existing_mods_changed':False}


def retire(session_directory, *, process_check=None):
    if os.name != 'nt':
        raise FoundryError('windows_required', 'Test-mod retirement currently requires Windows.')
    session = external_path(session_directory)
    record = _validate_record(read_json(session / 'install-complete.json'))
    with profile_lock(plain_path(record['profile'])):
        return _retire_locked(session, record, process_check=process_check)


def _retire_locked(session, record, *, process_check=None):
    if _validate_record(read_json(session / 'install-complete.json')) != record:
        raise FoundryError('invalid_record', 'Install completion changed while acquiring the profile lock.')
    if read_json(session / 'install-intent.json') != record:
        raise FoundryError('invalid_record', 'The original install intent does not match its completion.')
    root, mod_root = _profile(record['profile'])
    target = plain_path(mod_root / 'FoundryTest')
    retired = plain_path(root / ('.foundry-retired-' + record['session_id']))
    identities = _install_identities(session, record)
    _require_directories(root, mod_root, identities, target)
    if not target.is_dir() or retired.exists() or retired.is_symlink():
        raise FoundryError('target_changed', 'The exact test folder or recovery destination is unavailable.')
    _validate_files(target, record['files'])
    check = process_check or _no_game_candidates
    check()
    _require_directories(root, mod_root, identities, target)
    new_json(session / 'retire-intent.json', record)
    check()
    _require_directories(root, mod_root, identities, target)
    _validate_files(target, record['files'])
    check()
    _require_directories(root, mod_root, identities, target)
    plain_path(retired)
    if retired.exists() or retired.is_symlink():
        raise FoundryError('target_changed', 'The recovery destination appeared; no files were moved.')
    _validate_files(target, record['files'])
    _require_directories(root, mod_root, identities, target)
    try:
        os.rename(target, retired)
    except OSError as error:
        return {'ok':False, 'outcome':'unresolved', 'session_id':record['session_id'],
                'message':'Retirement could not be confirmed; inspect session status before any retry.',
                'error_type':type(error).__name__}
    try:
        _require_directories(root, mod_root, identities, retired)
        _validate_files(retired, record['files'])
        _require_directories(root, mod_root, identities, retired)
        new_json(session / 'retire-complete.json', {'schema':1, 'kind':'foundry_test_retirement',
                 'session_id':record['session_id'], 'recovered':str(retired),
                 'record_sha256':record['record_sha256']})
    except Exception as error:
        return {'ok':False, 'outcome':'unresolved', 'retired':True, 'recovered':str(retired),
                'error_type':type(error).__name__,
                'message':'Test files moved out of Mods, but final verification or the receipt failed.'}
    return {'ok':True, 'retired':True, 'recovered':str(retired), 'deleted':False}


def status(session_directory):
    """Read receipts and current path presence; never replay a mutation."""
    session = external_path(session_directory)
    intent = _validate_record(read_json(session / 'install-intent.json'))
    complete_path = session / 'install-complete.json'
    retired_intent_path = session / 'retire-intent.json'
    retired_complete_path = session / 'retire-complete.json'
    install_complete, complete = _optional_record(complete_path)
    retirement_started, retirement = _optional_record(retired_intent_path)
    retired_complete, retired_record = _optional_record(retired_complete_path)
    if install_complete and complete != intent:
        raise FoundryError('invalid_record', 'Install receipts disagree.')
    if retirement_started and retirement != intent:
        raise FoundryError('invalid_record', 'Retirement intent differs from installation.')
    if (retirement_started and not install_complete) or (retired_complete and not retirement_started):
        raise FoundryError('invalid_record', 'Lifecycle receipt history is incomplete; no terminal state is inferred.')
    root, mod_root = _profile(intent['profile'])
    target = mod_root / 'FoundryTest'
    staging = root / ('.foundry-stage-' + intent['session_id'])
    recovery = root / ('.foundry-retired-' + intent['session_id'])
    if retired_complete:
        expected = {'schema': 1, 'kind': 'foundry_test_retirement',
                    'session_id': intent['session_id'], 'recovered': str(recovery),
                    'record_sha256': intent['record_sha256']}
        if retired_record != expected or type(retired_record.get('schema')) is not int:
            raise FoundryError('invalid_record', 'Retirement receipt differs from installation.')
    state = ('retired_recorded' if retired_complete else
             'retirement_unresolved' if retirement_started else
             'install_recorded' if install_complete else 'installation_unresolved')
    return {'ok':True, 'state':state, 'session_id':intent['session_id'],
            'target_present':target.exists(), 'staging_present':staging.exists(),
            'recovery_present':recovery.exists(), 'target':str(target),
            'staging':str(staging), 'recovery':str(recovery), 'read_only':True}


def _optional_record(path):
    # Only actual absence is missing. A FIFO, directory, broken link or denied
    # metadata read must not silently become an absent completion record.
    try:
        path.lstat()
    except FileNotFoundError:
        return False, None
    return True, read_json(path)
