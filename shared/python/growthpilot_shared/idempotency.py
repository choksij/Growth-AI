from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Optional

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .config import SharedSettings
from .logging import get_logger

log = get_logger("shared.stream")


JsonDict = Dict[str, Any]


def _json_load(b: bytes) -> JsonDict:
    return orjson.loads(b)


def _json_dump(d: Any) -> bytes:
    return orjson.dumps(d)


@dataclass
class KafkaProducer:
    brokers: str
    _producer: Optional[AIOKafkaProducer] = None

    async def start(self) -> None:
        if self._producer:
            return
        self._producer = AIOKafkaProducer(bootstrap_servers=self.brokers, value_serializer=_json_dump)
        await self._producer.start()

    async def stop(self) -> None:
        if not self._producer:
            return
        await self._producer.stop()
        self._producer = None

    async def send(self, topic: str, value: Any, key: bytes | None = None) -> None:
        if not self._producer:
            raise RuntimeError("Producer not started")
        await self._producer.send_and_wait(topic, value=value, key=key)


@dataclass
class KafkaConsumer:
    brokers: str
    topic: str
    group_id: str
    auto_offset_reset: str = "earliest"
    enable_auto_commit: bool = True

    _consumer: Optional[AIOKafkaConsumer] = None

    async def start(self) -> None:
        if self._consumer:
            return
        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.brokers,
            group_id=self.group_id,
            auto_offset_reset=self.auto_offset_reset,
            enable_auto_commit=self.enable_auto_commit,
            value_deserializer=_json_load,
        )
        await self._consumer.start()

    async def stop(self) -> None:
        if not self._consumer:
            return
        await self._consumer.stop()
        self._consumer = None

    async def messages(self) -> AsyncIterator[JsonDict]:
        if not self._consumer:
            raise RuntimeError("Consumer not started")
        async for msg in self._consumer:
            yield msg.value


def kafka_from_env(settings: SharedSettings) -> str:
    return settings.redpanda_brokers