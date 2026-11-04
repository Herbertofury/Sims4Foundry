# Offline client intent and receipt ledger

`companion_journal.py` is current-client Python 3.11+ source, separate from the
synthetic engine and the embedded Python 3.7 modules. It supplies durable local
intent/attempt bookkeeping and a **default-denied injected callback harness**.
There is no production executor, production network integration, CLI execution flag, key
provisioner, game bootstrap, independent outcome verifier or live qualification.
Importing it does not create files, start a service, open sockets or touch a game.
All tests use fabricated values in temporary external directories/processes.

The CLI exposes only `foundry-game companion journal-status --directory PATH`
and its optional exact `--request-id ID` filter. It reads and validates existing
local history without creating, repairing, submitting, reconciling over a socket
or deleting anything. Exit zero confirms a successful history read; each entry's
unresolved/evidence fields remain authoritative about the stored claims. Missing,
corrupt or busy history returns a nonzero structured error. There are no create,
submit, reconcile, reset, network-address or live-authorization flags.

The separate [recovery suite](COMPANION_RECOVERY.md) composes the injected journal
callbacks with real authenticated fixture sockets. It is not production wiring
or a qualified host policy.

## Exact intent before possible transmission

A new ledger is an exclusively created external directory; its parent must exist.
Opening an existing ledger never initializes, repairs, resets or deletes history.
The stable `operation.lock` file must already exist and is never removed. The
existing OS-held `flock` / Windows byte-range lock covers the complete operation,
including its injected callbacks. Process death releases ownership, not evidence.

Each request ID binds the complete canonical schema-2 `Submission`: schema-1
request, session ID, PID plus creation FILETIME, request ID, operation/arguments,
original TTL, zone, household, save slot and save GUID. Its full transport digest
is retained. Canonical bytes are compared, so even integer `5` and floating `5.0`
TTLs are not silently treated as the same immutable envelope. An existing exact
ID returns its recorded status without admission or exchange; conflicting reuse
refuses. IDs are unique across the entire ledger, not just within a session.

The separate [schema-3 Live read](LIVE_READ_ROUTE.md) is an exact-type additive
intent: actor, full zone/household/client/save/generation context and exact Live
mode join session, process lifetime and TTL in its canonical digest. It is never
projected into the old generic observation request. Exact typed dispatch chooses
the corresponding receipt parser/query and read-versus-mutation classification;
it does not widen the legacy sender or accept arbitrary operations. Old journal
record shapes and limits remain unchanged. Older readers encountering a new
intent fail closed; no automatic downgrade or rewrite is provided.

`record_intent(submission)` only publishes intent. It is deliberately not a
prepare-now/send-later API: once an intent exists, `submit_once` will only return
its status. A crash or interruption cannot turn old intent into a fresh attempt.

For a new `submit_once` call:

1. Validate and detach the typed submission, acquire the lock, and validate all
   bounded existing history. Any unresolved mutation blocks a later mutation,
   even with a different request ID, session or process epoch. Read-only
   observation intents may continue but cannot resolve the earlier mutation.
2. Publish the exact immutable intent record and its new head before calling
   independent trusted host admission. The absence of either callback denies a
   new submission without creating intent.
3. An admission callback must return exact `True`, must not transmit or dispatch,
   and must not change the immutable binding. Exact `False` on this first new
   submission can publish a known local `not_dispatched` refusal. Exceptions and
   other return values leave intent unresolved. No failure implies a retry.
4. Publish an immutable attempt with a fresh local attempt ID, strictly increasing
   per-request sequence and `kind: submit` before calling exchange. At most one
   submit attempt is ever allowed. The attempt proves only that transmission
   became possible; it does not prove bytes were sent or an operation dispatched.
5. Correlate a bounded receipt to the persisted original submission, validate
   chronology, and publish its immutable receipt record bound to that exact
   attempt ID and sequence. There is at most one answer per attempt. Callback
   failures record a static uncertainty code when storage still works. A crash or
   publication failure leaves either an unanswered attempt or corrupt history.

The injected call signatures are `authorize(message, attempt)` and
`exchange(message, attempt)`. They exist for isolated tests and a future reviewed
host, not as a ready-to-use security policy. `authorize` must independently
establish permissions, exact process/profile ownership, shared prior history and
provider admission. `exchange` must authenticate its own peer, enforce deadlines
and honor the exact message. No such production implementation is supplied.
Arbitrary trusted Python can perform arbitrary actions or hang; the ledger is
not a sandbox and cannot preempt a callback. Normal callback exceptions use
static unchained diagnostics. Callback mutation of message/ticket values is
refused; later receipt validation uses freshly reloaded persisted intent.

Authorization and exchange receive separately reconstructed message and attempt
views. The attempt's six fields use exact primitive types and bounded values;
class-defined serializers and captured primitives protect the comparison from
instance-shadowed serializer methods. A retained authorization view therefore
cannot redirect the later supplied exchange view. Post-exchange binding drift
remains uncertain, with the original immutable attempt retained. These checks
protect supplied views and ledger consistency, not arbitrary Python execution.
Replaying a schema-3 receipt summary also rejects an impossible `verified`
adapter outcome, even when its local unkeyed record hashes have been recomputed.

## Receipt-only reconciliation and evidence boundaries

`reconcile(request_id)` loads the exact original submission and can hand only a
`ReceiptRead` to an independently admitted callback. Its attempt has
`kind: receipt_read`, a new local attempt ID and the next sequence. No API
resubmits, renews TTL, replaces intent, changes session, retries exchange, rewrites
terminal receipts or creates missing history. Known terminal non-dispatch returns
local recorded status without another exchange. Missing local intent refuses;
transport `not_found/history_missing` stays unresolved.

The first receipt is subject to the same exact identity/digest checks as later
receipts. Valid receipt-only progression includes `accepted` to `dispatched` to
`adapter_returned`, or `accepted` to `refused` while chronology still proves no
observed dispatch or uncertainty. A first refused submit response can establish
known non-dispatch. A first receipt read cannot invent an earlier submission.
After an unanswered/failed attempt or any `unknown`/`not_found` claim, a later
refusal cannot clear uncertainty. `dispatched` cannot regress to `accepted` or
`refused`. Terminal adapter snapshots must remain exactly identical, including
the full original receipt digest; they cannot change after restart.

All `adapter_returned` outcomes, including `adapter_result.outcome: verified`,
remain **unresolved as game outcomes**. There is no independent verifier and no
method to supply caller JSON as independent evidence. Status always reports
`independent_evidence: false` and `live_qualified: false`; it never emits the
synthetic engine's `verified_simulation`. Thus an adapter-returned mutation still
blocks subsequent mutations. Completing that barrier requires future separately
qualified, read-only outcome evidence integration, not a reset method.

Status separates `transmission` (`not_attempted` or `possible`), latest summarized
state, the last adapter receipt claim, `unresolved`, and `uncertainty_latched`.
An unanswered attempt is reported as `unknown`. Later receipt reads may obtain
more information without erasing earlier uncertainty. A receipt claim is never
proof of visibility, correct save contents, persistence or process exit.

Only an allowlisted public reason code or a fixed generic reason is stored.
Arbitrary adapter details, exception messages, keys, auth nonces and proofs are
not stored. A digest of the complete original receipt preserves consistency and
terminal comparison without retaining its arbitrary result body. The ledger
accepts no credential/account fields and does not integrate with peer-key storage.
For schema-3 Live reads, this also means the journal summary cannot reconstruct
the observation or distinguish its inner `observed`/`unavailable` read status.
The validated direct receipt carries that information; no body store is added.
Use fabricated data for the source-only harness, not real account information.

## Bounded storage and interruption behavior

The directory contains `journal.json`, `head.json`, `operation.lock` and `records`.
Records are immutable, sequential, hash-chained JSON files. The small head is an
atomic replaceable consistency anchor, not an authorization token. Each event
first uses the existing fsynced, no-overwrite `new_json` primitive, then updates
the head with `replace_json`. **Both must finish before handing off a submission.**

Every open scans at most 1,024 record names and reads files of at most 4,096 bytes.
There are at most 128 request IDs and 32 attempts per request, with no eviction.
Output reserves space for its newline; parsing also applies the strict protocol
JSON depth/node/number/duplicate-key bounds. Unknown directory entries, pending
publication files, missing/gapped records, duplicate IDs, incorrect attempt
sequences, impossible chronology, wrong hashes, hard-linked regular files,
nonregular files, symlinks and observed Windows reparse points fail closed.
The root directory has a separate four-entry scan bound. Capacity exhaustion
refuses further writes; it is not an invitation to delete history.

Publication failures are conservative:

- Intent publication failure cannot reach admission/exchange. If an intent was
  fully published, a later identical call only returns its recorded status.
- Failure before attempt publication cannot call exchange. Intact intent remains
  unresolved rather than being reused as a new submission.
- A completed attempt head followed by interruption is possible transmission,
  even if the callback never actually started.
- Receipt publication interrupted before the head is complete cannot resolve an
  operation. An orphan record/head mismatch blocks the entire ledger.
- A complete receipt and head survive a later exception as their exact recorded
  result. Existing immutable records are never overwritten to hide an error.

POSIX file and directory flushes use the existing primitives. **Windows
power-loss ordering and filesystem crash durability remain unqualified.** Process
crash tests do not establish power-loss behavior. Unsupported hard-link/atomic
publication filesystems fail without an overwrite or weaker fallback.

## Limits that a live host must not bypass

This is cooperative local workspace protection, not hostile-filesystem security.
The path checks cannot prevent a privileged local actor from swapping components
between checks. Hashes are consistency checks, **not authentication**. A coherent
rollback of the whole ledger, deleting/recreating the directory, or copying an
intact ledger into a separate workspace cannot be detected by local files alone.
No global exactly-once guarantee is claimed. Do not use a new directory, ID,
session, lock, game restart or missing transport history as recovery.

Before live use, an independently trusted host must bind this ledger to a durable
shared history anchor across all participating workspaces/process lifetimes and
perform read-only reconciliation. The existing cooperative process lease alone
does not provide this history and cannot exclude the user, Apex or another tool.
Secure peer/channel policy, actual game/profile admission, provider qualification,
Windows durability and independent outcome verification remain separate gates.
A caller-supplied path, JSON file, boolean or callback returning `True` does not
establish any of them. There is no live execution path in this checkpoint.

## Reproducible offline validation

From `tooling/game-cli`:

```sh
PYTHONPATH=src python -B -m unittest discover -s tests -p 'test_companion_journal.py' -v
PYTHONPATH=src python -B -m unittest discover -s tests -v
```

The ledger suite exercises real temporary files, an owned child process exiting
inside a callback, OS-lock contention/release, a child exiting during partial
publication, injected failures before/after each head update, exact submission
bindings, malformed/missing data, secret redaction, bounded history, receipt-only
progression, terminal immutability and impossible hash-consistent chronology.
It contains no socket exchange, game process/profile, real credential or desktop
interaction. Native Windows CI remains necessary for that platform's ordinary
filesystem/locking behavior and still cannot qualify power-loss or game behavior.
