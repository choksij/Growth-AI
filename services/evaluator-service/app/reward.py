from __future__ import annotations

"""
reward.py
---------
Computes the reward signal score_S used to update Thompson Sampling bandits.

Formula:
    score_S = w1 * ΔROAS_norm + w2 * ΔCPA_norm - w3 * volatility

Where:
    ΔROAS_norm  = (roas_after - roas_before) / max(roas_before, 0.001)
    ΔCPA_norm   = (cpa_before - cpa_after)  / max(cpa_before, 0.001)  (lower CPA = better)
    volatility  = std_dev of ROAS over evaluation window (penalizes noisy recovery)

Binary success for bandit update:
    success = (score_S >= threshold)  →  alpha += 1
    failure = (score_S < threshold)   →  beta  += 1
"""

from dataclasses import dataclass
from typing import Optional

# Default weights — tunable via env
W1_ROAS = 0.5   # weight for ROAS improvement
W2_CPA  = 0.3   # weight for CPA improvement
W3_VOL  = 0.2   # penalty for volatility

SUCCESS_THRESHOLD = 0.05  # score_S must exceed this to count as success


@dataclass(frozen=True)
class KPISnapshot:
    roas: float
    cpa: float
    ctr: float = 0.0
    cvr: float = 0.0
    spend: float = 0.0
    revenue: float = 0.0


@dataclass
class RewardResult:
    score_S: float
    success: bool
    delta_roas: float
    delta_cpa: float
    delta_roas_norm: float
    delta_cpa_norm: float
    volatility: float
    roas_before: float
    roas_after: float
    cpa_before: float
    cpa_after: float
    w1: float
    w2: float
    w3: float
    threshold: float
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "score_S": round(self.score_S, 6),
            "success": self.success,
            "delta_roas": round(self.delta_roas, 4),
            "delta_cpa": round(self.delta_cpa, 4),
            "delta_roas_norm": round(self.delta_roas_norm, 4),
            "delta_cpa_norm": round(self.delta_cpa_norm, 4),
            "volatility": round(self.volatility, 4),
            "roas_before": round(self.roas_before, 4),
            "roas_after": round(self.roas_after, 4),
            "cpa_before": round(self.cpa_before, 4),
            "cpa_after": round(self.cpa_after, 4),
            "weights": {"w1": self.w1, "w2": self.w2, "w3": self.w3},
            "threshold": self.threshold,
            "notes": self.notes,
        }


def compute_reward(
    before: KPISnapshot,
    after: KPISnapshot,
    roas_history: Optional[list[float]] = None,
    *,
    w1: float = W1_ROAS,
    w2: float = W2_CPA,
    w3: float = W3_VOL,
    threshold: float = SUCCESS_THRESHOLD,
) -> RewardResult:
    """
    Compute score_S from before/after KPI snapshots.

    Args:
        before:        KPI state just before the action was applied
        after:         KPI state after the evaluation window
        roas_history:  list of ROAS values during eval window (for volatility)
        w1, w2, w3:    reward weights
        threshold:     minimum score_S to count as success
    """
    # ---- ΔROAS ----
    roas_base = max(before.roas, 0.001)
    delta_roas = after.roas - before.roas
    delta_roas_norm = delta_roas / roas_base  # positive = improvement

    # ---- ΔCPA (inverted: lower is better) ----
    cpa_base = max(before.cpa, 0.001)
    delta_cpa = before.cpa - after.cpa       # positive = improvement
    delta_cpa_norm = delta_cpa / cpa_base

    # ---- volatility penalty ----
    volatility = 0.0
    if roas_history and len(roas_history) >= 2:
        mean = sum(roas_history) / len(roas_history)
        variance = sum((x - mean) ** 2 for x in roas_history) / len(roas_history)
        std = variance ** 0.5
        # Normalize by mean to get coefficient of variation
        volatility = std / max(mean, 0.001)

    # ---- score_S ----
    score_S = (w1 * delta_roas_norm) + (w2 * delta_cpa_norm) - (w3 * volatility)

    success = score_S >= threshold

    # Build notes for explainability
    parts = []
    if delta_roas_norm > 0:
        parts.append(f"ROAS improved {delta_roas_norm*100:.1f}%")
    elif delta_roas_norm < 0:
        parts.append(f"ROAS worsened {abs(delta_roas_norm)*100:.1f}%")
    if delta_cpa_norm > 0:
        parts.append(f"CPA improved {delta_cpa_norm*100:.1f}%")
    elif delta_cpa_norm < 0:
        parts.append(f"CPA worsened {abs(delta_cpa_norm)*100:.1f}%")
    if volatility > 0.1:
        parts.append(f"high volatility={volatility:.2f}")
    notes = " | ".join(parts) if parts else "no significant change"

    return RewardResult(
        score_S=score_S,
        success=success,
        delta_roas=delta_roas,
        delta_cpa=before.cpa - after.cpa,
        delta_roas_norm=delta_roas_norm,
        delta_cpa_norm=delta_cpa_norm,
        volatility=volatility,
        roas_before=before.roas,
        roas_after=after.roas,
        cpa_before=before.cpa,
        cpa_after=after.cpa,
        w1=w1,
        w2=w2,
        w3=w3,
        threshold=threshold,
        notes=notes,
    )