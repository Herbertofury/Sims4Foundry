# Bounded offline script-archive inspection

`mods check`, `mods inventory`, and the validation used before never-in-place
staging inspect `.ts4script` files as ZIP containers. They do not import, execute,
extract or install their members. Passing inspection is not a compatibility,
malware-safety or game-performance verdict.

## Why a preflight is needed

CPython constructs the directory of a `ZipFile` during its constructor. Its
`_RealGetContents` reads the declared central-directory byte extent, then creates
`ZipInfo` objects while walking that extent. Checking `infolist()` afterward is
too late to constrain those allocations. This ordering was checked in
[CPython 3.11.0](https://github.com/python/cpython/blob/v3.11.0/Lib/zipfile.py)
and the installed CPython 3.12.14 source.

A tiny reproduction, not a large archive: three empty stored members named
`0.py`, `1.py`, `2.py` produced a 274-byte ZIP with 150 bytes of central metadata.
The constructor read those 150 bytes and created all three `ZipInfo` objects
before a hypothetical caller limit of two could run. The regression test uses
the same three-member/cap-two ordering with harmless, never-executed text payloads.

## Limits and validation order

The existing artifact-size ceiling is 512 MiB. Before constructing `ZipFile`:

1. Check the same open source handle against the observed artifact length.
2. Read at most the final 65,557 bytes, using bounded reads, to locate the last
   classic end-of-central-directory (EOCD) signature. Require a complete 22-byte
   record and an exact declared comment length ending at the observed EOF.
3. Require one disk, matching disk/total entry counts from 1 through 4,096, and
   at most **4 MiB of central-directory metadata**, including all file names,
   per-member comments and extra fields. These limits are checked before a
   central-directory allocation. EOCD fields must describe the exact directory
   extent immediately before the EOCD; no offset repair or prefix inference.
4. Retain that bounded metadata through EOF. Verify the retained end record and
   comment match the initial read. Walk its fixed central headers and bounded
   variable fields without creating `ZipInfo` objects. Count actual entries,
   independently of the declared count, and require exact agreement. Require
   each referenced local header to start at least 30 bytes before the directory,
   with the earliest referenced header at byte zero. Check member disk fields,
   ZIP64 sentinels and well-formed extra-field lengths. Reject central ZIP64
   extra fields before the stdlib could reinterpret sizes or offsets.
5. Give stdlib `ZipFile` a seekable, fixed-size view of the same source handle.
   All directory/end/comment reads use the immutable validated metadata, not a
   second read from a possibly changed file. Payload/local-header reads remain
   bounded to the original file extent, with explicit read-size caps. A generic
   read-to-EOF request cannot allocate the entire maximum-size artifact.

The 4 MiB bound is not a claim that total process memory is 4 MiB: the stdlib,
metadata copies, decoded names, the bounded `ZipInfo` collection and CRC buffers
also use memory. The important limits apply before reading declared metadata or
creating archive member objects. Filesystem reads are at most 1 MiB each; the
retained metadata is at most 4 MiB plus the 22-byte EOCD and 65,535-byte comment.

Existing checks then enforce portable paths, original-versus-sanitized names,
case-colliding duplicates, no links or encrypted members, a 32 MiB declared
expanded limit per member, 256 MiB in aggregate, and a 1,000:1 expansion ratio.
Stdlib member reading and CRC validation remain in place. Source-size checks
and the existing before/after bounded hashes reject observed source changes.
No output path, staging receipt or no-overwrite behavior is relaxed.

## Deliberate format subset

The inspector accepts stored (method 0) and Deflate (method 8) members only.
It refuses other methods **before constructing a decompressor**, including valid
BZIP2, LZMA and Zstandard ZIPs. Declared expanded-size limits alone do not bound
every decompressor's internal allocation. CPython's ZIP LZMA adapter decodes
filter properties supplied by the input; this inspector does not attempt to
validate that separate format. CPython uses a fixed raw-Deflate window for
method 8. See the
[CPython 3.12.0 ZIP implementation](https://github.com/python/cpython/blob/v3.12.0/Lib/zipfile/__init__.py)
and [Python 3.11 zlib documentation](https://docs.python.org/3.11/library/zlib.html).

This is this validator's bounded subset, not a claim about all Python versions
or Sims 4's importer. For example,
[Python 3.11's importer documentation](https://docs.python.org/3.11/library/zipimport.html)
notes that archive-comment support changed in Python 3.8. Successful offline
inspection cannot establish support in a particular game build.

Additional intentionally unsupported formats, including otherwise valid ZIPs:

- ZIP64 central-directory/end records, ZIP64 central extra fields and split or
  spanned archives. A `PK\x06\x07` signature at the stdlib's ZIP64-locator probe
  location is refused even if it occurs coincidentally in ordinary metadata.
- Prefixed archives whose first referenced local header is not at byte zero,
  and concatenated layouts requiring stdlib offset inference or repair.
- Central-directory digital-signature records, archive extra-data records
  within the declared central extent, and compressed/encrypted directories.
  That extent must contain only contiguous central file headers; records are
  never stripped to make the directory pass.
- Extra trailer bytes, truncated comments, or inconsistent counts/extents.
  Comments containing an EOCD signature may be refused because the inspector
  follows the stdlib's last-signature interpretation, without searching for a
  different earlier record to make the archive pass.

Normal archive/member comments, non-ZIP64 well-formed central extra fields and
data-descriptor members remain supported within these bounds. **Local-only
ZIP64 headers are a precise exception:** Python's `force_zip64=True` writer can
produce a ZIP64 local header while retaining classic central/end metadata. This
case is accepted when the frozen classic central directory supplies bounded
sizes and offsets. The stdlib reads the local extra field using its 16-bit
length; it does not substitute that local data for the validated central bounds.
This is not general ZIP64 support.

The preflight does not exhaustively parse the physical payload region. Gaps,
unreferenced local records and other opaque bytes there are not automatically
identified or rejected. A constructed concatenation that still has an exact
classic central extent and a referenced local header at byte zero is not ruled
out by those conditions alone. Stdlib member/CRC checks still apply.

The binary field layout and legal optional records are referenced from
[PKWARE APPNOTE 6.3.10](https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT),
sections 4.3.7, 4.3.9, 4.3.11–4.3.16 and 4.4.1. No archive bytes are rewritten to
fit the supported subset.

## Mutation and qualification limits

The frozen metadata prevents a changed EOCD, entry count, compression method or
central extent between preflight and `ZipFile` construction from reopening the
allocation gap. The fixed source extent prevents following a growing EOF.
Opening the archive view on the same handle avoids reopening a replaced path.
Observed growth, shrinkage and changed hashes still reject the artifact.

This is cooperative-filesystem validation, not an atomic full-file snapshot or
a hostile concurrent-writer guarantee. A malicious writer that changes and
restores bytes between observations is outside the claim. Local payloads are
not frozen, no runtime sandbox is supplied, and CPU/latency or peak-memory
benchmarks are not asserted. Keep the source quiescent during inspection.

## Regression coverage

Run from `tooling/game-cli`:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_script_archive_bounds.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_io_mods.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_mod_update_adversarial.py' -v
```

Fixtures are generated offline, generally hundreds of bytes. The maximum-comment
case is under 66 KiB. Low patched caps test exact/one-over entry and metadata
bounds without allocating large archives. Tests cover dishonest counts, record
extents, malformed extras, ZIP64 variants, split disks, prefixes, comments,
unsupported methods before decompression, malformed Deflate, local-only ZIP64, source growth,
same-size metadata drift, bounded short reads and the same-handle view. The path
replacement test is POSIX-only because Windows may reject replacing an open
source file; it is not claimed as Windows filesystem qualification. Existing
mod tests continue to cover path/CRC refusals and never-in-place staging.
