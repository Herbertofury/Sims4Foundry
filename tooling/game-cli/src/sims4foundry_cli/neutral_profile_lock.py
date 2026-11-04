"""Stable per-profile exclusion shared by neutral lifecycle and test-mod writers.

The lock coordinates cooperating commands, not arbitrary external filesystem
changes. Never delete or steal its sibling lock file.
"""
from contextlib import contextmanager
import os
import stat

from .errors import FoundryError
from .io import plain_path


def _lstat_optional(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _regular(info):
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, 'st_file_attributes', 0) & 0x400)


@contextmanager
def profile_lock(profile):
    # Stable sibling, independent of session and unaffected by profile renames.
    # Never unlink it: processes may already hold/wait on this inode.
    profile = plain_path(profile)
    path = plain_path(profile.parent / '.foundry-profile-lifecycle.lock')
    before = _lstat_optional(path)
    if before is not None and not _regular(before):
        raise FoundryError('invalid_profile_lock', 'Profile lock must be an unlinked regular file.')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(fd)
        current = plain_path(path).lstat()
        if (not _regular(opened) or not _regular(current)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                or (before is not None and (before.st_dev, before.st_ino)
                    != (opened.st_dev, opened.st_ino))):
            raise FoundryError('invalid_profile_lock', 'Profile lock identity changed.')
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise FoundryError('busy', 'Another command owns the profile lifecycle lock.') from error
        try:
            # Lock byte zero before initializing, including on an empty file.
            if opened.st_size == 0:
                os.write(fd, b'\0')
                os.fsync(fd)
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == 'nt':
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)



def directory_identity(path):
    """Observe an unlinked directory identity without treating access errors as absence."""
    info = plain_path(path).lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise FoundryError('linked_path', 'Linked directories are not accepted.')
    if not stat.S_ISDIR(info.st_mode):
        raise FoundryError('directory_changed', 'Expected an existing unlinked directory.')
    if not info.st_dev or not info.st_ino:
        raise FoundryError('identity_unavailable', 'Directory identity is unavailable.')
    return {'device': info.st_dev, 'inode': info.st_ino}
