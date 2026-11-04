# Opaque package resource comparison

`foundry-game mods resource-diff --before old.package --after candidate.package`

Optionally add `--output report.json` to publish a new external JSON file. The
command requires two explicitly selected `.package` paths. It does not discover
a profile, fetch an update, install, stage, extract, decompress, interpret or
execute candidate content. The same path or a hard-link alias may be selected
on both sides: this is a valid version comparison, not two resource owners.
The separate `mods overlaps` command still refuses duplicate source inputs.

Exit **0** means a complete bounded report was generated, including when changes
were found. It does not mean unchanged, compatible or safe to install. Structured
inspection/output failures exit **2**. Missing required arguments and unknown
options are argparse usage errors, also exiting 2.

## What is compared

Resources are matched by the complete exact type/group/instance key, written as
fixed-width uppercase hexadecimal `TTTTTTTT:GGGGGGGG:IIIIIIIIIIIIIIII`. Filenames,
basenames, index order and resource offsets do not determine identity. The
before/after orientation is exactly the orientation supplied on the command line.

The schema-1 `foundry_package_resource_diff` report contains:

- `before` and `after`: source path, filename, source length/SHA-256/stat evidence,
  DBPF version, normalized index metadata and resource count. Complete resource
  arrays from the existing inspections are not embedded.
- `package_bytes_equal`: equality of whole-source SHA-256 **and** length.
  `equality_basis` is `sha256_and_length`.
- `index_layout_changed`: whether normalized index metadata differs, including
  its offset, size, position field, count and shared flags/fields. A mere index
  record reorder need not change this field; per-resource layout flags record it.
- `added` and `removed`: sorted rows for keys present on only one side. Each row
  contains its TGI and the present side's snapshot.
- `changed`: sorted common-key rows with independent `stored_bytes_changed`,
  `metadata_changed` and `layout_changed` booleans, plus both snapshots.
- Counts for the before/after resources, added, removed, shared, changed,
  unchanged, stored-byte changes, metadata changes and layout changes.

A snapshot contains `stored_sha256`, `stored_bytes`, `memory_bytes`,
`compression`, `extended_compression`, `committed`, `offset`, `entry`,
`index_entry_offset` and `index_entry_bytes`.

The three common-key classifications are independent:

| Field | Comparison |
| --- | --- |
| `stored_bytes_changed` | Stored length or stored-range SHA-256 differs. |
| `metadata_changed` | Memory size, raw normalized compression marker, extended-word presence or committed marker differs. `null`, 0 and 1 remain distinct. |
| `layout_changed` | Body offset, entry ordinal, index-entry offset or index-entry width differs. |

For example, moving a body with identical stored bytes is a layout change, not a
stored-byte change. Changing an opaque body and its memory-size field can set
both stored-byte and metadata flags. Shared-index packing and row reordering
can change resource layout without changing stored bytes.

Common keys with none of these changes have no row body in `changed`, but are
included in `unchanged_count`. Added/removed resources are not counted as changed
common resources. Count equations are:

```
before_count = removed_count + shared_count
after_count = added_count + shared_count
shared_count = changed_count + unchanged_count
```

The three classification counts overlap and must not be added together to infer
`changed_count`. Arrays are sorted by TGI. Reports contain no observation time or
random ID. Repeated inspection of identical paths and source state is stable;
copying files elsewhere changes the intentionally retained path/stat evidence.

## Interpretation limits

Bodies are opaque **stored bytes**, even for recognized compression markers.
Invalid compressed data can be hashed successfully because no decompressor is
called. A different compressed representation is a stored-byte change even if
its hypothetical decoded result would be identical. Hash-and-length equality
does not prove decoded equality, compatibility, a safe update or a pure repack.

The parser interprets only its supported header/index subset. Unexposed header
fields and unindexed gaps or trailing bytes are not interpreted. Consequently,
whole-package hashes may differ while all resource rows are unchanged. That is
reported as exactly those observations, never as a pure-repacking verdict.

`body_handling` is `opaque_stored_ranges_sha256`. The report explicitly sets
`resource_contents_validated`, `decoded_bytes_compared`, `decompressed`,
`code_executed`, `game_modified`, `compatibility_verified` and
`load_order_inferred` to false. It supplies no live qualification, execution
approval or installation admission. Existing `mods resources`, `mods overlaps`,
inventory `mods compare`, signature `mods check` and staging retain their
separate behavior and schemas.

## Source binding and bounded work

Both inputs are preflighted before either is hashed. Each uses the existing
regular-file/path protections and narrow DBPF 2.1 parser: no observed symlink or
Windows reparse-point traversal; no duplicate TGI, overlapping nonempty ranges,
unknown/deleted resource encoding or unsupported header/index layout.

The parser retains one original source handle for header/index reads and two
whole-source SHA-256 passes. Only after all index rows validate does it create a
private range collector. On the existing second full-source pass, bounded
sequential chunks are supplied with their absolute offsets. The collector has
no source path or stream, never seeks/reopens/reads, retains no body chunks, and
uses a sorted range cursor with at most one active nonempty resource hash. Zero
extents receive SHA-256 of empty bytes without per-range reads; their accepted
offsets may coincide with the index, EOF or another resource.

The second pass also confirms that its header/index slices match the bytes
already parsed. Collector results are accepted only after EOF, descriptor/path
stamp checks and equality of both whole-file hashes. The complete report is
then size-checked, and both source snapshots are verified again before returning
or publishing it. This catches an earlier source changing while the later source
is inspected, as well as ordinary source drift during report construction.

These checks detect ordinary persistent concurrent edits, not a hostile-
filesystem atomic snapshot or a general change-and-restore guarantee. The
metadata-slice check binds the parsed header/index to the hash-producing pass;
it does not eliminate all possible concurrent filesystem races.

Existing bounds remain:

- 512 MiB per package and per declared stored/memory resource size.
- 1 MiB of index bytes and 8,192 entries **per package**.
- Two selected inputs, hence at most 1 GiB of source data and 16,384 input rows.
  There is no additional combined-entry cap.
- The actual complete `indent=2`, `ensure_ascii=True` JSON representation **plus
  its final newline** must fit 4 MiB. An oversized changed report refuses with
  `oversized_json`, without truncation, partial results or an output file.
  Two maximum-entry unchanged packages can still produce a small valid report.

The collector adds no body I/O pass. Each input has two original-handle full
hashes and a final snapshot-verification pass, plus bounded header/index reads
and EOF probes. Two maximum-size inputs can therefore require roughly 3 GiB of
source I/O. This is not a single-pass inspection, latency guarantee or 4 MiB
total-process-memory guarantee; retained validated rows and report serialization
can use more memory than the output budget.

Optional output uses the existing external-path guard and durable new-file
publication. It never replaces an existing destination, including a source
package, and never writes into a protected game/profile directory. Source
verification and size checks precede output creation and stdout success. Reports
contain local paths and source metadata; review them before sharing.

## Offline verification

`tests/test_resource_diff.py` uses small generated DBPF wire fixtures and patched
bounds. It covers classifications/counts, all exact key parts, all shared-index
flag combinations, mixed entry widths, index-first/legacy layouts, row reorder,
zero extents, opaque compression markers, short/boundary reads, path/parser
refusals, source drift and temporary-index restoration, same-path/hardlink
comparisons, 8,192-entry cases, actual JSON limits, unchanged public inspection
behavior and CLI publication protections. The maximum-entry fixtures are small
index files, not multi-GiB packages. This is offline evidence, not Sims 4 runtime
qualification.

Run from `tooling/game-cli` after the normal editable installation:

```
python -m unittest discover -s tests -p test_resource_diff.py -v
python -m unittest discover -s tests -p test_package_resources.py -v
python -m compileall -q src
```
