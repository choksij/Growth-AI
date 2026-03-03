import asyncio
import contextlib
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from growthpilot_contracts.ad_event import AdEvent
from growthpilot_shared.config import load_settings
from growthpilot_shared.logging import configure_logging, get_logger, bind_trace_id

from .validators import validate_ad_event
from .normalizer import normalize_ad_event
from .repository import AdEventRepository


load_dotenv()
shared = load_settings()

# configure structured logging (JSON by default)
configure_logging(level=shared.log_level, json_logs=True, service_name="ingestion-service")
log = get_logger("ingestion")


# ==========================
# Settings (service-specific)
# ==========================

@dataclass(frozen=True)
class IngestionSettings:
    redpanda_brokers: str = os.getenv("REDPANDA_BROKERS", shared.redpanda_brokers)
    group_id: str = os.getenv("INGESTION_GROUP_ID", "growthpilot-ingestion")
    input_topic: str = os.getenv("INGESTION_INPUT_TOPIC", "ad.events")
    output_topic: str = os.getenv("INGESTION_OUTPUT_TOPIC", "ad.events.normalized")
    emit_normalized: bool = os.getenv("INGESTION_EMIT_NORMALIZED", "false").lower() == "true"

    @property
    def pg_conninfo(self) -> str:
        return shared.pg_conninfo


settings = IngestionSettings()
app = FastAPI(title="GrowthPilot Ingestion Service", version="0.1.0")


# ==========================
# Metrics
# ==========================

COUNTERS: Dict[str, int] = {
    "consumed": 0,
    "validated_ok": 0,
    "validated_fail": 0,
    "inserted": 0,
    "emitted_normalized": 0,
    "errors": 0,
}

_consumer_task: Optional[asyncio.Task] = None


# ==========================
# API
# ==========================

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return COUNTERS


# ==========================
# Consumer Loop
# ==========================

async def consumer_loop() -> None:
    log.info("consumer_loop started")

    repo = AdEventRepository(settings.pg_conninfo)

    while True:
        consumer = AIOKafkaConsumer(
            settings.input_topic,
            bootstrap_servers=settings.redpanda_brokers,
            group_id=settings.group_id,
            enable_auto_commit=True,
            auto_offset_reset="earliest",
            value_deserializer=lambda b: orjson.loads(b),
        )

        producer: Optional[AIOKafkaProducer] = None
        if settings.emit_normalized:
            producer = AIOKafkaProducer(
                bootstrap_servers=settings.redpanda_brokers,
                value_serializer=lambda d: orjson.dumps(d),
            )

        try:
            log.info(
                "Connecting Kafka consumer broker=%s topic=%s group=%s",
                settings.redpanda_brokers,
                settings.input_topic,
                settings.group_id,
            )

            await consumer.start()
            log.info("Kafka consumer connected.")

            if producer:
                await producer.start()
                log.info("Kafka producer connected topic=%s", settings.output_topic)

            async for msg in consumer:
                COUNTERS["consumed"] += 1

                try:
                    raw: Dict[str, Any] = msg.value

                    evt: AdEvent = validate_ad_event(raw)
                    COUNTERS["validated_ok"] += 1

                    evt = normalize_ad_event(evt)

                    # bind trace_id into structured logs for this message
                    bind_trace_id(getattr(evt, "trace_id", None))

                    # IMPORTANT: repository normalizes enums -> DB constraint strings
                    await run_in_threadpool(repo.insert_ad_event, evt)
                    COUNTERS["inserted"] += 1

                    if producer:
                        await producer.send_and_wait(
                            settings.output_topic,
                            evt.model_dump(mode="json"),
                        )
                        COUNTERS["emitted_normalized"] += 1

                except Exception as e:
                    COUNTERS["errors"] += 1
                    COUNTERS["validated_fail"] += 1
                    log.exception("Error processing message: %s", e)
                finally:
                    # clear trace_id so it doesn't leak across messages
                    bind_trace_id(None)

        except Exception as e:
            log.exception("Consumer loop error (will retry): %s", e)
            await asyncio.sleep(3)

        finally:
            with contextlib.suppress(Exception):
                await consumer.stop()

            if producer:
                with contextlib.suppress(Exception):
                    await producer.stop()


# ==========================
# Startup / Shutdown
# ==========================

@app.on_event("startup")
async def on_startup():
    global _consumer_task
    log.info("Startup: launching consumer loop task")
    _consumer_task = asyncio.create_task(consumer_loop())


@app.on_event("shutdown")
async def on_shutdown():
    global _consumer_task
    if _consumer_task:
        log.info("Shutdown: cancelling consumer loop task")
        _consumer_task.cancel()
        with contextlib.suppress(Exception):
            await _consumer_task


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=False)