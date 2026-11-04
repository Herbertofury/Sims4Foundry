# Bounded companion transport, source only

This checkpoint connects a real synthetic TCP exchange to the owner-thread queue.
It is not a game bootstrap, installed mod, authenticated provider, or qualified
controller. No code here has touched a Sims process, profile, save or desktop.
The CLI's real semantic execution gate remains closed.

## Explicit source-only lifecycle and security boundary

`foundry_companion.transport.LoopbackTransport` is Python-3.7-compatible source.
Construction and imports do not open a listener. A host constructs it on the game
owner thread, explicitly calls `start`, schedules `owner_tick`, and calls
`shutdown` outside performance-critical ticks. No startup hook, scheduler,
`.ts4script`, installer, CLI activation flag or production configuration ships.

Both server and client default deny. The server requires a separately reviewed
host policy to authorize listener creation, authenticate each exact peer socket,
authorize and durably record each new submission, then freshly admit dispatch on
the owner thread. The client also requires an independent server-authorization
hook before sending its request. Optional [explicit-key peer hooks](PEER_AUTH.md)
implement mutual key-possession proofs without provisioning credentials or
establishing OS/game identity. No production host authorization policy ships.
A policy that merely returns True is not security; the original transport tests
do that only with fabricated data and isolated sockets.

**Loopback, PID/creation epoch, session IDs and hashes do not authenticate anyone.**
The integration still lacks securely provisioned and qualified channel/peer policy, durable journal
reconciliation, exact profile/process admission, reviewed provider qualification,
owner scheduler and zone lifecycle hooks. Native admission remains independently
default denied. Wire JSON cannot provide those authorities or enable the hooks.

The socket is always AF_INET at `127.0.0.1`; there is no hostname, wildcard, IPv6
or remote-bind option. Windows additionally requires `SO_EXCLUSIVEADDRUSE` before
bind and fails closed if the option is missing or fails. It never substitutes
`SO_REUSEADDR`. This prevents competing binds, not unauthorized connections; peer
authentication remains mandatory. See [Microsoft's socket-option description](https://learn.microsoft.com/en-us/windows/win32/winsock/using-so-reuseaddr-and-so-exclusiveaddruse).

## Versioning and framing

The existing `companion_protocol.py` schema-1 request and response contracts are
unchanged. Schema 1 has no TTL/save preconditions, so it cannot enter this queue
by itself. Schema 2 is a separate explicit envelope:

- A `submit` has exactly `schema: 2`, `kind: "submit"`, the complete unchanged
  schema-1 `request`, `ttl_seconds`, and `expected_context`.
- Mutation context has exactly `zone_id`, `household_id`, `save_slot`, and
  `save_guid`. Zone/household are canonical positive uint64 decimal strings;
  save slot is an exact nonnegative uint64 integer; save GUID is canonical
  nonnegative uint64 decimal text. No field may be null or omitted.
- Observation requires `expected_context: null` and still binds the exact
  request ID, session and PID plus process-creation FILETIME.
- TTL is finite, positive and at most 30 seconds. It starts once at queue
  admission, bounds dispatch start only, and is part of immutable intent.
- A `receipt_read` has exactly schema, kind, request ID, session ID, process
  epoch and `request_sha256`. Its digest covers the entire canonical schema-2
  submission, including TTL and every context field, not merely the v1 request.
  It is a consistency check, not a signature or permission token.

A separate [schema-3 Live read route](LIVE_READ_ROUTE.md) adds a flat, exact
`live.observe_queue` submission with explicit actor and full context. Its parser,
types and strict result schema are separate from v1/v2. The companion handler
recognizes the explicit version and retains canonical intent before callbacks;
legacy parsing and the existing host `exchange_once` sender remain unchanged.
The latter does not send v3. Source integration tests inject handler calls and
owner ticks without starting sockets. No installed adapter or production policy
is supplied, and authentication does not negotiate schema-3 capability.

Each TCP connection carries exactly one four-byte-big-endian-length-prefixed
UTF-8 JSON object of 1 through 65,536 bytes. The client must half-close its write
side. The server waits for this EOF before any admission: additional bytes,
including coalesced complete frames or a partial next prefix, reject the entire
connection. Fragmentation is supported. A response is one frame followed by EOF.
There is no streaming, pipelining, resynchronization or generic command relay.

Strict JSON rejects duplicate keys, unpaired surrogates, nonfinite numbers,
oversized integers, overdeep structures and excess nodes. Structural depth is
checked before recursive JSON parsing. The portable server and current client
use matching byte/node/depth limits and cross-client fixtures.

A connection gets one absolute monotonic deadline at accept, at most five
seconds. Authentication, read, admission and response share that deadline.
Fragmentation and slow-drip input cannot renew it. At most eight workers exist
(default four); excess sockets are closed without admission. Frames, worker
tracking, retained intent, queued work and results are bounded.

The deadline is rechecked after blocking I/O, final EOF and payload validation,
not only before those steps. A complete response arriving or parsing too late
does not become successful evidence. A late send may already have transmitted
bytes: it is not retried, and an admitted server request retains its history and
mutation uncertainty. The client journal likewise records an uncertain exchange
and only permits receipt reads of the exact original intent.

Python cannot preempt an injected host hook that hangs. Worker hooks must honor
the supplied deadline, never call game APIs, and perform only bounded security
and durable-intent work. The owner admission hook must do bounded local/native
checks without socket/journal I/O or waiting for network workers. Those are
provider obligations, not capabilities established by these synthetic tests.

## Owner-thread admission and preserved context

Both supported schemas retain canonical bytes before policy callbacks. Submit
and dispatch policy stages receive distinct reconstructed submission/binding
views, with complete intent checked afterward. The queued request is rebuilt
from the original bytes, never retained from a policy-owned view. Public binding
values are detached from the original primitive identity. These checks preserve
supplied-view consistency; trusted Python callbacks are not sandboxed.

The transport preserves PID plus creation epoch, session, exact operation and
arguments, TTL, zone, household, save slot and save GUID into the immutable queue
request. The native adapter additionally requires a matching captured session and
process-epoch declaration plus full mutation save context before game access,
and compares those save preconditions before command imports and again with its
fresh context checks. This declaration is not observed OS identity or live
authority. A trusted host
must independently verify current process lifetime and profile admission; caller
claims cannot establish them.

Network workers can only validate, authenticate, submit, and read receipts. They
never call the native executor. Each explicit owner tick selects at most one
entry. Owner preflight runs before publishing `started`, with no queue/network
lock held. A receipt read during preflight remains `accepted`. After preflight,
lock reacquisition is nonblocking, then TTL, shutdown and uncertainty are checked
again. Contention refuses that selected request permanently rather than requeueing
it. No network lock is taken by an owner tick or held across an owner callback.

Once `started` is published, every executor exception or invalid result remains
`unknown`. It cannot be relabeled as a known-not-dispatched refusal. Native work
already started cannot be preempted by TTL, shutdown or a socket deadline.

## Receipts, uncertainty and no recovery by replay

Schema-2 receipt states are `accepted`, `dispatched`, `adapter_returned`,
`refused`, `unknown`, and `not_found`. Every receipt says
`independent_evidence: false`. A terminal adapter result may contain its existing
`outcome: "verified"` assertion inside `adapter_result`, but the outer state is
never protocol `verified` and has no independent evidence reference. It does not
prove CAS visibility, persistence, game exit, provider qualification or correct
semantics. This bridge does not synthesize independent verification.

Identical submissions only return retained snapshots and never call new-admission
hooks again. Receipt-only reconnects cannot create, renew, expire, requeue or
dispatch an operation. A missing receipt is `not_found`, not proof of no dispatch;
it latches mutation admission off. Conflicting IDs/digests, unknown or unverified
mutation results, and failures delivering a known receipt also latch subsequent
mutation admission and pending mutation dispatch off. Read-only observations may
continue when the transport itself remains open; they cannot clear the latch.

History never evicts or resets. Pending capacity defaults to 48 and retained
history to 128. Denied requests also retain their immutable intent. Once history
fills, every new ID is refused permanently; unadmitted overflow IDs cannot all be
remembered in bounded memory. Original retained receipts remain readable.

A disconnect or timeout after admission does not undo an action or permit a
retry. `exchange_once` never retries or reconnects. After an uncertain exchange,
only query the exact original receipt; never replace its ID or session. Transport
error frames explicitly say `outcome_known: false`, including malformed replies
and failures serializing an adapter result that exceeds the wire byte bound.

Fatal accept failure or worker-construction failure closes ingress and latches
future dispatch off. Shutdown closes sockets without deleting records, does not
restart, and does not claim a started operation was cancelled. Socket-close
failure is sticky and keeps shutdown's result incomplete even after worker
tracking is cleaned. An exception from `Thread.start` also leaves a sticky
startup-uncertain flag: an absent ident does not prove no OS thread was issued.
`shutdown` reports incomplete startup/worker/cleanup cases
rather than declaring successful closure. No reset/rebind/restart method exists.

History is volatile. Process loss or exhaustion requires external durable
reconciliation and an explicitly established new host session, never a restart
used to evade uncertainty. The separate [offline client ledger](COMPANION_JOURNAL.md) now persists exact
submission/attempt/receipt history, but it is not wired to this transport and
supplies no shared history anchor or independent verifier. The live durable,
security and provider integration remains absent, so no deployment is offered.

## Synthetic validation

From `tooling/game-cli`, run the full current-Python suite:

```sh
PYTHONPATH=src python -B -m unittest discover -s tests -v
```

Queue/native/transport tests also run on a trusted isolated Python 3.7 interpreter:

```sh
PYTHONPATH=tests /path/to/python3.7 -B -S -s -m unittest test_companion_queue test_companion_native test_companion_transport -v
```

To run an actual current-Python client against a separate Python-3.7 socket-server
process, not just in-process codec fixtures:

```sh
FOUNDRY_COMPANION_PYTHON37=/path/to/python3.7 PYTHONPATH=src python -B -m unittest discover -s tests -p 'test_companion_transport.py' -v
```

All data and callbacks in these tests are fabricated. The Windows-only collision
test uses a second owned loopback socket with `SO_REUSEADDR`; it never inspects a
game or desktop. Linux cannot establish native Windows socket behavior. Python
3.7 source/runtime compatibility is not current-game or production-network
qualification; this end-of-life interpreter is only an isolated compatibility
fixture. No synthetic result is independent game evidence.
