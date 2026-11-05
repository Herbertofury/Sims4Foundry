# Direct Build object transform

`build-adapter-src/foundry_build_adapter/build_transform.py` implements a guarded direct transform
of one explicitly identified, existing, unparented world object. It can set a
destination, world floor and yaw together after an explicit native placement
test. It does **not** purchase or place a catalog item, edit a swatch, delete an
object, or create a Build/Buy undo/redo transaction. It does not enter Build
mode. This distinction matters: direct `move_to` is not proof that the game
recorded an ordinary Build transaction or charged funds.

The module is outside the fixed companion archive manifest and can be built as
a separate development archive; it is not bootstrapped, installed, exposed
through a game-control CLI, or live-game qualified. See [BUILD_ADAPTER_PACKAGE.md](BUILD_ADAPTER_PACKAGE.md).
It has no Apex dependency. A separate integration must coordinate changes to
the companion package, transport and startup path with their current owner.

## Evidence for the native calls

Read-only inspection of the installed game's `simulation.zip` (SHA-256
`355e5cd305d229d191baff132147e3a72a226d8a06397fef1536abafe92b8c68`)
found the following in its compiled modules:

- `server_commands/placement_commands.pyc` constructs a world routing
  surface, `Vector3`, yaw quaternion, `Transform` and `Location`, then
  calls `build_buy.test_location_for_object(obj, location=location)`.
- `server_commands/object_commands.pyc` uses `move_to` with the keyword
  arguments `routing_surface`, `translation`, and `orientation`.

These are source call-shape observations, not an execution test, a placement
reservation, a safe purchase protocol, or evidence of undo/persistence. The
[generated native stubs](https://github.com/TURBODRIVER/TS4CPythonStubs/blob/76338aa350fb1bf723d03397c3ef44c375441b13/_buildbuy.pyi)
also describe a placement check but explicitly warn that signatures can be
incomplete or outdated. No stub code is copied or imported.

## Explicit trusted host

The host supplies:

- The existing Build native context and explicit-ID object readers.
- A reviewed provider implementing `require_build_transform(request)` that
  explicitly admits this exact request and its object, save, lot and profile.
- Already supplied native factories for `Vector3`,
  `angle_to_yaw_quaternion`, `SurfaceIdentifier`, `Transform`, `Location`
  and `build_buy.test_location_for_object`, plus the world surface type.
- Independent process/profile ownership, zone generation, durable intent and
  result journaling, expiration, and reconciliation across process restart.

`TransformRequest` contains an exact request ID, complete Build context,
selected object ID, before position/quaternion/routing surface, destination,
yaw in radians and destination floor. Before values can be obtained from the
existing Build observer. IDs, numeric values and field shapes are bounded;
booleans are not accepted as numbers. Only the current zone's world surface,
an exact non-Sim object with no parent, and a running Build-mode context pass.
The host must separately decide whether the object, children, lot, ownership,
funds and other gameplay state are within its authorized scope.

The provider is called before any reader. The executor checks a detached
request after admission, samples context around resolution, verifies the
supplied before values, makes native destination objects, tests placement,
then resolves the **same** object and same before values again before calling
`move_to` once. All callbacks and properties are trusted synchronous Python:
they can block or have side effects, and finite samples cannot freeze the world
or detect every intervening change. A successful placement test can become
stale before the move and is not an atomic reservation.

After dispatch the executor samples context and the same object again. A
returned `dispatched` result includes detached before/after values that match
the destination within 0.00001 (including quaternion sign equivalence). This
is not independent proof of transaction success, undo support or persistence.
An unmatched after-state, exception or context/identity loss after dispatch yields
`unresolved`; a host must reconcile and never blindly replay it. IDs are
consumed in a bounded in-memory set even on refusal. That set is lost on restart,
so it is **not** durable exactly-once execution; a host must persist an intent
before calling and retain uncertainty across process/session replacement.

## Fixture checks

```text
python -m unittest discover -s tests -p test_build_transform.py -v
python3.7 -m unittest discover -s tests -p test_build_transform.py -v
```

The tests fabricate services, admission, native constructors, placement results
and object mutation. They cover move/rotation/floor arguments, preflight and
placement refusal, changed context/object/baseline, reentry, request reuse,
post-dispatch uncertainty and a no-op native call. They do not qualify the
actual game, prove the current placement ABI, exercise other object classes or
prove any Build catalog, swatch, deletion, undo, save or reload behavior.
