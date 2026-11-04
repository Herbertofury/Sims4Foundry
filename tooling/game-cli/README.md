# Foundry Game CLI 0.1

A mod-neutral home for Sims 4 automation, regression workflows and mod updates.
This is an **offline-tested foundation, not a complete autonomous game controller**.
No-Apex launch, input, CAS/Live/Build mutation, save and normal exit remain unqualified.

## What works in this checkpoint

- Installable Python CLI with structured JSON and nonzero refusal/error exits.
- A typed catalog of 45 initial lifecycle, CAS, Live and Build operation contracts.
- A synthetic state model for testing plans, execution, save/load flow, undo/redo,
  interruption and read-only reconciliation. This is not Sims emulation.
- Hash-bound, expiring plans; one dispatch per operation ID; immutable intent/result
  filenames; before/after observations; OS-held exclusive workspace locks.
- Real offline `.package` signature checks, bounded `.ts4script` ZIP/CRC checks,
  inventory/diff and verified copies into a new external staging directory.
- Source-only Apex migration inventory. It parses source without importing it.
- Windows-only process/window identity inspection and pinned FFmpeg WGC capture
  implementations. Their native paths still need controlled Windows/game validation.

## What does not work yet

All real semantic actions are **unqualified and refused**. No flag or JSON field can
turn a simulated result into live qualification. This package does not install a mod,
change a profile, launch/close a game, drive a cursor, change foreground focus,
modify security settings, approve UAC, inject a DLL, or execute arbitrary commands.
It does not claim support for every Sims feature merely because a command is listed.

The existing Apex CLI is a distinct, Apex-dependent controller. Its successful
background clicks are not proof that no-Apex Windows messages work. See
[Migration evidence](docs/MIGRATION.md) and [qualification gates](docs/QUALIFICATION.md).

## Install and test

Python 3.11 or newer; runtime has no third-party Python dependencies.
From this directory:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
foundry-game capabilities
```

Without installing, set `PYTHONPATH=src` and run `python -m sims4foundry_cli`.
On PowerShell use `$env:PYTHONPATH = 'src'`.

`capabilities` defaults to the real Windows backend and reports the unqualified
status clearly. `--backend simulation` reports synthetic availability only.

## Exercise the controller without touching a game

Choose a never-existing external workspace:

```sh
foundry-game session create --directory ./scratch/example-session --backend simulation
foundry-game workflow validate --file examples/mod-regression.simulation.json
foundry-game workflow run --session ./scratch/example-session --file examples/mod-regression.simulation.json
foundry-game session status --directory ./scratch/example-session
```

The 14-step example models launch, new household, CAS edit, Live interaction,
Build placement, save, menu, reload and exit. Every result says `simulation: true`
and `real_game_evidence: false`. No process or game files are contacted.

For one operation, planning and execution are separate:

```sh
foundry-game game launch --session ./scratch/another-session
foundry-game execute --session ./scratch/another-session --plan-id RETURNED_ID --plan-sha256 RETURNED_SHA256
```

Create `another-session` first. A plan expires after 30 seconds and binds the exact
session revision/state. Use returned values, not placeholders. Repeating a verified
operation returns its existing receipt, never dispatches again. After an interrupted
operation:

```sh
foundry-game reconcile --session ./scratch/another-session --operation-id RETURNED_ID
```

Reconciliation never replays a mutation. An unresolved dispatch blocks later
mutations. Do not delete receipts to force progress. Receipt hashes are consistency
checks, not signatures or proof against a malicious filesystem actor.

## Prepare mod updates offline

```sh
foundry-game mods check --file ./candidate/Example.package
foundry-game mods inventory --directory ./candidate --output ./candidate-inventory.json
foundry-game mods compare --before ./old-inventory.json --after ./candidate-inventory.json
foundry-game mods stage --manifest ./candidate-inventory.json --destination ./staged/release-001
foundry-game mods stage --manifest ./candidate-inventory.json --destination ./staged/release-001 --execute
```

The first staging call is a dry run. Execution requires a never-existing external
directory, rechecks source hashes, preserves the sources, and writes a completion
receipt only after every copy matches. An interrupted tree without that receipt is
incomplete. There is no in-place install command in this checkpoint.

DBPF checking is signature-only, not resource validation. ZIP checks do not prove
Python/game-version compatibility, gameplay correctness or malware safety. Inventory
files contain local source paths; keep them private unless reviewed for sharing.

## Passive Windows tooling

`inspect` takes an explicitly selected PID, executable path/hash and optional HWND.
It uses limited query rights and checks process creation FILETIME, image identity,
visible non-minimized Canvas window ownership/thread, integrity and client geometry.
It refuses ambiguous or inaccessible identities.

`capture` takes that identity, an existing explicitly selected `ffmpeg.exe` and its
expected SHA-256, plus a new external destination. It reobserves identity before and
after one window-only WGC capture, never takes global desktop screenshots. A valid
PNG is evidence of captured bytes, not UI state or input success. Blank/stale frames
and capture geometry still require independent visual validation.

No Windows/game command was run to validate this checkpoint. Use the controlled
qualification procedure only when other tests and the user's activity are not at
risk. Launch can itself activate a window even without an explicit focus call.

## Exit codes

- `0`: operation succeeded within its stated scope, including synthetic-only scope.
- `2`: invalid input, unsupported/unqualified action, filesystem/access refusal.
- `3`: pending or uncertain dispatch requiring observation, not a retry.

## Development

Run the full unittest suite after every change. It never requires Sims, Apex or a
running desktop. CI uses Linux and Windows runners, including an isolated native-window fixture,
but does not test the game or run on the user's desktop.
See [Architecture](docs/ARCHITECTURE.md), [Validation](docs/VALIDATION.md) and
[qualification gates](docs/QUALIFICATION.md) before adding real mutation support.
