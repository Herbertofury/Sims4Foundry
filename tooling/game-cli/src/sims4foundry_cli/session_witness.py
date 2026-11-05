"""Experimental binding between the shared ledger and retained witness owner."""
from copy import deepcopy

from .errors import FoundryError
from .io import canonical, digest
from .retained_process_witness import RetainedProcessRegistry, _identity
from .session_registry import SessionRegistry


class SessionWitness:
    """Record acquisition once and recover only the original in-memory witness.

    This binds a trusted provider assertion to a cooperating registry; it does
    not authenticate clients, verify loaded image bytes, prove OS release, or
    recover a handle after the provider process exits. No game is opened here
    until a caller explicitly invokes acquire on the injected provider.
    """
    def __init__(self, registry, provider, identity, request_id):
        if type(registry) is not SessionRegistry or type(provider) is not RetainedProcessRegistry:
            raise FoundryError('witness_binding', 'Use the original registry and retained witness owner.')
        self._registry, self._provider = registry, provider
        self._identity = _identity(identity)
        self._request = request_id
        state = registry.inspect()
        if not state['events']:
            raise FoundryError('witness_binding', 'A bound session is required.')
        expected = {key:self._identity[key] for key in ('pid','creation_filetime','executable_sha256')}
        if canonical(state['events'][0]['lifetime']) != canonical(expected):
            raise FoundryError('witness_binding', 'Witness lifetime differs from the registry session.')
        self._intent = digest({'schema':1, 'scope':state['scope'], 'request_id':request_id,
                               'identity':self._identity, 'operation':'acquire'})

    def _status(self):
        status = self._provider.status(self._request)
        if (type(status) is not dict or status.get('request_id') != self._request
                or status.get('state') != 'held' or canonical(status.get('identity')) != canonical(self._identity)
                or status.get('os_handle_exported') is not False
                or status.get('os_release_verified') is not False
                or status.get('loaded_image_hash_verified') is not False
                or status.get('game_qualified') is not False or status.get('authenticated') is not False):
            raise FoundryError('witness_unresolved', 'Original provider witness is not confirmed held.')
        return status

    def _complete(self):
        status = self._status()
        events = self._registry.inspect()['events']
        matches = [i for i,event in enumerate(events) if event.get('kind') == 'attempt'
                   and event.get('operation') == 'acquire' and event.get('request_id') == self._request
                   and event.get('intent_sha256') == self._intent]
        if len(matches) != 1:
            raise FoundryError('witness_binding', 'Original acquisition intent is unavailable.')
        index = matches[0]
        if index + 1 == len(events):
            self._registry.resolve(self._request, self._intent, digest(status), 'acknowledged')
        else:
            recorded = events[index+1]
            if (recorded.get('kind') != 'resolved' or recorded.get('request_id') != self._request
                    or recorded.get('intent_sha256') != self._intent
                    or recorded.get('receipt_sha256') != digest(status)
                    or recorded.get('outcome') != 'acknowledged'):
                raise FoundryError('witness_unresolved', 'Original acquisition resolution differs from the witness.')

    def acquire(self):
        self._registry.begin(self._request, 'acquire', self._intent)
        lease = self._provider.acquire(self._request, deepcopy(self._identity))
        self._complete()
        return lease

    def recover(self):
        """Reconcile only the same owner's existing handle; never open a PID."""
        lease = self._provider.recover(self._request, deepcopy(self._identity))
        self._complete()
        return lease
