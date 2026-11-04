# Direct Build object creation

`build-adapter-src/build_create.py` adds one direct object-creation step. It
does not move the created object, charge household funds, select a catalog
item, record a Build/Buy transaction, clean up, or implement undo. A successful
creation result explicitly reports `placed: false`. This is not a qualified
Build/Buy placement workflow.

The code is outside the fixed companion archive, has no startup or CLI route,
and has not been executed in the game. It has no Apex dependency. Any later
integration must be coordinated with the owner of startup and packaging.

Read-only inspection of the installed game's `objects/system.pyc` identified
`objects.system.create_object(definition_or_id, ...)`; the game's own
`server_commands/object_commands.pyc` invokes it with a definition ID.
Inspection also found the placement-test call documented in
[BUILD_DIRECT_TRANSFORM.md](BUILD_DIRECT_TRANSFORM.md). The
[generated native stub](https://github.com/TURBODRIVER/TS4CPythonStubs/blob/76338aa350fb1bf723d03397c3ef44c375441b13/_buildbuy.pyi)
describes testing a candidate with a definition ID and location, but the
definition-only argument form and ABI remain **unverified in the current
game**. Source call shapes and generated stubs are not game qualification.

The host supplies current Build context and object readers, native position,
orientation, routing, transform and location constructors, the definition-based
placement test, `objects.system.create_object`, and a separately reviewed
`require_build_create(request)` admission. The admission must establish exact
process, profile, save, lot, definition and operation authorization, including
ownership, funds, object side effects and preservation. The host must persist a
durable unique intent before the call, and reconcile uncertain outcomes across
all operations and process restarts.

The exact request binds the definition, destination, yaw and floor to the
complete Build context. After admission and running-Build context checks, it
tests the proposed location. Only an exact true result permits one creation
call. The check is sequential and not a placement reservation. The game may
create an object at its own default position and may run other creation logic;
the code never assumes it is already at the requested destination.

If creation returns an object, the executor reads its exact ID, validates its
definition, verifies that the selected object reader returns the same object,
and returns detached position, orientation and surface. A host may use those
as a baseline for a **separately admitted and journaled** direct transform
request. An exception, missing object, changed context or failed readback
after the create call is unresolved. The result includes the object ID if it
was obtained, but the host must not automatically create again, delete an
object or assume that no object was created.

The bounded in-memory consumed-ID set belongs only to this executor and is lost
on restart; it is neither durable nor shared with the transform, variant or
destroy executors. No observation is atomic or a profile/lot lock.

```text
python -m unittest discover -s tests -p test_build_create.py -v
python3.7 -m unittest discover -s tests -p test_build_create.py -v
```

Fixture tests cover admission, proposed-location check, separate creation and
transform requests, exact identity, changed context, unavailable readback,
lost replies, duplicate requests, bounded history, reentry and thread checks.
They do not establish current-game creation, placement, billing, undo or
save/reload behavior.
