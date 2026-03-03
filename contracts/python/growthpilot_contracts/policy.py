from __future__ import annotations

from enum import Enum
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .envelope import EventEnvelope
from .actions import ActionType


class Reward(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roas_before: float = Field(ge=0)
    roas_after: float = Field(ge=0)
    cpa_before: float = Field(ge=0)
    cpa_after: float = Field(ge=0)
    score_S: float


class BanditUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alpha_before: int = Field(ge=0)
    beta_before: int = Field(ge=0)
    alpha_after: int = Field(ge=0)
    beta_after: int = Field(ge=0)


class PolicyUpdatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    bucket_key: str = Field(min_length=1)
    action_type: ActionType

    success: bool
    reward: Reward
    bandit_update: BanditUpdate


class PolicyUpdated(EventEnvelope[PolicyUpdatedPayload]):
    event_type: str = "POLICY_UPDATED"