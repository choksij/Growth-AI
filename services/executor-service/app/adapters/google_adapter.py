from __future__ import annotations

from typing import Any, Dict

from growthpilot_shared.logging import get_logger

log = get_logger("executor.adapters.google")


class GoogleAdapter:
    """
    Google Ads API adapter (stub).

    In production this would call:
    - Google Ads API v17+ (campaign budget, bidding strategy, ad group status)
    - OAuth2 service account auth
    - google-ads Python client library

    Swap this in by setting PLATFORM=google on the campaign record.
    """

    async def apply_action(
        self,
        action_type: str,
        parameters: Dict[str, Any],
        campaign_id: str,
    ) -> Dict[str, Any]:
        log.info(
            "GoogleAdapter.apply_action (stub)",
            extra={"action_type": action_type, "campaign_id": campaign_id},
        )
        # TODO: implement with google-ads client
        # from google.ads.googleads.client import GoogleAdsClient
        # client = GoogleAdsClient.load_from_env()
        # ...
        raise NotImplementedError(
            "Google Ads adapter is a stub. "
            "Implement with google-ads Python client to go live."
        )