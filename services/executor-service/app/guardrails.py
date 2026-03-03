from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from growthpilot_shared.logging import get_logger

log = get_logger("executor.guardrails")

# Hard limits - never exceed these regardless of policy
MAX_BUDGET_SHIFT_PCT: float = 0.10   # 10% max budget reallocation per action
MIN_COOLDOWN_SECONDS: int = 120      # 2 min minimum between actions on same campaign
MAX_ACTIONS_PER_CAMPAIGN_PER_HOUR: int = 5


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str


@dataclass
class Guardrails:
    """
    Stateful guardrail checker.
    Tracks cooldowns and action counts in memory (sufficient for single-instance demo).
    In production, this state would live in Redis.
    """
    _cooldowns: Dict[str, float] = field(default_factory=dict)       # campaign_id -> expires_at
    _action_counts: Dict[str, list] = field(default_factory=dict)    # campaign_id -> [ts, ...]
    _seen_idempotency: Dict[str, float] = field(default_factory=dict) # key -> ts

    def check(
        self,
        *,
        campaign_id: str,
        action_type: str,
        idempotency_key: str,
        max_budget_shift_pct: float,
        cooldown_minutes: int,
    ) -> GuardrailResult:
        now = time.time()

        # 1) Idempotency — never apply same action twice
        if idempotency_key in self._seen_idempotency:
            return GuardrailResult(
                allowed=False,
                reason=f"Duplicate idempotency_key: {idempotency_key}"
            )

        # 2) Cooldown — campaign must not be in cooldown
        cooldown_expires = self._cooldowns.get(campaign_id, 0)
        if now < cooldown_expires:
            remaining = int(cooldown_expires - now)
            return GuardrailResult(
                allowed=False,
                reason=f"Campaign {campaign_id} in cooldown for {remaining}s more"
            )

        # 3) Budget shift cap — requested must not exceed hard limit
        if max_budget_shift_pct > MAX_BUDGET_SHIFT_PCT:
            return GuardrailResult(
                allowed=False,
                reason=f"Budget shift {max_budget_shift_pct:.1%} exceeds hard cap {MAX_BUDGET_SHIFT_PCT:.1%}"
            )

        # 4) Rate limit — max actions per campaign per hour
        hour_ago = now - 3600
        counts = [t for t in self._action_counts.get(campaign_id, []) if t > hour_ago]
        if len(counts) >= MAX_ACTIONS_PER_CAMPAIGN_PER_HOUR:
            return GuardrailResult(
                allowed=False,
                reason=f"Campaign {campaign_id} exceeded {MAX_ACTIONS_PER_CAMPAIGN_PER_HOUR} actions/hour"
            )

        return GuardrailResult(allowed=True, reason="ok")

    def mark_applied(
        self,
        *,
        campaign_id: str,
        idempotency_key: str,
        cooldown_minutes: int,
    ) -> datetime:
        """Record the action as applied and set cooldown. Returns cooldown_until datetime."""
        now = time.time()
        effective_cooldown = max(cooldown_minutes * 60, MIN_COOLDOWN_SECONDS)

        self._seen_idempotency[idempotency_key] = now
        self._cooldowns[campaign_id] = now + effective_cooldown

        counts = self._action_counts.get(campaign_id, [])
        counts.append(now)
        self._action_counts[campaign_id] = counts

        cooldown_until = datetime.fromtimestamp(now + effective_cooldown, tz=timezone.utc)
        log.info(
            "action marked applied",
            extra={
                "campaign_id": campaign_id,
                "idempotency_key": idempotency_key,
                "cooldown_until": cooldown_until.isoformat(),
            }
        )
        return cooldown_until

    def gc(self) -> None:
        """Periodic garbage collection of expired state."""
        now = time.time()
        hour_ago = now - 3600

        # clear expired cooldowns
        self._cooldowns = {k: v for k, v in self._cooldowns.items() if v > now}

        # trim old action counts
        for cid in list(self._action_counts.keys()):
            self._action_counts[cid] = [t for t in self._action_counts[cid] if t > hour_ago]
            if not self._action_counts[cid]:
                del self._action_counts[cid]

        # trim old idempotency keys (keep 24h)
        day_ago = now - 86400
        self._seen_idempotency = {k: v for k, v in self._seen_idempotency.items() if v > day_ago}