"""Authenticated, externally anchored process-session registry experiment.

The anchor and key are trusted infrastructure, not supplied by this module.
No process, profile, game, transport, key store or OS access is performed here.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import hmac
import re
import threading

from .errors import FoundryError
from .io import canonical, digest, external_path, lock, new_json
from .neutral_records import read_json
from .windows import validate_identity

TOKEN = re.compile(r'[0-9a-f]{32}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
RECORD = re.compile(r'record-([0-9]{6})\.json\Z')
ZERO = '0' * 64
MAX_RECORDS = 4096


def _require(value, code, message):
    if not value:
        raise FoundryError(code, message)


def _token(value):
    return type(value) is str and TOKEN.fullmatch(value) is not None


def _hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def _lifetime(identity):
    validate_identity(identity)
    return {key: deepcopy(identity[key]) for key in ('pid', 'creation_filetime', 'executable_sha256')}


class SessionRegistry:
    """Cooperating controllers share a secret, canonical directory and anchor.

    `anchor.read(lifetime_hash)` returns None or an exact registry head digest.
    `anchor.compare_and_swap(lifetime_hash, previous, next)` must atomically and
    durably advance the independently protected anchor and return exactly True
    on success. A failed or lost acknowledgement never authorizes a second CAS.
    All anchor methods and file operations are trusted bounded providers.

    The registry cannot enforce a canonical path or key, authenticate anchor
    implementations, establish OS user identity, protect against a compromised
    key, or prevent unrelated tools from sending input. Do not treat it as a
    production admission authority without those separately qualified pieces.
    """
    def __init__(self, directory, identity, key, anchor):
        _require(type(key) is bytes and len(key) == 32, 'registry_key', 'A trusted 32-byte registry key is required.')
        _require(callable(getattr(anchor, 'read', None)) and callable(getattr(anchor, 'compare_and_swap', None)),
                 'registry_anchor', 'A trusted durable anchor provider is required.')
        self._root = external_path(directory)
        _require(self._root.is_dir() and not self._root.is_symlink(),
                 'registry_directory', 'Use the existing canonical registry directory.')
        self._lifetime = _lifetime(identity)
        self._scope = digest(self._lifetime)
        self._key, self._anchor = key, anchor
        self._directory = self._root / self._scope
        self._lock_owner = None

    @contextmanager
    def _hold_lock(self):
        with lock(self._root / ('scope-' + self._scope + '.lock')):
            _require(self._lock_owner is None, 'registry_pending', 'Registry lock is already held.')
            self._lock_owner = threading.current_thread()
            try:
                yield
            finally:
                self._lock_owner = None

    def _mac(self, content):
        return hmac.new(self._key, canonical(content), hashlib.sha256).hexdigest()

    def _load(self):
        if not self._directory.exists():
            return [], ZERO
        _require(self._directory.is_dir() and not self._directory.is_symlink(),
                 'registry_corrupt', 'Registry scope is not a regular directory.')
        names = sorted(item.name for item in self._directory.iterdir())
        _require(len(names) <= MAX_RECORDS and all(RECORD.fullmatch(name) for name in names),
                 'registry_corrupt', 'Registry contains unknown or excessive records.')
        records, previous = [], ZERO
        for index, name in enumerate(names):
            _require(name == 'record-%06d.json' % index, 'registry_corrupt', 'Registry chain is incomplete.')
            record = read_json(self._directory / name)
            _require(type(record) is dict and set(record) == {'schema','scope','index','previous','event','mac'}
                     and type(record['schema']) is int and record['schema'] == 1
                     and record['scope'] == self._scope and type(record['index']) is int and record['index'] == index
                     and record['previous'] == previous and _hash(record['mac']),
                     'registry_corrupt', 'Registry chain or scope is invalid.')
            body = {key: record[key] for key in ('schema','scope','index','previous','event')}
            _require(hmac.compare_digest(record['mac'], self._mac(body)),
                     'registry_authentication', 'Registry record authentication failed.')
            self._apply(records, record['event'])
            records.append(record)
            previous = digest(record)
        return records, previous

    def _apply(self, records, event):
        _require(type(event) is dict and type(event.get('kind')) is str,
                 'registry_corrupt', 'Registry event is invalid.')
        kind = event['kind']
        if not records:
            _require(set(event) == {'kind','session_id','journal_sha256','lifetime'}
                     and kind == 'session' and _token(event['session_id']) and _hash(event['journal_sha256'])
                     and canonical(event['lifetime']) == canonical(self._lifetime),
                     'registry_corrupt', 'Registry session binding is invalid.')
            return
        _require(kind in ('attempt', 'resolved') and _token(event.get('request_id')),
                 'registry_corrupt', 'Registry operation is invalid.')
        prior = [entry['event'] for entry in records[1:]]
        if kind == 'attempt':
            _require(set(event) == {'kind','request_id','operation','intent_sha256'}
                     and event['operation'] in ('acquire', 'input', 'exit') and _hash(event['intent_sha256'])
                     and not any(item['request_id'] == event['request_id'] for item in prior)
                     and (not prior or prior[-1]['kind'] == 'resolved'),
                     'registry_pending', 'A previous request is unresolved or this request already exists.')
        else:
            _require(set(event) == {'kind','request_id','intent_sha256','receipt_sha256','outcome'}
                     and _hash(event['intent_sha256']) and _hash(event['receipt_sha256'])
                     and event['outcome'] in ('acknowledged','rejected') and bool(prior)
                     and prior[-1]['kind'] == 'attempt' and prior[-1]['request_id'] == event['request_id']
                     and prior[-1]['intent_sha256'] == event['intent_sha256'],
                     'registry_pending', 'Resolution does not bind the outstanding original request.')

    def _read_anchor(self):
        try:
            value = self._anchor.read(self._scope)
        except Exception:
            raise FoundryError('registry_anchor_unavailable', 'Registry anchor is unavailable.') from None
        _require(value is None or _hash(value), 'registry_anchor_unavailable', 'Registry anchor is invalid.')
        return value

    def _verify(self):
        records, head = self._load()
        anchor = self._read_anchor()
        _require((not records and anchor is None) or (bool(records) and anchor == head),
                 'registry_unresolved', 'Registry and anchor disagree; inspect the original transition without replay.')
        return records, head

    def _append(self, event):
        with self._hold_lock():
            return self._append_locked(event)

    def _append_locked(self, event):
        """Append only while the caller already owns this registry's lock."""
        _require(self._lock_owner is threading.current_thread(), 'registry_pending', 'Original registry lock is required.')
        records, previous = self._verify()
        _require(len(records) < MAX_RECORDS, 'registry_full', 'Registry record limit reached.')
        if type(event) is dict and event.get('kind') == 'attempt' and event.get('operation') == 'acquire':
            _require(not any(record['event'].get('kind') == 'attempt'
                             and record['event'].get('operation') == 'acquire' for record in records),
                     'registry_pending', 'Original witness acquisition already exists; never acquire a replacement.')
        self._apply(records, event)
        self._directory.mkdir(exist_ok=True)
        body = {'schema':1, 'scope':self._scope, 'index':len(records), 'previous':previous, 'event':deepcopy(event)}
        record = dict(body, mac=self._mac(body))
        head = digest(record)
        new_json(self._directory / ('record-%06d.json' % len(records)), record)
        try:
            advanced = self._anchor.compare_and_swap(self._scope, None if not records else previous, head)
        except Exception:
            raise FoundryError('registry_unresolved', 'Anchor update acknowledgement was lost; do not replay.') from None
        _require(advanced is True, 'registry_unresolved', 'Anchor did not confirm the original update; do not replay.')
        _require(self._read_anchor() == head, 'registry_unresolved', 'Anchor did not retain the original update.')
        return {'scope':self._scope, 'head':head, 'index':len(records), 'event':deepcopy(event)}

    def open_session(self, session_id, journal_sha256):
        _require(_token(session_id) and _hash(journal_sha256), 'registry_session', 'Exact session and journal binding are required.')
        return self._append({'kind':'session', 'session_id':session_id, 'journal_sha256':journal_sha256,
                             'lifetime':deepcopy(self._lifetime)})

    def begin(self, request_id, operation, intent_sha256):
        _require(_token(request_id) and operation in ('acquire','input','exit') and _hash(intent_sha256),
                 'registry_request', 'Exact request, operation and intent are required.')
        return self._append({'kind':'attempt','request_id':request_id,'operation':operation,'intent_sha256':intent_sha256})

    def resolve(self, request_id, intent_sha256, receipt_sha256, outcome):
        _require(_token(request_id) and _hash(intent_sha256) and _hash(receipt_sha256)
                 and outcome in ('acknowledged','rejected'), 'registry_receipt', 'Exact original receipt binding is required.')
        return self._append({'kind':'resolved','request_id':request_id,'intent_sha256':intent_sha256,
                             'receipt_sha256':receipt_sha256,'outcome':outcome})

    def inspect(self):
        with self._hold_lock():
            records, head = self._verify()
            return {'scope':self._scope, 'head':head if records else None,
                    'events':[deepcopy(item['event']) for item in records]}

    @contextmanager
    def _hold_events(self):
        """Keep the cooperating registry lock held during a bounded host step."""
        with self._hold_lock():
            records,head = self._verify()
            yield {'scope':self._scope,'head':head if records else None,
                   'events':[deepcopy(item['event']) for item in records]}
