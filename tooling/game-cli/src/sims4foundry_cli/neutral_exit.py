"""One-shot normal-exit orchestration for an injected, unqualified provider.

No game import, native input, process discovery, profile access or default
provider exists here. A provider must separately qualify its semantic menu
reader, one-shot sender, session binding and retained process handle.
"""
from copy import deepcopy
import re
from pathlib import Path

from .errors import FoundryError
from .io import canonical, digest, external_path, new_json
from .neutral_records import read_json
from .windows import same_identity, validate_identity


_TOKEN = re.compile(r'[0-9a-f]{32}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_DECIMAL = re.compile(r'[1-9][0-9]{0,19}\Z')
_REQUEST_KEYS = {'request_id', 'policy', 'identity', 'save_guid', 'household_id', 'profile_binding_sha256'}
_VIEW_KEYS = {'identity', 'save_guid', 'household_id', 'profile_binding_sha256', 'surface', 'view_sha256', 'available_actions'}


def _request(value):
    if type(value) is not dict or set(value) != _REQUEST_KEYS:
        raise FoundryError('invalid_exit_request', 'An exact typed exit request is required.')
    if (type(value['request_id']) is not str or not _TOKEN.fullmatch(value['request_id']) or
            type(value['policy']) is not str or value['policy'] not in ('save_and_exit', 'discard_and_exit') or
            any(type(value[k]) is not str or not _DECIMAL.fullmatch(value[k]) or int(value[k]) >= 2**64
                for k in ('save_guid', 'household_id')) or
            type(value['profile_binding_sha256']) is not str or not _HASH.fullmatch(value['profile_binding_sha256'])):
        raise FoundryError('invalid_exit_request', 'Exit policy, owner identity and request ID must be explicit.')
    if type(value['identity']) is not dict:
        raise FoundryError('invalid_exit_request', 'An exact process/window identity is required.')
    validate_identity(value['identity'])
    # Work only from a detached, JSON-canonical baseline, not caller aliases.
    import json
    return json.loads(canonical(value).decode('ascii'))


def _record(kind, request_digest, fields):
    record = {'schema': 1, 'kind': kind, 'request_sha256': request_digest, **fields}
    record['record_sha256'] = digest(record)
    return record


def _validate_record(record, kind, request_digest):
    if (type(record) is not dict or type(record.get('schema')) is not int or record.get('schema') != 1 or record.get('kind') != kind or
            record.get('request_sha256') != request_digest or
            type(record.get('record_sha256')) is not str or
            digest({k: v for k, v in record.items() if k != 'record_sha256'}) != record['record_sha256']):
        raise FoundryError('exit_evidence_changed', 'Stored exit evidence is malformed or changed.')
    return record


def _same(first, second):
    # Python regards True, 1 and 1.0 as equal; record identity must not.
    return canonical(first) == canonical(second)


class ExitWorkflow:
    """Owns the original handle until close; never reopens a missing PID.

    Provider and lease callbacks are injected trusted boundaries and must be
    bounded and independently qualified. `run` is single-use. `reconcile`
    never sends input; after process restart or lost lease, termination may be
    unobservable. Runtime observations do not prove save or normal exit.
    """
    def __init__(self, request, session, reservation_root, provider):
        self._request = _request(request)
        self._request_digest = digest(self._request)
        self._session = external_path(session)
        self._reservation_root = external_path(reservation_root)
        if not self._reservation_root.is_dir() or self._reservation_root.is_symlink():
            raise FoundryError('exit_reservation_unavailable', 'Use the configured existing exit reservation directory.')
        process = self._request['identity']
        lifetime = {key: process[key] for key in ('pid', 'creation_filetime', 'executable_sha256')}
        self._reservation = self._reservation_root / ('exit-' + digest(lifetime) + '.json')
        self._reservation_record = _record('foundry_exit_lifetime_reservation', self._request_digest,
            {'request_id': self._request['request_id'], 'lifetime': lifetime, 'session': str(self._session)})
        self._provider = provider
        self._lease = None
        self._acquisition_state = 'not_started'
        self._started = False
        self._closed = False
        self._terminal = False
        self._release_state = 'not_attempted'

    def _identity(self):
        return deepcopy(self._request['identity'])

    def _binding(self):
        return {key: self._request[key] for key in ('save_guid', 'household_id', 'profile_binding_sha256')}

    def _write(self, name, kind, fields):
        new_json(self._session / name, _record(kind, self._request_digest, fields))

    def _read(self, name, kind):
        return _validate_record(read_json(self._session / name), kind, self._request_digest)

    def _verify_reservation(self):
        if not _same(read_json(self._reservation), self._reservation_record):
            raise FoundryError('exit_reservation_changed', 'Original lifetime reservation changed; no input is authorized.')

    def _alive(self):
        if self._lease is None or self._closed:
            raise FoundryError('exit_lease_unavailable', 'The original retained process handle is unavailable.')
        identity = self._lease.identity()
        same_identity(self._identity(), identity)
        if self._lease.is_running() is not True:
            raise FoundryError('exit_lifetime_unresolved', 'Original process is not confirmed running; do not infer an exit.')

    def _view(self, surface):
        self._alive()
        view = self._provider.readback(self._identity(), deepcopy(self._binding()))
        self._validate_view(view, surface)
        self._alive()
        return deepcopy(view)

    def _validate_view(self, view, surface):
        if type(view) is not dict or set(view) != _VIEW_KEYS:
            raise FoundryError('exit_readback_unverified', 'An exact provider readback is required.')
        same_identity(self._identity(), view['identity'])
        if (any(view[key] != value for key, value in self._binding().items()) or
                type(view['surface']) is not str or view['surface'] != surface or type(view['view_sha256']) is not str or
                not _HASH.fullmatch(view['view_sha256'])):
            raise FoundryError('exit_readback_unverified', 'Exit screen or context did not match.')
        choices = ['open_exit_menu'] if surface == 'live' else ['confirm_save_and_exit', 'confirm_discard_and_exit']
        if (type(view['available_actions']) is not list or
                any(type(item) is not str for item in view['available_actions']) or view['available_actions'] != choices):
            raise FoundryError('exit_readback_unverified', 'Exact semantic exit choices are unavailable.')

    def _send_once(self, action, view, prefix):
        view_digest = digest(view)
        fields = {'request_id': self._request['request_id'], 'action': action,
                  'view_sha256': view_digest, 'send_state': 'not_started_or_unknown'}
        # Persist before the only call. Its existence after interruption is
        # uncertainty, not proof that input was sent.
        self._write(prefix + '-intent.json', 'foundry_exit_input_intent', fields)
        if digest(self._view(view['surface'])) != view_digest:
            raise FoundryError('exit_view_changed', 'Exit view changed before input; no replay is authorized.')
        original = self._read('intent.json', 'foundry_exit_intent')
        expected_original = _record('foundry_exit_intent', self._request_digest, {'request': self._request})
        self._verify_reservation()
        if (not _same(original, expected_original) or
                not _same(self._read(prefix + '-intent.json', 'foundry_exit_input_intent'),
                          _record('foundry_exit_input_intent', self._request_digest, fields))):
            raise FoundryError('exit_evidence_changed', 'Input intent changed before dispatch.')
        result = self._provider.send_once(self._identity(), deepcopy(self._binding()),
                                          self._request['request_id'], action, deepcopy(view))
        if (type(result) is not dict or set(result) != {'request_id', 'action', 'view_sha256', 'accepted'} or
                result['request_id'] != self._request['request_id'] or result['action'] != action or
                result['view_sha256'] != view_digest or result['accepted'] is not True):
            raise FoundryError('exit_input_unresolved', 'Input acceptance was not established; never resend it.')
        self._write(prefix + '-accepted.json', 'foundry_exit_input_accepted',
                    {**fields, 'send_state': 'provider_accepted'})

    def run(self, seconds=30.0):
        if self._started or self._closed:
            raise FoundryError('exit_already_started', 'Exit cannot be dispatched twice.')
        if type(seconds) not in (int, float) or not 0 <= seconds <= 60:
            raise FoundryError('invalid_timeout', 'Exit observation accepts zero through sixty seconds.')
        self._started = True
        if self._session.exists() or self._session.is_symlink():
            raise FoundryError('session_exists', 'Use a new external exit session; never replay an existing intent.')
        self._session.mkdir(parents=True, exist_ok=False)
        self._write('intent.json', 'foundry_exit_intent', {'request': deepcopy(self._request)})
        stage = 'reserve_lifetime'
        try:
            # Host must supply one stable shared registry for this process. A
            # distinct registry is not an acceptable no-replay workaround.
            new_json(self._reservation, self._reservation_record)
            self._verify_reservation()
            stage = 'retain_process'
            # Must predate confirmation (and in fact all input). Missing PID or
            # a newly opened handle after exit is never proof of termination.
            self._write('lease-acquire-intent.json', 'foundry_exit_lease_acquire_intent',
                        {'request_id': self._request['request_id'], 'acquisition_state': 'not_started_or_unknown'})
            # A provider can acquire an OS handle then lose its return value.
            # Enter uncertainty before calling; never infer no handle on throw.
            self._acquisition_state = 'unresolved'
            lease = self._provider.retain_process(self._identity())
            if lease is None:
                raise FoundryError('exit_acquisition_unresolved', 'Lease acquisition has no returned witness; do not reacquire.')
            self._lease = lease
            self._acquisition_state = 'returned_unverified'
            self._alive()
            self._acquisition_state = 'provider_returned_and_sampled'
            stage = 'live_readback'
            live = self._view('live')
            self._write('live-readback.json', 'foundry_exit_readback', {'view': live})
            stage = 'open_menu'
            self._send_once('open_exit_menu', live, 'menu')
            stage = 'confirmation_readback'
            confirm = self._view('exit_confirmation')
            self._write('confirmation-readback.json', 'foundry_exit_readback', {'view': confirm})
            stage = 'confirm_once'
            action = 'confirm_' + self._request['policy']
            self._send_once(action, confirm, 'confirm')
            stage = 'observe_exit'
            return self.reconcile(seconds)
        except PermissionError:
            raise
        except Exception as error:
            return {'ok': False, 'outcome': 'unresolved', 'request_id': self._request['request_id'],
                    'stage': stage, 'error_type': type(error).__name__, 'action_replayed': False,
                    'process_exit_verified': False, 'normal_exit_verified': False, 'save_verified': False,
                    'game_qualified': False, 'message': 'Inspect the original session and retained handle; do not repeat input.'}

    def reconcile(self, seconds=30.0):
        """Observe the original retained lease and immutable receipt; never send."""
        if type(seconds) not in (int, float) or not 0 <= seconds <= 60:
            raise FoundryError('invalid_timeout', 'Exit observation accepts zero through sixty seconds.')
        if not self._started or self._closed or self._lease is None:
            raise FoundryError('exit_lease_unavailable', 'Original retained process handle is required; no reopen fallback.')
        self._verify_reservation()
        intent = self._read('intent.json', 'foundry_exit_intent')
        if set(intent) != {'schema', 'kind', 'request_sha256', 'record_sha256', 'request'} or not _same(intent.get('request'), self._request):
            raise FoundryError('exit_evidence_changed', 'Original request changed.')
        try:
            for prefix, surface, action, view_name in (
                ('menu', 'live', 'open_exit_menu', 'live-readback.json'),
                ('confirm', 'exit_confirmation', 'confirm_' + self._request['policy'], 'confirmation-readback.json')):
                view_record = self._read(view_name, 'foundry_exit_readback')
                if set(view_record) != {'schema', 'kind', 'request_sha256', 'record_sha256', 'view'}:
                    raise FoundryError('exit_evidence_changed', 'Readback receipt schema changed.')
                view = view_record['view']
                self._validate_view(view, surface)
                expected_fields = {'request_id': self._request['request_id'], 'action': action,
                                   'view_sha256': digest(view)}
                for name, kind, state in ((prefix + '-intent.json', 'foundry_exit_input_intent', 'not_started_or_unknown'),
                                          (prefix + '-accepted.json', 'foundry_exit_input_accepted', 'provider_accepted')):
                    if not _same(self._read(name, kind), _record(kind, self._request_digest,
                                                                 {**expected_fields, 'send_state': state})):
                        raise FoundryError('exit_evidence_changed', 'Input receipt differs from its original view.')
        except FileNotFoundError:
            return {'ok': False, 'outcome': 'unresolved', 'request_id': self._request['request_id'],
                    'process_exit_verified': False, 'normal_exit_verified': False, 'save_verified': False,
                    'game_qualified': False, 'action_replayed': False,
                    'message': 'Complete original input receipts are unavailable; never repeat confirmation.'}
        terminal_path = self._session / 'process-exit.json'
        if terminal_path.exists() or terminal_path.is_symlink():
            terminal = self._read('process-exit.json', 'foundry_exit_process_observation')
            prior = terminal.get('observation')
            if (set(terminal) != {'schema', 'kind', 'request_sha256', 'record_sha256', 'observation'} or
                    type(prior) is not dict or not _same(prior, self._observation(True))):
                raise FoundryError('exit_evidence_changed', 'Stored process observation differs from the request.')
            self._terminal = True
            return deepcopy(prior)
        result = self._lease.wait_exit(float(seconds))
        if (type(result) is not dict or set(result) != {'pid', 'creation_filetime', 'handle_retained', 'terminated'} or
                type(result['pid']) is not int or type(result['creation_filetime']) is not int or
                result['pid'] != self._request['identity']['pid'] or
                result['creation_filetime'] != self._request['identity']['creation_filetime'] or
                result['handle_retained'] is not True or type(result['terminated']) is not bool):
            raise FoundryError('exit_lifetime_unresolved', 'Retained process observation is invalid.')
        observation = self._observation(result['terminated'])
        if result['terminated']:
            self._write('process-exit.json', 'foundry_exit_process_observation', {'observation': observation})
            self._terminal = True
        return observation

    def _observation(self, terminated):
        return {'ok': False,
            'outcome': 'provider_exit_sequence_observed' if terminated else 'unresolved',
            'request_id': self._request['request_id'], 'policy': self._request['policy'],
            'pid': self._request['identity']['pid'], 'creation_filetime': self._request['identity']['creation_filetime'],
            'provider_terminated_observed': terminated, 'process_exit_verified': False,
            'normal_exit_verified': False, 'save_verified': False,
            'game_qualified': False, 'action_replayed': False,
            'scope': 'Injected provider evidence only; termination does not prove normal exit or saving.'}

    def close(self, *, abandon_unresolved=False):
        """Retain an unresolved lease unless explicitly relinquished.

        A failed/unknown close must not be retried: the handle could have been
        closed and its integer reused even if the acknowledgement was lost.
        """
        if self._closed:
            if self._release_state not in ('provider_acknowledged', 'no_lease'):
                raise FoundryError('exit_release_unresolved', 'Original handle release is unconfirmed; do not retry it.')
            return {'provider_release_acknowledged': self._release_state == 'provider_acknowledged',
                    'os_release_verified': False, 'repeated_close': False}
        if self._lease is None:
            if self._acquisition_state != 'not_started':
                raise FoundryError('exit_acquisition_unresolved', 'Original lease acquisition is uncertain; do not reacquire or infer no handle.')
            self._closed = True
            self._release_state = 'no_lease'
            return {'acquisition_attempted': False, 'lease_acquired': False,
                    'provider_release_acknowledged': False, 'os_release_verified': False}
        if self._acquisition_state != 'provider_returned_and_sampled':
            raise FoundryError('exit_acquisition_unresolved', 'Returned lease identity was not established; do not close or reacquire it.')
        # A provider-only terminal observation is not independent OS proof.
        # Until a qualified handle adapter exists, release always requires
        # explicit acceptance of lost observation, even after that claim.
        if abandon_unresolved is not True:
            raise FoundryError('exit_lease_retained', 'The original lease is unresolved and remains retained for observation.')
        if self._release_state != 'not_attempted':
            raise FoundryError('exit_release_unresolved', 'Prior release intent is unresolved; do not retry.')
        self._release_state = 'intent_unconfirmed'
        self._write('lease-release-intent.json', 'foundry_exit_lease_release_intent',
                    {'request_id': self._request['request_id'], 'provider_terminal_observed': self._terminal,
                     'abandon_unresolved': True})
        self._release_state = 'release_unconfirmed'
        self._closed = True
        receipt = self._lease.close()
        identity = self._request['identity']
        if (type(receipt) is not dict or set(receipt) != {'pid', 'creation_filetime', 'released'} or
                type(receipt['pid']) is not int or type(receipt['creation_filetime']) is not int or
                receipt['pid'] != identity['pid'] or receipt['creation_filetime'] != identity['creation_filetime'] or
                receipt['released'] is not True):
            raise FoundryError('exit_release_unresolved', 'Provider did not acknowledge the original lease release; do not retry it.')
        self._release_state = 'provider_acknowledged'
        return {'provider_release_acknowledged': True, 'os_release_verified': False,
                'abandoned_unresolved': True}
