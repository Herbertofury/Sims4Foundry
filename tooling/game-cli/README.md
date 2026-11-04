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
- Bounded DBPF 2.1 resource-index inspection and exact type/group/instance
  overlap reports for explicitly selected packages, without extracting resources.
- Read-only EA launch metadata preparation using the exact installation and a
  successful account Play record. It does not launch or write a cache.
- Pure click/right-click, navigation-key, wheel and drag packet constructors plus
  a backend-injected release-safe sequence runner. No native sender is exposed.
- A source-only native packet backend whose public entry always refuses. Its
  private test seam is restricted to newly owned disposable Windows CI fixtures;
  it does not supply live admission, a recorder or a game input command.
- Source-only Apex migration inventory. It parses source without importing it.
- Optional source-backed operation map that distinguishes inspected call sites,
  Apex-only paths and gaps in the bounded review; it grants no live capability.
- An uninstalled Python-3.7-compatible owner queue and bounded authenticated
  loopback transport, exercised with fabricated services rather than a game.
- A restart-safe companion intent/receipt journal and read-only local history
  inspection. Unknown mutations cannot be retried or cleared through the CLI.
- An uninstalled, default-denied Build observation seam for explicit object IDs
  and transition notifications, tested only with injected fabricated readers.
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

Inspect an existing companion journal with
`foundry-game companion journal-status --directory PATH`, optionally selecting
one stored `--request-id ID`. This reads local history only, never creates a
journal, contacts the game or network, or resolves uncertainty. Exit zero means
the history was read successfully, not that a recorded operation succeeded.
See [companion journal](docs/COMPANION_JOURNAL.md) and the
[isolated recovery regressions](docs/COMPANION_RECOVERY.md).

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

Staging requires matching source/destination artifact extensions and rejects
case-insensitive file/directory path conflicts before creating the output tree.
Inventory scans at most 16,384 entries across 32 nested directory levels and
accepts at most 4,096 artifacts. Observed symlinks and Windows reparse points
are rejected before descent; only bounded artifact rows are sorted.
Manifest paths have the same depth limit. Mod output paths, including temporary
receipt filenames, are limited to 259 UTF-16 units overall, with created directory
paths capped at 247. Each component is limited to both 255 UTF-16 units and 255
UTF-8 bytes before source reads or directory creation. These are conservative
portability limits, without assuming Windows long-path support; see Microsoft's
[path-length guidance](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation).
Hashes are bounded to the observed file size and copies to the inventoried size,
with one extra read probe that is never written. A changing source leaves an incomplete staging
tree without a completion marker; retrying never overwrites that tree.

The first staging call is a dry run. Execution requires a never-existing external
directory, rechecks source hashes, preserves the sources, and writes a completion
receipt only after every copy matches. An interrupted tree without that receipt is
incomplete. There is no in-place install command in this checkpoint.

`mods check` remains signature-only for DBPF packages. For deeper read-only
inspection of a supported DBPF 2.1 index, use:

```sh
foundry-game mods resources --file ./candidate/Example.package --output ./resources.json
foundry-game mods overlaps --file ./candidate/Example.package --file ./candidate/Other.package --output ./overlaps.json
```

The parser accepts a deliberately narrow, documented layout and refuses unknown
versions, flags, compression markers, deleted/duplicate keys and overlapping
extents. Resource bodies are hashed opaquely, never interpreted or decompressed.
Auditing is bounded to 128 packages, 1 GiB of source bytes and 16,384 entries.
Repeated exact type/group/instance keys identify competing resource owners, not
load order or a safe merge. Only equal whole-package hashes establish identical
package bytes; equal resource metadata does not establish equal resource bodies.

ZIP checks do not prove
Python/game-version compatibility, gameplay correctness or malware safety. Inventory
and resource reports contain local source paths; keep them private unless reviewed
for sharing.

## Prepare an EA launch without executing it

```sh
foundry-game launch-plan --game-root "INSTALLATION_PATH" --ea-log "EA_LOG_PATH" --executable-sha256 "EXPECTED_GAME_SHA256" --output ./launch-metadata.json
```

For rotated logs, pass the predecessor first and current log last with two
`--ea-log` arguments. This matches the successful Client Play record to the exact
launcher and installed content IDs. It never guesses an edition, opens the launch
URL, writes an account cache or authorizes a profile. The returned remaining gates
must be completed by the future lifecycle adapter before any actual launch.
All source extents are checked before reading: executable and launcher are each
limited to 512 MiB, the manifest to 1 MiB, and each log read to its final 2 MiB
plus one delimiter byte and an EOF-change probe. Hashes/readback stay within the
original extents and reject observed source drift. Evidence hashes use exact
selected request and success-record bytes, never replacement-decoded text. Log
scanning retains only a 30-record predecessor window. These are ordinary concurrent
change checks, not a hostile-filesystem snapshot or proof of launch authority.

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

## Uninstalled companion components

The `companion-src/foundry_companion` sources are a Python-3.7-compatible bounded
owner-thread queue, narrow native call bindings, and an explicit-lifecycle bounded
loopback transport. Imports and construction start no server or hooks. Server and
client default deny without independent host/peer authorization. Optional mutual
HMAC peer hooks accept an explicitly provisioned session key; no key provisioning
or production host policy is supplied. Tests use only public test keys, fabricated
services and isolated sockets.
They are not an installable mod or a working CAS/Build controller. See
[COMPANION.md](docs/COMPANION.md) for implemented boundaries and missing integration.
The unchanged v1 client protocol is wrapped by a separate schema-2 transport
contract carrying exact TTL/process/save context. Receipt-only reconnects never
requeue an operation. A returned adapter assertion is not independent game evidence.
See [TRANSPORT.md](docs/TRANSPORT.md). Neither this transport nor the cooperative
Windows process lease bypasses live qualification or resolves earlier actions.
See [PEER_AUTH.md](docs/PEER_AUTH.md) for the key-possession proof, exact socket
binding and remaining security limits. It is not encrypted application transport
or proof that a socket belongs to the game.

[Build observation](docs/BUILD_OBSERVATION.md) supplies a separately scoped,
owner-thread read contract. It distinguishes missing fields, non-atomic object
samples, current mode and event history without adding game hooks or transport
commands. Generic status-read permission does not admit these expanded reads.
[Live interaction source research](docs/LIVE_INTERACTION_RESEARCH.md) records why
tuning IDs, enqueue results, bulk cancellation returns and pre-call events are
not sufficient evidence of exact interaction identity or completion. Neither
addition enables real Build or Live actions.

## Passive process discovery

`foundry-game discover --executable "C:\Games\The Sims 4\Game\Bin\TS4_x64.exe" --executable-sha256 EXACT_HASH`

The Toolhelp name filter only finds candidates. Each still needs exact path/hash,
process lifetime and owned-window inspection. Inaccessible, changed, disappeared,
wrong-installation or ambiguous-window candidates remain explicit blockers.
An `ok: true` report means the inspection returned a complete report, not that its
`status` is actionable. Neither `none_observed` nor one matching identity proves
absence, grants launch admission, acquires a lease or qualifies game control.
See [UI_ADAPTER.md](docs/UI_ADAPTER.md) for the inspected CAS/Build provider limits.

## Inspect the source-backed operation map

`foundry-game capabilities --source-details` includes the reviewed legacy/native
call names, relative source references, missing contract requirements and readback
limits for all 45 desired operations. This is source lineage, not a dispatcher or
permission to use the old code. Every real operation remains unqualified. In
particular, an accept-continuation cancellation is not ordinary CAS cancellation,
an observed Build/Buy boolean is not a Build editor, and a CAS transition is not
verified on-disk persistence. The bounded review does not establish repository-wide
absence of operations it did not identify.
