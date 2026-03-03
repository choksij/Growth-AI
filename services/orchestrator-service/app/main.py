from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import orjson
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.responses import JSONResponse

from growthpilot_contracts.alert import AlertRaised
from growthpilot_contracts.actions import (
    ActionProposed,
    ActionProposedPayload,
    ActionType,
    ActionConstraints,
)
from growthpilot_contracts.envelope import EventSource
from growthpilot_shared.config import load_settings
from growthpilot_shared.db import db_from_env
from growthpilot_shared.logging import configure_logging, get_logger, bind_trace_id
from growthpilot_shared.stream import ConsumerConfig, ProducerConfig, KafkaConsumer, KafkaProducer

from .agents.airia_client import AiriaClient
from .agents.policy import ThompsonSamplingPolicy, build_bucket_key
from growthpilot_shared.datadog import dd

load_dotenv()
settings = load_settings()
configure_logging(
    level=settings.log_level,
    json_logs=settings.json_logs,
    service_name="orchestrator-service",
)
log = get_logger("orchestrator")


# -------------------------
# Service Settings
# -------------------------

@dataclass(frozen=True)
class OrchestratorSettings:
    brokers: str = os.getenv("REDPANDA_BROKERS", "localhost:9092")
    input_topic: str = os.getenv("ORCHESTRATOR_INPUT_TOPIC", "alerts.raised")
    output_topic: str = os.getenv("ORCHESTRATOR_OUTPUT_TOPIC", "actions.proposed")
    group_id: str = os.getenv("ORCHESTRATOR_GROUP_ID", "growthpilot-orchestrator")
    airia_api_key: str = os.getenv("AIRIA_API_KEY", "")
    airia_pipeline_id: str = os.getenv(
        "AIRIA_PIPELINE_ID", "6e88e303-94bc-4467-9564-0dce434e462e"
    )
    # Only act on HIGH severity alerts to avoid noise
    min_severity: str = os.getenv("ORCHESTRATOR_MIN_SEVERITY", "high")
    # Cooldown between actions per campaign (minutes)
    cooldown_minutes: int = int(os.getenv("ORCHESTRATOR_COOLDOWN_MINUTES", "30"))
    max_budget_shift_pct: float = float(os.getenv("ORCHESTRATOR_MAX_BUDGET_SHIFT_PCT", "0.08"))


cfg = OrchestratorSettings()

app = FastAPI(title="GrowthPilot Orchestrator Service", version="0.1.0")

COUNTERS: Dict[str, int] = {
    "consumed": 0,
    "parsed_ok": 0,
    "parsed_fail": 0,
    "skipped_severity": 0,
    "airia_calls": 0,
    "airia_fallback": 0,
    "actions_proposed": 0,
    "errors": 0,
}

_consumer_task: Optional[asyncio.Task] = None

# Track recently processed alert_ids to avoid duplicates
_processed_alerts: Dict[str, float] = {}
_DEDUP_TTL = 300  # 5 minutes


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _is_duplicate(alert_id: str) -> bool:
    import time
    now = time.time()
    # GC old entries
    expired = [k for k, v in _processed_alerts.items() if now - v > _DEDUP_TTL]
    for k in expired:
        del _processed_alerts[k]
    if alert_id in _processed_alerts:
        return True
    _processed_alerts[alert_id] = now
    return False


def _build_action_parameters(action_type: str, alert: AlertRaised) -> Dict[str, Any]:
    """Build action parameters based on action type and alert context."""
    campaign_id = alert.payload.campaign_id

    if action_type == "REALLOC_BUDGET":
        return {
            "budget_shift": {
                "from_segment": "seg_2",
                "to_segment": "seg_1",
                "pct": cfg.max_budget_shift_pct,
            },
            "pause_ad_ids": [],
            "campaign_id": campaign_id,
        }
    elif action_type == "PAUSE_CREATIVE":
        return {
            "pause_ad_ids": ["ad_underperforming"],
            "campaign_id": campaign_id,
        }
    elif action_type == "ADJUST_BID":
        return {
            "bid_adjustment": -0.10,
            "target_roas": alert.payload.baseline.roas * 0.9,
            "campaign_id": campaign_id,
        }
    elif action_type == "REFRESH_COPY":
        return {
            "copy_version": "v2",
            "campaign_id": campaign_id,
        }
    return {"campaign_id": campaign_id}


# -------------------------
# Core orchestration logic
# -------------------------

async def process_alert(
    alert: AlertRaised,
    policy: ThompsonSamplingPolicy,
    airia: AiriaClient,
    producer: KafkaProducer,
) -> None:
    payload = alert.payload
    alert_id = str(payload.alert_id)
    campaign_id = payload.campaign_id
    severity = payload.severity.value
    alert_type = payload.alert_type.value

    bind_trace_id(alert.trace_id)

    # Deduplication
    if _is_duplicate(alert_id):
        log.info("Skipping duplicate alert", extra={"alert_id": alert_id})
        return

    # Severity filter
    if severity not in ("high",) and cfg.min_severity == "high":
        COUNTERS["skipped_severity"] += 1
        log.info("Skipping low severity alert", extra={"severity": severity, "alert_id": alert_id})
        return

    log.info(
        "Processing alert",
        extra={
            "alert_id": alert_id,
            "campaign_id": campaign_id,
            "alert_type": alert_type,
            "severity": severity,
        },
    )

    # Step 1: Call Airia diagnosis agent
    COUNTERS["airia_calls"] += 1
    alert_context = {
        "campaign_id": campaign_id,
        "alert_type": alert_type,
        "severity": severity,
        "current_roas": payload.current.roas,
        "baseline_roas": payload.baseline.roas,
        "current_cpa": payload.current.cpa,
        "baseline_cpa": payload.baseline.cpa,
        "anomaly_score": payload.anomaly_score,
        "window": payload.window.value,
    }

    diagnosis = await airia.diagnose(alert_context)

    if diagnosis.get("source") == "fallback":
        COUNTERS["airia_fallback"] += 1

    # ---- Datadog: Airia diagnosis metrics ----
    dd.increment("airia.calls", tags=[
        f"source:{diagnosis.get('source', 'unknown')}",
        f"action:{diagnosis.get('recommended_action', 'unknown')}",
    ])
    dd.gauge("airia.confidence", float(diagnosis.get("confidence", 0.0)), tags=[
        f"action:{diagnosis.get('recommended_action', 'unknown')}",
        f"alert_type:{payload.alert_type.value}",
        f"campaign:{campaign_id}",
    ])

    log.info(
        "Diagnosis complete",
        extra={
            "campaign_id": campaign_id,
            "diagnosis": diagnosis.get("diagnosis"),
            "recommended_action": diagnosis.get("recommended_action"),
            "confidence": diagnosis.get("confidence"),
            "source": diagnosis.get("source"),
        },
    )

    # Step 2: Thompson Sampling to select action
    bucket_key = build_bucket_key(
        alert_type=alert_type,
        severity=severity,
    )

    bandit = policy.select_action(
        workspace_id=alert.workspace_id,
        bucket_key=bucket_key,
        airia_recommendation=diagnosis.get("recommended_action"),
        airia_confidence=float(diagnosis.get("confidence", 0.0)),
    )

    chosen_action = bandit.chosen_action
    action_id = uuid4()

    # Step 3: Build idempotency key
    ts_minute = _now_utc().strftime("%Y-%m-%dT%H:%MZ")
    idempotency_key = f"{campaign_id}|{chosen_action}|{ts_minute}"

    # Step 4: Build ACTION_PROPOSED event
    action_payload = ActionProposedPayload(
        action_id=action_id,
        alert_id=payload.alert_id,
        campaign_id=campaign_id,
        action_type=ActionType(chosen_action),
        parameters=_build_action_parameters(chosen_action, alert),
        constraints=ActionConstraints(
            max_budget_shift_pct=cfg.max_budget_shift_pct,
            cooldown_minutes=cfg.cooldown_minutes,
        ),
        policy={
            "bucket_key": bucket_key,
            "thompson_samples": {k: round(v, 4) for k, v in bandit.thompson_samples.items()},
            "chosen_reason": bandit.chosen_reason,
        },
        explainability={
            "airia_diagnosis": diagnosis.get("diagnosis"),
            "airia_reasoning": diagnosis.get("reasoning"),
            "airia_confidence": diagnosis.get("confidence"),
            "airia_source": diagnosis.get("source"),
            "alert_type": alert_type,
            "severity": severity,
            "anomaly_score": payload.anomaly_score,
        },
        idempotency_key=idempotency_key,
    )

    proposed_event = ActionProposed(
        schema_version="1.0",
        event_id=uuid4(),
        ts=_now_utc(),
        workspace_id=alert.workspace_id,
        source=EventSource.orchestrator_service,
        trace_id=alert.trace_id,
        payload=action_payload,
    )

    # Step 5: Emit to actions.proposed
    await producer.send(proposed_event.model_dump(mode="json"))
    COUNTERS["actions_proposed"] += 1

    # ---- Datadog: action proposed metrics ----
    dd.increment("actions.proposed", tags=[
        f"action_type:{chosen_action}",
        f"campaign:{campaign_id}",
        f"severity:{payload.severity.value}",
    ])
    dd.event(
        "GrowthPilot Action Proposed",
        f"Campaign {campaign_id}: {chosen_action} (confidence={diagnosis.get('confidence', 0):.2f}, airia_source={diagnosis.get('source', 'unknown')})",
        tags=[f"campaign:{campaign_id}", f"action:{chosen_action}"],
    )

    log.info(
        "ACTION_PROPOSED emitted",
        extra={
            "action_id": str(action_id),
            "action_type": chosen_action,
            "campaign_id": campaign_id,
            "bucket_key": bucket_key,
            "idempotency_key": idempotency_key,
        },
    )


# -------------------------
# Consumer loop
# -------------------------

async def consumer_loop() -> None:
    db = db_from_env(settings)
    policy = ThompsonSamplingPolicy(db=db)
    airia = AiriaClient(
        api_key=cfg.airia_api_key,
        pipeline_id=cfg.airia_pipeline_id,
    )

    consumer = KafkaConsumer(
        ConsumerConfig(
            topic=cfg.input_topic,
            brokers=cfg.brokers,
            group_id=cfg.group_id,
        ),
        value_deserializer=lambda b: orjson.loads(b),
    )

    producer = KafkaProducer(
        ProducerConfig(topic=cfg.output_topic, brokers=cfg.brokers)
    )

    await consumer.start()
    await producer.start()

    log.info(
        "orchestrator consumer started",
        extra={
            "brokers": cfg.brokers,
            "input": cfg.input_topic,
            "output": cfg.output_topic,
            "group": cfg.group_id,
            "min_severity": cfg.min_severity,
            "airia_pipeline_id": cfg.airia_pipeline_id,
        },
    )

    try:
        async for raw in consumer.messages():
            COUNTERS["consumed"] += 1
            try:
                alert = AlertRaised.model_validate(raw)
                COUNTERS["parsed_ok"] += 1
                await process_alert(alert, policy, airia, producer)
            except Exception as e:
                COUNTERS["parsed_fail"] += 1
                COUNTERS["errors"] += 1
                log.exception("failed to process alert: %s", e)
            finally:
                bind_trace_id(None)

    finally:
        with contextlib.suppress(Exception):
            await consumer.stop()
        with contextlib.suppress(Exception):
            await producer.stop()
        db.close()


# -------------------------
# FastAPI app
# -------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return JSONResponse(COUNTERS)


@app.on_event("startup")
async def _startup():
    global _consumer_task
    if _consumer_task is None:
        _consumer_task = asyncio.create_task(consumer_loop())


@app.on_event("shutdown")
async def _shutdown():
    global _consumer_task
    if _consumer_task:
        _consumer_task.cancel()
        with contextlib.suppress(Exception):
            await _consumer_task
        _consumer_task = None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8005, reload=False)