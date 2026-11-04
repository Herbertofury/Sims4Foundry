# Direct Build object definition variant

`build-adapter-src/build_variant.py` is an explicit, one-shot direct
definition change for an existing selected world object. It requires the
current and requested definition IDs to report the same nonzero variant group.
This is **not** a qualified catalog swatch operation: a shared group does not
prove visual equivalence, preserve object components or establish an
undoable Build transaction.

The module is outside the fixed companion package; it is not installed,
connected to the CLI, or tested in the game. It has no Apex dependency.

Read-only inspection of the installed game's `simulation.zip` identified
`server_commands.object_commands.set_object_definition` calling
`obj.set_definition(definition_id)` and treating a false return as a
non-similar definition. That game's `build_buy` module exposes
`get_variant_group_id`; the [generated native stubs](https://github.com/TURBODRIVER/TS4CPythonStubs/blob/76338aa350fb1bf723d03397c3ef44c375441b13/_buildbuy.pyi)
describe its input and return. The inspected archive hash is recorded in
[BUILD_DIRECT_TRANSFORM.md](BUILD_DIRECT_TRANSFORM.md). These source call
shapes do not establish current runtime compatibility, safety or catalog UI
semantics.

The trusted host supplies existing Build readers, a synchronous
`variant_group(definition_id)` provider and a separate reviewed
`require_build_variant(request)` admission. The exact request binds a
session/process/zone/save context, selected object, before position,
orientation, world surface and current and target definition IDs. The host
must establish explicit object, profile and operation scope, persist the
intent, and reconcile outcomes across restarts.

The executor checks Build mode, world surface, the parentless non-Sim object,
its existing definition and supplied before values. It reads both variant
groups twice and requires the same nonzero group, then resolves the same
object and calls `set_definition` exactly once. A native true return and
matching detached after-definition are reported as `dispatched`, still with
`swatch_verified: false` and `transaction_verified: false`. A false or
nonboolean return, changed definition, lost identity/context or exception
after dispatch is unresolved and must not be blindly retried. The bounded
in-memory ID set is per executor and is lost on restart. A host journal must
enforce cross-operation uniqueness and preserve uncertainty durably.

Sequential observations cannot freeze the lot or prove that a later game
save/reload preserves the appearance or component data. The host must
separately assess ownership, children, inventory and gameplay state.

```text
python -m unittest discover -s tests -p test_build_variant.py -v
python3.7 -m unittest discover -s tests -p test_build_variant.py -v
```

The tests use fabricated objects, groups and admission. They exercise group
and identity changes, denial, one dispatch, false/no-op results, errors after
effect and wrong-thread/reentry behavior. They are not a live swatch, undo or
save/reload test.

For follow-up on native undo, the installed game's generated
`BuildBuy_pb2.pyc` exposes a `BBUndoRedoState` message with `canUndo` and
`canRedo` fields. That observation is a state-message lead only; it does not
identify an input command, a transaction boundary, or a safe undo implementation.
