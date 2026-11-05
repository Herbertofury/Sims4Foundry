"""Experimental single-dispatch live sender owned by the current host thread.

This component does not create a process witness, listener, session authority,
or game profile. Its caller supplies the original retained witness, journal and
cooperative registry. Queue acceptance and a received frame do not establish a
UI transition, game state, save, or capture time. There is no restart recovery.
"""
import threading
import re
import os

from . import input_frame_evidence, input_journal, registered_input
from .errors import FoundryError
from .input_packets import (ClientBounds, MouseButton, NavigationKey, Point, ScanCode,
                            encode_click, encode_drag, encode_key_press, encode_wheel)
from .io import external_path
from .live_input import LiveInputBackend
from .session_registry import SessionRegistry
from .session_witness import SessionWitness

_UNRESOLVED_SENDERS = []
_OWNED_FIXTURE_ADMISSION = object()


def _fixture_pid():
    return os.getpid()


class LiveOwner:
    """One original thread, retained witness and cooperative journal/registry."""
    def __init__(self, registry, journal, witness, ffmpeg, ffmpeg_sha256, *, _fixture_admission=None):
        if _fixture_admission is not _OWNED_FIXTURE_ADMISSION:
            raise FoundryError('live_authority_unqualified',
                               'Live admission is disabled until cross-workspace authority is qualified.',
                               input_attempted=False,game_qualified=False)
        if (type(registry) is not SessionRegistry or type(journal) is not input_journal.InputJournal
                or type(witness) is not SessionWitness or witness._registry is not registry):
            raise FoundryError('live_owner_binding', 'Original registry, journal and witness are required.')
        registered_input._check(registry,journal)
        if journal.session['target']['pid'] != _fixture_pid():
            raise FoundryError('live_fixture_only', 'Only this test process may be selected by the fixture admission.')
        self._registry, self._journal, self._witness = registry, journal, witness
        self._ffmpeg, self._ffmpeg_sha256 = ffmpeg, ffmpeg_sha256
        self._thread = threading.current_thread()
        self._busy = False
        self._attempted = set()
        self._completion_attempted = set()
        self._backend, self._backend_open, self._close_attempted = None, False, False

    def _owner(self):
        if threading.current_thread() is not self._thread or self._busy:
            raise FoundryError('live_owner_busy', 'Use the original owner thread without reentry.')

    def click_once(self, request_id, x, y, *, expected_observation, evidence_directory, button='left'):
        """Post one exact click and save passive evidence, with no acknowledgement."""
        if type(x) is not int or type(y) is not int or type(button) is not str or button not in ('left','right'):
            raise FoundryError('invalid_request', 'Exact coordinates and a supported button are required.')
        target = input_journal._target(self._journal.session['target'])
        packets = encode_click(Point(x,y),ClientBounds(target['client_width'],target['client_height']),
                               button=MouseButton(button))
        return self._dispatch_once(request_id,packets,expected_observation,evidence_directory)

    def drag_once(self, request_id, path, duration_ms, *, expected_observation, evidence_directory):
        """Post one bounded client-area drag and retain its original release."""
        if (type(path) not in (list,tuple) or not 2 <= len(path) <= 128
                or any(type(point) not in (list,tuple) or len(point) != 2
                       or any(type(value) is not int for value in point) for point in path)):
            raise FoundryError('invalid_request', 'A bounded list of exact client-area points is required.')
        target = input_journal._target(self._journal.session['target'])
        packets = encode_drag(tuple(Point(*point) for point in path),
                              ClientBounds(target['client_width'],target['client_height']),duration_ms=duration_ms)
        return self._dispatch_once(request_id,packets,expected_observation,evidence_directory)

    def key_once(self, request_id, key, scan_code, extended, *, expected_observation, evidence_directory):
        """Post one allowlisted navigation key with an independently checked mapping."""
        if type(key) is not str or key not in NavigationKey.__members__:
            raise FoundryError('invalid_request', 'An allowlisted navigation key is required.')
        packets = encode_key_press(NavigationKey[key],ScanCode(scan_code,extended))
        return self._dispatch_once(request_id,packets,expected_observation,evidence_directory)

    def wheel_once(self, request_id, screen_x, screen_y, delta, *, expected_observation, evidence_directory):
        """Post one bounded wheel step at an explicit screen coordinate."""
        if type(screen_x) is not int or type(screen_y) is not int or type(delta) is not int:
            raise FoundryError('invalid_request', 'Exact screen coordinates and wheel delta are required.')
        packet = encode_wheel(Point(screen_x,screen_y),delta)
        return self._dispatch_once(request_id,(packet,),expected_observation,evidence_directory)

    def _dispatch_once(self, request_id, packets, expected_observation, evidence_directory):
        self._owner()
        if self._backend is not None:
            raise FoundryError('live_owner_unresolved', 'Original sender is retained; inspect it without reopening.')
        if not input_journal._token(request_id):
            raise FoundryError('invalid_request', 'An exact request ID is required.')
        target = input_journal._target(self._journal.session['target'])
        packets, _ = input_journal._packets(packets,target)
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

    def fixture_complete_step(self, request_id, observer):
        """Fixture-only: acknowledge an explicit trusted observation and retire the sender.

        The observer is injected by the fixture host. It is not authenticated,
        does not represent an operator, and does not establish game state. Any
        error after entry fences this sender; never repeat completion or input.
        """
        self._owner()
        if (not input_journal._token(request_id) or not callable(observer)
                or not self._backend_open or self._close_attempted
                or request_id not in self._attempted or request_id in self._completion_attempted):
            raise FoundryError('live_owner_unresolved', 'The original fixture sender and observer are required.')
        self._completion_attempted.add(request_id)
        self._busy = True
        try:
            with self._witness.guard_for_dispatch(self._registry) as check_live:
                check_live()
                self._journal.acknowledge(request_id, observer)
                check_live()
                registered_input.reconcile_acknowledged(self._registry,self._journal,request_id)
                check_live()
                self._close_sender()
            # Keep the old sender as the fence until cleanup flags are reset.
            # An interruption between assignments must not admit a new sender.
            self._close_attempted = False
            self._backend = None
            return {'outcome':'fixture_step_completed', 'request_id':request_id,
                    'observer_authenticated':False, 'semantic_state_verified':False,
                    'game_qualified':False, 'os_release_verified':False}
        except Exception as error:
            code = error.code if isinstance(error,FoundryError) else None
            code = code if type(code) is str and re.fullmatch(r'[a-z0-9_]{1,64}',code) else 'other'
            raise FoundryError('live_owner_completion_unresolved',
                               'Fixture acknowledgement or sender retirement may have occurred; inspect without replay.',
                               no_replay=True, original_error_code=code) from None
        finally:
            self._busy = False

    def close_sender(self):
        """One cleanup attempt for the original sender; never close the witness."""
        self._owner()
        return self._close_sender()

    def _close_sender(self):
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
