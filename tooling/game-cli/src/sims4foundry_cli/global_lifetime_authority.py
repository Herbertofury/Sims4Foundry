"""Single OS-located cooperative lifetime reservation; no process access.

This prevents cooperating callers using this module from selecting a different
workspace authority. It does not authenticate same-user programs or resist
same-user deletion, rollback, path races or Windows power-loss loss.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import uuid

from . import input_journal, session_registry
from .errors import FoundryError
from .io import canonical, digest, external_path, new_json, read_json


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
        self._reservation, self._attachment_attempted = reservation, False

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
    events = registered_input._check(registry,journal)
    if (canonical(witness._identity) != canonical(identity) or len(events) != 1
            or events[0].get('kind') != 'session' or journal._runs()):
        raise FoundryError('global_session_unresolved', 'An unused matching owner and session are required.')
    reservation = claim(target,journal.session['session_id'],witness._request,registered_input.journal_binding(journal))
    return BoundClaim(registry,journal,witness,reservation)


def attach_witness(registry, journal, witness, claim_ticket):
    """Bind an already-held original witness to its existing reservation.

    No process is opened and no input is sent. The guard samples the retained
    provider; registry, journal and reservation are not an atomic snapshot.
    """
    from . import registered_input
    from .session_registry import SessionRegistry
    from .session_witness import SessionWitness
    if (type(registry) is not SessionRegistry or type(journal) is not input_journal.InputJournal
            or type(witness) is not SessionWitness or witness._registry is not registry
            or type(claim_ticket) is not BoundClaim or claim_ticket._registry is not registry
            or claim_ticket._journal is not journal or claim_ticket._witness is not witness
            or claim_ticket._attachment_attempted):
        raise FoundryError('global_witness_binding', 'Original registry, journal and witness are required.')
    target = input_journal._target(journal.session['target'])
    identity = {key:target[key] for key in ('pid','creation_filetime','executable','executable_sha256')}
    if canonical(witness._identity) != canonical(identity):
        raise FoundryError('global_witness_binding', 'Witness lifetime differs from the reserved target.')
    with witness.guard_for_dispatch(registry) as check_live:
        events = registered_input._check(registry,journal)
        if (len(events) != 3 or events[1].get('kind') != 'attempt'
                or events[1].get('operation') != 'acquire' or events[1].get('request_id') != witness._request
                or events[1].get('intent_sha256') != witness._intent or events[-1].get('kind') != 'resolved'
                or events[-1].get('request_id') != witness._request
                or events[-1].get('intent_sha256') != witness._intent
                or events[-1].get('outcome') != 'acknowledged'):
            raise FoundryError('global_witness_binding', 'Original witness acquisition is not the latest resolved event.')
        reservation = inspect(target)
        binding = registered_input.journal_binding(journal)
        if (reservation['session_id'] != journal.session['session_id']
                or reservation['witness_id'] != witness._request or reservation['journal_sha256'] != binding
                or reservation['reservation_sha256'] != claim_ticket._reservation['reservation_sha256']):
            raise FoundryError('global_witness_binding', 'Original reservation differs from this witness and journal.')
        root,authority_id = _authority()
        row = {'schema':1,'authority_id':authority_id,'scope':reservation['scope'],
               'reservation_sha256':reservation['reservation_sha256'],'session_id':journal.session['session_id'],
               'witness_id':witness._request,'acquisition_intent_sha256':witness._intent,
               'registry_resolution_sha256':digest(events[-1])}
        check_live()
        claim_ticket._attachment_attempted = True
        try:
            new_json(root/'lifetimes'/(reservation['scope']+'.attached.json'),row)
        except FileExistsError:
            raise FoundryError('global_witness_unresolved', 'Original attachment exists; inspect without repeating.',
                               no_replay=True) from None
        except Exception:
            raise FoundryError('global_witness_unresolved', 'Witness attachment may have completed; inspect without retry.',
                               no_replay=True) from None
    return {'outcome':'global_witness_attached','scope':reservation['scope'],'attachment_sha256':digest(row),
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
