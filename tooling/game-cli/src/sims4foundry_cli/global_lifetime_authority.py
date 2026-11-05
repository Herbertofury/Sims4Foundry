"""Single OS-located cooperative lifetime reservation; no process access.

This prevents cooperating callers using this module from selecting a different
workspace authority. It does not authenticate same-user programs or resist
same-user deletion, rollback, path races or Windows power-loss loss.
"""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import os
from pathlib import Path
import uuid

from . import input_journal, session_registry
from .errors import FoundryError
from .io import canonical, digest, external_path, lock, new_json, read_json


def _local_appdata():
    """Resolve LocalAppData from Windows, not a caller-supplied environment."""
    if os.name != 'nt':
        raise FoundryError('windows_required', 'The per-user authority requires Windows.')
    class GUID(ctypes.Structure):
        _fields_ = [('bytes',ctypes.c_ubyte*16)]
    folder = GUID((ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID('f1b32785-6fba-4fcf-9d55-7b8e7f157091').bytes_le))
    shell = ctypes.WinDLL('shell32',use_last_error=True)
    ole = ctypes.WinDLL('ole32',use_last_error=True)
    get = shell.SHGetKnownFolderPath
    get.argtypes = [ctypes.POINTER(GUID),wintypes.DWORD,wintypes.HANDLE,ctypes.POINTER(ctypes.c_void_p)]
    get.restype = ctypes.c_long
    free = ole.CoTaskMemFree
    free.argtypes, free.restype = [ctypes.c_void_p],None
    result = ctypes.c_void_p()
    status = get(ctypes.byref(folder),0,None,ctypes.byref(result))
    if status != 0 or not result.value:
        raise FoundryError('global_authority_unavailable', 'Windows LocalAppData is unavailable.')
    try:
        return Path(ctypes.wstring_at(result))
    finally:
        free(result)


def _root():
    root = external_path(_local_appdata()/'Sims4Foundry'/'live-authority-v1')
    if not root.is_absolute():
        raise FoundryError('global_authority_unavailable', 'The OS authority location must be absolute.')
    return root


@contextmanager
def _publication_scope(code):
    """Preserve uncertainty if a lock or guard fails after publication entry."""
    entered = [False]
    try:
        yield entered
    except Exception as error:
        if entered[0] and not (isinstance(error,FoundryError) and error.details.get('no_replay') is True):
            raise FoundryError(code, 'Original publication or cleanup may have completed; inspect without retry.',
                               no_replay=True) from None
        raise


def initialize():
    """Create the one OS-located authority once; never replace an existing one."""
    root = _root()
    if root.exists() or root.is_symlink():
        raise FoundryError('global_authority_exists', 'Original authority exists; inspect it without recreating.')
    try:
        root.mkdir(parents=True,exist_ok=False)
        authority_id = uuid.uuid4().hex
        (root/'lifetimes').mkdir(exist_ok=False)
        new_json(root/'authority.json',{'schema':1,'authority_id':authority_id,'kind':'cooperative_lifetime_authority'})
    except FileExistsError:
        raise FoundryError('global_authority_exists', 'Original authority may exist; inspect without recreating.',
                           no_replay=True) from None
    except Exception:
        raise FoundryError('global_authority_unresolved', 'Authority initialization may have completed; inspect without retry.',
                           no_replay=True) from None
    return {'outcome':'global_authority_created','authority_id':authority_id,
            'same_user_authenticated':False,'rollback_protected':False,'game_qualified':False}


def _authority():
    root = _root()
    row = read_json(root/'authority.json')
    if (type(row) is not dict or set(row) != {'schema','authority_id','kind'}
            or type(row['schema']) is not int or row['schema'] != 1
            or row['kind'] != 'cooperative_lifetime_authority' or not input_journal._token(row['authority_id'])
            or not external_path(root/'lifetimes').is_dir()):
        raise FoundryError('global_authority_unavailable', 'Original authority is incomplete or invalid; do not recreate.')
    return root,row['authority_id']


def status():
    """Inspect the OS-located authority without reserving any lifetime."""
    _,authority_id = _authority()
    return {'ok':True,'outcome':'global_authority_observed','authority_id':authority_id,
            'same_user_authenticated':False,'rollback_protected':False,'game_qualified':False}


def _lifetime(target):
    selected = input_journal._target(target)
    lifetime = session_registry._lifetime(selected)
    return {'pid':lifetime['pid'],'creation_filetime':lifetime['creation_filetime']}


def claim(target, session_id, witness_id, journal_sha256):
    """Publish one original lifetime reservation before process acquisition."""
    if (not input_journal._token(session_id) or not input_journal._token(witness_id)
            or type(journal_sha256) is not str or len(journal_sha256) != 64
            or any(c not in '0123456789abcdef' for c in journal_sha256)):
        raise FoundryError('global_authority_arguments', 'Exact original session, witness and journal digest are required.')
    root,authority_id = _authority()
    selected = input_journal._target(target)
    lifetime = _lifetime(selected)
    scope = digest(lifetime)
    row = {'schema':1,'authority_id':authority_id,'lifetime':lifetime,'target_sha256':digest(selected),
           'session_id':session_id,'witness_id':witness_id,'journal_sha256':journal_sha256}
    destination = root/'lifetimes'/(scope+'.json')
    try:
        new_json(destination,row)
    except FileExistsError:
        raise FoundryError('global_lifetime_reserved', 'Original lifetime is already reserved; inspect without reacquiring.',
                           no_replay=True) from None
    except Exception:
        raise FoundryError('global_claim_unresolved', 'Lifetime reservation may have been published; inspect without retry.',
                           no_replay=True) from None
    return {'outcome':'global_lifetime_reserved','scope':scope,'reservation_sha256':digest(row),
            'same_user_authenticated':False,'rollback_protected':False,'game_qualified':False}


def inspect(target):
    """Read the original reservation only; never remove or recreate it."""
    root,authority_id = _authority()
    selected = input_journal._target(target)
    lifetime = _lifetime(selected)
    scope = digest(lifetime)
    row = read_json(root/'lifetimes'/(scope+'.json'))
    if (type(row) is not dict or set(row) != {'schema','authority_id','lifetime','target_sha256','session_id','witness_id','journal_sha256'}
            or type(row['schema']) is not int or row['schema'] != 1 or row['authority_id'] != authority_id
            or canonical(row['lifetime']) != canonical(lifetime) or row['target_sha256'] != digest(selected)
            or not input_journal._token(row['session_id'])
            or not input_journal._token(row['witness_id']) or type(row['journal_sha256']) is not str
            or len(row['journal_sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['journal_sha256'])):
        raise FoundryError('global_reservation_invalid', 'Original reservation is malformed or changed; do not retry.')
    return {'outcome':'global_reservation_observed','scope':scope,'reservation_sha256':digest(row),
            'session_id':row['session_id'],'witness_id':row['witness_id'],'journal_sha256':row['journal_sha256'],
            'same_user_authenticated':False,'rollback_protected':False,'game_qualified':False}


def claim_session(authority, session, witness_id):
    """Reserve the lifetime for an existing unused cooperative session.

    This rechecks original session/journal/registry records before publishing;
    it does not discover or reconcile other previously used authorities.
    """
    from . import independent_session, registered_input
    intent,registry,journal,state = independent_session._open(authority,session)
    events = state['events']
    if (len(events) != 1 or events[0].get('kind') != 'session'
            or events[0].get('session_id') != intent['session_id'] or journal._runs()):
        raise FoundryError('global_session_unresolved', 'An unused original session is required; inspect existing history.')
    binding = registered_input.journal_binding(journal)
    if events[0].get('journal_sha256') != binding:
        raise FoundryError('global_session_unresolved', 'Original registry and journal do not match.')
    return claim(intent['identity'],intent['session_id'],witness_id,binding)


class BoundClaim:
    """In-memory correlation to the exact owner objects; not restart recovery."""
    def __init__(self, registry, journal, witness, reservation):
        self._registry, self._journal, self._witness = registry, journal, witness
        self._reservation, self._acquisition_attempted, self._lease = reservation, False, None

    def __reduce_ex__(self, protocol):
        raise TypeError('A bound claim cannot be serialized or transferred.')


def claim_bound(registry, journal, witness):
    """Reserve for the exact in-process owner objects before witness acquisition."""
    from . import registered_input
    from .session_registry import SessionRegistry
    from .session_witness import SessionWitness
    if (type(registry) is not SessionRegistry or type(journal) is not input_journal.InputJournal
            or type(witness) is not SessionWitness or witness._registry is not registry):
        raise FoundryError('global_witness_binding', 'Original registry, journal and witness are required.')
    target = input_journal._target(journal.session['target'])
    identity = {key:target[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
    with _publication_scope('global_claim_unresolved') as publication, witness.guard_for_claim(registry):
        with lock(journal.root/'operation.lock'), registry._hold_events() as state:
            journal._verify()
            binding = registered_input.journal_binding(journal)
            events = state['events']
            if (canonical(witness._identity) != canonical(identity) or len(events) != 1
                    or events[0].get('kind') != 'session' or events[0].get('session_id') != journal.session['session_id']
                    or events[0].get('journal_sha256') != binding
                    or canonical(events[0].get('lifetime')) != canonical(session_registry._lifetime(target))
                    or journal._runs()):
                raise FoundryError('global_session_unresolved', 'An unused matching owner and session are required.')
            publication[0] = True
            witness._global_state = 'pending'
            registry._append_locked({'kind':'attempt','request_id':witness._request,
                                     'operation':'global_acquire','intent_sha256':witness._intent})
            reservation = claim(target,journal.session['session_id'],witness._request,binding)
            return BoundClaim(registry,journal,witness,reservation)


def acquire_bound(claim_ticket):
    """Acquire and attach the exact reserved witness once, without input.

    The registry acquisition stays pending until attachment is published.
    Unknown acquisition or publication outcomes are never retried here.
    """
    from . import registered_input
    from .session_registry import SessionRegistry
    from .session_witness import SessionWitness
    if type(claim_ticket) is not BoundClaim or claim_ticket._acquisition_attempted:
        raise FoundryError('global_witness_binding', 'An unused original claim ticket is required.')
    registry,journal,witness = claim_ticket._registry,claim_ticket._journal,claim_ticket._witness
    if (type(registry) is not SessionRegistry or type(journal) is not input_journal.InputJournal
            or type(witness) is not SessionWitness or witness._registry is not registry):
        raise FoundryError('global_witness_binding', 'Original registry, journal and witness are required.')
    target = input_journal._target(journal.session['target'])
    identity = {key:target[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
    if canonical(witness._identity) != canonical(identity):
        raise FoundryError('global_witness_binding', 'Witness lifetime differs from the reserved target.')
    with _publication_scope('global_witness_unresolved') as publication, witness._exclusive():
        with lock(journal.root/'operation.lock'), registry._hold_events() as state:
            journal._verify()
            binding = registered_input.journal_binding(journal)
            events = state['events']
            if (len(events) != 2 or events[0].get('session_id') != journal.session['session_id']
                    or events[0].get('journal_sha256') != binding
                    or canonical(events[0].get('lifetime')) != canonical(session_registry._lifetime(target))
                    or events[1].get('kind') != 'attempt'
                    or events[1].get('operation') != 'global_acquire' or events[1].get('request_id') != witness._request
                    or events[1].get('intent_sha256') != witness._intent):
                raise FoundryError('global_witness_binding', 'Original witness acquisition is not the pending attempt.')
            reservation = inspect(target)
            if (reservation['session_id'] != journal.session['session_id']
                    or reservation['witness_id'] != witness._request or reservation['journal_sha256'] != binding
                    or reservation['reservation_sha256'] != claim_ticket._reservation['reservation_sha256']):
                raise FoundryError('global_witness_binding', 'Original reservation differs from this witness and journal.')
            claim_ticket._acquisition_attempted = True
            publication[0] = True
            lease = witness._provider.acquire(witness._request,identity)
            claim_ticket._lease = lease
            with witness._provider.guard_held(witness._request,identity) as (status,check_live):
                witness._check_status(status)
                check_live()
                resolution = {'kind':'resolved','request_id':witness._request,'intent_sha256':witness._intent,
                              'receipt_sha256':digest(status),'outcome':'acknowledged'}
                root,authority_id = _authority()
                row = {'schema':1,'authority_id':authority_id,'scope':reservation['scope'],
                       'reservation_sha256':reservation['reservation_sha256'],'session_id':journal.session['session_id'],
                       'witness_id':witness._request,'acquisition_intent_sha256':witness._intent,
                       'registry_resolution_sha256':digest(resolution)}
                new_json(root/'lifetimes'/(reservation['scope']+'.attached.json'),row)
                registry._append_locked(resolution)
    # Do not authorize dispatch if a lock/guard cleanup acknowledgement was
    # lost after publication; the original witness stays pending in that case.
    witness._global_state = 'attached'
    return lease,{'outcome':'global_witness_attached','scope':reservation['scope'],'attachment_sha256':digest(row),
                  'same_user_authenticated':False,'atomic_snapshot':False,'game_qualified':False}


def inspect_attachment(target):
    """Observe an existing attachment; never assert the witness is still held."""
    reservation = inspect(target)
    root,authority_id = _authority()
    row = read_json(root/'lifetimes'/(reservation['scope']+'.attached.json'))
    fields = {'schema','authority_id','scope','reservation_sha256','session_id','witness_id',
              'acquisition_intent_sha256','registry_resolution_sha256'}
    hashes = ('acquisition_intent_sha256','registry_resolution_sha256')
    if (type(row) is not dict or set(row) != fields or type(row['schema']) is not int or row['schema'] != 1
            or row['authority_id'] != authority_id or row['scope'] != reservation['scope']
            or row['reservation_sha256'] != reservation['reservation_sha256']
            or row['session_id'] != reservation['session_id'] or row['witness_id'] != reservation['witness_id']
            or any(type(row[key]) is not str or len(row[key]) != 64
                   or any(c not in '0123456789abcdef' for c in row[key]) for key in hashes)):
        raise FoundryError('global_witness_unresolved', 'Original attachment is malformed or changed; do not retry.')
    return {'outcome':'global_attachment_observed','scope':reservation['scope'],'attachment_sha256':digest(row),
            'current_witness_verified':False,'registry_gate_verified':False,
            'atomic_snapshot':False,'game_qualified':False}


def inspect_prior_sessions(target, sessions):
    """Inspect explicitly supplied older sessions; never claim completeness.

    Existing cooperative lock files may be initialized, and SQLite may create
    its usual sidecars. The per-session checks are not one atomic snapshot.
    """
    from . import independent_session, registered_input
    if type(sessions) is not list or len(sessions) > 64:
        raise FoundryError('global_history_arguments', 'Supply at most 64 explicit authority/session pairs.')
    selected = input_journal._target(target)
    lifetime = session_registry._lifetime(selected)
    seen, rows = set(), []
    for pair in sessions:
        if (type(pair) is not dict or set(pair) != {'authority','session'}
                or type(pair['authority']) is not str or type(pair['session']) is not str):
            raise FoundryError('global_history_arguments', 'Each entry needs exact authority and session paths.')
        root, session = external_path(pair['authority']),external_path(pair['session'])
        key = (os.path.normcase(str(root)),os.path.normcase(str(session)))
        if key in seen:
            raise FoundryError('global_history_arguments', 'Duplicate session entries are not accepted.')
        seen.add(key)
        intent,registry,journal,_ = independent_session._open(root,session)
        if canonical(session_registry._lifetime(intent['identity'])) != canonical(lifetime):
            raise FoundryError('global_history_binding', 'A supplied prior session has a different process lifetime.')
        with lock(journal.root/'operation.lock'), registry._hold_events() as current:
            journal._verify()
            binding = registered_input.journal_binding(journal)
            events = current['events']
            if (not events or events[0].get('session_id') != journal.session['session_id']
                    or events[0].get('journal_sha256') != binding):
                raise FoundryError('global_history_binding', 'Original session records changed during inspection.')
            runs = [journal._status(request_id) for request_id in journal._runs()]
            pending = bool(events[-1].get('kind') == 'attempt') or any(
                row['state'] not in ('rejected','acknowledged_by_host') for row in runs)
            rows.append({'session_id':journal.session['session_id'],'journal_sha256':binding,
                         'registry_head':current['head'],'pending_observed':pending,
                         'witness_may_be_held':any(event.get('kind') == 'attempt'
                             and event.get('operation') in ('acquire','global_acquire') for event in events),
                         'input_states':[{'request_id':row['request_id'],'state':row['state']} for row in runs]})
    return {'outcome':'specified_sessions_observed','sessions':rows,
            'pending_observed':any(row['pending_observed'] for row in rows),
            'complete_history_verified':False,'atomic_snapshot':False,
            'live_admission_authorized':False,'game_qualified':False,
            'lock_or_sqlite_side_effects_possible':True}
