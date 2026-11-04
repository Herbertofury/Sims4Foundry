# Offline script bytecode-header reports

```sh
foundry-game mods bytecode --file candidate.ts4script
foundry-game mods bytecode --file candidate.ts4script --target-magic 420d0d0a --output report.json
```

This opt-in command reports header bytes from one explicitly selected offline
`.ts4script`. It is useful for comparing an update candidate with **four target
bytes supplied by the caller**. It does not discover the installed game's Python
version, use the host interpreter's magic, locate/enumerate a game profile,
inspect a running process, or change the existing check, inventory, comparison or staging policy.

**Exit 0 and `ok: true` mean the bounded report was generated. They do not mean
target matching, header validity, importability, compatibility or safety.** A
mismatch, short header or unsupported layout is a successful observation with a
structured status. Argument, source-drift, ZIP/CRC, size, report-budget and output
failures produce the existing structured nonzero error exit. An update workflow
must inspect report fields rather than treating exit zero as a compatibility gate.

## Input and output

Only a regular, unlinked `.ts4script` is supported, with a case-insensitive file
extension. Standalone `.pyc`, `.package`, directories, observed symlinks and
Windows reparse points are refused. Existing portable-path checks apply.

`--target-magic` is optional. When supplied, it must be **exactly eight ASCII
hexadecimal characters in file-byte order**. Uppercase is accepted and normalized
to lowercase. No `0x`, whitespace, sign or separators are accepted. Validation
precedes all file I/O. A target is only comparison data: even a matching unknown
target does not grant a header layout or identify a runtime.

The JSON report contains:

- `schema: 1`, `kind: foundry_script_bytecode_headers`, source path/name, artifact
  byte size and the existing before/after-verified SHA-256.
- `member_count`, `bytecode_count`, `python_source_count`, `other_file_count` and
  `directory_count`. The latter four are mutually exclusive counts. Source
  selection uses a case-insensitive `.py` suffix; source contents are not parsed.
- `members` in original archive order. Every non-directory member whose original
  name ends in `.pyc`, case-insensitively, is a header candidate. Exact spelling is
  retained, including nested names, `.PYC` and `__pycache__` names. This selection
  does not assert an importer searches any of those names or infer a cache-tag
  version.
- For each candidate: `member`, `declared_bytes` from the central directory,
  `prefix_bytes`, at most 32 `prefix_hex` characters, `magic_hex`, `header_status`,
  nullable reference fields, `target_comparison` and a list of fixed-code findings.
  `declared_bytes` is not an independently measured full member length.
- `target_magic_hex` and a `comparison` object with `match_count`,
  `mismatch_count`, `unavailable_count` and `status`.

Comparison precedence is:

1. No target: `not_requested`; all three comparison counts are zero.
2. Target but no candidates: `no_bytecode`, never a vacuous match.
3. At least one different four-byte magic: `mismatch`, retaining any unavailable
   count too.
4. No mismatch but at least one fewer-than-four-byte prefix: `incomplete`.
5. At least one candidate and all four-byte comparisons equal: `match`.

A matching four-byte prefix can coexist with a truncated reference header or
unsupported layout. Raw equality is independent of header interpretation.

The report explicitly keeps `code_executed`, `members_extracted`,
`marshal_body_validated`, `source_hash_verified`, `importability_verified`,
`compatibility_verified` and `game_modified` **false**. These are limits on what
was done or verified, not proof that the candidate is incompatible. No trusted
live-qualification fields are created.

Without `--output`, the report goes to stdout. A requested output uses existing
external-workspace guards and durable no-overwrite JSON publication. It cannot
overwrite an existing file or publish inside a likely game/profile directory.
The output file is compact ASCII JSON; stdout is indented ASCII JSON. Both are
limited using the larger indented representation before either is published.

## Deliberately narrow interpretation

The only supported magic is `420d0d0a`, interpreted against the pinned
[CPython v3.7.17 importlib classifier](https://github.com/python/cpython/blob/v3.7.17/Lib/importlib/_bootstrap_external.py#L438-L468)
and its
[source-loader branch](https://github.com/python/cpython/blob/v3.7.17/Lib/importlib/_bootstrap_external.py#L826-L849).
The report calls this reference `cpython_3_7_17_importlib`. The magic comes from
the release's
[source constant](https://github.com/python/cpython/blob/v3.7.17/Lib/importlib/_bootstrap_external.py#L245-L260),
not an exact-patch fingerprint or evidence about Sims 4's embedded interpreter.
[PEP 552](https://peps.python.org/pep-0552/#specification) describes the four-word
header, but actual loader behavior must be distinguished from that specification.

- 0–3 retained bytes: `truncated_magic`, no padded/invented magic.
- Four or more bytes with unknown magic: `unsupported_layout`. Remaining bytes
  stay uninterpreted. This includes a 12-byte legacy-looking header; older layouts
  are not automatically malformed for being shorter than 16 bytes. For example,
  [CPython v3.6.15 importlib](https://github.com/python/cpython/blob/v3.6.15/Lib/importlib/_bootstrap_external.py)
  used a 12-byte layout.
- Supported magic with 4–15 bytes: `truncated_reference_header`.
- Supported magic with 16 bytes: `reference_header_complete`. This describes
  presence of the reference fields, not validity of the file or its body.

For a complete reference header, flags are read as an unsigned little-endian
32-bit value, with `hash_based_bit` and `check_source_bit` reported separately.
Flags outside the reference mask `3` produce `outside_reference_flag_mask`; the
last eight bytes receive no semantic decoding. This is not a universal rejection
claim about every Python importer.

For flags inside that mask:

- `0`: `timestamp`, with raw unsigned `mtime_u32` and `source_size_u32`. There is
  no timezone conversion or freshness assertion.
- `1`: `unchecked_hash`, with eight raw `source_hash_hex` bytes.
- `3`: `checked_hash`, with eight raw `source_hash_hex` bytes. This label describes
  the reference flag interpretation only; no source hash is actually verified.
- `2`: `timestamp_bit_clear`, with the same two raw timestamp words and a
  `flags_2_loader_divergence` finding. Bit zero is clear, so the reference importlib
  follows its timestamp path despite bit one being set. It is neither outside
  that mask nor a checked-hash classification.

The pinned
[CPython v3.7.17 ZIP importer](https://github.com/python/cpython/blob/v3.7.17/Modules/zipimport.c#L1327-L1384)
has different behavior: its hash branch uses any nonzero flags. In default mode
only flags `1` pass that branch; `2` and `3` fall back. Its `always` and `never`
hash-check modes differ again. None of these conditional checks proves a complete
import would succeed. An importer can also fall back to source after a magic
mismatch. The report therefore does not declare the archive importable or
unimportable from its selected header candidates.

The marshal body is never deserialized, imported, executed, compiled, decompiled
or extracted to disk. The
[pinned marshal documentation](https://github.com/python/cpython/blob/v3.7.17/Doc/library/marshal.rst)
warns against unmarshalling untrusted data. No such operation is needed here.
Even a 16-byte header with no body or arbitrary opaque bytes can yield `match`.
Header equality cannot establish API compatibility, dependencies, source
freshness, gameplay behavior or absence of malicious code.

## Validation order and resource limits

The command shares the exact bounded inspector used by ordinary artifact checks.
See [SCRIPT_ARCHIVE.md](SCRIPT_ARCHIVE.md) for the existing classic-ZIP subset,
pre-parser allocation bounds and refusal details. Those constraints remain:

- At most 512 MiB artifact bytes, 4 MiB central metadata and 4,096 total entries.
- Stored and Deflate only, with fixed validated metadata over the original open
  source handle. No archive reopen for a separate header parser.
- Portable original names, no aliases/case collisions, links or encryption.
- At most 32 MiB declared expanded bytes per member, 256 MiB aggregate and a
  1,000:1 declared expansion ratio.

The full existing `ZipFile.testzip()` traversal completes **before** any selected
prefix is collected. A corrupt tail or later non-PYC member refuses the whole
report. The inspector then uses the same still-open `ZipFile`, frozen metadata,
original `ZipInfo` objects and source handle to read at most 16 retained bytes per
candidate. Short positive reads are retried within the remaining prefix length;
an unexpected early EOF refuses. The original stream size check and before/after
artifact hash/size checks occur **after** prefix collection and before return or
publication. Ordinary persistent changes between passes are rejected.

This is deliberately two passes: selected members may be opened/decompressed
again after full CRC validation. The prefix pass does not independently verify a
long member's whole CRC. `read(16)` is an **application-retention bound**, not a
16-byte disk-I/O or decompression guarantee. For example,
[CPython v3.11.0 ZipExtFile](https://github.com/python/cpython/blob/v3.11.0/Lib/zipfile.py)
uses a minimum internal read size of 4,096 bytes. A compressed stream may require
more input to emit a small prefix. There is no CPU-time or latency guarantee.

Additional report-only limits are checked against the existing bounded member
collection **before** creating the result rows or opening prefix readers:

- At most **1,024 PYC candidates**, therefore at most 16,384 retained binary
  prefix bytes.
- At most **1,024 Unicode code points per candidate name**, checked before name
  encoding; at most **4,096 UTF-8 bytes** and **4,096 ASCII-JSON bytes** per name.
- At most **256 KiB cumulative UTF-8 name bytes** and independently **256 KiB
  cumulative ASCII-JSON name bytes**. JSON name counts include quotes and escapes.
- At most **4,096 code points in the absolute source path**, checked before the
  source is inspected or copied into the report.
- At most **4 MiB for actual indented ASCII JSON plus its terminating newline**,
  checked before stdout or optional file publication.

Limits refuse rather than truncating names or returning partial rows. A file
accepted by ordinary `mods check` may exceed a narrower report-only limit; this
does not change ordinary inspection, inventory or staging acceptance. Memory
also includes metadata, decoded names, stdlib buffers and Python objects; these
budgets are not a total-process-memory promise.

The source checks detect ordinary cooperative-filesystem changes, not an atomic
snapshot or protection against adversarial change-and-restore races. This report
is offline update evidence only and does not qualify any game control.

## Tests

`tests/test_bytecode_headers.py` uses tiny generated stored/Deflate archives,
opaque bodies and header-only reference fixtures. Golden prefixes were generated
with official CPython 3.7.17 from an original harmless 67-byte source; no candidate
was compiled and no compiled blob is checked into the repository. Tests cover
every retained length from 0 through 16, flags, unknown layouts, independent raw
comparison, source-only archives, same-view CRC ordering, short reads, corrupt
tails, later non-PYC CRC failures, persistent source drift, exact/one-over budgets,
existing archive refusals, no execution/extraction and CLI publication guards.

Run the focused tests together with the existing archive/mod-update regressions,
then the complete suite. Tests do not require a Sims installation or real mods.
