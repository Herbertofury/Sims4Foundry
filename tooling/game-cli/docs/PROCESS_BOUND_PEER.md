# Source-only process-bound peer callback

`sims4foundry_cli.process_bound_peer.ProcessBoundPeer(lease, session_key)`
composes three existing, independent checks for the current-Python host:

1. An already held `process_lease(expected_identity)` context retaining the exact
   process handle, user/session scope and acquiring thread.
2. The server-side owner of the **actual connected socket**, observed through
   `loopback_owner.observe_connection_owner(expected_pid, server_port, client_port)`.
3. The existing `companion_auth.SessionKeyPeer` mutual HMAC exchange with an
   explicitly supplied session key.

This is a neutral Foundry host integration seam, with no Apex dependency. It is
not imported by the CLI and adds no command, live-send switch, listener, automatic
startup, profile access, game provider or qualification. Imports perform no I/O.
Construction does perform a fresh observation of the caller's already held lease.

The return value `True` means only that these checks completed for this callback
attempt. **It is not profile, journal, operation, mutation or game permission.**
HMAC itself is not PID proof. A caller-supplied PID or session ID is not OS proof.

## Public API and ownership

Construct the object inside an existing lease context, on its acquiring thread.
The constructor accepts only the exact concrete lease returned by
`process_lease`, not a serialized observation, duck-typed object, subclass or
previously exited context. It checks the held context, validates the supplied key,
and captures the exact PID and creation FILETIME from a fresh lease observation.
The existing lease verifies its retained process handle before and after its
independent process/window observation. Its other identity restrictions remain.

The callback has the existing sender signature:

```python
peer(connection, absolute_monotonic_deadline, original_submission) -> bool
```

A separately authorized host can pass a new instance as `authorize_peer` to:

- `companion_transport.exchange_once` for a schema-2 `Submission` or `ReceiptRead`.
- `companion_live_transport.exchange_once` for a schema-3 `LiveQueueSubmission`
  or `LiveQueueReceiptRead`.

Both senders give the callback the **original submission**, even for a receipt
read. Receipt objects themselves are not callback inputs. Exact typed request,
epoch, arguments and context values are revalidated before any owner query or
authentication bytes. Schema-2 mutations remain part of that existing data
contract; merely authenticating them grants no permission to send or dispatch
them. This module never invokes either sender itself.

The lease remains owned by the caller. The callback retains a reference but never
enters, exits, replaces or releases it. Keep the context active through the entire
exchange and any separately required admission checks, not just authentication.
The sender owns the socket and closes it on success or exception. A direct callback
caller must likewise close the socket on any failure and never continue sending.

Each callback object permits **one attempt**. A failure, malformed input, wrong
thread, timeout, cancellation or uncertain exchange consumes it too. There is no
reset, cache, retry, reconnect, backend switch or fallback policy. A separately
authorized receipt read uses a new callback, the original submission and an
appropriate still-held lease. A new callback is never authority to replay a
submission or replace its request/session ID.

There is no public source or policy injection: no alternate lease, owner observer,
authentication hook, clock, socket factory, key loader, namespace or permission
flag. Tests patch private bindings only with fabricated behavior.

## Call order before application send

The sender connects its one socket and passes its original absolute deadline.
The callback then performs the following sequence:

1. Consume the callback and validate the finite deadline and held same-thread
   lease context.
2. Capture a detached, exact typed schema-2/3 submission through strict plain
   values and canonical encoding. Validate all fields again. Supplied instance
   serializers are not called. The HMAC hook receives only the detached object.
3. Obtain a fresh lease observation. Require its PID **and creation FILETIME** to
   equal both the constructor's captured lifetime and the submission's epoch.
4. Require the actual concrete socket, not a wrapper or subclass. Require
   `AF_INET`, `SOCK_STREAM`, an open descriptor and OS `SO_TYPE == SOCK_STREAM`.
   Obtain `getsockname()` and `getpeername()` from that same object. Both must be
   two-element tuples with the exact string `127.0.0.1` and integer ports from
   1 through 65,535. The ports must be distinct.
5. Query the connected server-side tuple with server port from `getpeername()`
   and client port from `getsockname()`. Require the exact expected owner result.
   A listener-only result, inverse/client row, missing/ambiguous row, wrong PID,
   unavailable query or denial cannot satisfy this step.
6. Reobserve the held lifetime and recheck the same socket descriptor and both
   endpoint tuples. Recheck the original and detached submission against their
   captured canonical bytes. **No authentication bytes have been sent yet.**
7. Call the fixed `SessionKeyPeer` with this same socket, this same absolute
   deadline and this detached submission. Require exact `True` after the complete
   HMAC handshake, including server confirmation.
8. Again reobserve the held lifetime, verify the socket descriptor and both
   endpoints, and verify unchanged original/detached intent. Check the deadline
   once more before returning `True` to the sender.

Only then can the existing sender send its application frame. A failed lifetime,
socket or intent recheck after a successful HMAC still prevents that send.
Owner-query, lease, authentication and socket exceptions propagate rather than
becoming success, a fallback or an inferred known outcome. In particular a denied
query is not retried through another source. The owner's existing bounded
insufficient-buffer handling remains internal to that one observation.

## Deadline and key limits

The callback accepts the sender's absolute monotonic deadline, not a duration.
The remaining budget must be positive and at most five seconds, with finite exact
integer/float input and no boolean coercion. Deadline checks surround snapshot,
lease, owner, socket and authentication work. The HMAC transport receives the
unchanged deadline for its reads/writes. No phase renews the budget.

Synchronous Python/native work cannot be preempted by this wrapper. A slow lease
or table query can return after the deadline; the following check refuses before
the next phase or application send. There is no worker-thread escape from the
lease's thread requirement. Existing socket timeouts bound HMAC network waits.

The key must be explicitly supplied immutable `bytes`, 32 through 64 bytes. No
key is generated, loaded, saved, discovered, printed or sent as a key. Entropy,
secure provisioning, intended participants, session lifecycle and any permission
to establish access are separate prerequisites. Authentication generates its
existing fresh nonces, not session keys. The object retains the key in memory;
Python cannot guarantee erasure. Its representation is redacted, and serialization
and ordinary copying are refused. See [peer authentication](PEER_AUTH.md).

## Boundaries that remain

- The lease coordinates participating Foundry controllers in the same OS
  user/session, not the user, Apex, arbitrary tools or privileged local actors.
  Every admission still requires independent durable-journal reconciliation
  across workspaces. `abandoned == False` is not a clean-journal signal.
  `reconciliation_required` stays true and `mutation_permitted` stays false.
- Connection ownership is a transient OS observation, not proof of loaded code,
  game readiness or an untransferable socket capability. Rechecks narrow races;
  they cannot prevent a subsequent socket transfer, process exit or compromise.
  There is no post-auth ownership-table claim beyond the observed tuple and the
  subsequent held-lifetime/socket checks. See [connection owner](LOOPBACK_OWNER.md).
- The submitted application session ID is trusted caller intent bound into HMAC,
  not an independently discovered game session. OS user/session scope comes from
  the held lease and is a different concept.
- Application requests and receipts remain unMACed and unencrypted. This seam
  does not create an integrity-protected application channel or independently
  validate adapter claims, mutation outcomes, save persistence or game evidence.
- Detachment and unchanged-intent checks protect ordinary aliases during the
  callback. They do not sandbox hostile code in the same Python interpreter.
  In particular schema 2 already serializes its frame before invoking the hook;
  this addition neither rewrites that sender nor attests to a frame changed by
  hostile caller serialization/concurrent manipulation outside the callback.
  The host must supply stable, genuine typed inputs and trusted in-process code.
- No uncertainty is resolved here. After an uncertain exchange retain the exact
  original intent and follow the existing receipt-only reconciliation rules.
  A successful callback or authenticated receipt is not proof of dispatch or
  independent verification. See [companion journal](COMPANION_JOURNAL.md).

Profile scope, operation permission, durable intent, owner-thread admission,
readiness and game/provider qualification remain required from a separate host.
This file must not be presented as a live game authorization policy.

## Fabricated verification

From `tooling/game-cli`:

```sh
PYTHONPATH=src python -B -m unittest discover -s tests -p 'test_process_bound_peer.py' -v
PYTHONPATH=src python -B -m unittest discover -s tests -v
```

The new tests create no real sockets and make no native observations. They use
genuine lease objects with fabricated native/inspection bindings and a private
fabricated concrete socket binding. Coverage includes call ordering, both schemas
and receipt paths, strict detached values, lifecycle/thread reuse, exact oriented
ports, wrong PID/FILETIME, denied/ambiguous-style owner failures, endpoint/descriptor
changes, process exit, intent changes, bounded deadlines, exception propagation,
single-use behavior, key redaction and import inactivity. Both existing senders
also run through the real HMAC codec against an in-memory scripted peer, including
wrong keys and post-auth lifetime failure with no application send or reconnect.

This is not Windows/game qualification. No new native fixture, desktop check,
game process, profile read or live sender activation is introduced. Any actual
Windows connection-owner validation remains limited to the separately gated,
already-owned hosted-CI fixture described in `LOOPBACK_OWNER.md`.
