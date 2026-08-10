"""One-source HA STT activation canary worker."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from .budget import SubmissionBudget
from .config import HA_STT_CANARY_TOPIC, HaSttCanaryConfig, SourceConfig
from .ha_stt import HaSttClient
from .matcher import WakeWordMatch, match_wake_word_prefix
from .source import PCM_RATE, PCM_WIDTH, FfmpegPcmSource
from .vad import VAD_CHUNK_BYTES, capture_speech_segment, new_silero_detector

LOGGER = logging.getLogger(__name__)


class Publisher(Protocol):
    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None: ...


def build_command_payload(
    source: SourceConfig,
    match: WakeWordMatch,
    request_id: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "event": "wake_command_detected",
        "request_id": request_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "source_id": source.id,
        "room": source.room,
        "backend": "ha_stt",
        "wake_word_id": match.wake_word_id,
        "command": match.command,
        "canary": True,
    }


def _default_source_factory(config: SourceConfig) -> FfmpegPcmSource:
    return FfmpegPcmSource(config, chunk_bytes=VAD_CHUNK_BYTES)


class HaSttCanaryWorker:
    def __init__(
        self,
        source_config: SourceConfig,
        canary_config: HaSttCanaryConfig,
        publisher: Publisher,
        *,
        source_factory: Callable[[SourceConfig], Any] = _default_source_factory,
        detector_factory: Callable[[], Any] = new_silero_detector,
        stt_factory: Callable[[str], Any] = HaSttClient,
        budget: SubmissionBudget | None = None,
        minimum_backoff: float = 1,
        maximum_backoff: float = 30,
    ) -> None:
        self.source_config = source_config
        self.canary_config = canary_config
        self.publisher = publisher
        self.source_factory = source_factory
        self.detector_factory = detector_factory
        self.stt_factory = stt_factory
        self.budget = budget or SubmissionBudget(canary_config)
        self.minimum_backoff = minimum_backoff
        self.maximum_backoff = maximum_backoff

    async def run_once(self, stop_event: asyncio.Event) -> tuple[bool, float]:
        source = self.source_factory(self.source_config)
        detector = self.detector_factory()
        try:
            await source.start()
            audio = await capture_speech_segment(source.read_chunk, detector, stop_event)
            if audio is None:
                return False, 0
            audio_seconds = len(audio) / (PCM_RATE * PCM_WIDTH)
            decision = self.budget.consume(audio_seconds)
            if not decision.allowed:
                LOGGER.warning(
                    "HA STT privacy budget blocked submission source_id=%s reason=%s "
                    "retry_seconds=%.1f",
                    self.source_config.id,
                    decision.reason,
                    decision.retry_after,
                )
                return False, decision.retry_after

            request_id = str(uuid4())
            transcript = await self.stt_factory(self.canary_config.pipeline_id).transcribe(audio)
            match = match_wake_word_prefix(transcript, self.canary_config.wake_words)
            if match is None:
                LOGGER.info(
                    "HA STT candidate did not match a configured wake-word source_id=%s "
                    "request_id=%s",
                    self.source_config.id,
                    request_id,
                )
                return True, self.canary_config.cooldown_seconds

            payload = build_command_payload(self.source_config, match, request_id)
            encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            publish_backoff = self.minimum_backoff
            while not stop_event.is_set():
                try:
                    await self.publisher.publish(
                        HA_STT_CANARY_TOPIC,
                        encoded_payload,
                        qos=1,
                        retain=False,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOGGER.warning(
                        "HA STT diagnostic publish failed source_id=%s request_id=%s "
                        "error_type=%s retry_seconds=%.1f",
                        self.source_config.id,
                        request_id,
                        type(exc).__name__,
                        publish_backoff,
                    )
                    await _wait_or_stop(stop_event, publish_backoff)
                    publish_backoff = min(publish_backoff * 2, self.maximum_backoff)
            if stop_event.is_set():
                return False, 0
            LOGGER.info(
                "HA STT wake command matched source_id=%s room=%s wake_word_id=%s request_id=%s",
                payload["source_id"],
                payload["room"],
                payload["wake_word_id"],
                payload["request_id"],
            )
            return True, self.canary_config.cooldown_seconds
        finally:
            await source.close()

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        backoff = self.minimum_backoff
        while not stop_event.is_set():
            try:
                processed, wait_seconds = await self.run_once(stop_event)
                if stop_event.is_set():
                    break
                backoff = self.minimum_backoff
                if processed or wait_seconds:
                    await _wait_or_stop(stop_event, wait_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "HA STT canary session failed source_id=%s error_type=%s retry_seconds=%.3f",
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
