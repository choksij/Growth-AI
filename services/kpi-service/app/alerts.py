from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, List
from uuid import uuid4

from growthpilot_contracts.alert import (
    AlertRaised,
    AlertSeverity,
    AlertType,
    AlertWindow,
    AlertMetrics,
    EvidenceItem,
)


def _safe_float(v: Optional[float]) -> float:
    """
    Contract requires non-null float >= 0.
    If metric is None, treat as 0.0 (safe fallback).
    """
    try:
        if v is None:
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _map_window(window_s: int) -> AlertWindow:
    """
    Map seconds -> contract enum.
    """
    mapping = {
        60: AlertWindow.m1,
        300: AlertWindow.m5,
        900: AlertWindow.m15,
        3600: AlertWindow.m60,
    }
    return mapping.get(window_s, AlertWindow.m1)


def _map_alert_type(t: str) -> AlertType:
    mapping = {
        "roas_drop": AlertType.ROAS_DROP,
        "cpa_spike": AlertType.CPA_SPIKE,
    }
    if t not in mapping:
        raise ValueError(f"Unsupported alert_type: {t}")
    return mapping[t]


def _map_severity(s: str) -> AlertSeverity:
    return AlertSeverity(s)


@dataclass(frozen=True)
class Alert:
    alert_type: str          # "roas_drop" | "cpa_spike"
    severity: str            # "high" | "medium" | "low"
    workspace_id: str
    campaign_id: str
    ts: datetime
    message: str
    metrics: Dict[str, Any]
    trace_id: Optional[str] = None

    def to_contract(
        self,
        *,
        window_start: datetime,
        window_s: int,
        baseline: Dict[str, Any],
        current: Dict[str, Any],
        anomaly_score: float,
        source: str = "kpi-service",
    ) -> AlertRaised:

        alert_type_enum = _map_alert_type(self.alert_type)
        severity_enum = _map_severity(self.severity)
        window_enum = _map_window(window_s)

        # Build required AlertMetrics (must contain all 4 fields)
        baseline_metrics = AlertMetrics(
            roas=_safe_float(baseline.get("roas_mean")),
            cpa=_safe_float(baseline.get("cpa_mean")),
            ctr=_safe_float(baseline.get("ctr_mean")),
            cvr=_safe_float(baseline.get("cvr_mean")),
        )

        current_metrics = AlertMetrics(
            roas=_safe_float(current.get("roas")),
            cpa=_safe_float(current.get("cpa")),
            ctr=_safe_float(current.get("ctr")),
            cvr=_safe_float(current.get("cvr")),
        )

        evidence: List[EvidenceItem] = []

        # Add simple evidence from metrics dict
        for k, v in self.metrics.items():
            try:
                evidence.append(EvidenceItem(signal=str(k), value=float(v)))
            except Exception:
                continue

        data = {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "ts": self.ts,
            "workspace_id": self.workspace_id,
            "source": source,
            "trace_id": self.trace_id,
            "payload": {
                "alert_id": str(uuid4()),
                "campaign_id": self.campaign_id,
                "severity": severity_enum,
                "alert_type": alert_type_enum,
                "window": window_enum,
                "baseline": baseline_metrics,
                "current": current_metrics,
                "anomaly_score": float(abs(anomaly_score)),
                "evidence": evidence,
            },
        }

        return AlertRaised.model_validate(data)