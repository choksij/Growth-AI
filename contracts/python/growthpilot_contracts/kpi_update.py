from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from .envelope import EventEnvelope


class KPILevel(str, Enum):
    campaign = "campaign"
    adset = "adset"
    ad = "ad"
    segment = "segment"


class KPIWindow(str, Enum):
    m1 = "1m"
    m5 = "5m"
    m15 = "15m"
    m60 = "60m"
    d1 = "1d"


class KPIUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: KPILevel
    campaign_id: str = Field(min_length=1)
    adset_id: Optional[str] = None
    ad_id: Optional[str] = None
    segment_id: Optional[str] = None

    window: KPIWindow

    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: int = Field(ge=0)

    spend: float = Field(ge=0)
    revenue: float = Field(ge=0)

    ctr: float = Field(ge=0)
    cvr: float = Field(ge=0)
    cpa: float = Field(ge=0)
    roas: float = Field(ge=0)

    pacing_ratio: float = Field(ge=0)
    anomaly_score: float = Field(ge=0)

    # baseline is optional and free-form in schema
    baseline: Optional[Dict[str, Any]] = None


class KPIUpdate(EventEnvelope[KPIUpdatePayload]):
    event_type: str = "KPI_UPDATE"