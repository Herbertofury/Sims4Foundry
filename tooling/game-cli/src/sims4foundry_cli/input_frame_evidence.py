"""Record passive frame evidence correlated within the trusted sender host.

This is observational evidence only. A received frame timestamp is not a
capture timestamp, action causality, authenticated semantic state or consent
to acknowledge a journal request. It sends no input and accesses no profile.
Binding does not authenticate hostile code in the host. Output path checks and
separate file reads do not exclude concurrent replacement or establish a receipt.
"""
from . import companion_build, input_journal, registered_input
from .errors import FoundryError
from .io import digest, external_path, lock, new_json, plain_path, read_json, sha256
from .live_input import LiveInputBackend
from .png import inspect_png
from .windows import same_identity


def inspect(journal, request_id, destination):
    """Inspect existing evidence without dispatch, acknowledgement, or writes.

    This point-in-time path-based inspection is not a retained-file snapshot;
    concurrent replacement and hostile writers remain outside its guarantees.
    """
    import hashlib
    import io
    import math
    from .wgc_recorder import MAX_FRAME_BYTES, _read_frame

    if type(journal) is not input_journal.InputJournal or not input_journal._token(request_id):
        raise FoundryError('frame_binding', 'Use the original journal and exact request ID.')
    output = external_path(destination)
    journal._verify()
    original = journal._status(request_id)
    if original['state'] != 'awaiting_readback':
        raise FoundryError('input_unresolved', 'The original request must await readback.')
    paths = [plain_path(output/name) for name in ('evidence-intent.json','frame.png','evidence-complete.json')]
    present = [path.is_file() for path in paths]
    if not all(present):
        return {'state':'incomplete_or_unavailable', 'files_observed':present,
                'acknowledged':False, 'atomic_snapshot':False}
    intent = read_json(paths[0])
    fields = {'schema','request_id','session_id','intent_sha256','target_sha256','frame_sha256',
              'frame_sequence','frame_received_at_monotonic','width','height'}
    if (type(intent) is not dict or set(intent) != fields or type(intent['schema']) is not int or intent['schema'] != 1
            or intent['request_id'] != request_id or intent['session_id'] != journal.session['session_id']
            or intent['intent_sha256'] != original['intent_sha256']
            or intent['target_sha256'] != digest(journal.session['target'])
            or type(intent['frame_sha256']) is not str or len(intent['frame_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in intent['frame_sha256'])
            or type(intent['frame_sequence']) is not int or intent['frame_sequence'] < 1
            or type(intent['frame_received_at_monotonic']) not in (int,float)
            or not math.isfinite(intent['frame_received_at_monotonic'])
            or any(type(intent[k]) is not int or intent[k] < 1 for k in ('width','height'))):
        raise FoundryError('frame_binding', 'Stored frame intent does not match the original request.')
    with plain_path(paths[1]).open('rb') as stream:
        raw = stream.read(MAX_FRAME_BYTES+1)
    if len(raw) > MAX_FRAME_BYTES or hashlib.sha256(raw).hexdigest() != intent['frame_sha256']:
        raise FoundryError('frame_changed', 'Stored frame bytes differ from the recorded intent.')
    stream = io.BytesIO(raw)
    parsed, shape = _read_frame(stream)
    if stream.read(1) or parsed != raw or shape != (intent['width'], intent['height']):
        raise FoundryError('frame_changed', 'Stored frame structure or dimensions differ from the intent.')
    receipt = read_json(paths[2])
    expected = {'schema':1,'outcome':'frame_recorded','request_id':request_id,
                'intent_sha256':original['intent_sha256'],'evidence_intent_sha256':digest(intent),
                'frame_sha256':intent['frame_sha256'],'frame_sequence':intent['frame_sequence'],
                'capture_time_verified':False,'action_caused_verified':False,
                'semantic_state_verified':False,'acknowledged':False,'game_qualified':False}
    if type(receipt) is not dict or digest(receipt) != digest(expected):
        raise FoundryError('frame_changed', 'Stored completion differs from the original frame intent.')
    return {'state':'matching_files_observed', 'files_observed':present,
            'frame_sha256':intent['frame_sha256'], 'evidence_intent_sha256':digest(intent),
            'acknowledged':False, 'atomic_snapshot':False,
            'capture_time_verified':False, 'action_caused_verified':False,
            'semantic_state_verified':False, 'game_qualified':False}


def compare(journal, request_id, destination, frame_sha256, evidence_intent_sha256,
            reference, reference_sha256, region, *, max_channel_difference=0, max_mismatched_pixels=0):
    """Compare pinned evidence pixels without inferring delivery or game state."""
    from . import visual_probe
    observed = inspect(journal, request_id, destination)
    if (observed['state'] != 'matching_files_observed'
            or observed['frame_sha256'] != frame_sha256
            or observed['evidence_intent_sha256'] != evidence_intent_sha256):
        raise FoundryError('frame_binding', 'The original evidence or caller-provided pins do not match.')
    result = visual_probe.compare(external_path(destination)/'frame.png',frame_sha256,
                                  reference,reference_sha256,region,
                                  max_channel_difference=max_channel_difference,
                                  max_mismatched_pixels=max_mismatched_pixels)
    after = inspect(journal, request_id, destination)
    if after != observed:
        raise FoundryError('frame_changed', 'Evidence changed during comparison.')
    result.update({'request_id':request_id, 'evidence_intent_sha256':evidence_intent_sha256,
                   'acknowledged':False, 'atomic_snapshot':False, 'capture_time_verified':False,
                   'action_caused_verified':False})
    return result


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
        binding = (registered_input.journal_binding(journal), journal.session['session_id'], request_id,
                   original['intent_sha256'], journal.session['target'])
        backend._require_evidence_binding(*binding)
        same_identity(journal.session['target'],backend.observe())
        frame = backend.sample_frame()
        backend._require_evidence_binding(*binding)
        if frame.epoch is not backend._frame_epoch:
            raise FoundryError('frame_binding', 'The sampled frame is from a different recorder epoch.')
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
        backend._require_evidence_binding(*binding)
        result = {'schema':1,'outcome':'frame_recorded','request_id':request_id,
                  'intent_sha256':original['intent_sha256'],'evidence_intent_sha256':digest(intent),
                  'frame_sha256':frame.sha256,'frame_sequence':frame.sequence,
                  'capture_time_verified':False,'action_caused_verified':False,
                  'semantic_state_verified':False,'acknowledged':False,'game_qualified':False}
        new_json(output/'evidence-complete.json',result)
        return result
