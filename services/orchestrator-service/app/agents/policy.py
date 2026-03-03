from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from growthpilot_shared.db import Database
from growthpilot_shared.logging import get_logger

log = get_logger("orchestrator.policy")

ACTION_TYPES = ["PAUSE_CREATIVE", "REALLOC_BUDGET", "ADJUST_BID", "REFRESH_COPY"]

FETCH_BANDIT_SQL = """
SELECT action_type, alpha, beta
FROM growthpilot.policy_bandit
WHERE workspace_id = %(workspace_id)s
  AND bucket_key = %(bucket_key)s
ORDER BY action_type;
"""

UPDATE_BANDIT_SQL = """
INSERT INTO growthpilot.policy_bandit (workspace_id, bucket_key, action_type, alpha, beta, updated_at)
VALUES (%(workspace_id)s, %(bucket_key)s, %(action_type)s, %(alpha)s, %(beta)s, now())
ON CONFLICT (workspace_id, bucket_key, action_type)
DO UPDATE SET
  alpha = EXCLUDED.alpha,
  beta = EXCLUDED.beta,
  updated_at = now();
"""


@dataclass
class BanditResult:
    chosen_action: str
    bucket_key: str
    thompson_samples: Dict[str, float]
    chosen_reason: str
    alpha: int
    beta: int


def build_bucket_key(
    *,
    alert_type: str,
    severity: str,
    objective: str = "roas",
    campaign_type: str = "prospecting",
) -> str:
    """
    Build deterministic bucket key from context.
    Format: objective=roas|severity=high|type=prospecting
    """
    obj = "cpa" if alert_type == "CPA_SPIKE" else "roas"
    return f"objective={obj}|severity={severity}|type={campaign_type}"


class ThompsonSamplingPolicy:
    """
    Multi-armed bandit using Thompson Sampling.

    For each (bucket_key, action_type) pair we maintain Beta(alpha, beta).
    Selection: sample θ ~ Beta(α, β) for each action, pick argmax.
    This gives natural exploration/exploitation balance.
    """

    def __init__(self, db: Database):
        self.db = db
        self._rng = random.Random()

    def _beta_sample(self, alpha: int, beta: int) -> float:
        """Sample from Beta(alpha, beta) using the random module."""
        # Use gamma distribution to sample from Beta
        # Beta(a,b) = Gamma(a,1) / (Gamma(a,1) + Gamma(b,1))
        try:
            x = self._rng.gammavariate(max(alpha, 0.1), 1.0)
            y = self._rng.gammavariate(max(beta, 0.1), 1.0)
            return x / (x + y)
        except Exception:
            return 0.5

    def select_action(
        self,
        *,
        workspace_id: str,
        bucket_key: str,
        airia_recommendation: Optional[str] = None,
        airia_confidence: float = 0.0,
    ) -> BanditResult:
        """
        Select best action using Thompson Sampling.
        If Airia confidence is high (>0.8), we bias toward Airia's recommendation.
        """
        rows = self.db.fetchall(
            FETCH_BANDIT_SQL,
            {"workspace_id": workspace_id, "bucket_key": bucket_key},
        )

        # Build alpha/beta map — default to (1,1) if not found
        bandit_state: Dict[str, Tuple[int, int]] = {
            a: (1, 1) for a in ACTION_TYPES
        }
        for row in rows:
            bandit_state[row["action_type"]] = (row["alpha"], row["beta"])

        # Thompson Sampling — draw one sample per action
        samples: Dict[str, float] = {}
        for action, (alpha, beta) in bandit_state.items():
            sample = self._beta_sample(alpha, beta)

            # Bias toward Airia recommendation if confidence is high
            if action == airia_recommendation and airia_confidence >= 0.8:
                sample = min(1.0, sample * 1.2)
                log.info("Boosting Airia recommendation %s (confidence=%.2f)", action, airia_confidence)

            samples[action] = sample

        chosen = max(samples, key=lambda a: samples[a])
        chosen_alpha, chosen_beta = bandit_state[chosen]

        reason = (
            f"Thompson Sampling: highest θ={samples[chosen]:.3f} "
            f"(α={chosen_alpha}, β={chosen_beta})"
        )
        if chosen == airia_recommendation:
            reason += f" | aligned with Airia diagnosis (confidence={airia_confidence:.2f})"

        log.info(
            "Thompson Sampling selected action",
            extra={
                "bucket_key": bucket_key,
                "chosen": chosen,
                "samples": {k: round(v, 3) for k, v in samples.items()},
            },
        )

        return BanditResult(
            chosen_action=chosen,
            bucket_key=bucket_key,
            thompson_samples=samples,
            chosen_reason=reason,
            alpha=chosen_alpha,
            beta=chosen_beta,
        )