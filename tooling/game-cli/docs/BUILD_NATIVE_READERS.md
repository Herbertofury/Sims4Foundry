# Build/Buy native service readers

`build-adapter-src/foundry_build_adapter/build_native_readers.py` supplies the two reader callbacks
required by the existing `BuildObserver`. It derives a Build context from a
host-supplied services object and resolves only requested object IDs through
`services.object_manager().get(id)`. It has no Apex dependency.

This is source and fixture-tested integration, not an installed game feature.
The file is intentionally outside `companion-src`: the other Foundry workstream
owns startup and its fixed companion archive manifest. It can be built into a separate development archive, but is not bootstrapped,
connected to the transport or available as a game-control CLI command. See
[BUILD_ADAPTER_PACKAGE.md](BUILD_ADAPTER_PACKAGE.md).
Integrating it into the actual companion requires updating that manifest and
reviewing the owner/runtime path in coordination with that workstream.

## Host contract

Construct `BuildNativeReaders(services, generation, session_id, process_epoch)`
on the verified game owner thread, then give its `context` and `object`
methods to `BuildObserver`. Construction performs no game-service reads.
The observer's default admission denies reads. A trusted host must provide its
existing narrow `require_build_read` admission, establish process, profile and
session ownership, and enforce request freshness and any required journaling.
Calling the reader methods directly bypasses the observer's admission; they
are host-internal callbacks, not a public authorization boundary.

`services` is supplied by the host; this module imports no game service.
`generation()` is a trusted, synchronous positive integer provider that must
change on **every** zone lifetime, including a reload with the same zone ID.
This adapter cannot establish whether that provider is truthful or detect an
ABA transition between samples. It provides no generation source or rebind.

The context callback reads `current_zone()`, zone ID, running and Build/Buy
flags, active household ID, first client ID, save slot and save GUID. It checks
the same zone object and ID and the same exact generation after the read.
Missing or nonboolean Build/Buy mode is unavailable, not false; unavailable
required IDs, save fields or running state refuse the sample. Zero save slot
and save GUID remain valid. All reads are sequential, not an atomic snapshot.
The observer performs its existing context checks before and after each
selected-object resolution and field sample.

The object callback accepts only an exact positive uint64 integer, performs one
`get` without iteration or fallback, and checks that the current zone object,
zone ID, object-manager object and exact generation still match afterwards.
Its raw object return is consumed only by the trusted observer; the observer
validates the object ID, samples bounded fields and returns detached values.
It does not create objects, load a lot, change modes or authorize mutations.

Callbacks are bound to the constructing thread and reject reentry. Ordinary
service errors become static refusals without adding exception text to output.
This cannot constrain the behavior or runtime of a trusted service getter or
generation callback, guarantee a frozen world, prove Build mode, establish a
placement outcome, or protect against a process/profile change outside the host.

## Source evidence and checks

The service reads follow the existing native observation in
`foundry_companion/native.py`; explicit object lookup follows the
[Sims 4 Community Library object utilities](https://github.com/DeviantGameMods/Sims4CommunityLibrary/blob/a36107dbf1fab0b8e4bd1a87b1bdaadd41d5d8a5/Scripts/sims4communitylib/utils/objects/common_object_utils.py#L185-L283),
as documented in [BUILD_OBSERVATION.md](BUILD_OBSERVATION.md). This is new code
and does not import that library.

```text
python -m unittest discover -s tests -p test_build_native_readers.py -v
python3.7 -m unittest discover -s tests -p test_build_native_readers.py -v
```

The tests use fabricated services, objects and admission. They cover selected
readback through the existing observer, default denial, exact fields, missing
mode, identity and generation changes, no manager scan, error handling,
reentry and thread checks. Passing them is not evidence of live-game behavior,
placement, move, delete, undo, persistence or save/reload.
