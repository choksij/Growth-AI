"""
datadog.py  —  shared/python/growthpilot_shared/datadog.py
-----------------------------------------------------------
Thin DogStatsD wrapper for GrowthPilot services.
All custom metrics flow through here.

Usage in any service:
    from growthpilot_shared.datadog import dd
    dd.increment("actions.proposed", tags=["action_type:REALLOC_BUDGET"])
    dd.gauge("reward.score_s", 1.23, tags=["campaign:cmp_001", "success:true"])

If DD_ENABLED=false or agent unreachable, all calls are silent no-ops.
"""

from __future__ import annotations

import os
from typing import Optional

_enabled = os.getenv("DD_ENABLED", "true").lower() == "true"
_statsd = None


def _get_statsd():
    global _statsd
    if _statsd is not None:
        return _statsd
    if not _enabled:
        return None
    try:
        from datadog import initialize, statsd
        initialize(
            statsd_host=os.getenv("DD_AGENT_HOST", "datadog-agent"),
            statsd_port=int(os.getenv("DD_DOGSTATSD_PORT", "8125")),
        )
        _statsd = statsd
        return _statsd
    except Exception:
        return None


class _DD:
    """Safe DogStatsD client — never raises, always tags with service/env."""

    def __init__(self):
        self._service = os.getenv("DD_SERVICE_NAME", "growthpilot")
        self._env = os.getenv("DD_ENV", "demo")

    def _base_tags(self, tags: list[str]) -> list[str]:
        return [f"service:{self._service}", f"env:{self._env}"] + (tags or [])

    def increment(self, metric: str, value: int = 1, tags: Optional[list[str]] = None) -> None:
        try:
            s = _get_statsd()
            if s:
                s.increment(f"growthpilot.{metric}", value, tags=self._base_tags(tags or []))
        except Exception:
            pass

    def gauge(self, metric: str, value: float, tags: Optional[list[str]] = None) -> None:
        try:
            s = _get_statsd()
            if s:
                s.gauge(f"growthpilot.{metric}", value, tags=self._base_tags(tags or []))
        except Exception:
            pass

    def histogram(self, metric: str, value: float, tags: Optional[list[str]] = None) -> None:
        try:
            s = _get_statsd()
            if s:
                s.histogram(f"growthpilot.{metric}", value, tags=self._base_tags(tags or []))
        except Exception:
            pass

    def event(self, title: str, text: str, tags: Optional[list[str]] = None, alert_type: str = "info") -> None:
        """Send a Datadog event — shows up in the event stream and on dashboards."""
        try:
            s = _get_statsd()
            if s:
                s.event(title, text, alert_type=alert_type, tags=self._base_tags(tags or []))
        except Exception:
            pass


# Singleton — import and use directly
dd = _DD()