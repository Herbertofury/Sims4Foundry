# Request-bound Live queue read

This is an uninstalled, default-denied source integration for one explicit actor.
It connects a new typed request, the companion owner queue, the injected Live
reader, strict result/receipt validation and the client intent journal. It does
not provide a game adapter, bootstrap, native mode source, production permission
provider, key provisioning or a CLI network sender. No real game capability is
qualified by constructing a request or receiving an adapter result.

## Separate schema, unchanged legacy contracts

Schema 3 has one operation, `live.observe_queue`. Its flat submit envelope has
exactly `schema`, `kind`, `request_id`, `session_id`, `process_epoch`, `operation`,
`arguments`, `ttl_seconds` and `expected_context`.

- `arguments` contains only `actor_sim_id`, a canonical positive uint64 decimal
  string. There is no active-Sim fallback or mutable global actor selector.
- `expected_context` contains exact zone, household and client IDs; save slot and
  GUID; zone generation; and the exact mode string `live`.
- Process identity is the existing PID plus creation FILETIME, never PID alone.
- Request/session tokens retain the lowercase 32-hex format. IDs stay decimal
  strings where the existing reader contract uses strings. Save slot, generation
  and the outer process epoch use exact bounded Python integers.
- TTL is finite and positive, at most 30 seconds. Integer and float spellings
  are distinct intent. It limits the start of work, not trusted callback latency.

Every field, including actor and context, contributes to the canonical request
digest. Extra, missing or malformed fields refuse. The digest is an unkeyed
consistency/correlation value, not authentication or authorization.

The existing schema-1 request, schema-2 transport, operation allowlists and sender
remain unchanged. Old parsers reject the new submission; the new parser rejects
old operations. There is no automatic negotiation, downgrade or resend. The
simulation catalog and CLI do not gain a live send command through this change.

Schema-3 receipts retain the existing receipt fields and state meanings, while
binding the new submission's exact digest. A schema-2 success receipt cannot
satisfy a schema-3 request. An old peer's bounded error can only establish an
uncertain exchange error, never known non-dispatch or permission to fall back.

## Admission and immutable intent

The distinct Live request types carry actor and full context together. They are
not converted into a generic `observe` request. The source observer's separate
`require_live_read` admission receives the full request and detached scope data;
generic status, Build-read or mutation permission is insufficient.

The owner queue and transport retain primitive/canonical baselines and give
callbacks detached views. Frozen dataclasses alone are not an alias boundary.
Changing a caller-owned or retained callback view must not redirect a previously
admitted read under an old digest. Complete intent is checked again around
admission and before terminal result publication.

Public queue snapshots are detached copies, including every nested result
mapping and tuple. Their object identity is not stable; their content remains
equal. Altering a supplied snapshot or exploiting reflected mapping comparison
cannot alter retained results. This is a boundary for supplied views, not a
sandbox against arbitrary code running in the same Python interpreter.

Default listener, peer, submit, dispatch and reader gates remain denied. Owner
thread and process/session binding are required independently of the wire shape.
Exact host-declared mode, save identity, generation and actor context are checked
throughout sampling. The underlying readers are trusted synchronous callbacks;
their installed-game purity, mode accuracy and latency are not established here.
Sequential checks cannot detect every change-and-restore transition or prove an
atomic snapshot.

The old native executor rejects the distinct request before native imports. This
read path never selects a Sim, cancels an interaction, dispatches a command or
grants authority to any occurrence returned by the reader.

## Bounded whole observations

The request-bound reader accepts at most **49 combined queued/running rows**.
The separate standalone reader keeps its existing 64-row contract. The smaller
integration bound accounts for the complete structured result and queue budget,
including unavailable fields and receipt metadata.

A single overflow probe may detect row 50 but must not read its fields. The
entire observation is then abandoned. There is no silent truncation, field
removal, partial success, opaque JSON packing or pagination of a changing queue.
Accepted observations preserve row order, duplicates, unavailable values and
reasons, exact IDs and both channel states. Tuning IDs and transient occurrence
references are not durable selection/cancellation handles or proof of completion.

The result has exactly six fields:

- `schema_version`: 1.
- `outcome`: always `unverified`.
- `game_qualified`: always false.
- `read_status`: `observed` or `unavailable`.
- `observation`: the complete source DTO, or null for whole-read unavailability.
- `reason`: null for an observed report; otherwise only `row_limit_exceeded` or
  `result_budget_exceeded`.

A complete report may itself say that the actor or a channel is unavailable.
That remains an observed report of unavailability, not an empty queue or proof
that an actor is idle. Whole-read unavailability contains no partial rows.

The integrated 49-row worst-shape fixture occupies 1,012 queue nodes, below the
unchanged 1,024-node limit. Its complete receipt is about 10.4 KiB. These measured
fixtures are not an extension budget: actual complete result and receipt checks
still apply, and the schema permits no arbitrary metadata. Queue text/depth and
wire byte/node/depth limits remain unchanged.

The executor validates the complete source/result schema and receipt encoding.
The queue validates the frozen candidate against its retained original intent
before accepting a terminal result. Forged qualification, wrong actor/context,
extra fields or malformed data cannot become a successful observed result.
Rejected candidates are not retained as adapter data in an unknown receipt.

## Attempt outcomes and recovery

Known denial before `started` may be refused. After `started`, row overflow or a
legitimate result-budget failure returns the small unverified/read-unavailable
result. It is not relabeled as known-not-dispatched refusal. Unexpected execution
failures remain unknown; malformed terminal data remains unknown with
`invalid_execution_result`.

Read/mutation classification uses exact known types and operations, not a
caller-supplied flag or operation prefix. An uncertain read does not itself create
an unresolved-mutation latch, but cannot clear an existing one. Conflicts, lost
responses, missing history and shutdown retain their existing conservative
behavior. Duplicate IDs never renew TTL or rerun readers; changed actor/context
under the same ID is a conflict.

Each queue entry retains its admission-time classification. Later callback
changes to a legacy request cannot turn a started mutation into a read, break
completion accounting, or bypass the unresolved-mutation latch.
Legacy and Live entries also retain their complete original primitive intent.
Admission and execution receive different reconstructed views. Changing an
admission view refuses before start; changing an execution view records unknown
without retaining its result. Caller-side edits cannot redirect queued work.

The journal stores the complete typed intent before the one injected exchange
callback. Reconciliation constructs only a matching receipt read of that original
intent. There is no resubmission or replacement-ID/session escape from uncertainty.
Legacy journals remain readable without rewriting. Older clients encountering a
new intent fail closed rather than ignoring it or interpreting it as a legacy read.

Journal receipt storage is still summary/digest-only. It cannot reconstruct an
observation, retain row-level unavailable reasons, or distinguish the inner read
status from its summary alone. No observation-body artifact store is added.

## Authentication and qualification limits

Existing authentication is unchanged: it proves possession of a session key bound
to declared session/epoch, socket endpoints and fresh nonces. It does not MAC or
encrypt application payloads, supply operation permission, prove game identity,
or establish schema-3 capability. A separate [Live read exchange](LIVE_EXCHANGE.md)
now supplies a default-denied, one-shot host sender and authenticated synthetic
fixture composition. It does not supply an installed listener or production policy.

All integration evidence uses fabricated readers and explicitly injected policy.
Inert construction, handler calls, owner ticks, codec roundtrips and journal
reopening are not real-game validation. The same native mode, reader purity,
profile/process authority, owner scheduling, durable recovery and independent
outcome gates in [QUALIFICATION.md](QUALIFICATION.md) remain outstanding.

The complete local compatibility suite passes, but the final independent
owner/transport re-review remains incomplete after the last alias fixes.
This checkpoint is not approved for live use. See [VALIDATION.md](VALIDATION.md)
for the precise test and review scope.

Related contracts: [Live source reader](LIVE_NATIVE_OBSERVATION.md),
[observation transport budgets](OBSERVATION_TRANSPORT.md), and
[companion journal](COMPANION_JOURNAL.md).
