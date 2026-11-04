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

The Windows installer validates source hashes, records an external intent,
copies into a new staging folder outside Mods, verifies all copies, and
publishes the complete folder as `Mods/FoundryTest`. It checks for game
processes before staging and before publication. Those checks are snapshots,
not a lock preventing another program from launching the game concurrently.

Retirement first verifies the original intent, receipt, file names, sizes and
hashes, then moves the exact folder outside Mods as
`.foundry-retired-SESSIONID` under the same profile. It does not delete the
files. Changed or additional files refuse retirement. Game process checks are
repeated before the move.

`status` reads the records and reports path presence without replaying an
action. An unresolved installation or retirement must be inspected before
taking another action. In particular, a completion-receipt failure after a
move must not be interpreted as proof that no move occurred. This workflow
does not start the game, disable unrelated mods, validate compatibility or
restore a saved game.
