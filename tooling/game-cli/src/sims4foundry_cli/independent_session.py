"""Cooperative independent-session setup/status; live admission stays denied.

No game process, profile, input sender, capture or observer is accessed here.
SQLite and a same-user DPAPI key coordinate cooperating clients only. They do
not authenticate callers, prevent rollback or qualify game behavior. A durable
PID record cannot reconstruct a lost provider-owned process handle.
"""
from copy import deepcopy
import uuid

from . import input_journal, registered_input, session_registry, sqlite_anchor, windows_registry_key
from .errors import FoundryError
from .io import canonical, digest, external_path, new_json
from .neutral_records import read_json


def _record(kind, **fields):
    row = {'schema': 1, 'kind': kind, **fields}
    row['record_sha256'] = digest(row)
    return row


def _checked(path, kind, fields):
    row = read_json(path)
    if (type(row) is not dict or set(row) != {'schema', 'kind', 'record_sha256'} | set(fields) or
            type(row.get('schema')) is not int or row['schema'] != 1 or row['kind'] != kind or
            type(row['record_sha256']) is not str or
            row['record_sha256'] != digest({key: value for key, value in row.items() if key != 'record_sha256'})):
        raise FoundryError('session_record_invalid', 'Independent-session evidence is malformed or changed.')
    return row


def _roots(authority, session=None):
    root = external_path(authority)
    if session is None:
        return root
    target = external_path(session)
    if root == target or root in target.parents or target in root.parents:
        raise FoundryError('session_paths_overlap', 'Authority and session must be separate external directories.')
    return root, target


def initialize_authority(directory):
    """Create a fresh cooperative authority; never replace or repair one."""
    root = _roots(directory)
    if root.exists() or root.is_symlink():
        raise FoundryError('authority_exists', 'Choose a new authority directory; never recreate an uncertain one.')
    root.mkdir(parents=True, exist_ok=False)
    new_json(root / 'authority-intent.json', _record('foundry_independent_authority_intent'))
    for name in ('keys', 'lifetimes', 'registry'):
        (root / name).mkdir(exist_ok=False)
    sqlite_anchor.SQLiteAnchor.create(root / 'anchor.sqlite')
    new_json(root / 'authority-ready.json', _record('foundry_independent_authority_ready'))
    return {'ok': True, 'outcome': 'cooperative_authority_created', 'authority': str(root),
            'same_user_authenticated': False, 'rollback_protected': False, 'game_qualified': False,
            'process_accessed': False, 'live_dispatch_available': False}


def _authority(root):
    _checked(root / 'authority-intent.json', 'foundry_independent_authority_intent', ())
    _checked(root / 'authority-ready.json', 'foundry_independent_authority_ready', ())
    for name in ('keys', 'lifetimes', 'registry'):
        path = external_path(root / name)
        if not path.is_dir() or path.is_symlink():
            raise FoundryError('authority_unavailable', 'Cooperative authority components are unavailable.')
    return sqlite_anchor.SQLiteAnchor(root / 'anchor.sqlite')


def initialize_session(authority, session, identity):
    """Bind a supplied identity to new records; do not open or verify a process."""
    root, target = _roots(authority, session)
    anchor = _authority(root)
    selected = input_journal._target(identity)
    lifetime = session_registry._lifetime(selected)
    scope = digest(lifetime)
    # One PID/creation lifetime must not gain a second reservation merely by
    # presenting a different executable hash. This remains cooperative only.
    reservation_scope = digest({key: lifetime[key] for key in ('pid', 'creation_filetime')})
    if target.exists() or target.is_symlink():
        raise FoundryError('session_exists', 'Choose a new session directory; do not replay initialization.')
    session_id = uuid.uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    intent = _record('foundry_independent_session_intent', session_id=session_id, identity=selected,
                     lifetime_sha256=scope, reservation_sha256=reservation_scope, authority=str(root))
    new_json(target / 'session-intent.json', intent)
    # Exclusive per-lifetime reservation must precede key/journal creation;
    # a partial or lost acknowledgement stays reserved for inspection.
    reservation = _record('foundry_independent_lifetime_reservation', session_id=session_id,
                          lifetime_sha256=scope, reservation_sha256=reservation_scope,
                          session=str(target), intent_sha256=digest(intent))
    try:
        new_json(root / 'lifetimes' / (reservation_scope + '.json'), reservation)
    except FileExistsError:
        raise FoundryError('lifetime_reserved', 'Original lifetime already has a reservation; inspect it without reinitializing.') from None
    key = windows_registry_key.create_key(root / 'keys' / (scope + '.json'), scope)
    journal = input_journal.InputJournal.create(target / 'journal', session_id, selected)
    journal_sha = registered_input.journal_binding(journal)
    registry = session_registry.SessionRegistry(root / 'registry', selected, key, anchor)
    registry.open_session(session_id, journal_sha)
    state = registry.inspect()
    if (len(state['events']) != 1 or state['events'][0].get('session_id') != session_id or
            state['events'][0].get('journal_sha256') != journal_sha):
        raise FoundryError('session_unresolved', 'Original registry binding was not confirmed; do not reinitialize.')
    ready = _record('foundry_independent_session_ready', session_id=session_id,
                    intent_sha256=digest(intent), lifetime_sha256=scope, reservation_sha256=reservation_scope,
                    journal_sha256=journal_sha)
    new_json(target / 'session-ready.json', ready)
    return status(root, target)


def _open(authority, session):
    root, target = _roots(authority, session)
    anchor = _authority(root)
    intent = _checked(target / 'session-intent.json', 'foundry_independent_session_intent',
                      ('session_id', 'identity', 'lifetime_sha256', 'reservation_sha256', 'authority'))
    ready = _checked(target / 'session-ready.json', 'foundry_independent_session_ready',
                     ('session_id', 'intent_sha256', 'lifetime_sha256', 'reservation_sha256', 'journal_sha256'))
    if (type(intent['session_id']) is not str or not input_journal._token(intent['session_id']) or
            intent['authority'] != str(root) or ready['session_id'] != intent['session_id'] or
            ready['intent_sha256'] != digest(intent)):
        raise FoundryError('session_record_invalid', 'Session and authority identity do not match.')
    selected = input_journal._target(intent['identity'])
    lifetime = session_registry._lifetime(selected)
    scope = digest(lifetime)
    reservation_scope = digest({key: lifetime[key] for key in ('pid', 'creation_filetime')})
    if (canonical(selected) != canonical(intent['identity']) or intent['lifetime_sha256'] != scope or
            ready['lifetime_sha256'] != scope or intent['reservation_sha256'] != reservation_scope or
            ready['reservation_sha256'] != reservation_scope):
        raise FoundryError('session_record_invalid', 'Original process lifetime differs from the session.')
    reservation = _checked(root / 'lifetimes' / (reservation_scope + '.json'), 'foundry_independent_lifetime_reservation',
                           ('session_id', 'lifetime_sha256', 'reservation_sha256', 'session', 'intent_sha256'))
    if (reservation['session_id'] != intent['session_id'] or reservation['lifetime_sha256'] != scope or
            reservation['reservation_sha256'] != reservation_scope or
            reservation['session'] != str(target) or reservation['intent_sha256'] != digest(intent)):
        raise FoundryError('session_record_invalid', 'Original lifetime reservation differs.')
    key = windows_registry_key.load_key(root / 'keys' / (scope + '.json'), scope)
    journal = input_journal.InputJournal(target / 'journal')
    journal_sha = registered_input.journal_binding(journal)
    if journal_sha != ready['journal_sha256'] or journal.session['session_id'] != intent['session_id']:
        raise FoundryError('session_record_invalid', 'Input journal differs from its original binding.')
    registry = session_registry.SessionRegistry(root / 'registry', selected, key, anchor)
    state = registry.inspect()
    if (not state['events'] or state['events'][0].get('kind') != 'session' or
            state['events'][0].get('session_id') != intent['session_id'] or
            state['events'][0].get('journal_sha256') != journal_sha or
            canonical(state['events'][0].get('lifetime')) != canonical(session_registry._lifetime(selected))):
        raise FoundryError('session_record_invalid', 'Registry and journal binding differ.')
    return intent, registry, journal, state


def status(authority, session, request_id=None):
    """Inspect cooperative records; no process input or host acknowledgment."""
    try:
        intent, registry, journal, state = _open(authority, session)
    except FileNotFoundError:
        return {'ok': False, 'outcome': 'unresolved', 'state': 'incomplete_or_unavailable',
                'process_accessed': False, 'witness_owner_attached': False,
                'live_dispatch_available': False, 'game_qualified': False,
                'message': 'Original cooperative records are incomplete or unavailable; do not reinitialize or replay.'}
    events = state['events']
    pending = bool(events and events[-1].get('kind') == 'attempt')
    result = {'ok': True, 'outcome': 'cooperative_records_observed', 'session_id': intent['session_id'],
              'lifetime_sha256': intent['lifetime_sha256'], 'registry_pending': pending,
              'registry_head': state['head'], 'process_accessed': False, 'witness_owner_attached': False,
              'live_dispatch_available': False, 'observer_authenticated': False, 'game_qualified': False,
              'same_user_authenticated': False, 'rollback_protected': False,
              'scope': 'Cooperative configuration/status only; no native handle was acquired.'}
    if request_id is not None:
        row = journal.status(request_id)
        result['input'] = row
        result['host_acknowledgment_is_game_proof'] = False
    return result


def submit_input_once(*args, **kwargs):
    """Default-deny all live input until owner, observer and durability qualify."""
    raise FoundryError('live_admission_unqualified',
        'Live dispatch is disabled: the original retained owner, authority, observer and capture are not qualified.',
        input_attempted=False, game_qualified=False)
