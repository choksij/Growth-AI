from __future__ import annotations

"""
bucketing.py
------------
Deterministic bucket_key computation.
Must produce identical keys to orchestrator/agents/policy.py build_bucket_key().

Format: "objective=roas|severity=high|type=prospecting"
"""

_ROAS_ALERTS = {"ROAS_DROP", "PACING_ANOMALY", "CVR_DROP"}
_CPA_ALERTS  = {"CPA_SPIKE"}


def build_bucket_key(
    *,
    alert_type: str,
    severity: str,
    campaign_type: str = "prospecting",
) -> str:
    """
    Build the bucket key used to look up policy_bandit rows.

    Args:
        alert_type:    e.g. ROAS_DROP, CPA_SPIKE
        severity:      low | medium | high
        campaign_type: prospecting | retargeting (default: prospecting)

    Returns:
        e.g. "objective=roas|severity=high|type=prospecting"
    """
    obj = "cpa" if alert_type.upper() in _CPA_ALERTS else "roas"
    sev = severity.lower()
    ctype = campaign_type.lower()
    return f"objective={obj}|severity={sev}|type={ctype}"


def extract_bucket_from_action(action_row: dict) -> str:
    """
    Extract or reconstruct bucket_key from an action DB row.
    Prefers the stored policy.bucket_key, falls back to reconstruction.
    """
    policy = action_row.get("policy") or {}

    # Primary: directly stored in policy JSON
    if isinstance(policy, dict) and "bucket_key" in policy:
        return policy["bucket_key"]

    # Fallback: reconstruct from explainability
    explainability = action_row.get("explainability") or {}
    if isinstance(explainability, dict):
        alert_type = explainability.get("alert_type", "ROAS_DROP")
        severity   = explainability.get("severity", "high")
        return build_bucket_key(alert_type=alert_type, severity=severity)

    # Last resort
    return "objective=roas|severity=high|type=prospecting"