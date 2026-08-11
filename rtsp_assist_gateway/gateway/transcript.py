"""Bounded generic transcript-event contract and MQTT delivery."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from .budget import SubmissionBudget
from .config import TRANSCRIPT_TOPIC, SourceConfig, TranscriptEventsConfig
from .ha_stt import HaSttClient
from .source import PCM_RATE, PCM_WIDTH, FfmpegPcmSource
from .vad import VAD_CHUNK_BYTES, capture_speech_segment, new_silero_detector

LOGGER = logging.getLogger(__name__)
MAX_EVENT_BYTES = 16 * 1024
MAX_PUBLISH_ATTEMPTS = 3


class Publisher(Protocol):
    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None: ...


@dataclass(frozen=True)
class TranscriptResult:
    transcript: str | None
    encoded_event: str | None
    retry_after: float = 0.0

    @property
    def budget_allowed(self) -> bool:
        return self.retry_after <= 0


@dataclass
class TranscriptSegmentJob:
    audio: bytes
    completed_at: float
    result: asyncio.Future[TranscriptResult | None]
    published: asyncio.Future[bool]


def encode_transcript_event(
    source: SourceConfig,
    transcript: str,
    duration_seconds: float,
    *,
    event_id: str | None = None,
    timestamp: str | None = None,
) -> str | None:
    """Return one complete bounded JSON event, or None for empty STT output."""
    text = transcript.strip()
    if not text:
        return None
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("transcript duration must be finite and positive")
    payload = {
        "version": 1,
        "event": "transcript_observed",
        "event_id": event_id or str(uuid4()),
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
        "source_id": source.id,
        "room": source.room,
        "backend": "ha_stt",
        "transcript": text,
        "duration_ms": max(1, round(duration_seconds * 1000)),
        "truncated": False,
    }
    encoded = _encode(payload)
    if len(encoded.encode("utf-8")) <= MAX_EVENT_BYTES:
        return encoded

    payload["truncated"] = True
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        payload["transcript"] = text[:middle]
        if len(_encode(payload).encode("utf-8")) <= MAX_EVENT_BYTES:
            low = middle
        else:
            high = middle - 1
    payload["transcript"] = text[:low]
    encoded = _encode(payload)
    if not payload["transcript"] or len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError("transcript event metadata exceeds the fixed size limit")
    return encoded


def _encode(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class TranscriptEventPublisher:
    """Deliver one immutable event with bounded retry and no content logging."""

    def __init__(
        self,
        publisher: Publisher,
        *,
        attempts: int = MAX_PUBLISH_ATTEMPTS,
        initial_backoff: float = 0.25,
    ) -> None:
        if attempts < 1:
            raise ValueError("transcript publish attempts must be positive")
        self.publisher = publisher
        self.attempts = attempts
        self.initial_backoff = initial_backoff

    async def publish(self, encoded_event: str, stop_event: asyncio.Event) -> bool:
        event_id = _event_id(encoded_event)
        backoff = self.initial_backoff
        for attempt in range(1, self.attempts + 1):
            if stop_event.is_set():
                return False
            try:
                await self.publisher.publish(
                    TRANSCRIPT_TOPIC,
                    encoded_event,
                    qos=1,
                    retain=False,
                )
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "Transcript publish failed event_id=%s attempt=%d/%d error_type=%s",
                    event_id,
                    attempt,
                    self.attempts,
                    type(exc).__name__,
                )
                if attempt < self.attempts:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                    backoff *= 2
        LOGGER.warning("Transcript event dropped event_id=%s reason=publish_failed", event_id)
        return False


def _event_id(encoded_event: str) -> str:
    try:
        payload = json.loads(encoded_event)
    except json.JSONDecodeError as exc:
        raise ValueError("transcript event must be valid JSON") from exc
    event_id = payload.get("event_id") if isinstance(payload, dict) else None
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("transcript event must contain an event ID")
    return event_id


class TranscriptProcessor:
    """Charge one aggregate budget, run one STT request, and prepare one event."""

    def __init__(
        self,
        source: SourceConfig,
        config: TranscriptEventsConfig,
        publisher: Publisher,
        *,
        stt_factory: Callable[[str], object] = HaSttClient,
        budget: object | None = None,
        event_publisher: TranscriptEventPublisher | None = None,
    ) -> None:
        self.source = source
        self.config = config
        self.stt_factory = stt_factory
        self.budget = budget or SubmissionBudget(config)
        self.event_publisher = event_publisher or TranscriptEventPublisher(publisher)

    async def recognize(self, audio: bytes) -> TranscriptResult:
        duration_seconds = len(audio) / (PCM_RATE * PCM_WIDTH)
        decision = self.budget.consume(duration_seconds)
        if not decision.allowed:
            LOGGER.warning(
                "Transcript STT privacy budget blocked submission source_id=%s "
                "reason=%s retry_seconds=%.1f",
                self.source.id,
                decision.reason,
                decision.retry_after,
            )
            return TranscriptResult(None, None, decision.retry_after)
        transcript = await self.stt_factory(self.config.pipeline_id).transcribe(audio)
        encoded = encode_transcript_event(self.source, transcript, duration_seconds)
        return TranscriptResult(transcript.strip() or None, encoded)

    async def publish(self, result: TranscriptResult, stop_event: asyncio.Event) -> bool:
        if result.encoded_event is None:
            return False
        return await self.event_publisher.publish(result.encoded_event, stop_event)


class LatestTranscriptQueue:
    """Keep one STT request in flight and only the latest pending PCM segment."""

    def __init__(self, processor: TranscriptProcessor) -> None:
        self.processor = processor
        self.queue: asyncio.Queue[TranscriptSegmentJob] = asyncio.Queue(maxsize=1)
        self.dropped_segments = 0

    def submit(self, audio: bytes, completed_at: float) -> TranscriptSegmentJob:
        loop = asyncio.get_running_loop()
        job = TranscriptSegmentJob(
            audio=audio,
            completed_at=completed_at,
            result=loop.create_future(),
            published=loop.create_future(),
        )
        if self.queue.full():
            dropped = self.queue.get_nowait()
            self.queue.task_done()
            self._resolve_dropped(dropped)
            self.dropped_segments += 1
            LOGGER.warning(
                "Transcript segment dropped source_id=%s reason=latest_queue_replaced "
                "dropped_total=%d",
                self.processor.source.id,
                self.dropped_segments,
            )
        self.queue.put_nowait(job)
        return job

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            get_task = asyncio.create_task(self.queue.get())
            stop_task = asyncio.create_task(stop_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {get_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done and stop_task.result():
                    if get_task in done:
                        stopped_job = get_task.result()
                        self.queue.task_done()
                        self._resolve_dropped(stopped_job)
                    else:
                        get_task.cancel()
                        await asyncio.gather(get_task, return_exceptions=True)
                    break
                job = get_task.result()
            finally:
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)

            try:
                try:
                    result = await self.processor.recognize(job.audio)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOGGER.warning(
                        "Transcript processing failed source_id=%s error_type=%s",
                        self.processor.source.id,
                        type(exc).__name__,
                    )
                    if not job.result.done():
                        job.result.set_result(None)
                    if not job.published.done():
                        job.published.set_result(False)
                    continue

                if not job.result.done():
                    job.result.set_result(result)
                if result.budget_allowed:
                    delivered = await self.processor.publish(result, stop_event)
                else:
                    delivered = False
                if not job.published.done():
                    job.published.set_result(delivered)
                if not result.budget_allowed:
                    await _wait_or_stop(stop_event, result.retry_after)
            finally:
                self.queue.task_done()

        self.discard_pending()

    def discard_pending(self) -> None:
        while not self.queue.empty():
            job = self.queue.get_nowait()
            self.queue.task_done()
            self._resolve_dropped(job)

    @staticmethod
    def _resolve_dropped(job: TranscriptSegmentJob) -> None:
        if not job.result.done():
            job.result.set_result(None)
        if not job.published.done():
            job.published.set_result(False)


def _default_source_factory(config: SourceConfig) -> FfmpegPcmSource:
    return FfmpegPcmSource(config, chunk_bytes=VAD_CHUNK_BYTES)


class AmbientTranscriptWorker:
    """Standalone one-source VAD/STT transcript worker."""

    def __init__(
        self,
        source: SourceConfig,
        config: TranscriptEventsConfig,
        publisher: Publisher,
        *,
        source_factory: Callable[[SourceConfig], object] = _default_source_factory,
        detector_factory: Callable[[], object] = new_silero_detector,
        stt_factory: Callable[[str], object] = HaSttClient,
        budget: object | None = None,
        event_publisher: TranscriptEventPublisher | None = None,
        minimum_backoff: float = 1,
        maximum_backoff: float = 30,
    ) -> None:
        self.source = source
        self.source_factory = source_factory
        self.detector_factory = detector_factory
        self.processor = TranscriptProcessor(
            source,
            config,
            publisher,
            stt_factory=stt_factory,
            budget=budget,
            event_publisher=event_publisher,
        )
        self.minimum_backoff = minimum_backoff
        self.maximum_backoff = maximum_backoff

    async def run_once(self, stop_event: asyncio.Event) -> tuple[bool, float]:
        source = self.source_factory(self.source)
        try:
            await source.start()
            audio = await capture_speech_segment(
                source.read_chunk,
                self.detector_factory(),
                stop_event,
            )
            if audio is None:
                return False, 0
            result = await self.processor.recognize(audio)
            if not result.budget_allowed:
                return False, result.retry_after
            await self.processor.publish(result, stop_event)
            return True, 0
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
                if not processed and wait_seconds:
                    await _wait_or_stop(stop_event, wait_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "Transcript session failed source_id=%s error_type=%s retry_seconds=%.3f",
                    self.source.id,
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
