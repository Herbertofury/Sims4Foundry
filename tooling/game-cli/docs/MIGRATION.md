# Existing control evidence and reuse

The implementation reviewed the owner's supplied Modernize control handoff dated
2026-11-04 and a historical Apex source snapshot, without importing its modules,
contacting its bridge, or changing its checkout/profile/session.

Historical source identity:

- `tools/apex_cli.py`: SHA-256 `6cb0d5f1eca23330acf82a116427082df4248116b8a22879165dd44ac1075fef`
- `tools/reusable_profile.py`: SHA-256 `2f34f9bb5183d9c37e1983278ddd095e9863dd5330c13e7f7c9185f80c628ed8`

These hashes identify the reviewed snapshots, not the current desktop checkout.
`foundry-game apex-inventory --repository PATH` parses a selected source snapshot
and returns the actual operation declarations/hashes without executing source.

## Established by the supplied handoff

- Earlier focus-free clicks were Apex-assisted. The script bridge submitted input
  to an in-game DLL bound to the Sims window. They are not a no-Apex input proof.
- Existing Apex semantic CAS, entry, controlled save and normal exit use exact
  disposable-profile journals, installed script hashes and real native Sim,
  household, save GUID and request identities.
- WGC/FFmpeg capture is independent of Apex. Capture success does not establish an
  interaction, frame-completeness guarantee or semantic result.
- The external pointer prototype requires fresh PID/path/hash/creation FILETIME,
  HWND/thread/class/client geometry/DPI, equal known sender/target integrity,
  an actively advancing pinned recorder, and independent outcome observation.
- That pointer is not game-qualified. No known-good no-Apex scrolling, Live entry,
  save/load or normal-exit procedure was supplied.
- The one-shot EA permission broker requests ordinary UAC, never approves it or
  bypasses UIPI. Changed/obscured/stale prompts and integrity mismatches must refuse.

## Reuse priorities

1. Preserve Apex's exact-identity journal and request-ID discipline.
2. Extract protocol/intent/state concepts rather than rewriting old Apex profile
   journals or silently removing script/DLL requirements.
3. Review current pointer/recorder source and pins before adapting it. Never point
   old journals at a newer control profile, even if paths look similar.
4. Port semantic handlers to a deliberately separate Foundry provider, with explicit
   capabilities. Existing Apex-only code remains Apex-only until that work is real.
5. Qualify menus, CAS, Live, Build, save/reload and normal exit independently on an
   authorized disposable profile. Do not overlap active tests or steal focus.

Microsoft documents why input queue acceptance is insufficient and why global
input is not a safe background fallback:

- [PostMessageW and UIPI](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-postmessagew)
- [SendInput system input stream and UIPI](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
- [GetWindowThreadProcessId ownership query](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowthreadprocessid)
- [PrintWindow synchronous behavior](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow)

## Current external pointer source inspection

The complete external pointer source was also read and byte-verified:
`external_pointer.py` SHA-256
`d8df2930835408334411e9f141a3d704e9dc32536eb75cca8f44136b90dfdcbd`.
No helper or game-facing Windows class was executed. Foundry's backend-injected
sequence runner preserves the key release rule: after a Down may have queued,
attempt one Up to the same original process-owned HWND before optional evidence
writes. Recorder/viewport failure must not suppress release; unverifiable/replaced
ownership must never redirect it. Failed drag cleanup uses the last attempted
point, not a future destination. Queue acceptance still is not a game result.

The new packet module extends the construction contract to right click, bounded
drag, navigation keys and screen-relative wheel coordinates using Microsoft
message specifications. These are new implementations, not historically validated
Sims controls. Text entry, held keyboard chords and arbitrary keys remain absent.

## Bounded per-operation source map

`capabilities --source-details` maps all 45 desired operations to the inspected
source evidence: 14 have inspected call sites, four have Apex-only paths, and 27
were not identified in the bounded review. Even the first two categories have
only partial coverage of Foundry's desired contracts and no live qualification.
The exported map contains source-relative references and derived facts, not copied
helper modules, private desktop paths or game/account data.

The map preserves several essential distinctions: accept-continuation cancellation
is not ordinary CAS cancellation; generic modifiers are not a proven hue control;
physique setters take one parameter at a time; voice calls need actor/range binding;
undo/redo result changes do not identify an intended history action; and custom CAS
acceptance does not prove disk persistence. Occult-cache mutation, owner conversion,
wing-selection and first-tattoo paths remain quarantined. Current CAS getters may
also mutate caches, so read-only intent requires independent validation.

The resource-index audit can identify exact competing package owners before a
future UI integration. It cannot select the right override, merge it, establish
load order, or qualify the current installed game version. Use only reviewed,
explicitly selected packages and retain a single authorized UI resource owner.
