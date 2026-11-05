# Capture helper path pin

`capture` retains the explicitly selected `ffmpeg.exe` and every non-root
directory on its absolute local path while it starts the helper and checks the
capture. This closes the gap between hashing one file by path and letting the
operating system reopen a potentially replaced path for execution.

On Windows, Foundry opens each directory for attributes with no delete sharing
and opens the image for read with neither write nor delete sharing. Handles
are non-inheritable. Each opened directory must be a non-reparse directory;
the image must be an ordinary, single-link, non-reparse disk file on a fixed
local drive and within the byte bound. The pinned image is hashed through the
retained handle and compared to the caller's exact lowercase SHA-256. Parent
and image handles stay open until the helper exits or the runner returns, the
PNG has been inspected, and the target identity has been checked again. The
capture completion receipt is written only after handle cleanup succeeds.

Path sharing or access errors refuse before capture. If cleanup cannot be
confirmed, no completion receipt is written. A produced image may remain after
an incomplete capture and must not be treated as a successful receipt. Nothing
retries a helper automatically, changes permissions, elevates, or falls back to
another executable.

This pins the on-disk executable and the path components during the retained
interval under Windows sharing semantics. It does **not** authenticate the
executable's publisher, constrain DLLs, plugins, environment or configuration,
prove what code the child actually executed, or establish that a screenshot
represents the intended gameplay state. The target process/window checks and
their existing limits remain separate. A retained image is not authorization
for game input. The caller is responsible for selecting a trusted helper build.

`test_capture_pin.py` exercises fabricated metadata, reads, failures, pin
lifetimes and cleanup. Its Windows-only test creates an owned temporary
`ffmpeg.exe` fixture and verifies that writing, replacing the file, and
renaming its containing directory are refused while the pin is held. The file
is never executed. `test_windows_cli.py` uses a fake runner and fake observer
to check that the pin encloses dispatch and that failed cleanup does not create
a completion receipt. These tests do not touch or qualify the game, a real
FFmpeg build, WGC, process admission, or gameplay.
