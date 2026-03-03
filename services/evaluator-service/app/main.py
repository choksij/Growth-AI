from __future__ import annotations

"""
main.py
-------
Evaluator Service — closes the self-improvement loop.

Flow:
  1. Consume actions.applied from Kafka
  2. Capture before-KPI snapshot (from kpi_rollups at action time)
  3. Schedule evaluation after EVAL_WINDOW_SECONDS
  4. After window: capture after-KPI snapshot
  5. Compute reward score_S = w1*ΔROAS + w2*ΔCPA - w3*volatility
  6. Determine success (score_S >= threshold)
  7. Update policy_bandit: success → alpha+1 | failure → beta+1
  8. Write to outcomes table
  9. Emit policy.updated event to Kafka
  10. 🔁 Next alert → Thompson Sampling now uses updated alpha/beta
"""

import asyncio
import contextlib
import os
from datetime import datetime, timezone
from typing import Dict, Optional
from uuid import UUID, uuid4

import orjson
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.responses import JSONResponse

from growthpilot_contracts.actions import ActionApplied
from growthpilot_contracts.policy import PolicyUpdated, PolicyUpdatedPayload, Reward, BanditUpdate as ContractBanditUpdate
from growthpilot_contracts.envelope import EventSource
from growthpilot_shared.config import load_settings
from growthpilot_shared.db import db_from_env
from growthpilot_shared.logging import configure_logging, get_logger, bind_trace_id
from growthpilot_shared.stream import ConsumerConfig, ProducerConfig, KafkaConsumer, KafkaProducer

from .bandit import compute_bandit_update
from .bucketing import extract_bucket_from_action, build_bucket_key
from .repository import EvaluatorRepository
from .reward import KPISnapshot, compute_reward
from .scheduler import EvaluationScheduler, PendingEvaluation, EVAL_WINDOW_SECONDS
from growthpilot_shared.datadog import dd
from .voice import VoiceBriefing

load_dotenv()
settings = load_settings()
configure_logging(
    level=settings.log_level,
    json_logs=settings.json_logs,
    service_name="evaluator-service",
)
log = get_logger("evaluator")


# ---- Settings ----

BROKERS       = os.getenv("REDPANDA_BROKERS", "localhost:9092")
INPUT_TOPIC   = os.getenv("EVALUATOR_INPUT_TOPIC",  "actions.applied")
OUTPUT_TOPIC  = os.getenv("EVALUATOR_OUTPUT_TOPIC", "policy.updated")
GROUP_ID      = os.getenv("EVALUATOR_GROUP_ID",     "growthpilot-evaluator")

COUNTERS: Dict[str, int] = {
    "consumed": 0,
    "parsed_ok": 0,
    "parsed_fail": 0,
    "evaluations_scheduled": 0,
    "evaluations_completed": 0,
    "kpi_missing": 0,
    "policy_updated": 0,
    "errors": 0,
}

from fastapi.middleware.cors import CORSMiddleware
app = FastAPI(title="GrowthPilot Evaluator Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

_consumer_task: Optional[asyncio.Task] = None
_scheduler: Optional[EvaluationScheduler] = None
_repo: Optional[EvaluatorRepository] = None
_producer: Optional[KafkaProducer] = None
_voice: VoiceBriefing = VoiceBriefing()


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---- Evaluation logic ----

async def run_evaluation(ev: PendingEvaluation) -> None:
    """
    Called by the scheduler when the evaluation window expires.
    This is the heart of the self-improvement loop.
    """
    assert _repo is not None
    assert _producer is not None

    COUNTERS["evaluations_completed"] += 1
    bind_trace_id(str(ev.action_id))

    log.info(
        "Running evaluation",
        extra={
            "action_id": str(ev.action_id),
            "campaign_id": ev.campaign_id,
            "action_type": ev.action_type,
            "bucket_key": ev.bucket_key,
        },
    )

    # Step 1: Get after-KPI snapshot
    after_snapshot = _repo.get_kpi_snapshot(
        workspace_id=ev.workspace_id,
        campaign_id=ev.campaign_id,
        since=ev.applied_at,
        limit=3,
    )

    if after_snapshot is None:
        COUNTERS["kpi_missing"] += 1
        log.warning(
            "No after-KPI data available, using before values (neutral outcome)",
            extra={"action_id": str(ev.action_id), "campaign_id": ev.campaign_id},
        )
        # Use before values → score_S = 0 → no update (conservative)
        after_snapshot = KPISnapshot(
            roas=ev.roas_before,
            cpa=ev.cpa_before,
        )

    before_snapshot = KPISnapshot(
        roas=ev.roas_before,
        cpa=ev.cpa_before,
    )

    # Step 2: Get ROAS history for volatility
    roas_history = _repo.get_roas_history(
        workspace_id=ev.workspace_id,
        campaign_id=ev.campaign_id,
        since=ev.applied_at,
        limit=10,
    )

    # Step 3: Compute reward score_S
    reward = compute_reward(
        before=before_snapshot,
        after=after_snapshot,
        roas_history=roas_history,
    )

    log.info(
        "Reward computed",
        extra={
            "action_id": str(ev.action_id),
            "score_S": round(reward.score_S, 4),
            "success": reward.success,
            "notes": reward.notes,
        },
    )

    # ---- Datadog: reward metrics ----
    dd.gauge("reward.score_s", reward.score_S, tags=[
        f"campaign:{ev.campaign_id}",
        f"action_type:{ev.action_type}",
        f"success:{str(reward.success).lower()}",
        f"bucket:{ev.bucket_key}",
    ])
    dd.gauge("reward.delta_roas", reward.delta_roas_norm, tags=[
        f"campaign:{ev.campaign_id}",
        f"action_type:{ev.action_type}",
    ])
    dd.increment("evaluations.completed", tags=[
        f"success:{str(reward.success).lower()}",
        f"action_type:{ev.action_type}",
    ])

    # Step 4: Get current bandit state
    alpha_cur, beta_cur = _repo.get_bandit_row(
        workspace_id=ev.workspace_id,
        bucket_key=ev.bucket_key,
        action_type=ev.action_type,
    )

    # Step 5: Compute bandit update
    bandit_update = compute_bandit_update(
        workspace_id=ev.workspace_id,
        bucket_key=ev.bucket_key,
        action_type=ev.action_type,
        alpha_current=alpha_cur,
        beta_current=beta_cur,
        success=reward.success,
        score_S=reward.score_S,
    )

    # Step 6: Persist to DB
    _repo.write_outcome(
        workspace_id=ev.workspace_id,
        action_id=ev.action_id,
        campaign_id=ev.campaign_id,
        action_type=ev.action_type,
        bucket_key=ev.bucket_key,
        reward=reward,
        eval_started_at=ev.applied_at,
        eval_window_seconds=EVAL_WINDOW_SECONDS,
    )
    _repo.update_bandit(
        bandit_update,
        action_id=ev.action_id,
        trace_id=str(ev.action_id),
        roas_before=reward.roas_before,
        roas_after=reward.roas_after,
        cpa_before=reward.cpa_before,
        cpa_after=reward.cpa_after,
    )
    # mark_action_evaluated removed — actions_status_check constraint doesn't allow EVALUATED

    COUNTERS["policy_updated"] += 1

    # Step 7: Emit policy.updated event
    policy_event = PolicyUpdated(
        schema_version="1.0",
        event_id=uuid4(),
        ts=_now_utc(),
        workspace_id=ev.workspace_id,
        source=EventSource.evaluator_service,
        trace_id=str(ev.action_id),
        payload=PolicyUpdatedPayload(
            action_id=ev.action_id,
            action_type=ev.action_type,
            bucket_key=ev.bucket_key,
            success=reward.success,
            reward=Reward(
                score_S=reward.score_S,
                roas_before=max(reward.roas_before, 0),
                roas_after=max(reward.roas_after, 0),
                cpa_before=max(reward.cpa_before, 0),
                cpa_after=max(reward.cpa_after, 0),
            ),
            bandit_update=ContractBanditUpdate(
                alpha_before=bandit_update.alpha_before,
                beta_before=bandit_update.beta_before,
                alpha_after=bandit_update.alpha_after,
                beta_after=bandit_update.beta_after,
            ),
        ),
    )

    await _producer.send(policy_event.model_dump(mode="json"))

    log.info(
        "policy.updated emitted — self-improvement loop closed",
        extra={
            "action_type": ev.action_type,
            "bucket_key": ev.bucket_key,
            "alpha": f"{bandit_update.alpha_before} → {bandit_update.alpha_after}",
            "beta":  f"{bandit_update.beta_before} → {bandit_update.beta_after}",
            "win_rate": round(bandit_update.win_rate, 3),
            "score_S": round(reward.score_S, 4),
            "success": reward.success,
        },
    )

    # ---- Datadog: bandit + policy metrics ----
    dd.gauge("bandit.alpha", bandit_update.alpha_after, tags=[
        f"bucket:{ev.bucket_key}",
        f"action_type:{ev.action_type}",
    ])
    dd.gauge("bandit.win_rate", bandit_update.win_rate, tags=[
        f"bucket:{ev.bucket_key}",
        f"action_type:{ev.action_type}",
    ])
    dd.increment("policy.updated", tags=[
        f"action_type:{ev.action_type}",
        f"success:{str(reward.success).lower()}",
    ])
    dd.event(
        "GrowthPilot Policy Updated",
        f"{ev.action_type} on {ev.bucket_key}: score_S={reward.score_S:.2f}, win_rate={bandit_update.win_rate:.2f}",
        tags=[f"action:{ev.action_type}", f"bucket:{ev.bucket_key}"],
        alert_type="success" if reward.success else "warning",
    )

    # ---- ElevenLabs: voice CMO briefing ----
    asyncio.create_task(_voice.generate(
        campaign_id=ev.campaign_id,
        action_type=ev.action_type,
        alert_type=ev.alert_type,
        score_S=reward.score_S,
        success=reward.success,
        roas_before=reward.roas_before,
        roas_after=reward.roas_after,
        cpa_before=reward.cpa_before,
        cpa_after=reward.cpa_after,
        win_rate=bandit_update.win_rate,
        alpha_after=bandit_update.alpha_after,
        beta_after=bandit_update.beta_after,
        notes=reward.notes,
    ))

    bind_trace_id(None)


# ---- Consumer loop ----

async def consumer_loop() -> None:
    global _repo, _producer, _scheduler

    db = db_from_env(settings)
    _repo = EvaluatorRepository(db=db)

    consumer = KafkaConsumer(
        ConsumerConfig(
            topic=INPUT_TOPIC,
            brokers=BROKERS,
            group_id=GROUP_ID,
        ),
        value_deserializer=lambda b: orjson.loads(b),
    )

    _producer = KafkaProducer(
        ProducerConfig(topic=OUTPUT_TOPIC, brokers=BROKERS)
    )

    _scheduler = EvaluationScheduler(callback=run_evaluation)

    await consumer.start()
    await _producer.start()
    await _scheduler.start()

    log.info(
        "Evaluator consumer started",
        extra={
            "brokers": BROKERS,
            "input": INPUT_TOPIC,
            "output": OUTPUT_TOPIC,
            "group": GROUP_ID,
            "eval_window_s": EVAL_WINDOW_SECONDS,
        },
    )

    try:
        async for raw in consumer.messages():
            COUNTERS["consumed"] += 1
            try:
                event = ActionApplied.model_validate(raw)
                COUNTERS["parsed_ok"] += 1
                await _handle_action_applied(event)
            except Exception as e:
                COUNTERS["parsed_fail"] += 1
                COUNTERS["errors"] += 1
                log.exception("Failed to handle action.applied: %s", e)

    finally:
        with contextlib.suppress(Exception):
            await consumer.stop()
        with contextlib.suppress(Exception):
            await _producer.stop()
        if _scheduler:
            await _scheduler.stop()
        db.close()


async def _handle_action_applied(event: ActionApplied) -> None:
    """
    When an action is applied, capture the before-KPI and schedule evaluation.
    """
    assert _repo is not None
    assert _scheduler is not None

    payload = event.payload
    action_id = payload.action_id
    campaign_id = payload.campaign_id

    bind_trace_id(event.trace_id)

    # action_type is NOT on ActionAppliedPayload — look it up from the actions table
    action_row = _repo.get_action_row(action_id)
    if action_row is None:
        log.warning(
            "No action row found in DB, skipping evaluation",
            extra={"action_id": str(action_id)},
        )
        return

    action_type = action_row["action_type"]

    # Extract bucket_key from the stored action row
    bucket_key = extract_bucket_from_action({
        "policy": action_row.get("policy") or {},
        "explainability": action_row.get("explainability") or {},
    })

    # Get before-KPI — look BACKWARD from action time to get baseline state
    before_snapshot = _repo.get_kpi_before(
        workspace_id=event.workspace_id,
        campaign_id=campaign_id,
        before=event.ts,
        limit=3,
    )

    if before_snapshot is None:
        log.warning(
            "No before-KPI snapshot, using defaults",
            extra={"action_id": str(action_id), "campaign_id": campaign_id},
        )
        before_snapshot = KPISnapshot(roas=1.0, cpa=50.0)

    # Schedule evaluation after window
    pending = PendingEvaluation(
        action_id=action_id,
        action_type=action_type,
        campaign_id=campaign_id,
        workspace_id=event.workspace_id,
        bucket_key=bucket_key,
        alert_type=(action_row.get("explainability") or {}).get("alert_type", "ROAS_DROP"),
        severity=(action_row.get("explainability") or {}).get("severity", "high"),
        applied_at=event.ts,
        roas_before=before_snapshot.roas,
        cpa_before=before_snapshot.cpa,
        explainability=action_row.get("explainability") or {},
        policy=action_row.get("policy") or {},
    )

    _scheduler.schedule(pending)
    COUNTERS["evaluations_scheduled"] += 1

    log.info(
        "Action applied — evaluation scheduled",
        extra={
            "action_id": str(action_id),
            "campaign_id": campaign_id,
            "action_type": action_type,
            "bucket_key": bucket_key,
            "roas_before": before_snapshot.roas,
            "eval_window_s": EVAL_WINDOW_SECONDS,
        },
    )

    bind_trace_id(None)


# ---- FastAPI ----

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/briefing/latest")
def briefing_latest():
    """
    Stream the latest voice briefing MP3.
    Use this during the demo: open http://localhost:8006/briefing/latest in browser
    or curl it to play the most recent CMO update.
    """
    from fastapi.responses import FileResponse
    from pathlib import Path

    latest = Path("/tmp/briefings/latest.mp3")
    if not latest.exists():
        return JSONResponse(
            {"error": "No briefing generated yet. Wait for an evaluation to complete."},
            status_code=404,
        )
    return FileResponse(
        path=str(latest),
        media_type="audio/mpeg",
        filename="growthpilot-briefing.mp3",
    )


@app.get("/briefing/text")
def briefing_text():
    """Return the latest briefing as text (for display on demo screen)."""
    if _voice.latest is None:
        return JSONResponse({"text": None, "generated_at": None})
    b = _voice.latest
    return JSONResponse({
        "text": b.text,
        "campaign_id": b.campaign_id,
        "action_type": b.action_type,
        "success": b.success,
        "score_S": b.score_S,
        "generated_at": b.generated_at.isoformat(),
    })


@app.get("/metrics")
def metrics():
    pending = _scheduler.pending_count if _scheduler else 0
    return JSONResponse({**COUNTERS, "pending_evaluations": pending})


@app.get("/pending")
def pending():
    """List currently pending evaluations (for demo/debug)."""
    if _scheduler is None:
        return JSONResponse([])
    return JSONResponse([
        {
            "action_id": str(ev.action_id),
            "campaign_id": ev.campaign_id,
            "action_type": ev.action_type,
            "seconds_until_ready": round(ev.seconds_until_ready, 1),
            "roas_before": ev.roas_before,
            "cpa_before": ev.cpa_before,
        }
        for ev in _scheduler._pending.values()
    ])


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
    uvicorn.run("app.main:app", host="0.0.0.0", port=8006, reload=False)