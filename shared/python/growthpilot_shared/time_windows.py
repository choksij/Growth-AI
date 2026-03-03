from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .db import Database
from .logging import get_logger

log = get_logger("shared.idempotency")


@dataclass
class InMemoryIdempotency:
    ttl_s: int = 3600

    def __post_init__(self) -> None:
        self._seen: dict[str, float] = {}

    def _gc(self) -> None:
        now = time.time()
        cutoff = now - self.ttl_s
        # cheap GC
        for k, ts in list(self._seen.items()):
            if ts < cutoff:
                self._seen.pop(k, None)

    def seen(self, key: str) -> bool:
        self._gc()
        return key in self._seen

    def mark(self, key: str) -> None:
        self._gc()
        self._seen[key] = time.time()


CREATE_TABLE_SQL = """
create table if not exists growthpilot.idempotency_keys (
  k text primary key,
  created_at timestamptz not null default now()
);
"""

INSERT_KEY_SQL = """
insert into growthpilot.idempotency_keys (k)
values (%(k)s)
on conflict (k) do nothing;
"""

SELECT_KEY_SQL = """
select k from growthpilot.idempotency_keys where k = %(k)s;
"""


@dataclass
class PostgresIdempotency:
    db: Database

    def ensure_schema(self) -> None:
        self.db.execute(CREATE_TABLE_SQL)

    def seen(self, key: str) -> bool:
        row = self.db.fetchone(SELECT_KEY_SQL, {"k": key})
        return row is not None

    def mark(self, key: str) -> None:
        self.db.execute(INSERT_KEY_SQL, {"k": key})