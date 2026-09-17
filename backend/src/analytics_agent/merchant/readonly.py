"""Read-only access to an imported snapshot, independent of model instructions.

The caller supplies a server-owned snapshot path, never a model-supplied path.
This is a local SQLite boundary, not a sandbox for arbitrary remote connectors.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


class SnapshotReader:
    def __init__(self, path: Path, *, max_rows: int = 200, timeout_seconds: float = 2):
        if not 1 <= max_rows <= 5000 or not 0 < timeout_seconds <= 30:
            raise ValueError("Query budgets out of range")
        self.path = path.resolve(strict=True)
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _authorize(
        action: int, arg1: str | None, arg2: str | None, database: str | None, trigger: str | None
    ) -> int:
        # Allow evaluation and reads only. In particular no PRAGMA, ATTACH,
        # transactions, schema changes, or extension-loading escape hatch.
        if action == sqlite3.SQLITE_FUNCTION:
            return (
                sqlite3.SQLITE_DENY
                if (arg2 or "").lower() == "load_extension"
                else sqlite3.SQLITE_OK
            )
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    def query(self, sql: str, parameters: dict | None = None) -> dict[str, Any]:
        started = time.monotonic()
        result: dict[str, Any] = {"columns": [], "rows": [], "truncated": False}
        if not sql.strip() or len(sql) > 50_000:
            return {**result, "error": "invalid_query"}
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as conn:
                # Some macOS Python builds omit extension loading altogether.
                if hasattr(conn, "enable_load_extension"):
                    conn.enable_load_extension(False)
                conn.execute("PRAGMA query_only=ON")
                conn.set_authorizer(self._authorize)
                conn.set_progress_handler(
                    lambda: int(time.monotonic() - started >= self.timeout_seconds), 1000
                )
                cursor = conn.execute(sql, parameters or {})
                columns = [column[0] for column in cursor.description or []]
                if len(columns) != len(set(columns)):
                    return {**result, "error": "duplicate_columns_use_aliases"}
                rows = cursor.fetchmany(self.max_rows + 1)
                result.update(
                    columns=columns,
                    rows=[dict(zip(columns, row)) for row in rows[: self.max_rows]],
                    truncated=len(rows) > self.max_rows,
                )
        except sqlite3.Error as exc:
            # Do not expose filesystem paths or connection details to the model.
            error = "query_rejected"
            if "interrupted" in str(exc):
                error = "query_timeout"
            result["error"] = error
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return result
