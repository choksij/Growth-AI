from __future__ import annotations

import orjson
from datetime import datetime
from typing import Any, Dict, Optional

from growthpilot_shared.db import Database
from growthpilot_shared.logging import get_logger

log = get_logger("executor.repository")

UPSERT_ACTION_SQL = """
INSERT INTO growthpilot.actions (
  action_id,
  proposed_schema_version,
  proposed_event_id,
  proposed_ts,
  workspace_id,
  proposed_source,
  trace_id,
  alert_id,
  campaign_id,
  action_type,
  parameters,
  constraints,
  policy,
  explainability,
  idempotency_key,
  applied_event_id,
  applied_ts,
  applied_source,
  status,
  applied_changes,
  cooldown_until,
  error
)
VALUES (
  %(action_id)s::uuid,
  %(proposed_schema_version)s,
  %(proposed_event_id)s::uuid,
  %(proposed_ts)s,
  %(workspace_id)s,
  %(proposed_source)s,
  %(trace_id)s,
  %(alert_id)s::uuid,
  %(campaign_id)s,
  %(action_type)s,
  %(parameters)s::jsonb,
  %(constraints)s::jsonb,
  %(policy)s::jsonb,
  %(explainability)s::jsonb,
  %(idempotency_key)s,
  %(applied_event_id)s::uuid,
  %(applied_ts)s,
  %(applied_source)s,
  %(status)s,
  %(applied_changes)s::jsonb,
  %(cooldown_until)s,
  %(error)s
)
ON CONFLICT (action_id) DO UPDATE SET
  status         = EXCLUDED.status,
  applied_event_id = EXCLUDED.applied_event_id,
  applied_ts     = EXCLUDED.applied_ts,
  applied_source = EXCLUDED.applied_source,
  applied_changes = EXCLUDED.applied_changes,
  cooldown_until = EXCLUDED.cooldown_until,
  error          = EXCLUDED.error;
"""

UPDATE_ACTION_STATUS_SQL = """
UPDATE growthpilot.actions
SET
  status          = %(status)s,
  applied_event_id = %(applied_event_id)s::uuid,
  applied_ts      = %(applied_ts)s,
  applied_source  = %(applied_source)s,
  applied_changes = %(applied_changes)s::jsonb,
  cooldown_until  = %(cooldown_until)s,
  error           = %(error)s
WHERE action_id = %(action_id)s::uuid;
"""


class ExecutorRepository:
    def __init__(self, db: Database):
        self.db = db

    def upsert_action(self, row: Dict[str, Any]) -> None:
        """Insert or update an action record."""
        # Serialize jsonb fields
        for field in ("parameters", "constraints", "policy", "explainability", "applied_changes"):
            if row.get(field) is not None and not isinstance(row[field], str):
                row[field] = orjson.dumps(row[field]).decode("utf-8")
            elif row.get(field) is None:
                row[field] = "null"

        self.db.execute(UPSERT_ACTION_SQL, row)

    def update_status(self, row: Dict[str, Any]) -> None:
        """Update action status after apply attempt."""
        if row.get("applied_changes") is not None and not isinstance(row["applied_changes"], str):
            row["applied_changes"] = orjson.dumps(row["applied_changes"]).decode("utf-8")
        elif row.get("applied_changes") is None:
            row["applied_changes"] = "null"

        self.db.execute(UPDATE_ACTION_STATUS_SQL, row)