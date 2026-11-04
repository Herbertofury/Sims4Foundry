# Direct Build object destruction

`build-adapter-src/build_destroy.py` implements a one-shot call to
`destroy()` on one explicitly identified existing, unparented non-Sim world
object. It is **not** the Build/Buy delete/sell action: it does not establish
refunds, catalog behavior, child-object handling, undo/redo or persistence.
It supplies no implicit restore or retry. Its source evidence is the
installed-game `server_commands/object_commands.pyc` call shape described
in [BUILD_DIRECT_TRANSFORM.md](BUILD_DIRECT_TRANSFORM.md).

The code is outside the fixed companion archive and is not installed, exposed
to the CLI, or qualified in the game. It has no Apex dependency. Integration
with the parallel startup and transport work requires review and coordination.

The host supplies exact Build readers and a separately reviewed admission
provider implementing `require_build_destroy(request)`. It must establish
current process, profile, save, lot, request freshness and precise object
scope; persist an intent before execution; and reconcile the result before
any future action. The caller must account for object ownership, children,
inventory, funds, other gameplay state and preservation before permitting an
irreversible direct call. Generic read or transform permission is insufficient.

The request includes a unique ID, full Build context, exact object ID and
before position, orientation and routing surface. The executor checks the
running Build context, same world surface, parentless non-Sim status and
matching before values, and resolves the same object again before the single
call. It samples the same context and object ID afterward. `object_absent`
reports only whether that resolver returned `None`; it does not establish a
normal transaction, a refund, a save, or why the object is absent.
If the selected ID remains present after the call, the result is unresolved.

An exception or lost context after dispatch is `unresolved` even if the
object has already disappeared. The bounded in-memory consumed-ID set lasts
only for this executor and process. It is not shared with the transform
executor and is not durable; a trusted host's journal must enforce uniqueness
across operations and process restarts. Neither that set nor sequential
samples lock the world or prevent an intervening change.

Run fixture tests on current Python and the game's Python-compatible runtime:

```text
python -m unittest discover -s tests -p test_build_destroy.py -v
python3.7 -m unittest discover -s tests -p test_build_destroy.py -v
```

The fabricated tests cover admission, baseline/context and object changes,
single dispatch, a no-op method, absence reporting, error after effect,
duplicate IDs, history bounds and thread/reentry checks. They do not test an
actual game object or qualify any Build/Buy deletion, sale or undo behavior.
