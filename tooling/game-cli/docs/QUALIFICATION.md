# Live qualification gates

The user's goal is full, reusable CAS, Live and Build interaction plus autonomous
entry/exit and mod regression/update workflows. This checkpoint is not that outcome.
Do not close the task merely because simulator or generic Windows CI passes.

## Current status

| Area | Implemented | Exercised here | Live-qualified |
|---|---|---|---|
| Typed semantic contracts | 45 initial operations | Python tests | No |
| Plan/execute/reconcile | Synthetic model | Python tests and crash/lock checks | No |
| Mod inventory/diff/staging | Real local file operations | Synthetic artifacts | No gameplay/compatibility claim |
| Apex source migration inventory | AST-only read | Historical source snapshot | Not an input adapter |
| Passive process discovery | Bounded exact candidate reporting | Fake snapshots; native CI enumeration pending | No launch admission |
| Windows process/window inspection | Query-only implementation | Contract tests and native Windows CI fixture | Pending current game test |
| WGC capture | Pinned helper implementation | Mock helper plus real PNG validation | Pending current game test |
| No-Apex click/key/scroll/drag | Pure packet plans and injected runner only | Synthetic guard tests; fixture transport CI pending | No |
| Foundry semantic game bridge | Uninstalled typed queue and narrow native bindings; no transport/bootstrap | Fabricated services on actual Python 3.7 | No |
| No-Apex launch/entry/save/load/exit | Launch metadata preparation only | Synthetic EA metadata tests | No |

## Required controlled validation

Before any game-facing step, obtain a test window that does not overlap another
Apex/CAS session or the user's activity. Inspect existing state rather than
launching, attaching, changing a profile, or closing a preexisting process by guess.
Keep original saves/profiles protected. Use only an explicitly authorized disposable
profile with a matching, separately reviewed journal and a restoration procedure.

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

- Wire the offline protocol, owner queue and native bindings into an independently admitted, authenticated mod-neutral host. Preserve process epoch and zone/household/save preconditions across the boundary.
- A separately qualified external menu/input provider, including scroll/drag/keys.
- Integrate the cooperative process-lifetime lease with a shared durable journal and detect nonparticipating user/Apex/other-tool interference. A lease alone does not authorize input.
- Windows crash-durable pre-dispatch evidence ordering.
- Semantic state readers and independently checkable predicates for all domains.
- Reviewed, reversible mod installation/update/rollback on a closed disposable
  profile. Staging currently stops before installation.
- Coverage expansion beyond the initial operation catalog, driven by real tasks.

Normal OS consent/security prompts remain real boundaries. Do not change UAC,
UIPI, account permissions or security settings merely to make automation pass.
