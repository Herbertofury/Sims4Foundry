# Receipt-bound visual readback

`foundry-readback` compares the last frame of a completed
[foundry-record](WGC_RECORDER.md) run with an explicitly pinned PNG reference
for a selected rectangle. It requires the selected process/window identity,
the recording completion-receipt hash, the reference hash and a new output
file. It does not open the game, send input, depend on Apex or use Computer Use.

```text
foundry-readback --recording RECORDING_DIR --completion-sha256 RECEIPT_SHA256 --identity CURRENT_IDENTITY.json --reference REFERENCE.png --reference-sha256 REFERENCE_SHA256 --region X Y WIDTH HEIGHT --output NEW_READBACK.json
```

The optional `--max-channel-difference` (0–255) and
`--max-mismatched-pixels` allow explicit tolerance; both default to zero.
The reference image must be exactly the width and height of the selected
rectangle. The comparator handles non-interlaced 8-bit grayscale, grayscale
alpha, RGB and RGBA PNGs and the five PNG row filters. It rejects indexed,
animated and transparent-key PNGs rather than silently discarding their
semantics. The comparison is on raw RGBA channel values, not color-managed or
perceptual appearance.

The command checks the completion receipt's explicit hash, exact canonical
recording intent and frame metadata, the original process/window identity,
the selected frame's hash/dimensions, and the reference hash. Both images are
read as bounded bytes and compared from those exact checked bytes. A mismatch
is a valid observation and exits with status 1; malformed, inconsistent or
unavailable evidence exits with status 2. A matching region exits 0.

A matching region means only that those checked pixels met the specified
tolerance. It does not establish that an input caused the match, that the
reference is the correct UI state, that the game is still in that state, or
that a save, selection, transition or exit completed. The recorder's timestamp
is byte-receipt time, not independently verified game capture time. Receipt
hashes detect ordinary changes but are not authenticated; the host must retain
its own trusted record and provide a qualified expected-state interpretation
before using readback to acknowledge an input. In particular, an old matching
frame must not be used as evidence of a later request.

Fixture tests use fabricated PNGs, process identities and recording receipts.
They cover all five row filters, channel formats, match/mismatch/tolerance,
receipt and frame tampering, a different process lifetime, invalid files and
no-overwrite output. No current game UI or real WGC/FFmpeg capture was tested.
