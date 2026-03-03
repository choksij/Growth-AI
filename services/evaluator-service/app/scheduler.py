from __future__ import annotations

"""
scheduler.py
------------
Manages the evaluation window between action application and outcome measurement.

When an action is applied:
  1. We record a PendingEvaluation with the action details + before-KPI snapshot
  2. After EVAL_WINDOW_SECONDS, we take the after-KPI snapshot
  3. We compute reward and update the bandit

In production: 15-60 minutes (real ad platforms take time to react)
In simulation: 90-120 seconds (simulator reacts fast, good for demo)
"""

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, Optional
from uuid import UUID

from growthpilot_shared.logging import get_logger

log = get_logger("evaluator.scheduler")

# Configurable — short for demo, realistic for prod
EVAL_WINDOW_SECONDS = int(os.getenv("EVAL_WINDOW_SECONDS", "120"))  # 2 min default
MAX_PENDING = int(os.getenv("EVALUATOR_MAX_PENDING", "200"))


@dataclass
class PendingEvaluation:
    action_id: UUID
    action_type: str
    campaign_id: str
    workspace_id: str
    bucket_key: str
    alert_type: str
    severity: str
    applied_at: datetime

    # KPI state before action (captured from kpi_rollups at action time)
    roas_before: float
    cpa_before: float

    # Context for reward calculation
    explainability: Dict[str, Any] = field(default_factory=dict)
    policy: Dict[str, Any] = field(default_factory=dict)

    # Evaluation window deadline
    eval_after: datetime = field(init=False)

    def __post_init__(self):
        from datetime import timedelta
        self.eval_after = self.applied_at + timedelta(seconds=EVAL_WINDOW_SECONDS)

    @property
    def is_ready(self) -> bool:
        return datetime.now(tz=timezone.utc) >= self.eval_after

    @property
    def age_seconds(self) -> float:
        return (datetime.now(tz=timezone.utc) - self.applied_at).total_seconds()

    @property
    def seconds_until_ready(self) -> float:
        remaining = (self.eval_after - datetime.now(tz=timezone.utc)).total_seconds()
        return max(0.0, remaining)


# Type for the callback that runs when evaluation window expires
EvalCallback = Callable[[PendingEvaluation], Coroutine]


class EvaluationScheduler:
    """
    In-memory scheduler for pending evaluations.

    Stores PendingEvaluation objects keyed by action_id.
    A background task checks every 10s for matured evaluations
    and fires the callback.
    """

    def __init__(self, callback: EvalCallback):
        self._pending: Dict[UUID, PendingEvaluation] = {}
        self._callback = callback
        self._task: Optional[asyncio.Task] = None

    def schedule(self, evaluation: PendingEvaluation) -> None:
        """Add a pending evaluation to the scheduler."""
        if len(self._pending) >= MAX_PENDING:
            # Drop oldest to prevent memory bloat
            oldest_id = min(self._pending, key=lambda k: self._pending[k].applied_at)
            dropped = self._pending.pop(oldest_id)
            log.warning(
                "Pending queue full, dropping oldest",
                extra={"dropped_action_id": str(oldest_id), "campaign_id": dropped.campaign_id},
            )

        self._pending[evaluation.action_id] = evaluation
        log.info(
            "Evaluation scheduled",
            extra={
                "action_id": str(evaluation.action_id),
                "campaign_id": evaluation.campaign_id,
                "eval_window_s": EVAL_WINDOW_SECONDS,
                "eval_after": evaluation.eval_after.isoformat(),
            },
        )

    def cancel(self, action_id: UUID) -> bool:
        """Remove a pending evaluation (e.g. if action was rejected)."""
        if action_id in self._pending:
            del self._pending[action_id]
            return True
        return False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop())
            log.info("Evaluation scheduler started (window=%ds)", EVAL_WINDOW_SECONDS)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        """Poll every 10 seconds for matured evaluations."""
        while True:
            await asyncio.sleep(10)
            await self._fire_matured()

    async def _fire_matured(self) -> None:
        """Find and fire all matured evaluations."""
        matured = [ev for ev in self._pending.values() if ev.is_ready]

        for ev in matured:
            del self._pending[ev.action_id]
            log.info(
                "Evaluation window matured — running evaluation",
                extra={
                    "action_id": str(ev.action_id),
                    "campaign_id": ev.campaign_id,
                    "age_seconds": round(ev.age_seconds, 1),
                },
            )
            try:
                await self._callback(ev)
            except Exception as e:
                log.exception(
                    "Evaluation callback failed for action %s: %s",
                    ev.action_id, e,
                )