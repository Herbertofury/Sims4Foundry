# Guarded input journal

`InputJournal` connects the existing guarded packet runner to a durable
host-side history. It supplies no production input backend, recorder, game
process admission, live UI observer, or CLI control command. A host must
provide those separately; the existing public native input factory still
refuses live use. This module has no Apex dependency.

## One session and original target

Create a new external journal with an exact session ID and the existing
validated Windows process/window identity. The journal detaches and records
the PID, process creation time, executable/hash, HWND, owner thread/class,
geometry, DPI and integrity. A restarted host can reopen the same journal;
it must independently establish that the same process and window still exist.
A serialized identity or an unchanged HWND does not establish that fact.

`execute_once(request_id, packets, backend, expected_observation=...)`
validates and detaches one bounded typed packet sequence and a nonempty expected
UI observation, then persists the complete intent and an attempt marker before
calling `guarded_input.run_packets` once. The existing runner's intent, post,
release and result records are stored in a bounded chained event sequence. The
original input request ID cannot be reused, including under a different packet
plan. A process crash or lost result after the attempt is unresolved; the API
offers status, not an automatic resend.

Only `rejected` or `acknowledged_by_host` earlier runs permit another input
sequence in this journal. Queue acceptance by itself yields
`awaiting_readback`; a failed post, lost acknowledgement, incomplete history,
or lost result yields `unresolved` and blocks dependent controls. In
particular, `acknowledge` cannot clear an unresolved run or a potentially
unreleased input. A host must reconcile that condition through an independently
reviewed recovery path; this source does not supply one.

## Host readback

For a completed queue-only sequence, `acknowledge(request_id, observer)`
calls an explicitly supplied trusted observer with detached copies of the
original request/session binding, target and expected observation. It accepts
only an exactly matching returned observation and a lowercase SHA-256 evidence
reference. The acknowledgement persists hashes of the expected observation
and evidence, bound to the original request and intent. Changed request,
session, target or expected observation is refused.

The observer must actually inspect the intended UI state and verify that its
evidence is current and belongs to this exact game/window/action. This module
does not open the evidence, interpret an image, authenticate a callback, prove
process/window continuity, or turn a host assertion into independent proof.
`acknowledged_by_host` is a host declaration, not a live-game qualification.
The host must choose meaningful expected fields, not merely assert that a
frame exists.

## Persistence limits

Records use the existing cooperative OS lock and no-overwrite `new_json`
primitive. The session blocks concurrent cooperating input callers, but
cannot lock unrelated applications or the physical keyboard. Event hashes
detect ordinary record inconsistency; they are unkeyed and cannot detect a
coherent rollback or deletion of the whole workspace. A trusted external
history anchor is needed for that threat. Windows power-loss ordering has not
been qualified. Failed writes can leave an incomplete run, which remains
blocked rather than being silently removed or replayed.

`tests/test_input_journal.py` uses the synthetic guarded-input backend and
temporary files. It covers queue-only results, observed readback binding,
foreign/mutated acknowledgements, lost post/attempt/result replies,
interruption/reopen, corruption, no-replay and blocking dependent controls.
It does not send Windows messages, start a game, test a real WGC recording or
prove the intended CAS/Live/Build interaction.
