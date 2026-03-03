from __future__ import annotations

import asyncio
import os

import orjson
from aiokafka import AIOKafkaProducer
from dotenv import load_dotenv
import uvicorn

from growthpilot_shared.config import load_settings
from growthpilot_shared.logging import configure_logging, get_logger

from .generator import EventGenerator, GeneratorConfig
from .world import World
from .api import build_api


load_dotenv()
shared = load_settings()

configure_logging(level=shared.log_level, json_logs=True, service_name="simulator-service")
log = get_logger("simulator")


class Settings:
    redpanda_brokers: str = os.getenv("REDPANDA_BROKERS", shared.redpanda_brokers)
    topic: str = os.getenv("SIM_TOPIC", "ad.events")
    workspace_id: str = os.getenv("SIM_WORKSPACE_ID", "ws_demo")
    eps: float = float(os.getenv("SIM_RATE_EPS", "10"))
    campaigns: int = int(os.getenv("SIM_CAMPAIGNS", "3"))
    seed: str = os.getenv("SIM_SEED", "")
    enable_api: bool = os.getenv("SIM_ENABLE_API", "true").lower() == "true"
    api_port: int = int(os.getenv("SIM_API_PORT", "8002"))

    @property
    def seed_int(self):
        return int(self.seed) if self.seed.strip() != "" else None


settings = Settings()


async def produce_loop(world: World) -> None:
    cfg = GeneratorConfig(workspace_id=settings.workspace_id, eps=settings.eps, seed=settings.seed_int)
    gen = EventGenerator(world, cfg)

    delay = 1.0 / max(cfg.eps, 0.1)

    while True:
        producer = AIOKafkaProducer(
            bootstrap_servers=settings.redpanda_brokers,
            value_serializer=lambda d: orjson.dumps(d),
        )

        try:
            log.info("Connecting Kafka producer to %s ...", settings.redpanda_brokers)
            await producer.start()
            log.info("Kafka producer connected topic=%s eps=%s", settings.topic, settings.eps)

            count = 0
            for evt in gen.stream():
                await producer.send_and_wait(settings.topic, evt.model_dump(mode="json"))
                count += 1
                if count % 50 == 0:
                    log.info(
                        "Produced %d events campaign=%s scenario=%s",
                        count,
                        evt.payload.campaign_id,
                        (world.active.name if world.active else "none"),
                    )
                await asyncio.sleep(delay)

        except Exception as e:
            log.exception("Producer loop error (will retry): %s", e)
            await asyncio.sleep(3)

        finally:
            try:
                await producer.stop()
            except Exception:
                pass


async def run_api(world: World) -> None:
    api = build_api(world)
    config = uvicorn.Config(api, host="0.0.0.0", port=settings.api_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    world = World(num_campaigns=settings.campaigns, seed=settings.seed_int)

    tasks = [asyncio.create_task(produce_loop(world))]

    if settings.enable_api:
        tasks.append(asyncio.create_task(run_api(world)))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())