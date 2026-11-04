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
| WGC capture | Pinned helper implementation | Mock helper plus real PNG validation | Pending current game test |
| No-Apex click/key/scroll/drag | Pure packet plans, injected runner and source-only native backend with always-denied public entry | Native backend click/key/wheel/drag packet receipts and unchanged cursor/foreground on disposable Windows fixtures | No |
| Foundry semantic game bridge | Uninstalled typed queue, narrow native bindings, default-denied socket transport and explicit-key peer hooks; no bootstrap/admitted host | Fabricated services and real isolated sockets on actual Python 3.7 | No |
| Build object/mode observation | Uninstalled, separately admitted explicit-ID reader and transition-observation seam | Injected fabricated readers on current Python and actual Python 3.7 | No atomic/native/world-state proof |
| Live queue observation contract | Pure bounded fixture validator, no native reader or action methods | Fabricated availability/identity/order/bounds tests | No |
| No-Apex launch/entry/save/load/exit | Launch metadata preparation only | Synthetic EA metadata tests | No |

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

- Complete the independently admitted mod-neutral host around the source-only transport/owner queue/native bindings. Process epoch and zone/household/save preconditions are carried across the boundary; peer key-possession hooks exist, but secure key provisioning, qualified channel/peer policy, live admission, qualified exact UI-mode observation, scheduling and live durable reconciliation are not implemented. The offline client ledger stores exact intent and receipt history, but has no transport wiring, shared history anchor or independent outcome verifier; see [COMPANION_JOURNAL.md](COMPANION_JOURNAL.md).
- A separately qualified external menu/input provider, including scroll/drag/keys. The new [native backend](NATIVE_INPUT.md) is source/fixture-only; it has no production admission, independent window epoch or real recorder verifier.
- Integrate the cooperative process-lifetime lease with a shared durable journal and detect nonparticipating user/Apex/other-tool interference. A lease alone does not authorize input.
- Windows crash-durable pre-dispatch evidence ordering.
- Semantic state readers and independently checkable predicates for all domains.
  The [Build observation seam](BUILD_OBSERVATION.md) is a bounded source-only
  starting point, not a qualified reader. The [Live interaction source review](LIVE_INTERACTION_RESEARCH.md)
  separates tuning IDs from queue occurrences and pre-call events from completion;
  native queue observation, instance correlation and action outcomes remain open.
  The [Live fixture validator](LIVE_OBSERVATION.md) makes those data distinctions
  explicit but supplies no native observer, scheduler or outcome verification.
- Reviewed, reversible mod installation/update/rollback on a closed disposable
  profile. Staging currently stops before installation.
- Coverage expansion beyond the initial operation catalog, driven by real tasks.

Normal OS consent/security prompts remain real boundaries. Do not change UAC,
UIPI, account permissions or security settings merely to make automation pass.
