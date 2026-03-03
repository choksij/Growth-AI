from __future__ import annotations

from typing import Any, Dict, Optional

import orjson
import psycopg
from psycopg.rows import dict_row

from growthpilot_contracts import AdEvent


INSERT_SQL = """
INSERT INTO growthpilot.ad_events (
  schema_version, event_id, event_type, ts, workspace_id, source, trace_id,
  campaign_id, adset_id, ad_id, segment_id,
  value, currency, metadata, raw_event
)
VALUES (
  %(schema_version)s, %(event_id)s, %(event_type)s, %(ts)s, %(workspace_id)s, %(source)s, %(trace_id)s,
  %(campaign_id)s, %(adset_id)s, %(ad_id)s, %(segment_id)s,
  %(value)s, %(currency)s, %(metadata)s::jsonb, %(raw_event)s::jsonb
)
ON CONFLICT (event_id) DO NOTHING;
"""

# DB constraint:
# CHECK (event_type = ANY (ARRAY['IMPRESSION','CLICK','CONVERSION','SPEND','REVENUE']))
_ALLOWED_EVENT_TYPES = {"IMPRESSION", "CLICK", "CONVERSION", "SPEND", "REVENUE"}


def _enum_value(x: Any) -> Any:
    """Enum -> Enum.value ; otherwise return x as-is."""
    return getattr(x, "value", x)


def _to_text(x: Any) -> Optional[str]:
    """Any -> stripped string; None if empty."""
    x = _enum_value(x)
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


def _normalize_event_type(evt: AdEvent) -> str:
    """
    Normalize event_type to exactly one of:
      IMPRESSION | CLICK | CONVERSION | SPEND | REVENUE

    Handles:
      - Enum values: AdEventType.IMPRESSION -> "IMPRESSION"
      - Repr-like strings: "AdEventType.IMPRESSION" -> "IMPRESSION"
      - Lowercase values -> uppercased
    """
    raw = _to_text(getattr(evt, "event_type", None)) or ""
    s = raw.strip()

    # If we got something like "AdEventType.IMPRESSION", take the last token.
    if "." in s:
        s = s.split(".")[-1]

    s = s.upper()

    if s not in _ALLOWED_EVENT_TYPES:
        raise ValueError(f"Invalid event_type for DB: {raw!r} -> normalized {s!r}")

    return s


def _normalize_source(evt: AdEvent) -> str:
    raw = _to_text(getattr(evt, "source", None))
    if not raw:
        return "unknown"
    # store as simple lowercase string
    if "." in raw:
        raw = raw.split(".")[-1]
    return raw.strip().lower()


def _normalize_currency(payload: Any) -> Optional[str]:
    cur = _to_text(getattr(payload, "currency", None))
    if not cur:
        return None
    if "." in cur:
        cur = cur.split(".")[-1]
    cur = cur.strip().upper()
    return cur[:3] if cur else None


class AdEventRepository:
    def __init__(self, conninfo: str):
        self._conninfo = conninfo

    def insert_ad_event(self, evt: AdEvent) -> None:
        payload = evt.payload

        event_type = _normalize_event_type(evt)
        source = _normalize_source(evt)
        currency_str = _normalize_currency(payload)

        data: Dict[str, Any] = {
            "schema_version": str(evt.schema_version),  # ensure text matches regex check
            "event_id": str(evt.event_id),
            "event_type": event_type,
            "ts": evt.ts,
            "workspace_id": evt.workspace_id,
            "source": source,
            "trace_id": _to_text(evt.trace_id),

            "campaign_id": payload.campaign_id,
            "adset_id": payload.adset_id,
            "ad_id": payload.ad_id,
            "segment_id": payload.segment_id,

            "value": payload.value,
            "currency": currency_str,
            "metadata": orjson.dumps(payload.metadata).decode("utf-8"),
            "raw_event": orjson.dumps(evt.model_dump(mode="json")).decode("utf-8"),
        }

        with psycopg.connect(self._conninfo, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, data)
                conn.commit()