from __future__ import annotations

from typing import Any, Dict

from growthpilot_shared.logging import get_logger

from .adapters.simulator_adapter import SimulatorAdapter
from .adapters.google_adapter import GoogleAdapter
from .adapters.meta_adapter import MetaAdapter

log = get_logger("executor.action_router")


class ActionRouter:
    """
    Routes an action to the correct platform adapter.

    In production, the source field on the workspace/campaign
    determines which adapter gets called. For the hackathon demo,
    everything routes to the simulator adapter.
    """

    def __init__(self, simulator_url: str):
        self._adapters = {
            "simulator": SimulatorAdapter(base_url=simulator_url),
            "google":    GoogleAdapter(),
            "meta":      MetaAdapter(),
        }

    def get_adapter(self, source: str = "simulator"):
        adapter = self._adapters.get(source)
        if adapter is None:
            log.warning("Unknown source '%s', falling back to simulator", source)
            return self._adapters["simulator"]
        return adapter