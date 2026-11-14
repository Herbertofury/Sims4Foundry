# Staging directory publication

External staging flushes copied file contents, verifies their size/hash, then synchronizes affected directories child-first before publishing `staging-complete.json`. This includes intermediate artifact directories, the destination, any newly created destination ancestors, and the existing ancestor containing the first created directory. A directory-sync failure retains intent and copied evidence without a completion receipt; no automatic deletion or replay occurs. Planning creates and synchronizes nothing.

The shared `sync_directory` primitive uses POSIX directory fsync and remains a no-op on Windows. This is an ordering improvement, not Windows directory-durability qualification, a hostile-filesystem guarantee, or an experimentally proven power-loss guarantee. Tests observe ordering and injected failures on tiny offline artifacts. Native game/profile state is untouched.
