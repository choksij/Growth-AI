from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import random
from typing import Dict, Iterator, Optional
from uuid import uuid4

from growthpilot_contracts import AdEvent, AdEventPayload, AdEventType, EventSource
from .world import World, Campaign


@dataclass
class GeneratorConfig:
    workspace_id: str = "ws_demo"
    eps: float = 10.0  # events per second
    seed: Optional[int] = None


class EventGenerator:
    def __init__(self, world: World, cfg: GeneratorConfig):
        self.world = world
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)

    def _choose_campaign(self) -> Campaign:
        cid = self.rng.choice(list(self.world.campaigns.keys()))
        return self.world.campaigns[cid]

    def generate_one(self) -> AdEvent:
        self.world.tick()
        c = self._choose_campaign()
        ts = datetime.now(timezone.utc)

        if c.paused:
            # if paused, only impressions at low rate
            event_type = AdEventType.IMPRESSION
            value = int(self.rng.randint(20, 200))
        else:
            # pick an event type with realistic proportions
            event_type = self.rng.choices(
                population=[
                    AdEventType.IMPRESSION,
                    AdEventType.CLICK,
                    AdEventType.CONVERSION,
                    AdEventType.SPEND,
                    AdEventType.REVENUE,
                ],
                weights=[70, 12, 2, 10, 6],
                k=1,
            )[0]

            # generate values depending on event type
            if event_type == AdEventType.IMPRESSION:
                value = int(self.rng.randint(100, 1000))
            elif event_type == AdEventType.CLICK:
                # clicks depend on impressions * ctr
                value = int(self.rng.randint(1, 20))
            elif event_type == AdEventType.CONVERSION:
                value = int(self.rng.choices([0, 1, 2, 3], weights=[65, 25, 8, 2], k=1)[0])
            elif event_type == AdEventType.SPEND:
                # spend ~= clicks * cpc
                clicks = self.rng.uniform(1, 10)
                cpc = c.base_cpc * c.cpc_mult
                value = float(round(clicks * cpc, 4))
            else:  # REVENUE
                conv = self.rng.uniform(0, 3)
                rpv = c.base_rpv * c.rpv_mult
                value = float(round(conv * rpv, 4))

        payload = AdEventPayload(
            campaign_id=c.campaign_id,
            adset_id=None,
            ad_id=None,
            segment_id=None,
            value=float(value),
            currency="USD" if event_type in (AdEventType.SPEND, AdEventType.REVENUE) else None,
            metadata={
                "sim": True,
                "scenario": self.world.active.name if self.world.active else None,
                "ctr_mult": c.ctr_mult,
                "cvr_mult": c.cvr_mult,
                "cpc_mult": c.cpc_mult,
                "rpv_mult": c.rpv_mult,
            },
        )

        evt = AdEvent(
            schema_version="1.0",
            event_id=uuid4(),
            event_type=event_type,
            ts=ts,
            workspace_id=self.cfg.workspace_id,
            source=EventSource.simulator,
            trace_id=None,
            payload=payload,
        )
        return evt

    def stream(self) -> Iterator[AdEvent]:
        while True:
            yield self.generate_one()