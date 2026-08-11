"""microWakeWord-gated one-source activation worker."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol
from uuid import uuid4

from .budget import SubmissionBudget
from .config import (
    ACTIVATION_TOPIC,
    MicroWakeWordActivationConfig,
    MicroWakeWordMapping,
    SourceConfig,
    TranscriptEventsConfig,
)
from .ha_stt import HaSttClient
from .matcher import WakeWordMatch, match_wake_word_prefix, normalize_for_match
from .source import PCM_RATE, PCM_WIDTH, FfmpegPcmSource
from .stt_worker import COMMAND_MAX_LENGTH, build_command_payload
from .transcript import (
    LatestTranscriptQueue,
    TranscriptEventPublisher,
    TranscriptProcessor,
    TranscriptSegmentJob,
)
from .vad import (
    READ_TIMEOUT_SECONDS,
    VAD_CHUNK_BYTES,
    SpeechSegmentCollector,
    new_silero_detector,
)
from .wyoming_client import WyomingDetector

LOGGER = logging.getLogger(__name__)
ASSOCIATION_GRACE_SECONDS = 1.5


class Publisher(Protocol):
    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None: ...


def _default_source_factory(config: SourceConfig) -> FfmpegPcmSource:
    return FfmpegPcmSource(config, chunk_bytes=VAD_CHUNK_BYTES)


def command_from_transcript(transcript: str, word: MicroWakeWordMapping) -> str:
    """Best-effort wake-prefix removal after microWakeWord authorized the turn."""
    clean = transcript.strip()
    if not clean:
        return ""
    match = match_wake_word_prefix(clean, (word,))
    if match is not None:
        return match.command
    normalized = normalize_for_match(clean)
    if any(normalized == normalize_for_match(alias) for alias in word.aliases):
        return ""
    return clean


class MicroWakeWordActivationWorker:
    def __init__(
        self,
        source_config: SourceConfig,
        activation_config: MicroWakeWordActivationConfig,
        publisher: Publisher,
        *,
        source_factory: Callable[[SourceConfig], Any] = _default_source_factory,
        wake_detector_factory: Callable[[str, int, tuple[str, ...]], Any] = WyomingDetector,
        vad_detector_factory: Callable[[], Any] = new_silero_detector,
        stt_factory: Callable[[str], Any] = HaSttClient,
        budget: SubmissionBudget | None = None,
        clock: Callable[[], float] | None = None,
        association_grace_seconds: float = ASSOCIATION_GRACE_SECONDS,
        transcript_config: TranscriptEventsConfig | None = None,
        transcript_event_publisher: TranscriptEventPublisher | None = None,
        minimum_backoff: float = 1,
        maximum_backoff: float = 30,
    ) -> None:
        self.source_config = source_config
        self.activation_config = activation_config
        self.publisher = publisher
        self.source_factory = source_factory
        self.wake_detector_factory = wake_detector_factory
        self.vad_detector_factory = vad_detector_factory
        self.stt_factory = stt_factory
        self.budget = budget or SubmissionBudget(activation_config)
        self.clock = clock
        self.association_grace_seconds = association_grace_seconds
        self.minimum_backoff = minimum_backoff
        self.maximum_backoff = maximum_backoff
        self._words_by_model = {word.model: word for word in activation_config.wake_words}
        self.transcript_processor = (
            TranscriptProcessor(
                source_config,
                transcript_config,
                publisher,
                stt_factory=stt_factory,
                budget=self.budget,
                event_publisher=transcript_event_publisher,
            )
            if transcript_config is not None
            else None
        )

    def _now(self) -> float:
        if self.clock is not None:
            return self.clock()
        return asyncio.get_running_loop().time()

    async def run_once(self, stop_event: asyncio.Event) -> tuple[bool, float]:
        source = self.source_factory(self.source_config)
        collector = SpeechSegmentCollector(self.vad_detector_factory())
        wake_detector = self.wake_detector_factory(
            self.activation_config.wyoming_host,
            self.activation_config.wyoming_port,
            self.activation_config.models,
        )
        transcript_queue = (
            LatestTranscriptQueue(self.transcript_processor)
            if self.transcript_processor is not None
            else None
        )
        transcript_task: asyncio.Task[None] | None = None
        last_completed: tuple[bytes, float] | TranscriptSegmentJob | None = None

        async def read_for_wake() -> bytes:
            nonlocal last_completed
            chunk = await source.read_chunk()
            completed = collector.feed(chunk)
            if completed is not None:
                completed_at = self._now()
                if transcript_queue is not None:
                    last_completed = transcript_queue.submit(completed, completed_at)
                else:
                    last_completed = (completed, completed_at)
            return chunk

        try:
            await source.start()
            if transcript_queue is not None:
                transcript_task = asyncio.create_task(transcript_queue.run(stop_event))
            detection = await wake_detector.detect(read_for_wake, stop_event)
            if detection is None:
                return False, 0
            word = self._words_by_model.get(detection.name)
            if word is None:
                LOGGER.warning(
                    "microWakeWord detection rejected source_id=%s reason=unknown_model",
                    self.source_config.id,
                )
                return True, self.activation_config.cooldown_seconds

            audio: bytes | None = None
            transcript_job: TranscriptSegmentJob | None = None
            if collector.active:
                last_completed = None
                while collector.active and not stop_event.is_set():
                    chunk = await asyncio.wait_for(
                        source.read_chunk(),
                        timeout=READ_TIMEOUT_SECONDS,
                    )
                    completed = collector.feed(chunk)
                    if completed is not None:
                        if transcript_queue is not None:
                            last_completed = transcript_queue.submit(completed, self._now())
                            transcript_job = last_completed
                        else:
                            audio = completed
                        break
            elif (
                last_completed is not None
                and self._now()
                - (
                    last_completed.completed_at
                    if isinstance(last_completed, TranscriptSegmentJob)
                    else last_completed[1]
                )
                <= self.association_grace_seconds
            ):
                if isinstance(last_completed, TranscriptSegmentJob):
                    transcript_job = last_completed
                else:
                    audio = last_completed[0]

            transcript: str | None = None
            if transcript_job is not None and not stop_event.is_set():
                transcript_result = await transcript_job.result
                if transcript_result is not None and not transcript_result.budget_allowed:
                    return False, transcript_result.retry_after
                if transcript_result is not None:
                    transcript = transcript_result.transcript

            if (audio is None and transcript_job is None) or stop_event.is_set():
                LOGGER.info(
                    "microWakeWord detection had no associated speech segment source_id=%s",
                    self.source_config.id,
                )
                if isinstance(last_completed, TranscriptSegmentJob):
                    with suppress(TimeoutError):
                        await asyncio.wait_for(
                            asyncio.shield(last_completed.published),
                            timeout=1.0,
                        )
                return True, self.activation_config.cooldown_seconds

            request_id = str(uuid4())
            if transcript_job is None:
                assert audio is not None
                audio_seconds = len(audio) / (PCM_RATE * PCM_WIDTH)
                decision = self.budget.consume(audio_seconds)
                if not decision.allowed:
                    LOGGER.warning(
                        "microWakeWord STT privacy budget blocked submission source_id=%s "
                        "reason=%s retry_seconds=%.1f",
                        self.source_config.id,
                        decision.reason,
                        decision.retry_after,
                    )
                    return False, decision.retry_after
                transcript = await self.stt_factory(self.activation_config.pipeline_id).transcribe(
                    audio
                )
            if transcript is None:
                LOGGER.info(
                    "microWakeWord command rejected source_id=%s request_id=%s "
                    "reason=stt_unavailable",
                    self.source_config.id,
                    request_id,
                )
                return True, self.activation_config.cooldown_seconds
            command = command_from_transcript(transcript, word)
            if not command:
                LOGGER.info(
                    "microWakeWord command rejected source_id=%s request_id=%s reason=empty",
                    self.source_config.id,
                    request_id,
                )
                return True, self.activation_config.cooldown_seconds
            if len(command) > COMMAND_MAX_LENGTH:
                LOGGER.warning(
                    "microWakeWord command rejected source_id=%s request_id=%s reason=too_long",
                    self.source_config.id,
                    request_id,
                )
                return True, self.activation_config.cooldown_seconds

            payload = build_command_payload(
                self.source_config,
                WakeWordMatch(wake_word_id=word.id, command=command),
                request_id,
                canary=False,
                backend="microwakeword",
            )
            encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            publish_backoff = self.minimum_backoff
            while not stop_event.is_set():
                try:
                    await self.publisher.publish(
                        ACTIVATION_TOPIC,
                        encoded_payload,
                        qos=1,
                        retain=False,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOGGER.warning(
                        "microWakeWord publish failed source_id=%s request_id=%s "
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
                "microWakeWord command matched source_id=%s room=%s wake_word_id=%s request_id=%s",
                payload["source_id"],
                payload["room"],
                payload["wake_word_id"],
                payload["request_id"],
            )
            if transcript_job is not None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(transcript_job.published), timeout=1.0)
            return True, self.activation_config.cooldown_seconds
        finally:
            collector.reset()
            if transcript_task is not None:
                transcript_task.cancel()
                await asyncio.gather(transcript_task, return_exceptions=True)
            if transcript_queue is not None:
                transcript_queue.discard_pending()
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
                    "microWakeWord activation session failed source_id=%s "
                    "error_type=%s retry_seconds=%.3f",
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
