# Owned-window WGC timestamp experiment

The opt-in `FOUNDRY_WGC_TEE_PROBE=1` fixture controls only its own briefly
visible test window and an explicitly hash-pinned FFmpeg child. It sends an
allowlisted click to that window, captures PNG packets through WGC, and uses
FFmpeg's tee muxer to emit a SHA-256 framehash row and PTS for each same
encoded packet. The fixture checks each packet's hash, size and sequence,
decodes the center pixels, and compares a known marker-to-click QPC bracket
with the PTS interval from the marker frame to a later repaint frame.

On the local Windows fixture, the successful run observed 360 matched PNG
and framehash packets with time base 1/10000000. The selected marker-to-repaint
PTS interval was 2.4514143 seconds; the QPC marker-to-post bracket was
0.0391594 seconds, with an additional 0.05-second margin. The test window's
callback received the matching down/up messages. Earlier versions of the
fixture buffered all sampled frames before posting; those runs correctly failed
to find a later repaint. Their FFmpeg children exited and pins were released.

The time interpretation is conditional. Microsoft describes WGC's
[SystemRelativeTime](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.direct3d11captureframe.systemrelativetime)
as compositor QPC time; the reviewed [FFmpeg n9.0 source](https://github.com/FFmpeg/FFmpeg/blob/n9.0/libavfilter/vsrc_gfxcapture_winrt.cpp)
uses that frame timestamp and subtracts its first timestamp. Hashing a locally
installed executable does not prove it was built from that source. Tee/encoder
timestamp handling, clock assumptions, exclusive input, the marker's semantics,
and behavior in Sims require independent qualification. This fixture does not
authenticate an observer, acknowledge a journal, or prove a game action or save.
It does not access the game or profile. The production PNG recorder still drops
packet timestamps and has not been replaced by this experiment.
