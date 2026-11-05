# WGC frame-stream recording

`foundry-record` captures a bounded sequence of frames from an explicitly
identified Windows game window using a selected, hash-pinned FFmpeg build with
the `gfxcapture` filter. It does not send input, use Apex, or use Computer Use.
The existing Foundry process/window identity file and explicit FFmpeg path and
SHA-256 are required. The output is a new external directory.

```text
foundry-record --identity CURRENT_IDENTITY.json --ffmpeg C:\\PATH\\ffmpeg.exe --ffmpeg-sha256 EXPECTED_SHA256 --output NEW_EXTERNAL_DIRECTORY --frames 3 --seconds 10
```

For a source checkout without installing the console entry point, use
`python -m sims4foundry_cli.wgc_record_cli` with the same options. The
command writes an intent, up to ten distinct stream frames with per-frame
hash/size metadata, and a completion receipt only after the owned helper and
reader have closed. A partial frame or cleanup failure is reported as
incomplete or unresolved; existing output is never overwritten. An
interrupted run can leave partial evidence and is not automatically retried.

`WgcRecorder` is also available to a trusted host as a single-use Python
component. It holds the selected FFmpeg image/path pin through the helper
lifetime, starts no helper until explicitly requested, and retains only the
latest bounded frame. PNG framing and CRC are checked on the background reader;
the CLI additionally validates the complete PNG and its hash before recording
each frame. Startup has a five-second wait; if process creation returns late
after cancellation, the worker attempts to retire that same owned child.
Failure to confirm cleanup remains unresolved. The recorder never terminates a
game or another caller's process.

The frame's monotonic timestamp is when bytes were **received**, not an
independently verified game capture/presentation time. A stream can buffer,
repeat or freeze while still delivering valid frames. Matching PID/window
observations and PNG validity do not prove that a UI action occurred, establish
an HWND lifetime through every race, or supply semantic readback. A future
input integration must separately establish recording freshness, live window
ownership and the intended visual or in-game result. The existing public
native input backend remains unavailable until that integration is implemented.

Fixture coverage exercises framing, CRC/truncation, changed identity, late
startup, stale frames, cleanup failure, a real **owned Python fixture child**
and CLI output/interrupt handling. It does not invoke real FFmpeg or access a
game/profile. An isolated editable install verified the `foundry-record --help`
entry point; the Blender-supplied test Python printed an unrelated missing
`blender.shared` sitecustomize warning while the install and help command
completed. No actual-game WGC or UI qualification is claimed.
