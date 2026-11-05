"""Fixture-only sequential caller for one original in-memory LiveOwner.

No RPC, game admission, operator authentication or cross-owner authority is
provided. Trusted Python can bypass this client and call an owner directly.
"""
import threading

from . import independent_live_owner, input_journal
from .errors import FoundryError


class FixtureStepClient:
    """Fence this caller on unknown completion or confirmation outcomes."""
    def __init__(self, owner):
        if type(owner) is not independent_live_owner.LiveOwner:
            raise FoundryError('fixture_client_binding', 'The original fixture owner is required.')
        owner._owner()
        if owner._attempted or getattr(owner, '_fixture_client_claimed', False):
            raise FoundryError('fixture_client_binding', 'An unused owner and one original fixture client are required.')
        owner._fixture_client_claimed = True
        self._owner = owner
        self._thread = threading.current_thread()
        self._busy = False
        self._request_id = None
        self._phase = 'ready'
        self._inspected_token = None
        self._known_token = None

    def _check(self):
        if threading.current_thread() is not self._thread or self._busy:
            raise FoundryError('fixture_client_busy', 'Use the original client thread without reentry.')

    def _snapshot(self, value, state):
        if (type(value) is not dict or value.get('outcome') != 'fixture_step_completed'
                or value.get('request_id') != self._request_id
                or value.get('completion_state') != state
                or not input_journal._token(value.get('completion_token'))
                or value.get('observer_authenticated') is not False
                or value.get('semantic_state_verified') is not False
                or value.get('game_qualified') is not False
                or value.get('os_release_verified') is not False):
            raise FoundryError('fixture_client_unresolved', 'Original completion reply is unavailable or mismatched.',
                               no_replay=True)
        return value['completion_token']

    def click_once(self, request_id, x, y, *, expected_observation, evidence_directory):
        self._check()
        if self._phase != 'ready' or not input_journal._token(request_id):
            raise FoundryError('fixture_client_pending', 'Inspect the original request before another input.', no_replay=True)
        self._busy = True
        self._request_id = request_id
        self._phase = 'input_unknown'
        try:
            result = self._owner.click_once(request_id,x,y,expected_observation=expected_observation,
                                            evidence_directory=evidence_directory)
            if (type(result) is not dict or result.get('outcome') != 'frame_recorded'
                    or result.get('request_id') != request_id or result.get('acknowledged') is not False
                    or result.get('game_qualified') is not False):
                raise FoundryError('fixture_client_unresolved', 'Original input reply is uncertain.', no_replay=True)
            self._phase = 'awaiting_completion'
            return result
        finally:
            self._busy = False

    def complete(self, observer):
        self._check()
        if self._phase != 'awaiting_completion' or not callable(observer):
            raise FoundryError('fixture_client_pending', 'The original completed fixture input is required.', no_replay=True)
        self._busy = True
        self._phase = 'completion_unknown'
        try:
            value = self._owner.fixture_complete_step(self._request_id,observer)
            token = self._snapshot(value,'ready')
            self._known_token = token
            self._phase = 'confirmation_unknown'
            result = self._owner.fixture_confirm_completion(self._request_id,token)
            if self._snapshot(result,'confirmed') != token:
                raise FoundryError('fixture_client_unresolved', 'Confirmation token changed.', no_replay=True)
            self._phase, self._request_id = 'ready', None
            self._known_token = None
            return result
        finally:
            self._busy = False

    def inspect_completion(self):
        """Inspect the original owner; never send input, repeat cleanup or confirm."""
        self._check()
        if self._phase not in ('completion_unknown','confirmation_unknown'):
            raise FoundryError('fixture_client_pending', 'No uncertain completion is available to inspect.', no_replay=True)
        self._busy = True
        try:
            result = self._owner.fixture_completion_snapshot(self._request_id)
            state = result.get('completion_state') if type(result) is dict else None
            if state not in ('ready','confirmed'):
                raise FoundryError('fixture_client_unresolved', 'Original completion state is unavailable.', no_replay=True)
            token = self._snapshot(result,state)
            if self._known_token is not None and token != self._known_token:
                raise FoundryError('fixture_client_unresolved', 'Original completion token changed.', no_replay=True)
            if state == 'confirmed':
                if self._phase != 'confirmation_unknown':
                    raise FoundryError('fixture_client_unresolved', 'Unexpected confirmation history.', no_replay=True)
                self._phase, self._request_id = 'ready', None
                self._inspected_token = None
                self._known_token = None
            else:
                self._known_token = token
                self._inspected_token = token
            return result
        finally:
            self._busy = False

    def confirm_inspected(self):
        """Confirm only an explicitly inspected ready completion; never send input."""
        self._check()
        if (self._phase not in ('completion_unknown','confirmation_unknown')
                or not input_journal._token(self._inspected_token)):
            raise FoundryError('fixture_client_pending', 'Inspect the original ready completion first.', no_replay=True)
        self._busy = True
        self._phase = 'confirmation_unknown'
        token = self._inspected_token
        self._inspected_token = None
        try:
            result = self._owner.fixture_confirm_completion(self._request_id,token)
            if self._snapshot(result,'confirmed') != token:
                raise FoundryError('fixture_client_unresolved', 'Confirmation token changed.', no_replay=True)
            self._phase, self._request_id = 'ready', None
            self._known_token = None
            return result
        finally:
            self._busy = False
