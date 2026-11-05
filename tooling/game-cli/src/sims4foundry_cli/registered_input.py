"""Bind an input journal attempt to a cooperating session registry.

The registry and observer remain trusted experimental providers. This module
does not establish a canonical authority, verify game semantics or open a game.
"""
from contextlib import ExitStack
from pathlib import PureWindowsPath

from . import capture_pin, companion_build, input_journal
from .errors import FoundryError
from .io import canonical, digest, lock
from .session_registry import SessionRegistry, _lifetime
from .session_witness import SessionWitness

MAX_RECONCILE_HANDLES = 512


def _retain_record(path, checksum):
    """Retain the exact Windows record and its parent path until resolution."""
    return capture_pin._retain(str(path), checksum, capture_pin._WindowsApi(),
                               image_basename=None, max_image_bytes=input_journal.MAX_RECORD)


def journal_binding(journal):
    """Digest the chosen journal location and its immutable session record."""
    if type(journal) is not input_journal.InputJournal:
        raise FoundryError('registry_binding', 'Use an input journal.')
    journal._verify()
    return digest({'schema':1, 'root':str(journal.root), 'session':journal.session})


def _check(registry, journal):
    if type(registry) is not SessionRegistry or type(journal) is not input_journal.InputJournal:
        raise FoundryError('registry_binding', 'Use the original registry and input journal.')
    journal._verify()
    state = registry.inspect()
    events = state['events']
    if (not events or events[0]['session_id'] != journal.session['session_id']
            or events[0]['journal_sha256'] != journal_binding(journal)
            or canonical(events[0]['lifetime']) != canonical(_lifetime(journal.session['target']))):
        raise FoundryError('registry_binding', 'Registry and journal do not identify the same session.')
    return events


class _WitnessBoundBackend:
    def __init__(self, backend, check_live):
        self._backend, self._check_live = backend, check_live
    def __getattr__(self, name):
        return getattr(self._backend, name)
    def post(self, hwnd, packet):
        self._check_live()
        return self._backend.post(hwnd, packet)


def execute_once(registry, journal, request_id, packets, backend, *, expected_observation, witness):
    """Persist a shared attempt and retain the original live witness during dispatch."""
    _check(registry, journal)
    if not input_journal._token(request_id):
        raise FoundryError('invalid_request', 'An exact request ID is required.')
    target = input_journal._target(journal.session['target'])
    _, wire = input_journal._packets(packets, target)
    expectation = input_journal._expectation(expected_observation)
    intent = {'schema':1, 'request_id':request_id, 'session_id':journal.session['session_id'],
              'target':target, 'packets':wire, 'expectation':expectation}
    expected_hash = digest(intent)
    if type(witness) is not SessionWitness:
        raise FoundryError('witness_binding', 'The original retained session witness is required.')
    with witness.guard_for_dispatch(registry) as check_live:
        registry.begin(request_id, 'input', expected_hash)
        # Any failure, including one before native dispatch, leaves the shared
        # gate unresolved. A fresh handle check immediately precedes each post.
        result = journal.execute_once(request_id, packets, _WitnessBoundBackend(backend, check_live),
                                      expected_observation=expectation)
    if result.get('intent_sha256') != expected_hash:
        raise FoundryError('registry_binding', 'Journal returned a different intent; do not replay.')
    return result


def reconcile_acknowledged(registry, journal, request_id):
    """Bind an already written host acknowledgement; never performs input."""
    events = _check(registry, journal)
    attempts = [index for index, item in enumerate(events)
                if item.get('kind') == 'attempt' and item.get('operation') == 'input'
                and item.get('request_id') == request_id]
    if len(attempts) != 1:
        raise FoundryError('registry_binding', 'No matching original input attempt is pending.')
    index = attempts[0]
    attempt = events[index]
    resolution = events[index+1] if index+1 < len(events) else None
    if resolution is not None and (resolution.get('kind') != 'resolved'
                                    or resolution.get('request_id') != request_id
                                    or resolution.get('outcome') != 'acknowledged'
                                    or resolution.get('intent_sha256') != attempt['intent_sha256']):
        raise FoundryError('registry_binding', 'Original input does not have a matching acknowledgement.')
    with lock(journal.root / 'operation.lock'), ExitStack() as retained:
        journal._verify()
        status = journal._status(request_id)
        if (status['state'] != 'acknowledged_by_host'
                or status['intent_sha256'] != attempt['intent_sha256']):
            raise FoundryError('registry_pending', 'The original input lacks matching host acknowledgement.')
        run = journal.root / 'runs' / request_id
        event_files = sorted((run / 'events').iterdir())
        paths = ([journal.root / 'session.json', run / 'intent.json', run / 'attempt.json',
                  run / 'result.json', run / 'ack.json'] + event_files)
        handles = sum(max(1, len(PureWindowsPath(str(path)).parts)-1) for path in paths)
        if handles > MAX_RECONCILE_HANDLES:
            raise FoundryError('registry_bound', 'Journal evidence exceeds the retained-handle bound.')
        for path in paths:
            _, checksum, _ = companion_build._regular(path, input_journal.MAX_RECORD)
            retained.enter_context(_retain_record(path, checksum))
        # Revalidate the full receipt and all journal links only after the
        # exact files and their directory paths are held. The gate resolution
        # runs while both the cooperative lock and those pins remain held.
        journal._verify()
        verified = journal._status(request_id)
        if (verified['state'] != 'acknowledged_by_host'
                or verified['intent_sha256'] != attempt['intent_sha256']
                or sorted((run / 'events').iterdir()) != event_files):
            raise FoundryError('registry_pending', 'The original acknowledgement changed during reconciliation.')
        receipt = input_journal._read(run / 'ack.json')
        if resolution is not None:
            if resolution['receipt_sha256'] != digest(receipt):
                raise FoundryError('registry_binding', 'Anchored acknowledgement differs from the original journal.')
            return {'scope':registry._scope, 'head':None, 'index':index+1,
                    'event':resolution, 'reconciled_existing':True}
        return registry.resolve(request_id, verified['intent_sha256'], digest(receipt), 'acknowledged')
