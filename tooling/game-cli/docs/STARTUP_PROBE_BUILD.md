# Separate startup diagnostic archive

`startup-probe-build` creates a credential-free diagnostic archive outside the
game. It does not install the archive, launch a game, activate the companion,
authorize a profile, configure a key, or send a command. No Apex component is
required. The normal companion archive remains inert on import.

```text
foundry-game startup-probe-build --source "tooling/game-cli/diagnostic-src" --output "NEW_EXTERNAL/FoundryStartupProbe.ts4script" --python37 "TRUSTED_PYTHON37" --compiler-sha256 "EXPECTED_COMPILER_SHA256" --run-id "32_LOWERCASE_HEX_CHARACTERS" --probe-output "C:\EXPLICIT_EXTERNAL_EVIDENCE\NEW_PROBE.json" --seconds 30
```

The run ID must actually be 32 lowercase hexadecimal characters. The evidence
parent must already exist on the game machine and must be independently selected
outside the game and all profiles. The builder validates Windows path spelling,
rejecting UNC/device/relative paths, traversal, ADS, reserved names and obvious
game/profile directories. It does not inspect that machine, prove the location is
external, or grant permission to write there. Its configuration contains a path,
so retain the configured archive and manifest privately when that path is private.

The explicit source root must contain only `foundry_diagnostic/__init__.py` and
`foundry_diagnostic/runtime.py`. A fixed generated root candidate named
`sims4foundry_startup_probe.py` passes configuration as data to `start()`. It does
not accept arbitrary root code or an arbitrary entrypoint. Startup exceptions
are contained so a failed diagnostic does not abort the surrounding loader.
Missing evidence is unknown, never a successful result. Package import by itself
is inert. See [STARTUP_DIAGNOSTIC.md](STARTUP_DIAGNOSTIC.md) for runtime behavior.

The builder reuses the bounded, pinned Python 3.7 compiler and deterministic ZIP
publication primitives from [COMPANION_BUILD.md](COMPANION_BUILD.md). It never
imports or executes either source module or the generated root. All six source
and checked-hash bytecode members are recorded in a completion manifest. Both
output names must be new; a partial two-file publication remains incomplete.
There is no fallback installation, alternate output, or overwrite.

## What a later controlled run can establish

The game may not load this root candidate at all. A separately coordinated,
authorized disposable-profile run is needed to determine whether the entry is
called, its actual module origin, and the relationship between the startup Thread
object and an original-first successful core tick. It must preserve existing
profile/session ownership and any active mod task. Do not drop this archive into
an active game or use it to recover an unbound session.

Even a matching thread sample does not prove import occurred outside a tick or
that the thread is the authoritative game owner. The report is in-process
self-report, not independent process-lifetime, profile, save, appearance or
gameplay evidence. A timeout, hook conflict, cleanup pending, or absent report
does not authorize trying an alternate game entry route. Automatic neutral
companion startup remains unqualified until its actual lifecycle is established.
If startup finds the captured original tick's exact Python code already on its
bounded call stack, it refuses before metadata I/O, hook installation or writer
startup. Absence of that exact frame still does not prove outside-tick startup.

## Verification

Sixteen cloud-only build/entry tests cover the fixed six-member layout,
determinism and run-ID changes, malformed configuration, Unicode data, compiler
pin refusal, source allowlist, output collisions, incomplete publication, CLI
routing, contained startup failure, hostile-looking filenames as data, and a
competing archive writer during compilation. The configured actual Python 3.7 compiler
also compiles the repository diagnostic without executing it. The entry execution
test uses only a fabricated `start` function, not a game or the real diagnostic.
Runtime tests and Windows CI are recorded separately; none establish game loading.


## Inspect a returned diagnostic

The read-only `startup-probe-report` command checks explicit report/manifest
files against the original run ID and an independently supplied expected PID.
It can compare optional archive bytes, but never promotes a self-report into
loader, owner-thread, profile or game authority. See
[STARTUP_PROBE_REPORT.md](STARTUP_PROBE_REPORT.md).
