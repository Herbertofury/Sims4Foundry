# Standalone startup diagnostic

This is an **uninstalled, credential-free self-report probe**, not a game
controller or an activation path for the companion. Offline fixtures do not
qualify a game build, a loader, a profile, startup timing or any native action.
No desktop/game activity, profile installation or game launch is part of the
source tests.

## Deliberately separate activation

`diagnostic-src/foundry_diagnostic/__init__.py` is inert and does not even import
its runtime. Importing `foundry_diagnostic.runtime` also starts no work. A
separately generated, explicitly selected root entry may call:

```python
from foundry_diagnostic.runtime import start
run = start(
    {'run_id': '0123456789abcdef0123456789abcdef',
     'output_path': '/explicit/existing/external-directory/new-evidence.json',
     'wait_seconds': 5.0},
    module_origin=__file__,
    loader_type=type(__loader__).__name__)
```

Use a native absolute Windows drive path on Windows. The example is an API
example, not installation or live-run permission. The generated entry should
catch `Exception` so an invalid diagnostic request cannot abort loader work;
absence of a report remains unknown, not success. Root entry and archive
construction are separate from this runtime.

The config must be an exact dictionary with only `run_id`, `output_path` and
`wait_seconds`. The run ID is exactly 32 lowercase hexadecimal characters; it
is correlation, **not a key or credential**. The finite sampling window is
0 through 30 seconds, inclusive; booleans are rejected. Optional loader fields
must be strings or `None`, never objects to stringify. Origin is bounded to
1,024 characters and 6,144 ASCII-JSON payload bytes; loader type to 256
characters and 1,536 payload bytes, with truncation flags. Astral characters
count as twelve escaped bytes, so maximum Unicode inputs still fit the file
budget. There is no
configuration file, environment-variable lookup, profile discovery or marker
read.

Every explicit call consumes one activation attempt, including invalid config.
Further calls refuse with `already_started`; reloading the runtime does not
reset that claim. Deleting/replacing trusted runtime modules is not a supported
retry or adversarial-security boundary. No API renews, replays or takes over an
attempt. `DiagnosticRun.status` exposes a small in-memory snapshot; it cannot
start or retry anything.

## What it records

Startup captures `os.getpid()`, the actual current Python Thread object and
ident, Python version, and the root's bounded loader/origin strings. The object
is retained for actual `is` comparison; serialized `object_id`/`ident` numbers
are only local correlations and can be reused after object/thread lifetimes.
The runtime is restricted to CPython because its single-entry list and
single-result dictionary arbitration rely on the GIL's built-in operations.

It consults only `sys.modules['sims4.core_services']`, and only when that value
has exact `ModuleType` type and its dictionary already contains callable
`on_tick`. It never imports the core, services, commands, companion, transports,
sockets, authentication or native game APIs to fill a gap. Missing core/tick
produces an unavailable report, not a fallback import.

A bounded startup stack check visits at most 64 frames. When the captured
original is an exact Python function, it reports whether that exact function's
code object occurs in the current stack. Only depth, truncation, code
availability and the identity-match boolean are kept. No frame, local,
filename, code representation or traceback is retained or emitted. A positive
match refuses startup before selected-path metadata I/O, hook installation or
worker/Thread creation. It returns only in-memory `startup_inside_known_tick`
status, publishes no file and consumes the one-shot attempt. Absence, wrappers
and native code cannot prove that startup is outside a game tick. When that
check is negative or unavailable, explicit startup still performs metadata I/O
and starts its worker; their safety relative to the actual loader remains an
unqualified question for a separately authorized controlled diagnostic.

The first eligible, outer successful original callback records its actual
registered Thread object/ident relationship to startup:

- `tick_observed`: the callback's Thread object is the captured startup object.
- `thread_mismatch`: a registered foreign Thread completed the original. Sampling
  stops, but this foreign thread does not restore or overwrite the hook.
- `tick_thread_unavailable`: the native callback has no existing Thread object in
  the captured CPython registry. It records the ident with unknown object
  relationship, without constructing a `DummyThread` or taking its internal
  lock.

These names describe only the instrumented callable. They do not establish
which thread is the game's authoritative owner. `import_outside_tick` and
`owner_thread_authority` remain `unknown`, even with matching Thread objects.
`independent_evidence`, `profile_authority`, `os_process_lifetime_proof` and
`game_qualified` are always false. A reported PID is not an independently
verified OS process identity or lifetime. No result authorizes later control.

## Tick behavior and cleanup

The wrapper calls the captured original first, forwarding the exact positional
and keyword arguments, returning the same object and preserving the original
exception object, including `BaseException`. It never samples a failed original
call. An inner reentrant success cannot stand in for an outer failure. Nested
or concurrent invocations pass through to the original and cannot claim a
second sample.

Diagnostic work after a successful original is bounded and in-memory only:
registry lookup, code-owned state, identity comparison and, only on the captured
startup Thread, conditional restoration of its own hook. There are no imports,
file/socket operations, thread creation, waits, blocking locks, logging or
reporting callbacks inside the diagnostic hook. The original game/mod callable
is outside that guarantee and retains its existing behavior.

The runtime restores only when the captured core is still the exact current
`sys.modules` object and its `on_tick` is still this exact wrapper. A later mod's
wrapper is never called by the diagnostic as a substitute, unwrapped, traversed
or overwritten. A foreign-thread sample, replacement or timeout can leave
cleanup pending. After timeout, the remaining wrapper passes through the
original and never collects another sample; a later successful callback on the
captured startup Thread can remove only its own still-current wrapper. The
already-written evidence is not updated. A later mod retaining the wrapper in
its chain can leave it reachable until process exit.

Hook identity checks are cooperative Python checks, not a compare-and-swap
security primitive against a hostile concurrent mod writing the same attribute.
No third-party code or native services are executed by the diagnostic beyond
preserving the existing original callable.

## External evidence output

The output must be a new `.json` path in an independently selected, already
existing external directory. No parent directories are created. Components beginning with `The Sims 4`, exact `Game`, `Mods`, `Saves`, `Tray`,
and components ending in `.ts4script` are refused (case-insensitively). This conservative lexical check is
not universal proof of profile exclusion. The caller remains responsible for
selecting an external directory; renamed profiles cannot be discovered safely
without broader information that this probe deliberately does not read.

Windows UNC/device paths, alternate-data-stream colons, reserved device names
(including spaces before extensions), traversal, control/surrogate characters,
ambiguous trailing spaces/dots and unsafe component characters are refused.
Ordinary Unicode names are supported. On POSIX, paths must also be native
absolute paths, without Windows backslash syntax or leading double slash.

Before installing any hook, startup reads only metadata of the explicitly
selected path and its ancestors. An existing destination, missing parent,
symlink or Windows reparse ancestor fails without installing a hook or starting
a worker. Contents of an existing destination are never read.

A single background worker polls the one bounded memory result until the
sampling deadline or completion, then makes one output attempt. Worker and
startup use one atomic resource-ownership claim because `Thread.start()` can
create a thread and still raise. If startup cancellation wins, a late worker
returns without touching resources. If the worker wins, an interrupted start
leaves its descriptor and result under worker ownership and reports
`start_interrupted_worker_owned` rather than falsely claiming completion. The
JSON launch field is explicitly a snapshot at serialization; a later interrupted
startup return can only be reflected in the current in-memory status. It serializes
at most 16 KiB of JSON, creates a same-directory temporary file exclusively,
flushes and fsyncs it, and exclusively hard-links the complete file to the new
final name. The final name is never overwritten. Unsupported hard linking,
collisions, errors or interruption do not trigger fallback names or retries.
The worker removes only a temporary file it created whose current identity
still matches; a collision or changed identity is left untouched and reported
in local status. A crash can leave a temp file; that residue is not a successful
run or permission to take it over.

On supported POSIX systems the worker uses a pinned directory descriptor reached
component-by-component with no-follow opens, anchored file operations and
ancestor identity rechecks. On Windows, Python 3.7 stdlib provides metadata
checks and reparse rejection but not equivalent anchored directory operations.
The report explicitly says `metadata_checks_not_race_free` there. This is a
trusted-local diagnostic, not a security boundary against concurrent malicious
filesystem changes. Windows ACL privacy is not established by the POSIX-style
creation mode. Filesystem calls themselves cannot be given a portable hard
latency bound; the 30-second bound is the collection/poll window, not a promise
that an unresponsive filesystem completes publication within 30 seconds.

Hook cleanup in the JSON is a snapshot and can conservatively remain pending
when publication races with safe restoration. Publication/temp-cleanup errors
are visible through `run.status`; a failure to create the file cannot report
itself through that absent file. A published file whose selected ancestors
subsequently fail verification has local status `published_location_unverified`,
not verified delivery. The probe neither deletes a final report nor retries it.

## Offline verification

Only fabricated modules, owned temporary paths and owned test threads are used.
The same focused suite runs on both the host interpreter and actual CPython
3.7.17; syntax parsing alone is not presented as an actual 3.7 execution.

```sh
python -m unittest discover -s tooling/game-cli/tests -p test_startup_diagnostic.py -v
/path/to/trusted/python3.7 -m unittest discover -s tooling/game-cli/tests -p test_startup_diagnostic.py -v
```

Coverage includes inert imports, argument/return/error identity, missing and
non-exact modules, duplicate/reloaded starts, nested/concurrent/foreign/native
threads, hook replacement, bounded stack facts, timeout, worker failure,
forbidden tick operations, late/interrupted worker starts, astral JSON budgets,
path validation, collision, complete publication,
symlink/reparse rejection, changed parents, changed temp identity and cleanup.
None of these fixtures establish actual game loader or live owner behavior.
