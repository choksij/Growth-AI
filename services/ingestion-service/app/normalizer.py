from __future__ import annotations

from typing import Any, Dict

from growthpilot_contracts import AdEvent


def normalize_ad_event(evt: AdEvent) -> AdEvent:
    """
    Normalize event to canonical form (lightweight).
    We keep the envelope identical, and only adjust safe payload formatting.
    """
    p = evt.payload

    # metadata always present
    if p.metadata is None:
        p.metadata = {}

    # currency normalized
    if p.currency is not None:
        p.currency = p.currency.upper()

    # normalize optional ids to None if empty strings slip through (defensive)
    for attr in ("adset_id", "ad_id", "segment_id"):
        v = getattr(p, attr)
        if isinstance(v, str) and v.strip() == "":
            setattr(p, attr, None)

    # ensure float
    p.value = float(p.value)

    return evt