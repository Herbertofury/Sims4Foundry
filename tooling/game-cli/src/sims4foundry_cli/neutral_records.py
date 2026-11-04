"""Bounded stable regular-file reads for neutral lifecycle evidence.

Reuse the existing no-link, descriptor-bound reader. Reading a byte-limited
stream alone is not sufficient: opening a FIFO can block before any JSON bytes
arrive. This module never compiles, executes, repairs or writes a record.
"""
from .companion_build import _regular
from .io import MAX_JSON, loads


def read_json(path):
    """Read one stable, unlinked regular file, then apply strict JSON decoding.

    Missing files and access failures propagate without another path/API or
    retry. Digests and schema checks belong to the caller; this is not evidence
    authentication or permission to act on the record.
    """
    raw, _digest, _identity = _regular(path, MAX_JSON)
    return loads(raw)
