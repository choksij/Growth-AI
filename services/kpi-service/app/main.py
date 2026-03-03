from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

import orjson
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.responses import JSONResponse

from growthpilot_contracts import AdEvent
from growthpilot_shared.config import load_settings
from growthpilot_shared.db import db_from_env
from growthpilot_shared.logging import configure_logging, get_logger
from growthpilot_shared.stream import ConsumerConfig, ProducerConfig, KafkaConsumer, KafkaProducer
from growthpilot_shared.tracing import try_enable_ddtrace

from .rollups import Rollup, rollup_key
from .anomaly import zscore, AnomalyResult
from .alerts import Alert
from .repository import KpiRepository

load_dotenv()

settings = load_settings()
configure_logging(level=settings.log_level, json_logs=settings.json_logs, service_name="kpi-service")
log = get_logger("kpi")

try_enable_ddtrace(
    enabled=settings.ddtrace_enabled,
    service="kpi-service",
    env=settings.env,
    version=settings.ddtrace_version,
)

# -------------------------
# Service Settings
# -------------------------

@dataclass(frozen=True)
class KpiSettings:
    brokers: str = os.getenv("REDPANDA_BROKERS", "localhost:9092")
    input_topic: str = os.getenv("KPI_INPUT_TOPIC", "ad.events.normalized")
    group_id: str = os.getenv("KPI_GROUP_ID", "growthpilot-kpi")
    alerts_topic: str = os.getenv("KPI_ALERTS_TOPIC", "alerts.raised")

    window_s: int = int(os.getenv("KPI_WINDOW_SECONDS", "60"))
    history_minutes: int = int(os.getenv("KPI_HISTORY_MINUTES", "30"))

    # For demo, use 1.5-2.0. Production can be 3.0+
    z_threshold: float = float(os.getenv("KPI_ZSCORE_THRESHOLD", "2.0"))

    emit_alerts: bool = os.getenv("KPI_EMIT_ALERTS", "true").strip().lower() == "true"


cfg = KpiSettings()

app = FastAPI(title="GrowthPilot KPI Service", version="0.1.0")

COUNTERS: Dict[str, int] = {
    "consumed": 0,
    "parsed_ok": 0,
    "parsed_fail": 0,
    "rollups_upserted": 0,
    "alerts_raised": 0,
    "errors": 0,
}

_consumer_task: Optional[asyncio.Task] = None


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_ad_event(raw: Dict[str, Any]) -> AdEvent:
    return AdEvent.model_validate(raw)


def _window_size(window_s: int) -> str:
    """
    Maps seconds to the only values allowed by your DB constraint:
      1m, 5m, 15m, 60m
    """
    if window_s <= 60:
        return "1m"
    if window_s <= 300:
        return "5m"
    if window_s <= 900:
        return "15m"
    return "60m"


# -------------------------
# Internal state
# -------------------------
RollupKey = Tuple[str, str, datetime]  # (workspace_id, campaign_id, window_start)
_rollups: Dict[RollupKey, Rollup] = {}
_rollups_lock = asyncio.Lock()


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _safe_z(current: Optional[float], hist: list[float]) -> Optional[float]:
    """
    zscore-safe wrapper:
    - if current is None -> None
    - if not enough history -> None
    - if zscore throws -> None (do not crash alerting loop)
    """
    if current is None:
        return None
    if len(hist) < 5:
        return None
    try:
        return zscore(float(current), [float(x) for x in hist])
    except Exception:
        return None


def _baseline_from_history(hist: Dict[str, list[float]]) -> Dict[str, Any]:
    return {
        "roas_mean": _mean(hist.get("roas", [])),
        "cpa_mean": _mean(hist.get("cpa", [])),
        "ctr_mean": _mean(hist.get("ctr", [])),
        "n_roas": len(hist.get("roas", [])),
        "n_cpa": len(hist.get("cpa", [])),
        "n_ctr": len(hist.get("ctr", [])),
    }


async def flush_rollups(repo: KpiRepository, producer: Optional[KafkaProducer]) -> None:
    async with _rollups_lock:
        items = list(_rollups.items())
        _rollups.clear()

    for (ws, cid, wstart), r in items:
        row = {
            "workspace_id": ws,
            "campaign_id": cid,
            "window_start": wstart,
            "window_s": r.window_s,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "conversions": r.conversions,
            "spend": float(r.spend),
            "revenue": float(r.revenue),
            "ctr": r.ctr,
            "cvr": r.cvr,
            "cpa": r.cpa,
            "roas": r.roas,
        }

        try:
            repo.upsert_rollup(row)
            COUNTERS["rollups_upserted"] += 1
        except Exception as e:
            COUNTERS["errors"] += 1
            log.exception("rollup upsert failed: %s", e)
            continue

        try:
            hist = repo.fetch_history(
                workspace_id=ws,
                campaign_id=cid,
                window_s=row["window_s"],
                minutes=cfg.history_minutes,
                exclude_window_start=wstart,
                limit=60,
            )

            roas_z = _safe_z(r.roas, hist["roas"])
            cpa_z = _safe_z(r.cpa, hist["cpa"])
            ctr_z = _safe_z(r.ctr, hist["ctr"])

            a = AnomalyResult(roas_z=roas_z, cpa_z=cpa_z, ctr_z=ctr_z)
            baseline = _baseline_from_history(hist)

            # current snapshot for contract payload + DB current json
            current = {
                "campaign_id": cid,
                "roas": r.roas,
                "cpa": r.cpa,
                "ctr": r.ctr,
                "spend": float(r.spend),
                "revenue": float(r.revenue),
                "impressions": int(r.impressions),
                "clicks": int(r.clicks),
                "conversions": int(r.conversions),
                "roas_z": a.roas_z,
                "cpa_z": a.cpa_z,
                "ctr_z": a.ctr_z,
            }

            alerts: list[tuple[Alert, float]] = []

            # ---- Rule 1: Z-score based (preferred)
            if a.roas_z is not None and a.roas_z <= -cfg.z_threshold:
                alerts.append(
                    (
                        Alert(
                            alert_type="roas_drop",
                            severity="high",
                            workspace_id=ws,
                            campaign_id=cid,
                            ts=_now_utc(),
                            message=f"ROAS dropped (z={a.roas_z:.2f})",
                            metrics={
                                "roas": r.roas,
                                "roas_z": a.roas_z,
                                "spend": r.spend,
                                "revenue": r.revenue,
                            },
                        ),
                        abs(a.roas_z),
                    )
                )

            if a.cpa_z is not None and a.cpa_z >= cfg.z_threshold:
                alerts.append(
                    (
                        Alert(
                            alert_type="cpa_spike",
                            severity="high",
                            workspace_id=ws,
                            campaign_id=cid,
                            ts=_now_utc(),
                            message=f"CPA spiked (z={a.cpa_z:.2f})",
                            metrics={
                                "cpa": r.cpa,
                                "cpa_z": a.cpa_z,
                                "spend": r.spend,
                                "conversions": r.conversions,
                            },
                        ),
                        abs(a.cpa_z),
                    )
                )

            # ---- Rule 2: Fallback when history is too small / z is None
            if not any(al.alert_type == "roas_drop" for al, _ in alerts):
                bm = baseline.get("roas_mean")
                if r.roas is not None and bm is not None and bm > 0 and float(r.roas) < 0.7 * float(bm):
                    score = float(bm - float(r.roas)) / float(bm)
                    alerts.append(
                        (
                            Alert(
                                alert_type="roas_drop",
                                severity="high",
                                workspace_id=ws,
                                campaign_id=cid,
                                ts=_now_utc(),
                                message=f"ROAS dropped vs baseline (current={r.roas:.3f}, baseline_mean={bm:.3f})",
                                metrics={"roas": r.roas, "baseline_roas_mean": bm, "ratio": float(r.roas) / float(bm)},
                            ),
                            score,
                        )
                    )

            if not any(al.alert_type == "cpa_spike" for al, _ in alerts):
                bm = baseline.get("cpa_mean")
                if r.cpa is not None and bm is not None and bm > 0 and float(r.cpa) > 1.3 * float(bm):
                    score = float(float(r.cpa) - float(bm)) / float(bm)
                    alerts.append(
                        (
                            Alert(
                                alert_type="cpa_spike",
                                severity="high",
                                workspace_id=ws,
                                campaign_id=cid,
                                ts=_now_utc(),
                                message=f"CPA spiked vs baseline (current={r.cpa:.3f}, baseline_mean={bm:.3f})",
                                metrics={"cpa": r.cpa, "baseline_cpa_mean": bm, "ratio": float(r.cpa) / float(bm)},
                            ),
                            score,
                        )
                    )

            # ---- Persist + Emit
            for al, score in alerts:
                # build contract once (also gives us DB-valid enums)
                contract = al.to_contract(
                    window_start=wstart,
                    window_s=row["window_s"],
                    baseline=baseline,
                    current=current,
                    anomaly_score=float(score),
                    source="kpi-service",
                )

                # raw_event stored in DB
                raw_event = contract.model_dump(mode="json")

                # DB constraints:
                # - alert_type must be enum strings like "ROAS_DROP"
                # - severity must be lowercase
                db_alert_type = contract.payload.alert_type
                db_severity = al.severity.lower()
                if db_severity not in {"low", "medium", "high"}:
                    db_severity = "high"

                # optional evidence
                evidence = [
                    {"kind": "message", "text": al.message},
                    {"kind": "metrics", "value": al.metrics},
                ]

                try:
                    repo.insert_alert_row(
                        {
                            "alert_id": str(uuid4()),
                            "schema_version": "1.0",
                            "event_id": str(uuid4()),
                            "event_type": "ALERT_RAISED",
                            "ts": al.ts,
                            "workspace_id": ws,
                            "source": "kpi-service",
                            "trace_id": al.trace_id,
                            "campaign_id": cid,
                            "severity": db_severity,
                            "alert_type": db_alert_type,
                            "window_size": _window_size(row["window_s"]),
                            "baseline": json.dumps(baseline),
                            "current": json.dumps(current),
                            "anomaly_score": float(score),
                            "evidence": json.dumps(evidence),
                            "status": "OPEN",
                            "resolved_at": None,
                            "raw_event": json.dumps(raw_event),
                        }
                    )
                    COUNTERS["alerts_raised"] += 1
                except Exception as e:
                    COUNTERS["errors"] += 1
                    log.exception("alert insert failed: %s", e)
                    continue

                if producer is not None:
                    try:
                        await producer.send(contract.model_dump(mode="json"))
                    except Exception as e:
                        COUNTERS["errors"] += 1
                        log.exception("alert emit failed: %s", e)

        except Exception as e:
            COUNTERS["errors"] += 1
            log.exception("anomaly/alerting failed: %s", e)


async def consumer_loop() -> None:
    db = db_from_env(settings)
    repo = KpiRepository(db=db)
    repo.ensure_schema()

    consumer = KafkaConsumer(
        ConsumerConfig(topic=cfg.input_topic, brokers=cfg.brokers, group_id=cfg.group_id),
        value_deserializer=lambda b: orjson.loads(b),
    )

    producer: Optional[KafkaProducer] = None
    if cfg.emit_alerts:
        producer = KafkaProducer(ProducerConfig(topic=cfg.alerts_topic, brokers=cfg.brokers))

    await consumer.start()
    if producer is not None:
        await producer.start()

    log.info(
        "kpi consumer started",
        extra={
            "brokers": cfg.brokers,
            "topic": cfg.input_topic,
            "group": cfg.group_id,
            "alerts_topic": cfg.alerts_topic,
            "window_s": cfg.window_s,
            "history_minutes": cfg.history_minutes,
            "z_threshold": cfg.z_threshold,
        },
    )

    async def flusher() -> None:
        while True:
            await asyncio.sleep(cfg.window_s)
            await flush_rollups(repo, producer)

    flush_task = asyncio.create_task(flusher())

    try:
        async for raw in consumer.messages():
            COUNTERS["consumed"] += 1
            try:
                evt = _parse_ad_event(raw)
                COUNTERS["parsed_ok"] += 1

                ws, cid, wstart = rollup_key(evt, cfg.window_s)
                key: RollupKey = (ws, cid, wstart)

                async with _rollups_lock:
                    r = _rollups.get(key)
                    if r is None:
                        r = Rollup(workspace_id=ws, campaign_id=cid, window_start=wstart, window_s=cfg.window_s)
                        _rollups[key] = r
                    r.apply(evt)

            except Exception as e:
                COUNTERS["parsed_fail"] += 1
                COUNTERS["errors"] += 1
                log.exception("failed to parse/apply event: %s", e)

    finally:
        flush_task.cancel()
        with contextlib.suppress(Exception):
            await flush_task

        with contextlib.suppress(Exception):
            await consumer.stop()

        if producer is not None:
            with contextlib.suppress(Exception):
                await producer.stop()

        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return JSONResponse(COUNTERS)


# NOTE: keeping on_event for now (your logs show a DeprecationWarning; we can convert to lifespan later)
@app.on_event("startup")
async def _startup_deprecated():
    global _consumer_task
    if _consumer_task is None:
        _consumer_task = asyncio.create_task(consumer_loop())


@app.on_event("shutdown")
async def _shutdown_deprecated():
    global _consumer_task
    if _consumer_task:
        _consumer_task.cancel()
        with contextlib.suppress(Exception):
            await _consumer_task
        _consumer_task = None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8003, reload=False)