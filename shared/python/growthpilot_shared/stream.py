from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .logging import get_logger

log = get_logger("shared.stream")


def _orjson_loads(b: bytes) -> Any:
    return orjson.loads(b)


def _orjson_dumps(d: Any) -> bytes:
    return orjson.dumps(d)


@dataclass(frozen=True)
class ConsumerConfig:
    topic: str
    brokers: str
    group_id: str
    auto_offset_reset: str = "earliest"
    enable_auto_commit: bool = True


@dataclass(frozen=True)
class ProducerConfig:
    topic: str
    brokers: str


class KafkaConsumer:
    def __init__(
        self,
        cfg: ConsumerConfig,
        *,
        value_deserializer: Callable[[bytes], Any] = _orjson_loads,
    ):
        self.cfg = cfg
        self._consumer = AIOKafkaConsumer(
            cfg.topic,
            bootstrap_servers=cfg.brokers,
            group_id=cfg.group_id,
            auto_offset_reset=cfg.auto_offset_reset,
            enable_auto_commit=cfg.enable_auto_commit,
            value_deserializer=value_deserializer,
        )

    async def start(self) -> None:
        log.info(
            "consumer starting",
            extra={"brokers": self.cfg.brokers, "topic": self.cfg.topic, "group": self.cfg.group_id},
        )
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def messages(self) -> AsyncIterator[Any]:
        async for msg in self._consumer:
            yield msg.value


class KafkaProducer:
    def __init__(
        self,
        cfg: ProducerConfig,
        *,
        value_serializer: Callable[[Any], bytes] = _orjson_dumps,
    ):
        self.cfg = cfg
        self._producer = AIOKafkaProducer(
            bootstrap_servers=cfg.brokers,
            value_serializer=value_serializer,
        )

    async def start(self) -> None:
        log.info("producer starting", extra={"brokers": self.cfg.brokers, "topic": self.cfg.topic})
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def send(self, value: Any, *, topic: Optional[str] = None) -> None:
        t = topic or self.cfg.topic
        await self._producer.send_and_wait(t, value)