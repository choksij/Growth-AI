from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from growthpilot_contracts import AdEvent


def floor_to_window(ts: datetime, window_s: int) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    epoch = int(ts.timestamp())
    start = epoch - (epoch % window_s)
    return datetime.fromtimestamp(start, tz=timezone.utc)


def _enum_value(x):
    return getattr(x, "value", x)


def normalize_event_type(evt: AdEvent) -> str:
    """
    Makes this robust to:
      - Enum: AdEventType.IMPRESSION -> "IMPRESSION"
      - String: "AdEventType.IMPRESSION" -> "IMPRESSION"
      - Lowercase: "impression" -> "IMPRESSION"
    """
    raw = _enum_value(getattr(evt, "event_type", "")) or ""
    s = str(raw).strip()
    if "." in s:
        s = s.split(".")[-1]
    return s.upper()


@dataclass
class Rollup:
    workspace_id: str
    campaign_id: str
    window_start: datetime
    window_s: int

    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    spend: float = 0.0
    revenue: float = 0.0

    def apply(self, evt: AdEvent) -> None:
        t = normalize_event_type(evt)

        # NOTE: payload.value is meaningful for SPEND/REVENUE.
        # For impression/click/conversion we treat them as counters.
        v = float(getattr(evt.payload, "value", 0.0) or 0.0)

        if t == "IMPRESSION":
            self.impressions += 1
        elif t == "CLICK":
            self.clicks += 1
        elif t == "CONVERSION":
            self.conversions += 1
        elif t == "SPEND":
            self.spend += v
        elif t == "REVENUE":
            self.revenue += v

    @property
    def ctr(self) -> Optional[float]:
        return (self.clicks / self.impressions) if self.impressions > 0 else None

    @property
    def cvr(self) -> Optional[float]:
        return (self.conversions / self.clicks) if self.clicks > 0 else None

    @property
    def cpa(self) -> Optional[float]:
        return (self.spend / self.conversions) if self.conversions > 0 else None

    @property
    def roas(self) -> Optional[float]:
        return (self.revenue / self.spend) if self.spend > 0 else None


def rollup_key(evt: AdEvent, window_s: int) -> Tuple[str, str, datetime]:
    ws = str(evt.workspace_id)
    cid = str(evt.payload.campaign_id)
    wstart = floor_to_window(evt.ts, window_s)
    return ws, cid, wstart