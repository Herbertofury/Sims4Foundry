# Neutral test profile and EA launch

This Windows development path has no Apex dependency and uses no general
Computer Use session. It has passed fabricated-process/owned-directory tests,
not a real EA/game qualification. Do not apply it to another task's session or
use it to work around denied profile or process access.

## Prepare an explicitly authorized disposable profile

```text
foundry-game test-profile activate --profile PROFILE --session NEW_PROFILE_SESSION
foundry-game test-profile status --session PROFILE_SESSION
```

Activation prepares empty saves and Tray folders and a Mods/Resource.cfg. It
copies Options.ini as-is when present, not saves, Tray or existing mods. Script
mod settings are not changed or asserted. The original directory is moved to a
unique sibling and preserved. Exact directory identities, create-only intent
and completion receipts, and one shared OS-held lock coordinate profile,
launch and test-mod commands. External programs do not obey that lock; process
checks and filesystem identities remain snapshots, not a security boundary.
All journal and Options.ini reads are bounded regular-file reads; links,
nonregular files, oversized files and changing inputs are refused.

The separate [test-mod workflow](TEST_MODS.md) installs a selected flat artifact
set into this explicitly selected closed profile. Pass `--profile-session
PROFILE_SESSION` to `test-mods install`; retirement uses its stored binding and
refuses a pending launch even when no process is visible. It does not make any mod load
or become compatible. Retire the test set before profile restoration if that
is the desired workflow. A stale test-mod session cannot move files out of a
different replacement profile even if their bytes match.

## Submit one EA handoff

```text
foundry-game game-launch --game-root GAME_ROOT --ea-log EA_LOG --executable-sha256 GAME_SHA256 --ea-launcher EA_LAUNCHER --launcher-sha256 EA_LAUNCHER_SHA256 --profile-session PROFILE_SESSION --session NEW_LAUNCH_SESSION --seconds 30
foundry-game game-launch-observe --session LAUNCH_SESSION --seconds 30 --output NEW_OBSERVATION.json
```

Required pins are the explicitly selected game installation and executable
SHA-256, the registered EALauncher.exe path and SHA-256, the current selected EA
log, and this exact profile activation session. The launcher may use EA's
versioned directory symlink; its resolved regular executable and hash are
rechecked. The registered URI command must match the selected launcher without
extra arguments or an ambiguous executable prefix. The prepared profile must
match Windows' current redirected Documents location.

Before dispatch, the CLI writes a sticky launch reservation while holding the
profile lock. One dispatch is attempted. If dispatch returns, a separate
`launch-handoff.json` binds that return to the exact intent and current profile.
An observation is evidence of a matching visible process/window, not causal
ownership, household readiness, a successful load, or a completed launch.

A timeout, lost acknowledgment, absent process or later process termination
**does not clear the reservation**. Observe without relaunching. The reservation
blocks profile restore/recovery and a new launch on this activation, even if a
process scan is empty, because EA can still be handling the earlier request.
This checkpoint has no qualified reconciliation path for a handed-off request.
Do not delete receipts, reuse an old marker, or change a PID to force progress.

## Optional ordinary EA dialog acknowledgment

```text
foundry-game game-launch-ea-context --session LAUNCH_SESSION
foundry-game game-launch-ea-consent --session LAUNCH_SESSION --ea-image EA_DESKTOP_EXE --ea-sha256 EA_DESKTOP_SHA256 --seconds 20
```

Consent requires a positive handoff receipt, exact active profile binding and
reservation, fresh unambiguous EA log context, and a selected EADesktop.exe hash
and process lifetime that predates the correlated log event. A missing/uncertain
handoff receipt is observe-only. Any pre-dispatch abort receipt prevents consent.

The one-shot helper uses Windows UI Automation for the exact English EA
permissions wording and one enabled OK button in the selected process. It
revalidates scope, request evidence and lifetime before commit, uses an original
bounded deadline including helper startup, and checks a shared performance-counter
expiry before Invoke. Cleanup failure or lost acknowledgment stays uncertain and
is never retried automatically. Executable reads are stable and capped at 512 MiB.

The generic EA wording does not itself identify a specific launch request. Log
correlation and process checks narrow the scope but do not prove dialog ownership.
Native EA behavior remains unqualified. This helper does not approve Windows UAC,
change security settings, bypass a warning, issue desktop-wide clicks or move the
OS cursor. A separate security prompt may still require user action.

## Recover only a proven pre-dispatch failure

```text
foundry-game game-launch-reconcile-not-dispatched --profile-session PROFILE_SESSION --session LAUNCH_SESSION
foundry-game test-profile status --session PROFILE_SESSION
foundry-game test-profile restore --session PROFILE_SESSION
```

The reconciliation command succeeds only when the launch coordinator recorded
that it never entered dispatch, and the exact activation, intent, reservation
and proof still match. Handoff, observation or consent evidence contradicts this
path and causes refusal. It adds a create-only resolution receipt, preserves all
original evidence, does not restore the profile, and does not permit replay or
consent. Status reports `resolved_before_dispatch`. Lost receipt acknowledgment
can be inspected and reconciled read-only without rewriting it.

Restoration, when no unresolved launch remains, preserves the entire test
profile as a unique sibling and returns the exact original to its original
location. It never deletes unknown data. After an interrupted activation or
restoration, inspect status before the explicit recovery command:

```text
foundry-game test-profile recover-activation --session PROFILE_SESSION
foundry-game test-profile recover --session PROFILE_SESSION
```

Recovery accepts only unambiguous current identities and consistent receipts,
including the interrupted state before the first restore move. Contradictory,
partial or changed evidence is a blocker, not a reason to overwrite anything.

## Validation boundary

The neutral profile, EA launch/dialog and test-mod tests use owned temporary
files and fabricated processes/helpers. They do not invoke native discovery,
EA or a real game. No live profile was swapped, no mod installed in a user
profile, and no real EA consent was sent during this implementation/review.
Existing Windows owned-process and capture fixtures test separate primitives;
they do not qualify these orchestration commands. Full CAS/Live/Build control,
automatic startup/admission and normal game exit remain separate work.
