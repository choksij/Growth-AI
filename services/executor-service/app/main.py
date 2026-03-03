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

from growthpilot_contracts.actions import ActionProposed, ActionApplied, ActionAppliedPayload
from growthpilot_contracts.envelope import EventSource
from growthpilot_shared.config import load_settings
from growthpilot_shared.db import db_from_env
from growthpilot_shared.logging import configure_logging, get_logger, bind_trace_id
from growthpilot_shared.stream import ConsumerConfig, ProducerConfig, KafkaConsumer, KafkaProducer

from .guardrails import Guardrails
from .repository import ExecutorRepository
from .adapters.simulator_adapter import SimulatorAdapter

load_dotenv()
settings = load_settings()
configure_logging(level=settings.log_level, json_logs=settings.json_logs, service_name="executor-service")
log = get_logger("executor")


# -------------------------
# Service Settings
# -------------------------

@dataclass(frozen=True)
class ExecutorSettings:
    brokers: str = os.getenv("REDPANDA_BROKERS", "localhost:9092")
    input_topic: str = os.getenv("EXECUTOR_INPUT_TOPIC", "actions.proposed")
    output_topic: str = os.getenv("EXECUTOR_OUTPUT_TOPIC", "actions.applied")
    group_id: str = os.getenv("EXECUTOR_GROUP_ID", "growthpilot-executor")
    simulator_url: str = os.getenv("SIMULATOR_URL", "http://simulator-service:8002")


cfg = ExecutorSettings()

app = FastAPI(title="GrowthPilot Executor Service", version="0.1.0")

COUNTERS: Dict[str, int] = {
    "consumed": 0,
    "parsed_ok": 0,
    "parsed_fail": 0,
    "guardrail_passed": 0,
    "guardrail_blocked": 0,
    "applied": 0,
    "failed": 0,
    "errors": 0,
}

_consumer_task: Optional[asyncio.Task] = None
_guardrails = Guardrails()


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# -------------------------
# Core execution logic
# -------------------------

async def execute_action(
    proposed: ActionProposed,
    repo: ExecutorRepository,
    producer: KafkaProducer,
    adapter: SimulatorAdapter,
) -> None:
    p = proposed.payload
    action_id = str(p.action_id)
    campaign_id = p.campaign_id
    action_type = p.action_type.value
    trace_id = proposed.trace_id

    bind_trace_id(trace_id)

    log.info(
        "processing action",
        extra={
            "action_id": action_id,
            "campaign_id": campaign_id,
            "action_type": action_type,
            "idempotency_key": p.idempotency_key,
        }
    )

    # Write PROPOSED record first (audit trail)
    repo.upsert_action({
        "action_id": action_id,
        "proposed_schema_version": proposed.schema_version,
        "proposed_event_id": str(proposed.event_id),
        "proposed_ts": proposed.ts,
        "workspace_id": proposed.workspace_id,
        "proposed_source": proposed.source.value if hasattr(proposed.source, "value") else str(proposed.source),
        "trace_id": trace_id,
        "alert_id": str(p.alert_id),
        "campaign_id": campaign_id,
        "action_type": action_type,
        "parameters": dict(p.parameters),
        "constraints": p.constraints.model_dump(),
        "policy": p.policy.model_dump(),
        "explainability": p.explainability,
        "idempotency_key": p.idempotency_key,
        "applied_event_id": None,
        "applied_ts": None,
        "applied_source": None,
        "status": "PROPOSED",
        "applied_changes": None,
        "cooldown_until": None,
        "error": None,
    })

    # Check guardrails
    guard = _guardrails.check(
        campaign_id=campaign_id,
        action_type=action_type,
        idempotency_key=p.idempotency_key,
        max_budget_shift_pct=p.constraints.max_budget_shift_pct,
        cooldown_minutes=p.constraints.cooldown_minutes,
    )

    if not guard.allowed:
        COUNTERS["guardrail_blocked"] += 1
        log.warning("guardrail blocked action", extra={"reason": guard.reason, "action_id": action_id})

        repo.update_status({
            "action_id": action_id,
            "status": "REJECTED",
            "applied_event_id": str(uuid4()),
            "applied_ts": _now_utc(),
            "applied_source": "executor-service",
            "applied_changes": {"reason": guard.reason},
            "cooldown_until": None,
            "error": guard.reason,
        })
        return

    COUNTERS["guardrail_passed"] += 1

    # Apply action via adapter
    applied_event_id = str(uuid4())
    applied_ts = _now_utc()
    status = "APPLIED"
    applied_changes = {}
    error_msg = None
    cooldown_until = None

    try:
        applied_changes = await adapter.apply_action(
            action_type=action_type,
            parameters=dict(p.parameters),
            campaign_id=campaign_id,
        )

        cooldown_until = _guardrails.mark_applied(
            campaign_id=campaign_id,
            idempotency_key=p.idempotency_key,
            cooldown_minutes=p.constraints.cooldown_minutes,
        )

        COUNTERS["applied"] += 1
        log.info("action applied", extra={"action_id": action_id, "action_type": action_type})

    except Exception as e:
        status = "FAILED"
        error_msg = str(e)
        COUNTERS["failed"] += 1
        log.exception("action failed: %s", e)

    # Update DB
    repo.update_status({
        "action_id": action_id,
        "status": status,
        "applied_event_id": applied_event_id,
        "applied_ts": applied_ts,
        "applied_source": "executor-service",
        "applied_changes": applied_changes,
        "cooldown_until": cooldown_until,
        "error": error_msg,
    })

    # Emit ACTION_APPLIED event
    applied_payload = ActionAppliedPayload(
        action_id=p.action_id,
        campaign_id=campaign_id,
        status=status,
        applied_changes=applied_changes,
        cooldown_until=cooldown_until or _now_utc(),
        error=error_msg,
    )

    applied_event = ActionApplied(
        schema_version="1.0",
        event_id=uuid4(),
        ts=applied_ts,
        workspace_id=proposed.workspace_id,
        source=EventSource.executor_service,
        trace_id=trace_id,
        payload=applied_payload,
    )

    await producer.send(applied_event.model_dump(mode="json"))
    log.info("emitted ACTION_APPLIED", extra={"action_id": action_id, "status": status})


# -------------------------
# Consumer loop
# -------------------------

async def consumer_loop() -> None:
    db = db_from_env(settings)
    repo = ExecutorRepository(db=db)
    adapter = SimulatorAdapter(base_url=cfg.simulator_url)

    consumer = KafkaConsumer(
        ConsumerConfig(
            topic=cfg.input_topic,
            brokers=cfg.brokers,
            group_id=cfg.group_id,
        ),
        value_deserializer=lambda b: orjson.loads(b),
    )

    producer = KafkaProducer(ProducerConfig(topic=cfg.output_topic, brokers=cfg.brokers))

    await consumer.start()
    await producer.start()

    log.info(
        "executor consumer started",
        extra={
            "brokers": cfg.brokers,
            "input": cfg.input_topic,
            "output": cfg.output_topic,
            "group": cfg.group_id,
            "simulator_url": cfg.simulator_url,
        }
    )

    # GC guardrails every 5 minutes
    async def gc_loop():
        while True:
            await asyncio.sleep(300)
            _guardrails.gc()

    gc_task = asyncio.create_task(gc_loop())

    try:
        async for raw in consumer.messages():
            COUNTERS["consumed"] += 1
            try:
                proposed = ActionProposed.model_validate(raw)
                COUNTERS["parsed_ok"] += 1

                await execute_action(proposed, repo, producer, adapter)

            except Exception as e:
                COUNTERS["parsed_fail"] += 1
                COUNTERS["errors"] += 1
                log.exception("failed to process action: %s", e)
            finally:
                bind_trace_id(None)

    finally:
        gc_task.cancel()
        with contextlib.suppress(Exception):
            await gc_task
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
    uvicorn.run("app.main:app", host="0.0.0.0", port=8004, reload=False)