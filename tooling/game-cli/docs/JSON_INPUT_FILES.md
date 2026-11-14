# JSON input file boundary

The shared JSON file reader requires a regular file before opening it and rechecks the opened descriptor before reading. Observed FIFOs, devices and directories are refused with `invalid_json_file`, rather than potentially blocking while opening/reading a stream. Existing no-linked-path checks, 4 MiB read limit and strict JSON parsing remain unchanged; missing files continue to surface as filesystem errors.

The preflight and descriptor checks are ordinary-file validation, not a hostile-filesystem atomic-open guarantee. A replacement between preflight and open can still race. Regular network-backed files may also block on I/O; no wall-clock timeout is claimed. Tests cover an actual POSIX FIFO without opening it, directories, a synthetic mismatched descriptor and strict regular-file parsing. No native game or profile was touched.
