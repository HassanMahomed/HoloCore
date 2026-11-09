"""Reuse one SQLite connection per thread instead of opening one per operation.

Opening and closing costs more than most HoloCore writes, and closing the last
connection to a WAL database folds the WAL back and deletes it, so doing that on
every write made each memory several times slower.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Callable, Iterator


class DatabaseBusyError(sqlite3.OperationalError):
    """Another program held the database's write lock for longer than the busy timeout."""


def _busy(error: BaseException) -> bool:
    if not isinstance(error, sqlite3.OperationalError) or isinstance(error, DatabaseBusyError):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    return (code & 0xFF) == sqlite3.SQLITE_BUSY if code is not None else str(error) == "database is locked"


class ThreadConnections:
    def __init__(self, connect: Callable[[], sqlite3.Connection], path: object = "the database"):
        self._connect = connect
        self._path = path
        self._local = threading.local()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit on success and roll back on error, like ``with connection:``."""
        try:
            with self._transaction() as db:
                yield db
        except sqlite3.OperationalError as error:
            if not _busy(error):
                raise
            # SQLite already waited the connection's busy timeout before giving up.
            raise DatabaseBusyError(
                f"HoloCore could not write to {self._path}: another program kept it locked. "
                "Wait for any running backup, restore, or migration to finish, then try again."
            ) from error

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        local = self._local
        if getattr(local, "busy", False):
            # Nested use gets its own connection, so an inner commit never ends the outer transaction.
            db = self._connect()
            try:
                with db:
                    yield db
            finally:
                db.close()
            return
        db = getattr(local, "db", None) or self._connect()
        local.db, local.busy = db, True
        try:
            with db:
                yield db
        except BaseException:
            local.db = None  # Never reuse a connection that just failed.
            db.close()
            raise
        finally:
            local.busy = False

    def close(self) -> None:
        """Close this thread's connection; other threads' close when those threads end."""
        db = getattr(self._local, "db", None)
        self._local.db = None
        if db is not None:
            db.close()
