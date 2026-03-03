from __future__ import annotations

from typing import Any, Dict

from growthpilot_shared.logging import get_logger

log = get_logger("executor.adapters.meta")


class MetaAdapter:
    """
    Meta (Facebook) Ads API adapter (stub).

    In production this would call:
    - Meta Marketing API v19+
    - App access token auth
    - facebook-business Python SDK

    Swap this in by setting PLATFORM=meta on the campaign record.
    """

    async def apply_action(
        self,
        action_type: str,
        parameters: Dict[str, Any],
        campaign_id: str,
    ) -> Dict[str, Any]:
        log.info(
            "MetaAdapter.apply_action (stub)",
            extra={"action_type": action_type, "campaign_id": campaign_id},
        )
        # TODO: implement with facebook-business SDK
        # from facebook_business.adobjects.campaign import Campaign
        # ...
        raise NotImplementedError(
            "Meta Ads adapter is a stub. "
            "Implement with facebook-business SDK to go live."
        )