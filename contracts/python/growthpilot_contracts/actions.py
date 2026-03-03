from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .envelope import EventEnvelope


class ActionType(str, Enum):
    PAUSE_CREATIVE = "PAUSE_CREATIVE"
    REALLOC_BUDGET = "REALLOC_BUDGET"
    ADJUST_BID = "ADJUST_BID"
    REFRESH_COPY = "REFRESH_COPY"


class ActionConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_budget_shift_pct: float = Field(ge=0, le=1)
    cooldown_minutes: int = Field(ge=0)


class ActionPolicyBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_key: str = Field(min_length=1)
    thompson_samples: Dict[str, float] = Field(
        description="Sampled probabilities for each action type."
    )
    chosen_reason: str = Field(min_length=1)


class ActionProposedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    alert_id: UUID
    campaign_id: str = Field(min_length=1)

    action_type: ActionType
    parameters: Dict[str, Any] = Field(default_factory=dict)

    constraints: ActionConstraints
    policy: ActionPolicyBlock

    explainability: Optional[Dict[str, Any]] = None
    idempotency_key: str = Field(min_length=1)


class ActionProposed(EventEnvelope[ActionProposedPayload]):
    event_type: str = "ACTION_PROPOSED"


class ActionAppliedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    campaign_id: str = Field(min_length=1)

    status: str = Field(pattern="^(APPLIED|REJECTED|FAILED)$")
    applied_changes: Dict[str, Any] = Field(default_factory=dict)

    cooldown_until: datetime
    error: Optional[str] = None


class ActionApplied(EventEnvelope[ActionAppliedPayload]):
    event_type: str = "ACTION_APPLIED"