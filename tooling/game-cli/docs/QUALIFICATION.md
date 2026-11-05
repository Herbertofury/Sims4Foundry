# Live qualification gates

The user's goal is full, reusable CAS, Live and Build interaction plus autonomous
entry/exit and mod regression/update workflows. This checkpoint is not that outcome.
Do not close the task merely because simulator or generic Windows CI passes.

## Current status

| Area | Implemented | Exercised here | Live-qualified |
|---|---|---|---|
| Typed semantic contracts | 45 initial operations | Python tests | No |
| Plan/execute/reconcile | Synthetic model | Python tests and crash/lock checks | No |
| Companion client intent/receipt ledger | Bounded immutable history and default-denied injected callback seam | Fabricated temp-file/process-crash/publication-failure tests | No |
| Mod inventory/diff/staging | Real local file operations | Synthetic artifacts | No gameplay/compatibility claim |
| Script bytecode-header report | Bounded selected-archive prefix inspection and optional target-byte comparison | Fabricated headers and source-backed layout checks | No body/importability/game compatibility claim |
| DBPF resource ownership audit | Bounded header/index parser and exact TGI overlap report | Fabricated packages on Windows/Linux CI | No content/load-order/compatibility claim |
| Apex source migration inventory | AST-only read | Historical source snapshot | Not an input adapter |
| Operation source map | 45 source-backed lineage/gap records | Detached-data and coverage tests | No dispatch capability |
| Passive process discovery | Bounded exact candidate reporting | Fake snapshots and native Windows CI enumeration | No launch admission |
| Windows process/window inspection | Query-only implementation | Contract tests and native Windows CI fixture | Pending current game test |
| WGC capture | Pinned helper and bounded streaming implementation | Mock helper, real PNG validation and an opt-in pinned-FFmpeg capture of an owned Windows fixture | Pending current game test |
| Exact process-exit observation | Retained query/synchronize-only handle, path/hash/lifetime binding; no quit request | Fake-handle tests; owned-child Windows CI pending | No normal-exit or save proof |
| Neutral companion archive build | Deterministic source/Python 3.7 bytecode and separate manifest; no Apex dependency | Fake compiler and actual isolated 3.7 repeat builds | No game loading/activation proof |
| Neutral owner-runtime integration | Explicit original-first tick wrapper, narrow native/Live routing, one-shot lifecycle and conflict-aware cleanup | Fabricated core/services and owned loopback, including actual Python 3.7 | No production admission or bootstrap |
| No-Apex click/key/scroll/drag | Typed packet plans, guarded journal and experimental retained-process native sender; no supported live CLI route | Synthetic failure tests and actual click messages delivered to an owned Windows fixture; an opt-in fixture also observed expected pixels through WGC | No |
| Foundry semantic game bridge | Uninstalled typed queue, narrow native bindings, default-denied socket transport and explicit-key peer hooks; no bootstrap/admitted host | Fabricated services and real isolated sockets on actual Python 3.7 | No |
| Build object/mode observation | Uninstalled, separately admitted explicit-ID reader and transition-observation seam | Injected fabricated readers on current Python and actual Python 3.7 | No atomic/native/world-state proof |
| Live queue observation contract | Pure bounded fixture validator, no native reader or action methods | Fabricated availability/identity/order/bounds tests | No |
| Live queue reader seam | Uninstalled, separately admitted selected-actor observer with bounded queued/running iteration | Injected fabricated readers only | No native mode/identity/atomicity/completion proof |
| No-Apex launch/entry/save/load/exit | Launch and profile-lifecycle source plus gated normal-exit orchestration; no complete qualified flow | Synthetic EA, profile and exit fixtures | No |

## Required controlled validation

Before any game-facing step, obtain a test window that does not overlap another
Apex/CAS session or the user's activity. Inspect existing state rather than
launching, attaching, changing a profile, or closing a preexisting process by guess.
Keep original saves/profiles protected. Use only an explicitly authorized disposable
profile with a matching, separately reviewed journal and a restoration procedure.

The first reserved Foundry window should be passive: verify the installed CLI source
checkpoint and tool hashes, discover/inspect the explicitly selected game lifetime,
and test one pinned window-only capture. Do not launch, attach a mod, send input or
close an existing game merely to obtain this evidence. A discovery report is not
an identity file for capture: select its one qualified identity with `inspect
--output`, then give that exact output to `capture --identity`. Failure, ambiguous
windows, an active external controller or any ownership change ends this pass.
Only after this evidence is reviewed should a separate single-action test begin.

1. **Source parity.** Verify current helper source and dependency pins. Record which
   paths are historical, which are new, and which tests actually ran.
2. **Passive identity.** Observe the exact game lifetime and owned window. Exercise
   inaccessible process, PID/HWND reuse, multiple windows, minimized target, changed
   DPI/size, changed executable and different integrity. All must fail closed.
3. **Capture.** Compare actual pixels with the owned game surface. Confirm geometry,
   freshness, nonblank frames, recorder progress and pinned recorder lifetime.
4. **Single-step input.** On a known screen, submit one narrowly scoped event with
   fresh before/after recording. Independently verify both intended transition and
   no global cursor/foreground changes. Never infer success from queue acceptance.
5. **Native semantics.** Qualify each CAS edit, interaction queue operation, Build
   transaction and camera/navigation path separately using native readback or
   unambiguous pixels. Unknown dialogs or user interference stop the sequence.
6. **Lifecycle.** Prove one normal launch, menu readiness, selected household entry,
   controlled save to the exact slot, reload of the saved changes, and normal
   explicitly no-save exit. Exit requires the exact process lifetime to terminate,
   not merely the window disappearing. Never force-kill as a silent fallback.
7. **Interruption/recovery.** Simulate transport loss after acceptance, native result
   delay, request deduplication, storage failure, game restart, process replacement,
   partial multi-step actions, unknown dialogs and concurrent operators. Recover by
   observation without repeating an uncertain input.
8. **Evidence-bound promotion.** Promote only the exact operation/provider/game
   build/UI configuration validated. Caller-supplied JSON or a command-line flag
   must not unlock live qualification. Repeat affected gates after game/mod updates.

## Still required engineering

- Complete the independently admitted mod-neutral host around the transport/owner queue/native bindings. The new owner runtime implements an explicit original-first tick lifecycle, but does not automatically activate or provide admission. Process epoch and zone/household/save preconditions are carried across the boundary; peer key-possession hooks exist, but secure key provisioning, qualified channel/peer policy, live admission, qualified exact UI-mode observation and live durable reconciliation are not implemented. The offline client ledger stores exact intent and receipt history and has one-shot transport integration, but no shared history anchor or independent outcome verifier; see [COMPANION_JOURNAL.md](COMPANION_JOURNAL.md).
- A separately qualified external menu/input provider, including scroll/drag/keys. The experimental [retained native input integration](LIVE_INPUT_EXPERIMENT.md) has no production admission, independent window epoch, atomic dispatch or qualified game-state observer. The opt-in owned-window WGC fixture does not establish game behavior or capture time.
- Integrate the cooperative process-lifetime lease with a shared durable journal and detect nonparticipating user/Apex/other-tool interference. A lease alone does not authorize input.
- Windows crash-durable pre-dispatch evidence ordering.
- Semantic state readers and independently checkable predicates for all domains.
  The [Build observation seam](BUILD_OBSERVATION.md) is a bounded source-only
  starting point, not a qualified reader. The [Live interaction source review](LIVE_INTERACTION_RESEARCH.md)
  separates tuning IDs from queue occurrences and pre-call events from completion;
  native queue observation, instance correlation and action outcomes remain open.
  The [Live fixture validator](LIVE_OBSERVATION.md) makes those data distinctions
  explicit but supplies no native observer, scheduler or outcome verification.
  The separate [Live reader seam](LIVE_NATIVE_OBSERVATION.md) adds only an
  uninstalled injected-reader contract; no native adapters, mode source, bootstrap
  or action outcome verifier are qualified.
  [Cross-component budget tests](OBSERVATION_TRANSPORT.md) show that valid maximum
  standalone reader DTOs can exceed the existing queue's node limit. The separate
  [request-bound Live path](LIVE_READ_ROUTE.md) uses a smaller whole-read bound,
  strict structured result/receipt validation and full actor/context intent.
  It remains uninstalled, default-denied and based on injected readers, with no
  native adapter, production sender or installed qualification. Build integration
  remains open. Journal receipt digests cannot recover observation bodies.
- Reviewed, reversible mod installation/update/rollback on a closed disposable
  profile. Staging currently stops before installation. The read-only
  [resource comparison](RESOURCE_DIFF.md) reports opaque stored-byte and index
  changes only; it does not decode resources or admit a candidate for installation.
- Coverage expansion beyond the initial operation catalog, driven by real tasks.

Normal OS consent/security prompts remain real boundaries. Do not change UAC,
UIPI, account permissions or security settings merely to make automation pass.
