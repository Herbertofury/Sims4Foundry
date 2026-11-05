"""Record a passive frame from the original sender's active recorder.

This is observational evidence only. A received frame timestamp is not a
capture timestamp, action causality, authenticated semantic state or consent
to acknowledge a journal request. It sends no input and accesses no profile.
"""
from . import companion_build, input_journal
from .errors import FoundryError
from .io import digest, external_path, lock, new_json, sha256
from .live_input import LiveInputBackend
from .png import inspect_png
from .windows import same_identity


def capture(journal, request_id, backend, destination):
    """Write one new request-bound frame without resolving the original action."""
    if type(journal) is not input_journal.InputJournal or type(backend) is not LiveInputBackend:
        raise FoundryError('frame_binding', 'Use the original input journal and active live sender.')
    if not input_journal._token(request_id):
        raise FoundryError('invalid_request', 'An exact original input request ID is required.')
    output = external_path(destination)
    if output.exists() or output.is_symlink():
        raise FoundryError('output_exists', 'Choose a new evidence directory.')
    with lock(journal.root/'operation.lock'):
        journal._verify()
        original = journal._status(request_id)
        if original['state'] != 'awaiting_readback':
            raise FoundryError('input_unresolved', 'A completed original request awaiting readback is required.')
        same_identity(journal.session['target'],backend.observe())
        frame = backend.sample_frame()
        same_identity(journal.session['target'],backend.observe())
        output.mkdir(parents=True,exist_ok=False)
        intent = {'schema':1,'request_id':request_id,'session_id':journal.session['session_id'],
                  'intent_sha256':original['intent_sha256'],'target_sha256':digest(journal.session['target']),
                  'frame_sha256':frame.sha256,'frame_sequence':frame.sequence,
                  'frame_received_at_monotonic':frame.received_at,
                  'width':frame.width,'height':frame.height}
        new_json(output/'evidence-intent.json',intent)
        companion_build._publish_archive(output/'frame.png',frame.png)
        if sha256(output/'frame.png') != frame.sha256 or inspect_png(output/'frame.png') != (frame.width,frame.height):
            raise FoundryError('frame_changed', 'The stored frame did not match the original recorder sample.')
        journal._verify()
        current = journal._status(request_id)
        if current['state'] != 'awaiting_readback' or current['intent_sha256'] != original['intent_sha256']:
            raise FoundryError('frame_binding', 'Original input history changed during capture.')
        result = {'schema':1,'outcome':'frame_recorded','request_id':request_id,
                  'intent_sha256':original['intent_sha256'],'evidence_intent_sha256':digest(intent),
                  'frame_sha256':frame.sha256,'frame_sequence':frame.sequence,
                  'capture_time_verified':False,'action_caused_verified':False,
                  'semantic_state_verified':False,'acknowledged':False,'game_qualified':False}
        new_json(output/'evidence-complete.json',result)
        return result
