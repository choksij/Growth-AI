from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Optional, Sequence


@dataclass(frozen=True)
class AnomalyResult:
    roas_z: Optional[float]
    cpa_z: Optional[float]
    ctr_z: Optional[float]


def zscore(value: Optional[float], history: Sequence[float]) -> Optional[float]:
    """
    Safe z-score:
    - returns None if value is None
    - returns None if history has < 2 usable points
    - returns 0.0 if stdev is 0 (all history same)
    - never throws
    """
    if value is None:
        return None

    # filter out None / NaN-like values defensively
    xs: list[float] = []
    for x in history:
        try:
            if x is None:
                continue
            xf = float(x)
            # drop NaN
            if xf != xf:
                continue
            xs.append(xf)
        except Exception:
            continue

    if len(xs) < 2:
        return None

    mean = sum(xs) / len(xs)

    # population variance (fine for MVP)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    if var <= 0:
        return 0.0

    sd = sqrt(var)
    return (float(value) - mean) / sd