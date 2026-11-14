# Capture PNG structure checks

The bounded capture inspector now enforces consecutive IDAT chunks and validates optional PLTE placement, uniqueness, length and applicability to the supported pixel formats. CRC-valid data with a decompressible pixel stream is not sufficient to excuse malformed critical-chunk structure.

Rules follow [W3C PNG Third Edition, critical-chunk ordering and PLTE](https://www.w3.org/TR/png-3/#11PLTE). The accepted subset remains non-interlaced 8-bit grayscale/truecolor, optionally with alpha; indexed-color support is not added.

Tiny generated regression fixtures include split data separated by text, duplicate/late/empty/misaligned/oversized palettes and forbidden grayscale palettes. Valid consecutive IDAT chunks, including empty chunks, a suggested truecolor palette, and trailing text remain accepted. Inputs are verified unchanged.

These checks do not establish image freshness, correct window, game semantics, native capture acceptance or general PNG conformance. The game-control foundation remains unqualified for live deployment.

## Receipt-to-image binding

The image dimensions and SHA-256 now come from the same bounded bytes that passed PNG validation. After the process/window identity recheck, the capture reads and validates the frame again and requires identical evidence before publishing completion. A malformed or changed frame retains the intent and output but no completion marker. This detects ordinary drift during verification, not a hostile-filesystem snapshot or protection against later edits after the receipt.

The selected capture helper is also hash-rechecked immediately before runner dispatch, after identity inspection and intent publication. Ordinary replacement during those steps refuses without running the changed file. This is not an OS-held execution-image lease: hostile swapping between the final check and process creation is not qualified.

Chunk type bytes must be ASCII letters per [W3C PNG section 5.3](https://www.w3.org/TR/png-3/#5Chunk-layout). CRC-valid digit, punctuation, NUL and non-ASCII types are rejected before interpreting their ancillary bit. Unknown ancillary letter-only chunks remain accepted; their payload semantics are not validated. The tests use tiny synthetic fixtures, not live capture evidence.
