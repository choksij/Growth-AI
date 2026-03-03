from __future__ import annotations

from typing import Any, Dict

from growthpilot_contracts import AdEvent


def validate_ad_event(data: Dict[str, Any]) -> AdEvent:
    """
    Validate incoming JSON dict against the shared contract.

    Raises:
      pydantic.ValidationError if invalid
    """
    return AdEvent.model_validate(data)