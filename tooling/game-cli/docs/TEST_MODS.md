# Test-mod sets

Use a selected, flat directory of candidate `.package` and `.ts4script`
files. The installer keeps script mods one folder below Mods, does not replace
existing mods, and refuses names already present elsewhere in Mods. Existing
mods remain active; this does not create a clean or isolated game profile.

```text
foundry-game mods inventory --directory SOURCE --output INVENTORY.json
foundry-game test-mods install --manifest INVENTORY.json --profile PROFILE --session NEW_SESSION_DIRECTORY
foundry-game test-mods status --session NEW_SESSION_DIRECTORY
foundry-game test-mods retire --session NEW_SESSION_DIRECTORY
```

Use only a profile the current task is authorized to change, with no active
game or competing controller. These commands do not establish ownership of an
existing session or override a denied process/profile inspection.

The Windows installer validates source hashes, records an external intent,
copies into a new staging folder outside Mods, verifies all copies, and
publishes the complete folder as `Mods/FoundryTest`. It checks for game
processes before staging and before publication. Those checks are snapshots,
not a lock preventing another program from launching the game concurrently.
The guard includes supported game and launcher basenames; access/query failures
propagate instead of becoming a clear-process result. The Python API's explicit
test dependency injection is not a security or authorization boundary.

Retirement first verifies the original intent, receipt, file names, sizes and
hashes, then moves the exact folder outside Mods as
`.foundry-retired-SESSIONID` under the same profile. It does not delete the
files. Changes observed before moving refuse retirement. Game process checks are
repeated before the move, followed by fresh exact-content verification. Both
installation and retirement verify moved files again before a completion receipt.
If files change during the move or final verification fails, the operation stays
unresolved and preserves the moved bytes. It does not roll them back or retry.

Path/content observations and no-overwrite directory publication narrow races;
they do not exclude every external writer or concurrent launcher. A status or
completion record is not continuous protection of the folder or proof the game
loaded it. Receipts are corruption checks, not authenticated permission grants.

`status` reads the records and reports path presence without replaying an
action. An unresolved installation or retirement must be inspected before
taking another action. In particular, a completion-receipt failure after a
move must not be interpreted as proof that no move occurred. This workflow
does not start the game, disable unrelated mods, validate compatibility or
restore a saved game.

Session records use a bounded, stable, unlinked regular-file reader before strict
JSON/schema checks. FIFOs, directories, links and changing inputs refuse; a read
failure does not try another path or API. The configured byte bound is not a hard
latency guarantee for an unresponsive filesystem.

## Source integration and fixtures

This preserves the standalone workflow from the visible task's
[d29e8d5 source checkpoint](https://github.com/Herbertofury/Sims4Foundry/commit/d29e8d50dcc4c74fb1f84a8d2aba31a499480574),
plus its expanded game/launcher guard. The integration retains startup-probe
commands and the foundation's newer Windows-name checks. Focused fixtures
cover final-scan changes, post-move uncertainty, shared locking and profile
activation binding. Nine separate record-reader tests cover strict JSON,
extent bounds, links, directories, FIFOs, file-to-FIFO substitution and denied
reads. They use owned temporary paths and fabricated process checks, never a
real profile or game. Hosted Windows results are recorded in VALIDATION.md.

## Shared lifecycle lock and directory binding

Install and retire now hold the same stable OS lock as profile activation,
restoration and guarded launch. Fresh profile, Mods and selected folder identities
are checked after the last process scan and after the move. An immutable
`install-identities.json` binds these directories before copying. This prevents
a stale session from retiring byte-identical files in a replacement profile.
Old sessions without that identity evidence remain readable, but retirement
refuses. The lock coordinates Foundry commands, not unrelated applications.
Fifty-seven owned test-mod/CLI fixtures cover the shared-lock integration; they
do not establish native Windows/game loading or profile qualification.

For a profile created by `test-profile activate`, installation must also pass
`--profile-session PROFILE_SESSION`. The exact activation is stored in the
identity sidecar for retirement. A pending EA handoff blocks installation and
retirement even when no game process is currently visible. A resolved, proven
never-dispatched request may proceed. Supplied/stored binding with a missing or
corrupt marker fails; an intact Foundry marker without a binding also fails.
Arbitrary external deletion of all provenance is outside the cooperating-command
model; this does not turn markers into an authentication boundary.
