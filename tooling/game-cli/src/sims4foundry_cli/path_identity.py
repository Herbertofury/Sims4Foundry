"""Conservative Win32 spelling comparisons, not filesystem identity proof."""
from pathlib import PureWindowsPath

_ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


def ascii_lower(value):
    """Never expand Unicode into another filename (for example ß into ss)."""
    return value.translate(_ASCII_LOWER)


def same_windows_path(first, second):
    """Allow ASCII case/separator differences; keep other spelling exact.

    This may refuse equivalent non-ASCII case variants. It never replaces
    retained process/file identity, executable hashes or reparse-point checks.
    """
    if (type(first) is not str or type(second) is not str or not first or not second
            or '\0' in first or '\0' in second):
        return False
    # PureWindowsPath preserves '..'. Do not resolve caller spellings across an
    # unexamined directory or broaden the existing process-path comparison.
    return ascii_lower(str(PureWindowsPath(first))) == ascii_lower(str(PureWindowsPath(second)))
