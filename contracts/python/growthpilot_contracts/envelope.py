from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Generic, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PayloadT = TypeVar("PayloadT", bound=BaseModel)


class EventSource(str, Enum):
    simulator = "simulator"
    google = "google"
    meta = "meta"
    tiktok = "tiktok"
    ingestion_service = "ingestion-service"
    kpi_service = "kpi-service"
    orchestrator_service = "orchestrator-service"
    executor_service = "executor-service"
    evaluator_service = "evaluator-service"


class EventEnvelope(BaseModel, Generic[PayloadT]):
    """
    Canonical event envelope for all GrowthPilot topics.

    Must match contracts/schemas/envelope.schema.json
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^[0-9]+\.[0-9]+$", default="1.0")
    event_id: UUID
    event_type: str
    ts: datetime
    workspace_id: str = Field(min_length=1)
    source: EventSource
    trace_id: Optional[str] = Field(default=None, min_length=1)

    payload: PayloadT

    def as_dict(self) -> Dict[str, Any]:
        """Useful for producing JSON to Redpanda."""
        return self.model_dump(mode="json")