from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

from growthpilot_shared.db import Database
from growthpilot_shared.logging import get_logger

log = get_logger("kpi.repository")

CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS growthpilot;"

# Rollups table (fine as-is)
CREATE_ROLLUPS_SQL = """
CREATE TABLE IF NOT EXISTS growthpilot.kpi_campaign_minute (
  workspace_id text NOT NULL,
  campaign_id text NOT NULL,
  window_start timestamptz NOT NULL,
  window_s int NOT NULL,
  impressions int NOT NULL,
  clicks int NOT NULL,
  conversions int NOT NULL,
  spend double precision NOT NULL,
  revenue double precision NOT NULL,
  ctr double precision NULL,
  cvr double precision NULL,
  cpa double precision NULL,
  roas double precision NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, campaign_id, window_start)
);
"""

CREATE_HISTORY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kpi_campaign_minute_hist
ON growthpilot.kpi_campaign_minute (workspace_id, campaign_id, window_start DESC);
"""

UPSERT_ROLLUP_SQL = """
INSERT INTO growthpilot.kpi_campaign_minute (
  workspace_id, campaign_id, window_start, window_s,
  impressions, clicks, conversions, spend, revenue,
  ctr, cvr, cpa, roas, updated_at
)
VALUES (
  %(workspace_id)s, %(campaign_id)s, %(window_start)s, %(window_s)s,
  %(impressions)s, %(clicks)s, %(conversions)s, %(spend)s, %(revenue)s,
  %(ctr)s, %(cvr)s, %(cpa)s, %(roas)s, now()
)
ON CONFLICT (workspace_id, campaign_id, window_start)
DO UPDATE SET
  window_s = EXCLUDED.window_s,
  impressions = EXCLUDED.impressions,
  clicks = EXCLUDED.clicks,
  conversions = EXCLUDED.conversions,
  spend = EXCLUDED.spend,
  revenue = EXCLUDED.revenue,
  ctr = EXCLUDED.ctr,
  cvr = EXCLUDED.cvr,
  cpa = EXCLUDED.cpa,
  roas = EXCLUDED.roas,
  updated_at = now();
"""

# IMPORTANT: this matches YOUR existing growthpilot.alerts table (from migrations)
INSERT_ALERT_SQL = """
INSERT INTO growthpilot.alerts (
  alert_id,
  schema_version,
  event_id,
  event_type,
  ts,
  workspace_id,
  source,
  trace_id,
  campaign_id,
  severity,
  alert_type,
  window_size,
  baseline,
  current,
  anomaly_score,
  evidence,
  status,
  resolved_at,
  raw_event
)
VALUES (
  %(alert_id)s::uuid,
  %(schema_version)s,
  %(event_id)s::uuid,
  %(event_type)s,
  %(ts)s,
  %(workspace_id)s,
  %(source)s,
  %(trace_id)s,
  %(campaign_id)s,
  %(severity)s,
  %(alert_type)s,
  %(window_size)s,
  %(baseline)s::jsonb,
  %(current)s::jsonb,
  %(anomaly_score)s,
  COALESCE(%(evidence)s::jsonb, '[]'::jsonb),
  COALESCE(%(status)s, 'OPEN'),
  %(resolved_at)s,
  %(raw_event)s::jsonb
)
ON CONFLICT (event_id) DO NOTHING;
"""

HISTORY_SQL_TEMPLATE = """
SELECT {col}
FROM growthpilot.kpi_campaign_minute
WHERE workspace_id = %(workspace_id)s
  AND campaign_id = %(campaign_id)s
  AND window_s = %(window_s)s
  AND window_start >= now() - (%(minutes)s || ' minutes')::interval
  AND window_start <> %(exclude_window_start)s
  AND {col} IS NOT NULL
ORDER BY window_start DESC
LIMIT %(limit)s;
"""


@dataclass
class KpiRepository:
    db: Database

    def ensure_schema(self) -> None:
        # Do NOT create growthpilot.alerts here — it already exists and has constraints.
        self.db.execute(CREATE_SCHEMA_SQL)
        self.db.execute(CREATE_ROLLUPS_SQL)
        self.db.execute(CREATE_HISTORY_INDEX_SQL)

    def upsert_rollup(self, row: Dict[str, Any]) -> None:
        self.db.execute(UPSERT_ROLLUP_SQL, row)

    def insert_alert_row(self, row: Dict[str, Any]) -> None:
        self.db.execute(INSERT_ALERT_SQL, row)

    def fetch_history(
        self,
        *,
        workspace_id: str,
        campaign_id: str,
        window_s: int,
        minutes: int,
        exclude_window_start: datetime,
        limit: int = 60,
    ) -> Dict[str, list[float]]:
        params = {
            "workspace_id": workspace_id,
            "campaign_id": campaign_id,
            "window_s": window_s,
            "exclude_window_start": exclude_window_start,
            "minutes": minutes,
            "limit": limit,
        }

        roas_rows = self.db.fetchall(HISTORY_SQL_TEMPLATE.format(col="roas"), params)
        cpa_rows = self.db.fetchall(HISTORY_SQL_TEMPLATE.format(col="cpa"), params)
        ctr_rows = self.db.fetchall(HISTORY_SQL_TEMPLATE.format(col="ctr"), params)

        return {
            "roas": [float(r["roas"]) for r in roas_rows if r.get("roas") is not None],
            "cpa": [float(r["cpa"]) for r in cpa_rows if r.get("cpa") is not None],
            "ctr": [float(r["ctr"]) for r in ctr_rows if r.get("ctr") is not None],
        }