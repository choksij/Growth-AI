from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional
import random
import time


@dataclass
class Campaign:
    campaign_id: str
    base_cvr: float = 0.02          # conversion rate per click
    base_cpc: float = 1.20          # $ per click
    base_rpv: float = 25.0          # revenue per conversion
    base_ctr: float = 0.015         # click-through rate per impression

    # scenario multipliers (dynamic)
    ctr_mult: float = 1.0
    cvr_mult: float = 1.0
    cpc_mult: float = 1.0
    rpv_mult: float = 1.0

    # optional: can simulate “paused”
    paused: bool = False


@dataclass
class ActiveScenario:
    name: str
    ends_at: float
    params: Dict[str, float] = field(default_factory=dict)


class World:
    """
    Holds simulated campaigns and active scenarios.
    Scenarios modify campaign multipliers temporarily.
    """

    def __init__(self, num_campaigns: int = 3, seed: Optional[int] = None):
        self.rng = random.Random(seed)
        self.campaigns: Dict[str, Campaign] = {}
        for i in range(num_campaigns):
            cid = f"cmp_{i+1:03d}"
            self.campaigns[cid] = Campaign(
                campaign_id=cid,
                base_cvr=self.rng.uniform(0.01, 0.03),
                base_cpc=self.rng.uniform(0.8, 2.0),
                base_rpv=self.rng.uniform(15.0, 50.0),
                base_ctr=self.rng.uniform(0.008, 0.03),
            )

        self.active: Optional[ActiveScenario] = None

    def now(self) -> float:
        return time.time()

    def apply_scenario(self, name: str, duration_s: int = 120) -> None:
        ends = self.now() + duration_s
        self.active = ActiveScenario(name=name, ends_at=ends, params={})
        self._apply_modifiers()

    def clear_scenario(self) -> None:
        self.active = None
        for c in self.campaigns.values():
            c.ctr_mult = c.cvr_mult = c.cpc_mult = c.rpv_mult = 1.0

    def tick(self) -> None:
        if self.active and self.now() >= self.active.ends_at:
            self.clear_scenario()

    def _apply_modifiers(self) -> None:
        # reset first
        for c in self.campaigns.values():
            c.ctr_mult = c.cvr_mult = c.cpc_mult = c.rpv_mult = 1.0

        if not self.active:
            return

        name = self.active.name

        if name == "roas_drop":
            # ROAS drop: revenue per conversion drops + CVR slightly drops
            for c in self.campaigns.values():
                c.rpv_mult = 0.55
                c.cvr_mult = 0.75

        elif name == "cpa_spike":
            # CPA spike: CPC increases and CVR decreases
            for c in self.campaigns.values():
                c.cpc_mult = 1.6
                c.cvr_mult = 0.7

        elif name == "creative_fatigue":
            # creative fatigue: CTR slowly drops
            for c in self.campaigns.values():
                c.ctr_mult = 0.5

    def snapshot(self) -> Dict:
        self.tick()
        return {
            "active_scenario": None
            if not self.active
            else {"name": self.active.name, "ends_at": self.active.ends_at},
            "campaigns": {
                cid: {
                    "paused": c.paused,
                    "base_ctr": c.base_ctr,
                    "base_cvr": c.base_cvr,
                    "base_cpc": c.base_cpc,
                    "base_rpv": c.base_rpv,
                    "ctr_mult": c.ctr_mult,
                    "cvr_mult": c.cvr_mult,
                    "cpc_mult": c.cpc_mult,
                    "rpv_mult": c.rpv_mult,
                }
                for cid, c in self.campaigns.items()
            },
        }