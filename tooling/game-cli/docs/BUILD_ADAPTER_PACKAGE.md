# Experimental Build adapter package

The standalone Build adapter can be compiled as a separate, uninstalled
`ts4script` archive. It imports the neutral `foundry_companion` package and has
no Apex dependency. Packaging it does not install it, start it, provide the
required owner-thread integration, or establish game compatibility. The current
adapters implement guarded direct object operations; they are not qualified
catalog purchasing, sales, swatches, placement transactions, undo, or redo.

From an installed checkout, select an independently trusted CPython 3.7 compiler
and its SHA-256 explicitly:

```text
foundry-build-adapter --source "tooling/game-cli/build-adapter-src" --output "NEW_EXTERNAL/FoundryBuildAdapter.ts4script" --python37 "TRUSTED_PYTHON37" --compiler-sha256 "EXPECTED_COMPILER_SHA256"
```

The builder accepts a fixed package source list, compiles without importing the
adapter, publishes without overwriting an existing archive, and writes a
separate manifest. An incomplete publication must be inspected at the original
paths rather than retried over them. The companion is separately built with
`foundry-game companion-build`; neither archive supplies automatic game startup
or an approved host, transport, admission policy, or observer. Fixture tests
use fake providers and a fake compiler; they do not establish actual game use.

`BuildJournal` is an experimental host library, not a game-control command. It
records an intent and attempt before a supplied trusted callback. A prepared or unresolved prior request prevents another journal request until
that original state can be inspected. There is currently no recovery mechanism
to advance a pending journal, and separate journals do not fence each other.
`returned` means only that the callback returned a bounded result: it is not
proof of game state or permission to proceed. The host remains responsible for
validating actual results and coordinating any dependent operation. Journal
files and lock semantics do not authenticate a hostile same-user process or
prove power-loss durability.
