# Source observation / queue / transport boundary audit

Audited source: `6d5931bb8cc06007f9557407d43eb92daa8bea21`, tree
`5dbaf6c7aeec0b310ea971662b74e73194e9ab00`.
Date: 2026-11-04 UTC.

## Result

There is a **future integration contract gap**, not a present live integration bug. Neither source observer is wired into queue/transport/CLI. Legitimate maximum source DTOs do not fit the existing queue's structural budget, even though their compact JSON is comfortably below the wire frame limit and the Live source's 32 KiB guard.

The hypothesis that 64 fully available Live rows might fit is false: the source DTO alone has 1,027 nodes, already above the queue's 1,024-node ceiling. No outcome wrapper can make that unchanged structured DTO fit.

This audit adds only a document and fabricated regression tests. It changes no production code, global limits, routes, admission, result semantics or authority. No game, desktop, process, profile or save was accessed, and no real transport service was started.

## Reproduction and scope

From `tooling/game-cli`:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_observation_transport_bounds.py -v
PYTHONPATH=src /path/to/isolated/python3.7 -m unittest discover -s tests -p test_observation_transport_bounds.py -v
```

The dedicated regression file uses fabricated native-style objects and the real
`BuildObserver.observe`, `LiveQueueObserver.observe`, typed `CompanionQueue.submit`
and `drain`, companion wire codec, current host codec and receipt parser. It also
writes and reopens temporary synthetic journals with a pure injected callback;
no network or service is involved. Tests compare all 16 Build field masks and
exact threshold variants, rather than merely estimating JSON sizes.

Companion checks run on Python 3.7. Host codec/journal checks are explicitly
skipped there because the host CLI requires Python 3.11+. The dedicated file
contains 16 tests: all pass on Python 3.12; 12 pass and four host-only checks skip
on actual Python 3.7. These are synthetic boundary tests, not a full-suite claim
or installed-game qualification.

All contexts use maximum legal numeric widths: uint64 maximum `18446744073709551615`, maximum PID, 32-character session/request IDs. Build object IDs are distinct descending uint64 strings. Live tuning/runtime IDs use maximum and maximum minus one. The synthetic wrapper is the existing native observe shape:

```
{"outcome":"unverified","observation":SOURCE_DTO,"game_qualified":false}
```

This is a deliberately fabricated eventual-adapter shape, not an existing Build/Live adapter. Using `unverified` does not confer gameplay verification.

## Exact counterexamples

Counts include dict keys as nodes and text, matching queue `_freeze_result`. Depth starts at zero for the result root. Bytes below are compact JSON payload bytes, excluding the four-byte frame prefix. Examples use valid bounded finite floats; byte counts are exact for these fixtures, not claims of the absolute longest float rendering.

| Source case | Source nodes | Source bytes | Wrapped nodes | Wrapped bytes | Queue result |
|---|---:|---:|---:|---:|---|
| Build, 32 objects, every field available | 1,755 | 19,211 | 1,761 | 19,273 | unknown / invalid_execution_result |
| Build, 32 objects, missing definition IDs, valid transforms/surfaces, unknown mode | 1,821 | 19,556 | 1,827 | 19,618 | unknown / invalid_execution_result |
| Build, 32 objects, all four fields unavailable (missing) | 1,371 | 13,995 | 1,377 | 14,057 | unknown / invalid_execution_result |
| Live, 64 rows, both ID fields available | 1,027 | 11,058 | 1,033 | 11,120 | unknown / invalid_execution_result |
| Live, 64 rows, both ID fields unavailable (missing) | 1,283 | 12,210 | 1,289 | 12,272 | unknown / invalid_execution_result |
| Live, 50 rows, both ID fields unavailable, other channel unavailable | 1,019 | 9,759 | 1,025 | 9,821 | unknown / invalid_execution_result |

The failure is structural, not text or depth, in these cases. Build's worst text-shape fixture, all field reads throwing sanitized `reader_exception` plus unknown mode and retained transition, has 10,199 wrapped text characters. The analogous 64-row Live case with the other channel throwing `reader_exception` has 8,748. Both remain below 16,384. Typical nonempty Build result depth is 7; Live is 6; both are within 8 before extra wrapping.

The actual queue publishes `started` before executing, then `_freeze_result` rejects oversized returns. Its deliberately conservative outcome is `unknown`, reason `invalid_execution_result`, with no retained adapter result. The rejected raw DTO is not attached to the final snapshot, retained queue entry, completion queue, receipt or journal. The source caller may still own its returned DTO; this is not a claim of secure memory erasure. The resulting error receipt is 390 bytes in these fixtures. It does not quietly clip rows, emit a partial observation, or falsely claim a successful observation.

## Thresholds and why availability matters

With the exact native-style wrapper above:

- Build base is 65 nodes, or 67 if mode is unavailable. Each fully available object costs 53. Making only `definition_id` unavailable adds two nodes; the vector/surface fields should remain available for the greatest structure. This is the worst field combination among all 16 tested masks. Thus 18 fully available objects fit at 1,019 nodes, but 18 worst-shape objects fail at 1,057. **The fixed-wrapper worst-shape fixture fits at 17 selected objects**, at at most 1,002 nodes, with 22 nodes of remaining structural budget.
- Live base is 73 nodes with both channels observed. Each fully available row costs 15 nodes; each unavailable ID field adds two. Thus 63 all-available rows fit at 1,018; 64 fail at 1,033. Fifty rows with two unavailable fields fit at 1,023, but replacing the other observed empty channel with an unavailable channel adds two nodes and fails at 1,025. **The fixed-wrapper worst-shape fixture fits at 49 combined rows**, at at most 1,006 nodes, with 18 nodes remaining.
- Wrapper selection matters. A minimal `{"outcome":"unverified","observation":DTO}` omits two nodes: **50 Live rows including an unavailable other channel fit at 1,023**. It is safe only with that exact wrapper and no further metadata. Do not document a universal “50-row safe” rule independently of its envelope.
- A 32-object Build result whose objects themselves are unavailable fits at 353 wrapped nodes. Therefore blanket “all 32-object results fail” would be wrong; the issue is the advertised maximum not guaranteeing transportability for every legitimate shape.

These numbers describe the exact wrapper and schemas audited here, not general API guarantees or new source limits. They are not proposals to silently truncate the current source APIs. Additional metadata, combined Build+Live results, arbitrary extensions or nested envelopes require re-budgeting.

## Wire and metadata boundaries

Both companion and host wire codecs allow 65,536 payload bytes, 8,192 nodes and container nesting 32. The full hypothetical receipt for 32 available Build objects is 19,646 bytes / 1,785 nodes; for the 32 worst-node Build example, 19,991 bytes / 1,851 nodes; for 64 available Live rows, 11,493 bytes / 1,057 nodes. These pass both codecs and host receipt parsing when tested directly, independently of the queue rejection. The fabricated receipt is not evidence that the queue produced it.

For these fixed unverified-result receipts, the receipt envelope adds **373 bytes, 24 nodes, 273 text characters and one depth level** over the wrapped result. The source-to-result wrapper adds 62 bytes and six nodes. The full envelope, not just the observation, must be tested.

Metadata edge tests:

- A 63-available-row Live result plus one bounded extension object containing `unknown: null` and a uint64 decimal string reaches exactly **1,024 nodes** and survives queue freeze/thaw and both codecs. One more boolean property reaches 1,026 and fails.
- One extra nested object around a Build observation raises result depth from 7 to 8 and passes; two raise it to 9 and fail, even with only one object. Live permits two such extra levels (6 -> 8); three fail.
- Queue text limits count Python characters, not serialized bytes. A zero-object Build result plus metadata containing 15,814 NUL characters reaches exactly **16,384 aggregate text characters** and is accepted by `_freeze_result`, yet its JSON-escaped full receipt is **96,113 bytes** and companion encoding refuses `oversized_frame`. A 16,385-character result is refused by the queue. This arbitrary-metadata example is separate from the fixed source DTOs, which contain bounded ordinary ASCII strings; it demonstrates why “queue accepted” is not a universal frame-fit proof.

## Exact values and retention

- Maximum uint64 and its neighbor remain exact through Python 3.7/current companion serialization and current host parsing. Live actor/tuning/runtime IDs and Live numeric context identities are decimal strings, not floats.
- Build object and definition IDs and zone/household/client/save GUID remain decimal strings. Build save slot, generation, process epoch and routing-surface primary ID remain exact JSON integers in the current Python codecs. The schema-2 outer receipt epoch is also numeric. This audit does **not** establish lossless generic JavaScript `Number` parsing; future non-Python consumers must preserve these integers explicitly rather than coerce IDs through floating point.
- `unavailable`, `value: null`, static reasons, unavailable channels and unknown mode survive queue freeze/thaw and host parsing unchanged. No zero/default value is substituted.
- Unknown result-extension keys are currently retained through queue/receipt/host parse when within bounds. Host receipt parsing validates the outer envelope and `outcome`, not the semantics/schema of the nested observation. A future adapter needs a versioned observation validator and a defined unknown-field policy; it must not silently shed unknown or unavailable fields to fit.
- The journal's 4,096-byte record bound is **not an observation-body storage budget**. `_snapshot` validates and hashes the complete receipt but deliberately retains only `state`, sanitized `reason`, `adapter_outcome`, and `receipt_sha256`. The tested summary is 157 compact bytes. A public synthetic journal write/reopen succeeded with three records, largest stored file 587 bytes. Neither the source body nor extension values were persisted. Its digest commits to the receipt, but cannot reconstruct unavailable values or exact source IDs. A future consumer must not promise observation recovery from this journal; preserving observation bodies would require a separately specified, authorized bounded artifact contract.

## Smallest safe future contract options

1. **Keep this source-only boundary explicit now.** No integration change is necessary merely to fix a currently nonexistent route. Record the verified mismatch as an integration gate.
2. **For a first structured adapter, specify one immutable wrapper plus exact budgets.** A conservative per-call contract can reject Build selections over 17 before readers and reject an entire Live observation over 49 combined occurrences with the native-style wrapper (50 only with the minimal no-extra-metadata wrapper). A prospective Live limit should use an overflow probe and reject the whole read, never drop or truncate rows. Preserve source provenance/unknown markers and every accepted ID. Existing standalone source maxima need not change.
3. **Check the complete final result and complete receipt before asserting read completion.** Reuse the actual queue-compatible node/text/depth predicate and both envelope/schema validation and bounded encoding, including metadata. A size failure must be explicit. A conservative pre-start admission rejection can be a known refusal; throwing after `started` currently becomes `unknown` and must not be relabeled as known-not-dispatched. If a future read adapter needs a returned “read refused” diagnostic after sampling, define that outcome explicitly and keep it distinct from a successful observation or evidence. Do not weaken queue unknown/no-replay behavior.
4. **If all 32/64 structured results must be supported, design and review a versioned lossless representation or separate bounded result artifact contract first.** Raising global limits, deleting reasons/rows, treating a digest as retained data, implicit pagination of a changing non-atomic read, or packing opaque JSON merely to evade a structural validator are not fixes. Any new representation needs its own bounded decode, complete field/ID roundtrip and authority tests.

The synthetic regression gate pins maximum-width contexts; all available/unavailable Build masks; Live 63/64 available, 49/50/51 unavailable and unavailable opposite channel; exact wrapper/metadata boundaries; depth 8/9; escaped text/frame expansion; decimal IDs above 2^53; unknown markers; and journal digest-versus-body semantics. Any future adapter must additionally prove explicit whole-read refusal before a success receipt when a budget is exceeded; this audit does not introduce such an adapter.

## Relevant source locations

- `companion-src/foundry_companion/queue.py:38-40,212-253,441-550`: result budgets, freeze traversal, post-start invalid-result outcome.
- `companion-src/foundry_companion/build_observation.py:18,235-285`: row shape, maximum selection and observation envelope.
- `companion-src/foundry_companion/live_observation.py:17-18,270-324`: combined row bound, unavailable structure and output-byte check.
- `companion-src/foundry_companion/native.py:308-311`: existing native observe wrapper used as the fabricated adapter comparison.
- `companion-src/foundry_companion/wire.py:19-22,105-173,263-284`: wire budgets and receipt thaw/envelope.
- `src/sims4foundry_cli/companion_protocol.py:49-53,134-206`: host codec budgets and exact bounded numeric parser.
- `src/sims4foundry_cli/companion_transport.py:101-138`: receipt outer validation, opaque nested adapter result.
- `src/sims4foundry_cli/companion_journal.py:31,64-67,149-160,442-489`: journal budget, receipt summary/digest and persistence flow.
