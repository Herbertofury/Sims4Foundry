"""One-shot neutral EA launch handoff for an explicitly prepared test profile."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import time
import uuid

from . import discovery, launch, neutral_ea_context, neutral_ea_process, neutral_profile, neutral_testmods
from .errors import FoundryError
from .io import digest, external_path, new_json, plain_path
from .path_identity import same_windows_path
from .neutral_records import read_json

MAX_OBSERVE_SECONDS = 60.0


def _is_windows():
    return os.name == 'nt'


def _dispatch(url):
    if not _is_windows():
        raise FoundryError('windows_required', 'EA launch requires Windows.')
    os.startfile(url)


def _default_profile():
    if not _is_windows():
        raise FoundryError('windows_required', 'EA launch requires Windows.')
    class GUID(ctypes.Structure):
        _fields_ = [('Data1',wintypes.DWORD),('Data2',wintypes.WORD),('Data3',wintypes.WORD),
                    ('Data4',ctypes.c_ubyte * 8)]
    # FOLDERID_Documents, honoring Windows folder redirection.
    folder = GUID.from_buffer_copy(uuid.UUID('fdd39ad0-238f-46af-adb4-6c85480369c7').bytes_le)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    get = shell.SHGetKnownFolderPath
    get.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                    ctypes.POINTER(ctypes.c_void_p)]
    get.restype = ctypes.c_long
    free = ole.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    result = ctypes.c_void_p()
    if get(ctypes.byref(folder), 0, None, ctypes.byref(result)) != 0 or not result.value:
        raise FoundryError('documents_unavailable', 'The Windows Documents folder is unavailable.')
    try:
        documents = ctypes.wstring_at(result)
    finally:
        free(result)
    return str(Path(documents) / 'Electronic Arts' / 'The Sims 4')


def _handler_command():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'origin2\shell\open\command') as key:
        value, kind = winreg.QueryValueEx(key, None)
    if kind == winreg.REG_EXPAND_SZ:
        value = os.path.expandvars(value)
    if type(value) is not str:
        raise FoundryError('ea_handler', 'The registered EA protocol handler is invalid.')
    return value


def _validate_handler(command, ea_launcher, expected_sha256):
    # EA's known registration has one executable and one %1 URI argument.
    # The installed EA registration may leave a path containing spaces unquoted.
    if type(command) is not str or len(command) > 32768:
        raise FoundryError('ea_handler', 'The EA protocol command is invalid.')
    match = re.fullmatch(r'\s*(?:"([^"]+)"|([^"]+?))\s+(?:"%1"|%1)\s*', command, re.IGNORECASE)
    if not match:
        raise FoundryError('ea_handler', 'The EA protocol handler has unexpected arguments.')
    configured = (match.group(1) or match.group(2)).strip()
    selected = Path(os.path.abspath(ea_launcher))
    if (selected.name.casefold() != 'ealauncher.exe'
            or not same_windows_path(configured, str(selected))):
        raise FoundryError('ea_handler', 'The registered EA launcher does not match the selected path.')
    # EA updates use a versioned directory symlink. Resolve the exact selected
    # path, then reject any remaining links in the resolved path and hash it.
    # Repeating this immediately before dispatch detects ordinary link changes.
    resolved = plain_path(selected.resolve(strict=True))
    if type(expected_sha256) is not str or re.fullmatch(r'[0-9a-f]{64}', expected_sha256) is None:
        raise FoundryError('ea_handler', 'Expected an exact EA launcher SHA-256.')
    snapshot = neutral_ea_process.stable_executable(resolved)
    if resolved.name.casefold() != 'ealauncher.exe' or snapshot['sha256'] != expected_sha256:
        raise FoundryError('ea_handler', 'The resolved EA launcher does not match the selected hash.')
    if match.group(1) is None:
        # Unquoted executable paths with spaces can select an earlier .exe.
        # Refuse if any candidate prefix exists; no handler modification occurs.
        for index, character in enumerate(configured):
            if character != ' ':
                continue
            candidate = Path(configured[:index] + '.exe')
            try:
                candidate.stat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise FoundryError('ea_handler_unobservable', 'Cannot exclude an unquoted executable prefix.') from error
            raise FoundryError('ea_handler_ambiguous', 'An unquoted EA handler has an executable prefix collision.')
    return {'launcher':str(selected), 'resolved_launcher':str(resolved),
            'sha256':expected_sha256, 'command':command, 'identity':snapshot['identity']}


def _check_profile(profile_session, expected_path, profile_status, known_profile):
    value = profile_status(profile_session)
    if (value.get('state') != 'activation_recorded'
            or value.get('test_at_profile') is not True or value.get('original_at_backup') is not True
            or not same_windows_path(value.get('profile', ''), expected_path)
            or not same_windows_path(value.get('profile', ''), known_profile())):
        raise FoundryError('profile_unverified', 'The prepared profile does not match the active Windows Documents location.')
    return value



def _profile_binding(value):
    keys = ('profile', 'session_id', 'activation_record_sha256', 'original_identity', 'test_identity')
    try:
        binding = {key:value[key] for key in keys}
        if (type(binding['profile']) is not str or not Path(binding['profile']).is_absolute()
                or type(binding['session_id']) is not str
                or re.fullmatch(r'[0-9a-f]{32}', binding['session_id']) is None
                or type(binding['activation_record_sha256']) is not str
                or re.fullmatch(r'[0-9a-f]{64}', binding['activation_record_sha256']) is None):
            raise ValueError('invalid activation identity')
        for key in ('original_identity', 'test_identity'):
            identity = binding[key]
            if (not isinstance(identity, dict) or set(identity) != {'device','inode'}
                    or any(type(identity[k]) is not int or identity[k] <= 0 for k in identity)):
                raise ValueError('invalid directory identity')
        return binding
    except (KeyError, TypeError, ValueError) as error:
        raise FoundryError('profile_unverified', 'Exact profile activation binding is unavailable.') from error


def execute(game_root, ea_logs, executable_sha256, ea_launcher, launcher_sha256,
            profile_session, output_session, seconds=30.0, **options):
    if not _is_windows():
        raise FoundryError('windows_required', 'EA launch requires Windows.')
    # The shared per-profile lock excludes cooperating lifecycle writers. The
    # sticky reservation survives this lock and any uncertain handoff outcome.
    with neutral_profile.active_profile_guard(profile_session) as current:
        if current.get('launch_reservation_present') is True:
            raise FoundryError('launch_pending', 'This profile has a previous handoff reservation; observe without replay.')
        return _execute_locked(game_root, ea_logs, executable_sha256, ea_launcher,
                               launcher_sha256, profile_session, output_session,
                               seconds, **options)

def _execute_locked(game_root, ea_logs, executable_sha256, ea_launcher, launcher_sha256,
            profile_session, output_session, seconds=30.0, *,
            prepare_fn=None, profile_status=None, known_profile=None,
            handler_command=None, process_check=None, dispatch=None,
            discover_fn=None, monotonic=None, sleep=None):
    if not _is_windows():
        raise FoundryError('windows_required', 'EA launch requires Windows.')
    if type(seconds) not in (int, float) or not 0 < seconds <= MAX_OBSERVE_SECONDS:
        raise FoundryError('invalid_timeout', 'Launch observation must be between zero and 60 seconds.')
    prepare_fn = prepare_fn or launch.prepare
    profile_status = profile_status or neutral_profile.status
    known_profile = known_profile or _default_profile
    handler_command = handler_command or _handler_command
    process_check = process_check or neutral_testmods._no_game_candidates
    dispatch = dispatch or _dispatch
    discover_fn = discover_fn or discovery.discover
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    plan = prepare_fn(game_root, ea_logs, executable_sha256)
    if (not isinstance(plan, dict) or plan.get('ok') is not True
            or plan.get('launched') is not False or not isinstance(plan.get('url'), str)
            or not plan['url'].startswith('origin2://game/launch?')):
        raise FoundryError('launch_plan', 'A validated EA launch plan is required.')
    profile_session = external_path(profile_session)
    current = profile_status(profile_session)
    profile_path = current.get('profile', '')
    current = _check_profile(profile_session, profile_path, profile_status, known_profile)
    handler = _validate_handler(handler_command(), ea_launcher, launcher_sha256)
    process_check()
    session = external_path(output_session)
    if session.exists() or session.is_symlink():
        raise FoundryError('session_exists', 'Use a new external launch session directory.')
    record = {'schema':2, 'kind':'foundry_ea_launch_intent', 'request_id':uuid.uuid4().hex,
              'profile_session':str(profile_session), 'profile':profile_path,
              'profile_binding':_profile_binding(current),
              'game_root':str(plain_path(game_root)), 'game_executable_sha256':executable_sha256,
              'ea_launcher':handler['launcher'], 'ea_launcher_sha256':launcher_sha256,
              'plan_sha256':digest(plan)}
    record['record_sha256'] = digest(record)
    session.mkdir(parents=True, exist_ok=False)
    new_json(session / 'launch-intent.json', record)
    # Recheck all inputs after persistence and immediately before the only handoff.
    if _profile_binding(_check_profile(profile_session, profile_path, profile_status, known_profile)) != record['profile_binding']:
        raise FoundryError('profile_unverified', 'Profile activation changed before handoff.')
    refreshed = prepare_fn(game_root, ea_logs, executable_sha256)
    if digest(refreshed) != record['plan_sha256']:
        raise FoundryError('launch_plan_changed', 'EA launch evidence changed before handoff.')
    if _validate_handler(handler_command(), ea_launcher, launcher_sha256) != handler:
        raise FoundryError('ea_handler_changed', 'The registered EA handler changed before handoff.')
    process_check()
    account = plan.get('account_launch')
    if not isinstance(account, dict):
        raise FoundryError('launch_plan', 'Launch plan lacks verified EA account identity.')
    expected = {'offer_id':account.get('offer_id'), 'content_id':account.get('content_id'),
                'launcher':str(Path(record['game_root']) / 'Game' / 'Bin' / 'TS4_Launcher_x64.exe')}
    neutral_ea_context._expected(expected)
    if not isinstance(ea_logs, (list, tuple)) or not ea_logs:
        raise FoundryError('launch_plan', 'An explicit current EA log is required.')
    context_intent = {'schema':1, 'kind':'foundry_ea_context_intent',
                      'request_id':record['request_id'],
                      'cursor':neutral_ea_context.capture(ea_logs[-1]),
                      'expected':expected, 'handoff_at':time.time()}
    context_intent['record_sha256'] = digest(context_intent)
    new_json(session / 'ea-context-intent.json', context_intent)
    # Persistence can be slow: repeat the current Documents/marker/identity
    # binding after it, then reserve before the only external handoff.
    if _profile_binding(_check_profile(profile_session, profile_path, profile_status, known_profile)) != record['profile_binding']:
        raise FoundryError('profile_unverified', 'Profile activation changed before handoff.')
    if _validate_handler(handler_command(), ea_launcher, launcher_sha256) != handler:
        raise FoundryError('ea_handler_changed', 'The registered EA handler changed before handoff.')
    process_check()
    try:
        neutral_profile.reserve_launch(profile_session, session, record['request_id'])
        neutral_profile.validate_launch_reservation(profile_session, session, record['request_id'])
        if digest(prepare_fn(game_root, ea_logs, executable_sha256)) != record['plan_sha256']:
            raise FoundryError('launch_plan_changed', 'EA launch evidence changed after launch reservation.')
        if _validate_handler(handler_command(), ea_launcher, launcher_sha256) != handler:
            raise FoundryError('ea_handler_changed', 'The registered EA handler changed after launch reservation.')
        process_check()
        if _profile_binding(_check_profile(profile_session, profile_path, profile_status, known_profile)) != record['profile_binding']:
            raise FoundryError('profile_unverified', 'Profile activation changed after launch reservation.')
    except BaseException:
        # This receipt only covers code paths that never entered dispatch. It is
        # evidence for later reconciliation, never permission to clear/replay.
        try:
            new_json(session / 'launch-not-dispatched.json',
                     {'schema':1, 'kind':'foundry_ea_launch_not_dispatched',
                      'request_id':record['request_id'], 'dispatch_attempted':False})
        except Exception:
            pass
        raise
    try:
        dispatch(plan['url'])
    except Exception as error:
        return {'ok':False, 'outcome':'unresolved', 'request_id':record['request_id'],
                'message':'EA handoff outcome is unknown; observe without sending it again.',
                'error_type':type(error).__name__}
    try:
        # The external dispatch returned, but no game-readiness claim follows.
        # No absence-of-game check here: a legitimate game may already exist.
        neutral_profile.validate_launch_reservation(profile_session, session, record['request_id'])
        if _profile_binding(_check_profile(profile_session, profile_path, profile_status, known_profile)) != record['profile_binding']:
            raise FoundryError('profile_unverified', 'Profile activation changed after EA handoff.')
        new_json(session / 'launch-handoff.json', _handoff_receipt(record))
    except Exception as error:
        return {'ok':False, 'outcome':'unresolved', 'request_id':record['request_id'],
                'message':'EA handoff returned but its verified receipt was not confirmed; observe without repeating.',
                'error_type':type(error).__name__}
    deadline = monotonic() + float(seconds)
    executable = str(Path(record['game_root']) / 'Game' / 'Bin' / 'TS4_x64.exe')
    last_status = None
    while True:
        try:
            observed = discover_fn(executable, executable_sha256)
            if not isinstance(observed, dict):
                raise ValueError('Invalid discovery response')
            last_status = observed.get('status')
            matches = observed.get('matches')
            if (last_status == 'one_observed' and observed.get('matched_count') == 1
                    and isinstance(matches, list) and len(matches) == 1 and isinstance(matches[0], dict)):
                identity = matches[0]
                result = {'ok':True, 'outcome':'game_observed', 'request_id':record['request_id'],
                          'identity':identity, 'session':str(session), 'ea_dialog_handled':False,
                          'household_ready':False, 'action_replayed':False}
                break
        except Exception:
            last_status = 'observation_unavailable'
        remaining = deadline - monotonic()
        if remaining <= 0:
            result = {'ok':False, 'outcome':'unresolved', 'request_id':record['request_id'],
                      'session':str(session), 'last_observation':last_status,
                      'message':'No exact game window was confirmed; do not repeat the launch.',
                      'ea_dialog_handled':False, 'action_replayed':False}
            break
        sleep(min(.5, remaining))
    try:
        new_json(session / 'launch-observation.json', result)
    except Exception as error:
        return {'ok':False, 'outcome':'unresolved', 'request_id':record['request_id'],
                'session':str(session), 'message':'Launch was handed off but observation recording failed.',
                'error_type':type(error).__name__}
    return result


def _read_intent(session):
    record = read_json(session / 'launch-intent.json')
    fields = {'schema','kind','request_id','profile_session','profile','game_root',
              'game_executable_sha256','ea_launcher','ea_launcher_sha256','plan_sha256','record_sha256'}
    if isinstance(record, dict) and record.get('schema') == 2:
        fields.add('profile_binding')
    if (not isinstance(record, dict) or set(record) != fields
            or type(record['schema']) is not int or record['schema'] not in (1,2)
            or record['kind'] != 'foundry_ea_launch_intent'
            or type(record['request_id']) is not str
            or re.fullmatch(r'[0-9a-f]{32}', record['request_id']) is None
            or any(type(record[key]) is not str or re.fullmatch(r'[0-9a-f]{64}', record[key]) is None
                   for key in ('game_executable_sha256','ea_launcher_sha256','plan_sha256','record_sha256'))
            or digest({k:v for k,v in record.items() if k != 'record_sha256'}) != record['record_sha256']):
        raise FoundryError('invalid_launch_intent', 'Launch intent is malformed or changed.')
    if record['schema'] == 2:
        binding = _profile_binding(record['profile_binding'])
        if set(record['profile_binding']) != set(binding) or not same_windows_path(binding['profile'], record['profile']):
            raise FoundryError('invalid_launch_intent', 'Launch profile binding does not match the intent.')
    return record


def observe(output_session, seconds=30.0, output=None, *,
            discover_fn=None, monotonic=None, sleep=None):
    """Observe a previous handoff only; never invoke the EA handler again."""
    if type(seconds) not in (int, float) or not 0 < seconds <= MAX_OBSERVE_SECONDS:
        raise FoundryError('invalid_timeout', 'Observation must be between zero and 60 seconds.')
    session = external_path(output_session)
    record = _read_intent(session)
    root = plain_path(record['game_root'])
    executable = str(root / 'Game' / 'Bin' / 'TS4_x64.exe')
    discover_fn = discover_fn or discovery.discover
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    destination = external_path(output) if output else None
    if destination is not None and (destination.exists() or destination.is_symlink()):
        raise FoundryError('output_exists', 'Use a new observation output path.')
    deadline = monotonic() + float(seconds)
    last_status = None
    while True:
        try:
            observed = discover_fn(executable, record['game_executable_sha256'])
            if not isinstance(observed, dict):
                raise ValueError('Invalid discovery response')
            last_status = observed.get('status')
            matches = observed.get('matches')
            if (last_status == 'one_observed' and observed.get('matched_count') == 1
                    and isinstance(matches, list) and len(matches) == 1 and isinstance(matches[0], dict)):
                result = {'ok':True, 'outcome':'game_observed', 'request_id':record['request_id'],
                          'identity':matches[0], 'read_only':True, 'action_replayed':False,
                          'household_ready':False}
                break
        except Exception:
            last_status = 'observation_unavailable'
        remaining = deadline - monotonic()
        if remaining <= 0:
            result = {'ok':False, 'outcome':'unresolved', 'request_id':record['request_id'],
                      'last_observation':last_status, 'read_only':True, 'action_replayed':False,
                      'message':'No exact game window was confirmed; do not repeat the launch.'}
            break
        sleep(min(.5, remaining))
    if destination is not None:
        new_json(destination, result)
    return result


def ea_context(output_session, output=None):
    """Correlate a recorded handoff with a fresh EA log request, without input."""
    session = external_path(output_session)
    record = _read_intent(session)
    context = read_json(session / 'ea-context-intent.json')
    fields = {'schema','kind','request_id','cursor','expected','handoff_at','record_sha256'}
    if (not isinstance(context, dict) or set(context) != fields
            or type(context['schema']) is not int or context['schema'] != 1
            or context['kind'] != 'foundry_ea_context_intent'
            or context['request_id'] != record['request_id']
            or digest({k:v for k,v in context.items() if k != 'record_sha256'}) != context['record_sha256']):
        raise FoundryError('invalid_ea_context', 'EA context intent is malformed or changed.')
    result = neutral_ea_context.observe(context['cursor'], context['expected'], context['handoff_at'])
    result['request_id'] = record['request_id']
    if output is not None:
        target = external_path(output)
        if target.exists() or target.is_symlink():
            raise FoundryError('output_exists', 'Use a new EA context output path.')
        new_json(target, result)
    return result



def _handoff_receipt(record):
    value = {'schema':1, 'kind':'foundry_ea_launch_handoff',
             'request_id':record['request_id'], 'launch_intent_sha256':record['record_sha256'],
             'dispatch_returned':True}
    value['record_sha256'] = digest(value)
    return value


def _require_handoff_receipt(session, record):
    try:
        value = read_json(session / 'launch-handoff.json')
    except FileNotFoundError as error:
        raise FoundryError('ea_handoff_unverified', 'A returned EA handoff has not been recorded; observe without automatic consent.') from error
    if value != _handoff_receipt(record) or type(value.get('schema')) is not int or value.get('dispatch_returned') is not True:
        raise FoundryError('ea_handoff_unverified', 'EA handoff receipt does not match the exact launch intent.')

def _require_no_abort_receipt(session):
    target = session / 'launch-not-dispatched.json'
    try:
        target.lstat()
    except FileNotFoundError:
        return
    # A present malformed, linked, special or unreadable abort record is a
    # blocker too. Its absence is not itself evidence that dispatch occurred.
    read_json(target)
    raise FoundryError('launch_not_dispatched', 'This launch was recorded as not dispatched; consent is not authorized.')


@contextmanager
def consent_guard(output_session, *, known_profile=None, process_check=None):
    """Keep the exact recorded activation reserved throughout one consent.

    No process snapshot cancels a previously handed-off launch. Old intents
    lacking an exact activation binding are readable but cannot authorize input.
    """
    session = external_path(output_session)
    record = _read_intent(session)
    _require_no_abort_receipt(session)
    _require_handoff_receipt(session, record)
    if record['schema'] != 2:
        raise FoundryError('profile_unverified', 'This launch lacks an exact profile activation binding.')
    profile_session = external_path(record['profile_session'])
    known_profile = known_profile or _default_profile
    process_check = process_check or neutral_testmods._no_game_candidates
    with neutral_profile.active_profile_guard(profile_session):
        def validate():
            _require_no_abort_receipt(session)
            _require_handoff_receipt(session, record)
            if _read_intent(session) != record:
                raise FoundryError('invalid_launch_intent', 'Launch intent changed during consent.')
            neutral_profile.validate_launch_reservation(profile_session, session, record['request_id'])
            current = _check_profile(profile_session, record['profile'], neutral_profile.status, known_profile)
            if _profile_binding(current) != record['profile_binding']:
                raise FoundryError('profile_unverified', 'The launch profile activation no longer matches.')
            process_check()
            current = _check_profile(profile_session, record['profile'], neutral_profile.status, known_profile)
            if _profile_binding(current) != record['profile_binding']:
                raise FoundryError('profile_unverified', 'The launch profile changed during consent validation.')
            return record['profile_binding']
        validate()
        yield validate
