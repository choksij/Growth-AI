from __future__ import annotations

"""
bandit.py
---------
Updates policy_bandit alpha/beta based on reward outcome.

Thompson Sampling update rule:
    success=True  →  alpha += 1  (action reinforced)
    success=False →  beta  += 1  (action penalized)

Over many iterations, actions that consistently produce positive outcomes
accumulate high alpha, making Thompson Sampling select them with increasing
probability. This is the "self-improvement" core of GrowthPilot.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class BanditUpdate:
    workspace_id: str
    bucket_key: str
    action_type: str
    alpha_before: int
    beta_before: int
    alpha_after: int
    beta_after: int
    success: bool
    score_S: float

    @property
    def win_rate(self) -> float:
        """Current estimated win rate = alpha / (alpha + beta)."""
        total = self.alpha_after + self.beta_after
        return self.alpha_after / total if total > 0 else 0.5

    def as_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "bucket_key": self.bucket_key,
            "action_type": self.action_type,
            "alpha_before": self.alpha_before,
            "beta_before": self.beta_before,
            "alpha_after": self.alpha_after,
            "beta_after": self.beta_after,
            "success": self.success,
            "score_S": round(self.score_S, 6),
            "win_rate": round(self.win_rate, 4),
        }


def compute_bandit_update(
    *,
    workspace_id: str,
    bucket_key: str,
    action_type: str,
    alpha_current: int,
    beta_current: int,
    success: bool,
    score_S: float,
) -> BanditUpdate:
    """
    Compute the new alpha/beta values after observing outcome.

    Args:
        workspace_id:   tenant identifier
        bucket_key:     e.g. "objective=roas|severity=high|type=prospecting"
        action_type:    e.g. "REALLOC_BUDGET"
        alpha_current:  current alpha (successes + 1)
        beta_current:   current beta  (failures  + 1)
        success:        True if score_S >= threshold
        score_S:        raw reward score for logging

    Returns:
        BanditUpdate with before/after alpha/beta
    """
    alpha_after = alpha_current + (1 if success else 0)
    beta_after  = beta_current  + (0 if success else 1)

    return BanditUpdate(
        workspace_id=workspace_id,
        bucket_key=bucket_key,
        action_type=action_type,
        alpha_before=alpha_current,
        beta_before=beta_current,
        alpha_after=alpha_after,
        beta_after=beta_after,
        success=success,
        score_S=score_S,
    )