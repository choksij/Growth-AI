from __future__ import annotations

from typing import Any, Dict

import httpx

from growthpilot_shared.logging import get_logger

log = get_logger("executor.adapters.simulator")

# Maps our action types to simulator scenario names + what state changes to make
ACTION_TO_SCENARIO: Dict[str, str] = {
    "REALLOC_BUDGET":  "roas_drop",      # clear scenario to simulate recovery
    "PAUSE_CREATIVE":  "creative_fatigue",
    "ADJUST_BID":      "cpa_spike",
    "REFRESH_COPY":    "creative_fatigue",
}


class SimulatorAdapter:
    """
    Applies actions to the simulator service via its HTTP API.

    In production this would be replaced with Google/Meta Ads API calls.
    The interface is identical — just swap the adapter.
    """

    def __init__(self, base_url: str = "http://simulator-service:8002"):
        self.base_url = base_url.rstrip("/")

    async def apply_action(
        self,
        action_type: str,
        parameters: Dict[str, Any],
        campaign_id: str,
    ) -> Dict[str, Any]:
        """
        Apply an action to the simulator.
        Returns a dict describing what changed (for applied_changes field).
        """
        async with httpx.AsyncClient(timeout=10.0) as client:

            # First get current world state for before/after comparison
            world_before = await self._get_world(client)

            if action_type == "REALLOC_BUDGET":
                # Budget reallocation = clear the degrading scenario → simulate recovery
                result = await self._clear_scenario(client)
                action_desc = {
                    "action": "realloc_budget",
                    "effect": "cleared_active_scenario",
                    "budget_shift_pct": parameters.get("budget_shift", {}).get("pct", 0.08),
                    "from_segment": parameters.get("budget_shift", {}).get("from_segment", "seg_2"),
                    "to_segment": parameters.get("budget_shift", {}).get("to_segment", "seg_1"),
                }

            elif action_type == "PAUSE_CREATIVE":
                # Pause creative = clear scenario (removes fatigue)
                result = await self._clear_scenario(client)
                action_desc = {
                    "action": "pause_creative",
                    "effect": "cleared_active_scenario",
                    "paused_ad_ids": parameters.get("pause_ad_ids", []),
                }

            elif action_type == "ADJUST_BID":
                # Adjust bid = clear CPA spike scenario
                result = await self._clear_scenario(client)
                action_desc = {
                    "action": "adjust_bid",
                    "effect": "cleared_active_scenario",
                    "bid_adjustment": parameters.get("bid_adjustment", -0.1),
                }

            elif action_type == "REFRESH_COPY":
                # Refresh copy = clear creative fatigue
                result = await self._clear_scenario(client)
                action_desc = {
                    "action": "refresh_copy",
                    "effect": "cleared_active_scenario",
                    "new_copy_version": parameters.get("copy_version", "v2"),
                }

            else:
                raise ValueError(f"Unknown action_type: {action_type}")

            world_after = await self._get_world(client)

            return {
                **action_desc,
                "campaign_id": campaign_id,
                "world_before": {
                    "active_scenario": world_before.get("active_scenario"),
                },
                "world_after": {
                    "active_scenario": world_after.get("active_scenario"),
                },
                "adapter": "simulator",
            }

    async def _get_world(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        try:
            resp = await client.get(f"{self.base_url}/world")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning("Could not fetch world state: %s", e)
            return {}

    async def _clear_scenario(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        resp = await client.post(f"{self.base_url}/scenario/clear")
        resp.raise_for_status()
        return resp.json()

    async def _apply_scenario(
        self, client: httpx.AsyncClient, name: str, duration_s: int = 60
    ) -> Dict[str, Any]:
        resp = await client.post(
            f"{self.base_url}/scenario/apply",
            json={"name": name, "duration_s": duration_s},
        )
        resp.raise_for_status()
        return resp.json()