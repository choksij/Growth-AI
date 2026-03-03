from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

from .envelope import EventEnvelope


class AlertSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class AlertType(str, Enum):
    ROAS_DROP = "ROAS_DROP"
    CPA_SPIKE = "CPA_SPIKE"
    PACING_ANOMALY = "PACING_ANOMALY"
    CVR_DROP = "CVR_DROP"


class AlertWindow(str, Enum):
    m1 = "1m"
    m5 = "5m"
    m15 = "15m"
    m60 = "60m"


class AlertMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roas: float = Field(ge=0)
    cpa: float = Field(ge=0)
    ctr: float = Field(ge=0)
    cvr: float = Field(ge=0)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str = Field(min_length=1)
    value: float


class AlertRaisedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: UUID
    campaign_id: str = Field(min_length=1)
    severity: AlertSeverity
    alert_type: AlertType
    window: AlertWindow

    baseline: AlertMetrics
    current: AlertMetrics
    anomaly_score: float = Field(ge=0)

    evidence: List[EvidenceItem] = Field(default_factory=list)


class AlertRaised(EventEnvelope[AlertRaisedPayload]):
    event_type: str = "ALERT_RAISED"