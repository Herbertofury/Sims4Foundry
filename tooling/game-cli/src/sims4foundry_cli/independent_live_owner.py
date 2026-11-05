"""Experimental single-dispatch live sender owned by the current host thread.

This component does not create a process witness, listener, session authority,
or game profile. Its caller supplies the original retained witness, journal and
cooperative registry. Queue acceptance and a received frame do not establish a
UI transition, game state, save, or capture time. There is no restart recovery.
"""
import threading
import re

from . import input_frame_evidence, input_journal, registered_input
from .errors import FoundryError
from .input_packets import ClientBounds, Point, encode_click
from .io import external_path
from .live_input import LiveInputBackend
from .session_registry import SessionRegistry
from .session_witness import SessionWitness

_UNRESOLVED_SENDERS = []


class LiveOwner:
    """One original thread, retained witness and cooperative journal/registry."""
    def __init__(self, registry, journal, witness, ffmpeg, ffmpeg_sha256):
        if (type(registry) is not SessionRegistry or type(journal) is not input_journal.InputJournal
                or type(witness) is not SessionWitness or witness._registry is not registry):
            raise FoundryError('live_owner_binding', 'Original registry, journal and witness are required.')
        registered_input._check(registry,journal)
        self._registry, self._journal, self._witness = registry, journal, witness
        self._ffmpeg, self._ffmpeg_sha256 = ffmpeg, ffmpeg_sha256
        self._thread = threading.current_thread()
        self._busy = False
        self._attempted = set()
        self._backend, self._backend_open, self._close_attempted = None, False, False

    def _owner(self):
        if threading.current_thread() is not self._thread or self._busy:
            raise FoundryError('live_owner_busy', 'Use the original owner thread without reentry.')

    def click_once(self, request_id, x, y, *, expected_observation, evidence_directory):
        """Post one exact click and save passive evidence, with no acknowledgement."""
        self._owner()
        if self._backend is not None:
            raise FoundryError('live_owner_unresolved', 'Original sender is retained; inspect it without reopening.')
        if not input_journal._token(request_id) or type(x) is not int or type(y) is not int:
            raise FoundryError('invalid_request', 'An exact request ID and integer coordinates are required.')
        target = input_journal._target(self._journal.session['target'])
        packets = encode_click(Point(x,y),ClientBounds(target['client_width'],target['client_height']))
        expectation = input_journal._expectation(expected_observation)
        output = external_path(evidence_directory)
        if output.exists() or output.is_symlink():
            raise FoundryError('output_exists', 'Choose a new evidence directory.')
        if request_id in self._attempted:
            raise FoundryError('live_owner_unresolved', 'The original request was already attempted; do not replay.')
        if len(self._attempted) >= input_journal.MAX_RUNS:
            raise FoundryError('live_owner_full', 'Owner request history is full; do not replace it.')
        self._busy = True
        self._attempted.add(request_id)
        admission_entered = False
        try:
            # Never reopen a backend for an existing journal request. The
            # registry performs the final shared pending check before dispatch.
            status = self._journal.status(request_id)
            if status['state'] != 'not_found':
                raise FoundryError('live_owner_unresolved', 'Original request exists; inspect it without replay.')
            events = registered_input._check(self._registry,self._journal)
            if events and events[-1].get('kind') == 'attempt':
                raise FoundryError('registry_pending', 'A prior shared attempt remains unresolved.')
            def prepare():
                backend = LiveInputBackend(target,packets,self._ffmpeg,self._ffmpeg_sha256)
                self._backend = backend
                # Retain before entering: an asynchronous interruption after
                # __enter__ returns but before the next assignment must not
                # silently drop ownership of a possibly open sender.
                _UNRESOLVED_SENDERS.append(backend)
                backend.__enter__()
                self._backend_open = True
                return backend
            admission_entered = True
            result = registered_input.execute_after_admission(
                self._registry,self._journal,request_id,packets,prepare,
                expected_observation=expectation,witness=self._witness)
            if result.get('state') != 'awaiting_readback':
                return {'outcome':'input_unresolved','request_id':request_id,'input':result,
                        'phase':'after_admission_entry','input_may_have_been_sent':True,'no_replay':True,
                        'acknowledged':False,'game_qualified':False}
            evidence = input_frame_evidence.capture(self._journal,request_id,self._backend,output)
            return {'outcome':'frame_recorded','request_id':request_id,'input':result,'evidence':evidence,
                    'input_may_have_been_sent':True,'no_replay':True,
                    'acknowledged':False,'semantic_state_verified':False,'game_qualified':False}
        except Exception as error:
            if not admission_entered and isinstance(error,FoundryError):
                raise
            code = error.code if isinstance(error,FoundryError) else None
            code = code if type(code) is str and re.fullmatch(r'[a-z0-9_]{1,64}',code) else 'other'
            raise FoundryError('live_owner_unresolved', 'Original attempt or evidence may exist; inspect without replay.',
                               phase='after_admission_entry',input_may_have_been_sent=True,
                               no_replay=True,original_error_code=code) from None
        finally:
            self._busy = False

    def recover_frame_binding(self, request_id):
        """Inspect the original sender and pending attempt without another input."""
        self._owner()
        if not self._backend_open or self._close_attempted:
            raise FoundryError('frame_binding', 'The original retained sender is unavailable.')
        return registered_input.reconcile_frame_binding(self._registry,self._journal,request_id,
                                                        self._backend,witness=self._witness)

    def capture_frame(self, request_id, evidence_directory):
        """Capture from the still-retained original sender; never send input."""
        self._owner()
        if not self._backend_open or self._close_attempted:
            raise FoundryError('frame_binding', 'The original retained sender is unavailable.')
        self._busy = True
        try:
            return input_frame_evidence.capture(self._journal,request_id,self._backend,evidence_directory)
        finally:
            self._busy = False

    def close_sender(self):
        """One cleanup attempt for the original sender; never close the witness."""
        self._owner()
        if not self._backend_open or self._close_attempted:
            raise FoundryError('live_owner_unresolved', 'Original sender cleanup is unavailable or already attempted.')
        self._close_attempted = True
        try:
            self._backend.__exit__(None,None,None)
        except BaseException:
            raise FoundryError('live_owner_cleanup_unresolved', 'Original sender cleanup is uncertain; do not retry.',
                               no_replay=True) from None
        self._backend_open = False
        for index,item in enumerate(_UNRESOLVED_SENDERS):
            if item is self._backend:
                del _UNRESOLVED_SENDERS[index]
                break
        return {'outcome':'sender_cleanup_reported','os_release_verified':False,'game_qualified':False}
