from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from .envelope import EventEnvelope


class AdEventType(str, Enum):
    IMPRESSION = "IMPRESSION"
    CLICK = "CLICK"
    CONVERSION = "CONVERSION"
    SPEND = "SPEND"
    REVENUE = "REVENUE"


class AdEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(min_length=1)
    adset_id: Optional[str] = Field(default=None, min_length=1)
    ad_id: Optional[str] = Field(default=None, min_length=1)
    segment_id: Optional[str] = Field(default=None, min_length=1)

    value: float
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)

    # metadata is free-form per schema
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AdEvent(EventEnvelope[AdEventPayload]):
    """
    For topic: ad.events / ad.events.normalized
    event_type must be one of AdEventType.
    """

    event_type: AdEventType