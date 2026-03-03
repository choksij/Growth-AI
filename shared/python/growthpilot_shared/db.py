from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

try:
    from psycopg_pool import ConnectionPool
except Exception:  # pragma: no cover
    ConnectionPool = None  # type: ignore

from .config import SharedSettings
from .logging import get_logger

log = get_logger("shared.db")


@dataclass
class Database:
    conninfo: str
    pool_min: int = 1
    pool_max: int = 5
    timeout_s: int = 30

    def __post_init__(self) -> None:
        self._pool = None

        if ConnectionPool is None:
            log.warning("psycopg_pool not available; using one-off connections")
            return

        self._pool = ConnectionPool(
            conninfo=self.conninfo,
            min_size=self.pool_min,
            max_size=self.pool_max,
            timeout=self.timeout_s,
            kwargs={"row_factory": dict_row},
        )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """
        Context manager yielding a psycopg connection.
        Uses pool if available, else creates one-off connection.
        """
        if self._pool is not None:
            with self._pool.connection() as conn:
                yield conn
            return

        with psycopg.connect(self.conninfo, row_factory=dict_row) as conn:
            yield conn

    def execute(self, sql: str, params: Dict[str, Any] | None = None) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                conn.commit()

    def fetchall(self, sql: str, params: Dict[str, Any] | None = None) -> list[dict]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                rows = cur.fetchall()
                return list(rows)

    def fetchone(self, sql: str, params: Dict[str, Any] | None = None) -> Optional[dict]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                row = cur.fetchone()
                return row


def db_from_env(settings: SharedSettings) -> Database:
    return Database(
        conninfo=settings.pg_conninfo,
        pool_min=settings.pg_pool_min,
        pool_max=settings.pg_pool_max,
        timeout_s=settings.pg_pool_timeout_s,
    )