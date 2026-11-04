# Build the standalone neutral companion

`foundry-game companion-build` creates a real, deterministic `.ts4script` archive
and a separate JSON completion manifest from this project's `companion-src`.
It does not require Apex sources, libraries, an Apex installation, a running game
or a Sims profile. It does not install anything or execute companion code.

This is a **development archive, not a live-qualified mod**. The archive contains
the neutral queue, protocol, transport, observation, native boundary and explicit
owner-runtime components. It contains no automatic startup bootstrap, production
profile-admission provider, credentials or automatically activated listener.
Building it does not supply missing CAS/Live/Build/gameplay capabilities.

## Explicit compiler and external output

The host CLI uses Python 3.11 or newer. Bytecode compilation requires an explicitly
selected **trusted CPython 3.7 executable** with magic `420d0d0a`, and an explicit
SHA-256 pin for that executable. There is no interpreter download, PATH lookup,
default compiler, shell command, silent version fallback or profile installation.
Use an isolated compiler acquired from an official Python source and verify its
provenance independently. Python 3.7 is end-of-life; this compiler is a local build
tool, not a network service or general-purpose replacement for a current Python.

From `tooling/game-cli`, after installing the CLI or setting `PYTHONPATH=src`:

```sh
foundry-game companion-build \
  --source ./companion-src \
  --output /external/foundry-builds/release-001/FoundryCompanion.ts4script \
  --python37 /trusted/python37/python \
  --compiler-sha256 EXPECTED_64_HEX_DIGIT_SHA256
```

`--source`, `--output`, `--python37` and `--compiler-sha256` are required.
An optional `--manifest-output /external/evidence/release-001.json` selects a new
manifest path. Otherwise the manifest is adjacent to the archive, named
`FoundryCompanion.ts4script.manifest.json`.

Both outputs must be new, ordinary paths outside the source tree and every game
or user-profile directory. Existing output files are never overwritten. The
builder rejects observed links/reparse points, recognizable game/profile path
names, profile-content names (`Mods`, `saves`, `Tray`) and observed profile markers
in ancestors. Marker detection cannot recognize every deliberately renamed or
empty profile: select a known external build workspace, not an active profile.

To call the same implementation from Python:

```python
from sims4foundry_cli.companion_build import build_companion

manifest = build_companion(
    source_root="./companion-src",
    output="/external/builds/release-001/FoundryCompanion.ts4script",
    python37="/trusted/python37/python",
    compiler_sha256="EXPECTED_64_HEX_DIGIT_SHA256",
    manifest_output=None,
)
```

The pin is checked before a process starts, then checked again immediately before
launch. The builder checks the executable's observed identity after compilation.
The executable digest is **not** a signature or a hash of its standard library,
shared libraries, dependent DLLs or vendor provenance. The complete compiler
installation and filesystem remain trusted inputs. A pin alone cannot make an
unknown executable safe. This is not a sandbox against a malicious runtime or an
attacker racing the local filesystem.

## Accepted sources and deterministic bytes

The source root must contain only `foundry_companion/`, with this fixed allowlist:

- `__init__.py`
- `build_observation.py`
- `build_native_readers.py`
- `build_transform.py`
- `build_create.py`
- `build_destroy.py`
- `build_variant.py`
- `build_executor.py`
- `build_request.py`
- `build_schema.py`
- `build_wire.py`
- `live_executor.py`
- `live_observation.py`
- `live_schema.py`
- `live_wire.py`
- `native.py`
- `owner_runtime.py`
- `peer_auth.py`
- `queue.py`
- `transport.py`
- `wire.py`

Missing modules, additional source files, nested packages, special files, hard
links, symlinks and reparse points are refused. A regular `__pycache__` directory
may be present but is never traversed, read or included. No candidate archives or
historical Apex sources are read by the build operation. The allowlist constrains
module ownership; it is not a semantic audit of caller-modified source content.

The trusted compiler driver calls `compile()` with package-relative filenames,
`dont_inherit=True`, and `optimize=0`. It never imports or executes those code
objects. Python 3.7 itself checks source syntax. The archive contains all 21 source
files and their 21 adjacent `.pyc` files, for 42 members in sorted order.
Every generated `.pyc` must have:

- CPython magic `420d0d0a`.
- PEP 552 checked-hash flags `03000000`.
- An eight-byte CPython source hash in the remainder of its 16-byte header.
- A nonempty marshalled code body produced by the selected compiler.

The parent validates exact member ownership, source SHA-256 bindings, header
fields and compiler-response bounds. It does not import or unmarshal returned
bytecode. The selected trusted compiler generates the body and source hash;
header checks alone would not prove that an untrusted compiler produced valid
bytecode.

`PYTHONHASHSEED=0` is fixed. The child uses `-B -S -s`, a fresh temporary working
directory, and a minimal environment that excludes inherited Python paths,
startup hooks, dynamic-loader hooks, credentials and proxy configuration.
Windows receives only the required system-root variables and a compiler-local
PATH in addition to the fixed settings. An observed compiler `_pth` file is
refused because its environment isolation can ignore the required hash seed.

ZIP entries use stored bytes rather than Deflate, fixed 1980-01-01 timestamps,
fixed regular-file permissions, no comments and no extra fields. This avoids
zlib-version changes as well as input paths and modification times. Matching
source bytes, compiler/runtime and driver produce identical archive bytes;
source or compiler changes deliberately change the evidence. The manifest is
also deterministic when the archive basename remains the same.

## Bounds and publication

| Input or operation | Limit |
| --- | ---: |
| Entries examined in either source directory | 64 |
| One source file | 256 KiB |
| All retained source bytes | 1 MiB |
| Compiler executable hashing | 64 MiB |
| One compiled module | 512 KiB |
| All compiled modules | 2 MiB |
| Compiler stdin/stdout JSON | 4 MiB each |
| Compiler stderr | 64 KiB, with at most 4 KiB in a diagnostic |
| Complete archive | 4 MiB |
| Compilation wall-clock wait | 60 seconds |
| POSIX compiler CPU / address space | 45 seconds / 512 MiB |

Pipe readers retain only bounded output and kill the compiler on overflow. The
parent kills a timed-out child and allows bounded cleanup waits. POSIX resource
limits apply in the trusted driver; Windows does not have that POSIX CPU/address
space limiter. The wall-clock timeout and input/output bounds apply on both.
Ordinary filesystem access is not claimed to have a hard latency guarantee.

Source and compiler reads are bounded to the observed file size, with one extra
probe for growth, and check identity/size/times before and after reading. The
archive's exact stored-ZIP size is checked before allocation. The completed
in-memory archive is read back for member names, CRCs and exact member bytes.

Outputs use exclusive, same-directory publication of fully written/fsynced
temporary files. There is no overwrite fallback. The archive is published first;
the separate manifest is the completion marker. This is **not a two-file atomic
transaction**. If manifest publication fails, the archive may remain, and the
operation reports `build_publication_incomplete`. A failure while syncing a
published file can also leave that destination present. Do not label such a
build complete or blindly retry the same filenames. Inspect the reported paths
and use a fresh release directory for a new build. A concurrent writer is never
overwritten.

The manifest records each source and compiled-module SHA-256 and byte count, the
archive SHA-256, the compiler-executable and driver SHA-256, the Python version,
magic, hash-seed and invalidation settings. It explicitly reports
`installed: false`, `profile_modified: false`, `game_contacted: false`,
`companion_code_executed: false` and `live_qualified: false`.

## Tests and evidence

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_companion_build.py -v

FOUNDRY_COMPANION_PYTHON37=/trusted/python37/python \
  PYTHONPATH=src python -m unittest discover -s tests -p test_companion_build.py -v
```

Without the explicit test interpreter, the real-runtime cases are skipped and
reported as such. Mock-compiler tests cover exact source selection, links,
source/compiler/output limits, protected destinations, existing and concurrently
created outputs, interrupted publication, malformed compiler replies, wrong
bytecode headers, subprocess timeout/overflow and environment sanitization.
The actual-runtime cases compile the complete neutral source set, compare repeat
archive bytes with different ambient environment values, and reject syntax not
accepted by Python 3.7. Top-level fixture code raises if executed, so successful
compilation also verifies that the compiler did not execute it.

The isolated official-source CPython 3.7.17 compiler used for local verification
on 2026-11-04 had executable SHA-256
`88becce0e5fe698cd9c73632bebf8f7e4592c243c2b16f48dfc1086bd28f47c7`.
This local build hash is evidence for that executable, not a universal Python
download hash or a compiler pin to copy onto another machine.

The previous read-only script-build reference supplied algorithm evidence for
checked-hash bytecode and relative filenames. This builder is a separate neutral
implementation; it does not import that builder, copy its mod source, port Apex
packages, run candidate code or reuse an existing `.ts4script` payload.

Remaining gates include a reviewed startup activation path, explicit production
host/profile admission, game-loader qualification and controlled gameplay tests.
ZIP validity and deterministic compilation establish none of those gates.

## Packaged Build adapters

The five Build reader/direct-operation modules come from the verified public
`9bf905924c92183fa73975c4b0dd11ea0bcc44f5` checkpoint. Imports are package-relative,
and each module records its source blob. Sequential running/Build flag rechecks
reject observed changes; they are not atomic snapshot guarantees.

These direct adapters alone do not supply profile admission or a bootstrap.
The separately integrated schema-4 capture/transform route is documented in
[BUILD_TRANSFORM_ROUTE.md](BUILD_TRANSFORM_ROUTE.md).
Create is not purchase/placement, destroy is not sale, and definition changes are
not qualified swatches. The direct transform does not retain original selection
lifetime or supply durable history by itself. Packaging does not qualify any
native callback or demonstrate actual game execution.
