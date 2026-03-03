from __future__ import annotations

"""
repository.py
-------------
All database operations for the evaluator service.

Reads:
  - kpi_rollups (get before/after KPI snapshots)
  - policy_bandit (get current alpha/beta)
  - actions (get action details for evaluation)

Writes:
  - outcomes (evaluation result)
  - policy_bandit (updated alpha/beta)
  - policy_updates (audit log)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID
import json

from growthpilot_shared.db import Database
from growthpilot_shared.logging import get_logger

from .bandit import BanditUpdate
from .reward import KPISnapshot, RewardResult

log = get_logger("evaluator.repository")


# ---- SQL ----

GET_KPI_SNAPSHOT_SQL = """
SELECT
    roas,
    cpa,
    ctr,
    cvr,
    spend,
    revenue
FROM growthpilot.kpi_campaign_minute
WHERE workspace_id = %(workspace_id)s
  AND campaign_id  = %(campaign_id)s
  AND window_start >= %(since)s
  AND roas IS NOT NULL
  AND cpa  IS NOT NULL
ORDER BY window_start DESC
LIMIT %(limit)s;
"""

# Looks BACKWARD from a timestamp — used to capture before-KPI at action time
GET_KPI_BEFORE_SQL = """
SELECT
    roas,
    cpa,
    ctr,
    cvr,
    spend,
    revenue
FROM growthpilot.kpi_campaign_minute
WHERE workspace_id = %(workspace_id)s
  AND campaign_id  = %(campaign_id)s
  AND window_start <= %(before)s
  AND roas IS NOT NULL
  AND cpa  IS NOT NULL
ORDER BY window_start DESC
LIMIT %(limit)s;
"""

GET_BANDIT_ROW_SQL = """
SELECT alpha, beta
FROM growthpilot.policy_bandit
WHERE workspace_id = %(workspace_id)s
  AND bucket_key   = %(bucket_key)s
  AND action_type  = %(action_type)s;
"""

UPSERT_BANDIT_SQL = """
INSERT INTO growthpilot.policy_bandit
    (workspace_id, bucket_key, action_type, alpha, beta, updated_at)
VALUES
    (%(workspace_id)s, %(bucket_key)s, %(action_type)s, %(alpha)s, %(beta)s, now())
ON CONFLICT (workspace_id, bucket_key, action_type)
DO UPDATE SET
    alpha      = EXCLUDED.alpha,
    beta       = EXCLUDED.beta,
    updated_at = now();
"""

INSERT_OUTCOME_SQL = """
INSERT INTO growthpilot.outcomes (
    workspace_id,
    action_id,
    campaign_id,
    action_type,
    bucket_key,
    roas_before,
    roas_after,
    cpa_before,
    cpa_after,
    score_s,
    success,
    eval_started_at,
    eval_ended_at,
    eval_window_seconds
) VALUES (
    %(workspace_id)s,
    %(action_id)s,
    %(campaign_id)s,
    %(action_type)s,
    %(bucket_key)s,
    %(roas_before)s,
    %(roas_after)s,
    %(cpa_before)s,
    %(cpa_after)s,
    %(score_s)s,
    %(success)s,
    %(eval_started_at)s,
    %(eval_ended_at)s,
    %(eval_window_seconds)s
);
"""

INSERT_POLICY_UPDATE_SQL = """
INSERT INTO growthpilot.policy_updates (
    schema_version,
    event_id,
    event_type,
    ts,
    workspace_id,
    source,
    trace_id,
    action_id,
    bucket_key,
    action_type,
    success,
    reward,
    bandit_update,
    raw_event
) VALUES (
    '1.0',
    gen_random_uuid(),
    'POLICY_UPDATED',
    now(),
    %(workspace_id)s,
    'evaluator-service',
    %(trace_id)s,
    %(action_id)s,
    %(bucket_key)s,
    %(action_type)s,
    %(success)s,
    %(reward)s,
    %(bandit_update)s,
    %(raw_event)s
)
ON CONFLICT (event_id) DO NOTHING;
"""

UPDATE_ACTION_STATUS_SQL = """
UPDATE growthpilot.actions
SET status = 'EVALUATED'
WHERE action_id = %(action_id)s;
"""


class EvaluatorRepository:

    def __init__(self, db: Database):
        self.db = db

    def get_kpi_before(
        self,
        *,
        workspace_id: str,
        campaign_id: str,
        before: datetime,
        limit: int = 3,
    ) -> Optional[KPISnapshot]:
        """
        Get KPI snapshot from BEFORE a given timestamp.
        Used to capture baseline state at the moment an action was applied.
        """
        rows = self.db.fetchall(
            GET_KPI_BEFORE_SQL,
            {
                "workspace_id": workspace_id,
                "campaign_id": campaign_id,
                "before": before,
                "limit": limit,
            },
        )

        if not rows:
            return None

        n = len(rows)
        return KPISnapshot(
            roas=sum(r["roas"] for r in rows) / n,
            cpa=sum(r["cpa"] for r in rows) / n,
            ctr=sum(r.get("ctr") or 0 for r in rows) / n,
            cvr=sum(r.get("cvr") or 0 for r in rows) / n,
            spend=sum(r.get("spend") or 0 for r in rows) / n,
            revenue=sum(r.get("revenue") or 0 for r in rows) / n,
        )

    def get_kpi_snapshot(
        self,
        *,
        workspace_id: str,
        campaign_id: str,
        since: datetime,
        limit: int = 3,
    ) -> Optional[KPISnapshot]:
        """
        Get the most recent KPI rollup for a campaign since a given time.
        Returns None if no data is available yet.
        """
        rows = self.db.fetchall(
            GET_KPI_SNAPSHOT_SQL,
            {
                "workspace_id": workspace_id,
                "campaign_id": campaign_id,
                "since": since,
                "limit": limit,
            },
        )

        if not rows:
            log.warning(
                "No KPI snapshot found",
                extra={"campaign_id": campaign_id, "since": since.isoformat()},
            )
            return None

        # Average over available rows for stability
        n = len(rows)
        return KPISnapshot(
            roas=sum(r["roas"] for r in rows) / n,
            cpa=sum(r["cpa"] for r in rows) / n,
            ctr=sum(r.get("ctr", 0) for r in rows) / n,
            cvr=sum(r.get("cvr", 0) for r in rows) / n,
            spend=sum(r.get("spend", 0) for r in rows) / n,
            revenue=sum(r.get("revenue", 0) for r in rows) / n,
        )

    def get_roas_history(
        self,
        *,
        workspace_id: str,
        campaign_id: str,
        since: datetime,
        limit: int = 10,
    ) -> List[float]:
        """Get list of ROAS values during evaluation window for volatility calculation."""
        rows = self.db.fetchall(
            GET_KPI_SNAPSHOT_SQL,
            {
                "workspace_id": workspace_id,
                "campaign_id": campaign_id,
                "since": since,
                "limit": limit,
            },
        )
        return [r["roas"] for r in rows if r.get("roas") is not None]

    def get_bandit_row(
        self,
        *,
        workspace_id: str,
        bucket_key: str,
        action_type: str,
    ) -> tuple[int, int]:
        """
        Get current alpha/beta for a (workspace, bucket, action) triple.
        Returns (1, 1) if no row exists yet (uniform prior).
        """
        row = self.db.fetchone(
            GET_BANDIT_ROW_SQL,
            {
                "workspace_id": workspace_id,
                "bucket_key": bucket_key,
                "action_type": action_type,
            },
        )
        if row is None:
            return (1, 1)
        return (row["alpha"], row["beta"])

    def write_outcome(
        self,
        *,
        workspace_id: str,
        action_id: UUID,
        campaign_id: str,
        action_type: str,
        bucket_key: str,
        reward: RewardResult,
        eval_started_at: datetime,
        eval_window_seconds: int,
    ) -> None:
        """Write evaluation result to outcomes table."""
        self.db.execute(
            INSERT_OUTCOME_SQL,
            {
                "workspace_id": workspace_id,
                "action_id": str(action_id),
                "campaign_id": campaign_id,
                "action_type": action_type,
                "bucket_key": bucket_key,
                "roas_before": reward.roas_before,
                "roas_after": reward.roas_after,
                "cpa_before": reward.cpa_before,
                "cpa_after": reward.cpa_after,
                "score_s": reward.score_S,
                "success": reward.success,
                "eval_started_at": eval_started_at,
                "eval_ended_at": datetime.now(tz=timezone.utc),
                "eval_window_seconds": eval_window_seconds,
            },
        )
        log.info(
            "Outcome written",
            extra={
                "action_id": str(action_id),
                "score_S": reward.score_S,
                "success": reward.success,
            },
        )

    def update_bandit(
        self,
        update: BanditUpdate,
        *,
        action_id: UUID,
        trace_id: Optional[str],
        roas_before: float,
        roas_after: float,
        cpa_before: float,
        cpa_after: float,
    ) -> None:
        """Update policy_bandit alpha/beta and write policy_updates audit row."""
        self.db.execute(
            UPSERT_BANDIT_SQL,
            {
                "workspace_id": update.workspace_id,
                "bucket_key": update.bucket_key,
                "action_type": update.action_type,
                "alpha": update.alpha_after,
                "beta": update.beta_after,
            },
        )

        self.db.execute(
            INSERT_POLICY_UPDATE_SQL,
            {
                "workspace_id": update.workspace_id,
                "trace_id": trace_id,
                "action_id": str(action_id),
                "bucket_key": update.bucket_key,
                "action_type": update.action_type,
                "success": update.success,
                "reward": json.dumps({
                    "score_S": update.score_S,
                    "roas_before": roas_before,
                    "roas_after": roas_after,
                    "cpa_before": cpa_before,
                    "cpa_after": cpa_after,
                }),
                "bandit_update": json.dumps({
                    "alpha_before": update.alpha_before,
                    "beta_before": update.beta_before,
                    "alpha_after": update.alpha_after,
                    "beta_after": update.beta_after,
                    "win_rate": update.win_rate,
                }),
                "raw_event": json.dumps({
                    "bucket_key": update.bucket_key,
                    "action_type": update.action_type,
                    "action_id": str(action_id),
                }),
            },
        )

        log.info(
            "Bandit updated",
            extra={
                "bucket_key": update.bucket_key,
                "action_type": update.action_type,
                "alpha": f"{update.alpha_before} → {update.alpha_after}",
                "beta": f"{update.beta_before} → {update.beta_after}",
                "win_rate": round(update.win_rate, 3),
            },
        )

    def get_action_row(self, action_id: UUID) -> dict | None:
        """
        Fetch the action row from DB by action_id.
        Returns dict with action_type, campaign_id, bucket_key, policy, explainability.
        Used by evaluator to recover fields not present in ActionAppliedPayload.
        """
        return self.db.fetchone(
            """
            SELECT action_type, campaign_id, policy, explainability
            FROM growthpilot.actions
            WHERE action_id = %(action_id)s
            LIMIT 1;
            """,
            {"action_id": str(action_id)},
        )

    def mark_action_evaluated(self, action_id: UUID) -> None:
        """Mark the action row as EVALUATED."""
        self.db.execute(
            UPDATE_ACTION_STATUS_SQL,
            {"action_id": str(action_id)},
        )