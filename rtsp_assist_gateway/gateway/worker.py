"""One-source passive-canary worker and diagnostic payload contract."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from wyoming.wake import Detection

from .config import CANARY_TOPIC, PassiveCanaryConfig, SourceConfig
from .source import FfmpegPcmSource
from .wyoming_client import WyomingDetector

LOGGER = logging.getLogger(__name__)


class Publisher(Protocol):
    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None: ...


def build_detection_payload(source: SourceConfig, detection: Detection) -> dict[str, Any]:
    return {
        "version": 1,
        "event": "wake_detected",
        "request_id": str(uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "source_id": source.id,
        "room": source.room,
        "backend": "microwakeword",
        "model": detection.name,
        "canary": True,
    }


class PassiveCanaryWorker:
    def __init__(
        self,
        source_config: SourceConfig,
        canary_config: PassiveCanaryConfig,
        publisher: Publisher,
        *,
        source_factory: Callable[[SourceConfig], Any] = FfmpegPcmSource,
        detector_factory: Callable[[str, int, tuple[str, ...]], Any] = WyomingDetector,
        minimum_backoff: float = 1,
        maximum_backoff: float = 30,
    ) -> None:
        self.source_config = source_config
        self.canary_config = canary_config
        self.publisher = publisher
        self.source_factory = source_factory
        self.detector_factory = detector_factory
        self.minimum_backoff = minimum_backoff
        self.maximum_backoff = maximum_backoff

    async def run_once(self, stop_event: asyncio.Event) -> bool:
        source = self.source_factory(self.source_config)
        detector = self.detector_factory(
            self.canary_config.wyoming_host,
            self.canary_config.wyoming_port,
            self.canary_config.models,
        )
        try:
            await source.start()
            detection = await detector.detect(source.read_chunk, stop_event)
            if detection is None:
                return False
            payload = build_detection_payload(self.source_config, detection)
            encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            publish_backoff = self.minimum_backoff
            while not stop_event.is_set():
                try:
                    await self.publisher.publish(CANARY_TOPIC, encoded_payload, qos=1, retain=False)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOGGER.warning(
                        "Diagnostic publish failed source_id=%s request_id=%s "
                        "error_type=%s retry_seconds=%.1f",
                        self.source_config.id,
                        payload["request_id"],
                        type(exc).__name__,
                        publish_backoff,
                    )
                    await _wait_or_stop(stop_event, publish_backoff)
                    publish_backoff = min(publish_backoff * 2, self.maximum_backoff)
            if stop_event.is_set():
                return False
            LOGGER.info(
                "Passive wake detection source_id=%s room=%s model=%s request_id=%s",
                payload["source_id"],
                payload["room"],
                payload["model"],
                payload["request_id"],
            )
            return True
        finally:
            await source.close()

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        backoff = self.minimum_backoff
        while not stop_event.is_set():
            try:
                detected = await self.run_once(stop_event)
                if stop_event.is_set():
                    break
                backoff = self.minimum_backoff
                if detected:
                    await _wait_or_stop(stop_event, self.canary_config.cooldown_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "Passive canary session failed source_id=%s error_type=%s retry_seconds=%.3f",
                    self.source_config.id,
                    type(exc).__name__,
                    backoff,
                )
                await _wait_or_stop(stop_event, backoff)
                backoff = min(backoff * 2, self.maximum_backoff)


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    if seconds <= 0:
        return
    with suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
