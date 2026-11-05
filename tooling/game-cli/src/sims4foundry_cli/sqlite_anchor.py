"""Cooperative local SQLite head anchor for experimental registry testing.

This provides atomic cross-process compare-and-swap and durable SQLite commits.
It does not prevent same-user modification, deletion, replacement or rollback.
It does not meet SessionRegistry's independently protected anchor requirement.
No database is opened or created until explicitly requested by a caller.
"""
import re
import sqlite3

from .errors import FoundryError
from .io import external_path

HASH = re.compile(r'[0-9a-f]{64}\Z')
SCHEMA = 'foundry-cooperative-anchor-1'


def _hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


class SQLiteAnchor:
    """Explicit database for cooperative clients; not a security authority."""
    def __init__(self, path):
        self.path = external_path(path)
        if not self.path.is_file() or self.path.is_symlink():
            raise FoundryError('anchor_unavailable', 'The existing anchor database is required.')

    @classmethod
    def create(cls, path):
        path = external_path(path)
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise FoundryError('anchor_exists', 'Use a new anchor database in an existing directory.')
        # Reserve the name exclusively before SQLite opens it. Never overwrite.
        with path.open('xb'):
            pass
        try:
            connection = sqlite3.connect(str(path), timeout=0, isolation_level=None)
            try:
                connection.execute('PRAGMA synchronous=FULL')
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
                connection.execute('CREATE TABLE heads (scope TEXT PRIMARY KEY, head TEXT NOT NULL)')
                connection.execute('INSERT INTO metadata VALUES (?, ?)', ('schema', SCHEMA))
                connection.commit()
            finally:
                connection.close()
        except Exception:
            # Retain the reserved/partial file for explicit reconciliation.
            raise FoundryError('anchor_create_unresolved', 'Anchor creation did not complete; inspect the original file.') from None
        return cls(path)

    def _connect(self):
        # mode=rw refuses to silently recreate an absent or renamed database.
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=0, isolation_level=None)
            connection.execute('PRAGMA synchronous=FULL')
            row = connection.execute('SELECT value FROM metadata WHERE name = ?', ('schema',)).fetchone()
            if row != (SCHEMA,):
                raise FoundryError('anchor_invalid', 'Anchor schema is unavailable or changed.')
            return connection
        except FoundryError:
            if connection is not None:
                connection.close()
            raise
        except Exception:
            if connection is not None:
                connection.close()
            raise FoundryError('anchor_unavailable', 'Anchor database is unavailable.') from None

    def read(self, scope):
        if not _hash(scope):
            raise FoundryError('anchor_scope', 'An exact lifetime digest is required.')
        connection = self._connect()
        try:
            row = connection.execute('SELECT head FROM heads WHERE scope = ?', (scope,)).fetchone()
            if row is None:
                return None
            if len(row) != 1 or not _hash(row[0]):
                raise FoundryError('anchor_invalid', 'Anchor head is invalid.')
            return row[0]
        except FoundryError:
            raise
        except Exception:
            raise FoundryError('anchor_unavailable', 'Anchor head is unavailable.') from None
        finally:
            connection.close()

    def compare_and_swap(self, scope, previous, next_head):
        if not _hash(scope) or not (previous is None or _hash(previous)) or not _hash(next_head):
            raise FoundryError('anchor_scope', 'Exact anchor digests are required.')
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT head FROM heads WHERE scope = ?', (scope,)).fetchone()
            current = None if row is None else row[0]
            if current != previous:
                connection.rollback()
                return False
            if row is None:
                connection.execute('INSERT INTO heads (scope, head) VALUES (?, ?)', (scope,next_head))
            else:
                connection.execute('UPDATE heads SET head = ? WHERE scope = ?', (next_head,scope))
            connection.commit()
            return True
        except Exception:
            # The caller must treat commit or close failures as unknown, never retry.
            raise FoundryError('anchor_update_unresolved', 'Anchor update may have committed; do not retry.') from None
        finally:
            connection.close()
