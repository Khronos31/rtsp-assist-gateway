from __future__ import annotations

import asyncio
import json
import logging
from typing import ClassVar

import gateway.transcript as transcript_module
from gateway.budget import BudgetDecision
from gateway.config import TRANSCRIPT_TOPIC, SourceConfig, TranscriptEventsConfig
from gateway.transcript import (
    MAX_EVENT_BYTES,
    AmbientTranscriptWorker,
    LatestTranscriptQueue,
    TranscriptEventPublisher,
    TranscriptResult,
    encode_transcript_event,
)


class RecordingPublisher:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[str, str, int, bool]] = []

    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        self.calls.append((topic, payload, qos, retain))
        if len(self.calls) <= self.failures:
            raise OSError("broker failed with secret transcript that must not be logged")


def source() -> SourceConfig:
    return SourceConfig(
        id="study",
        url="rtsp://user:password@example.invalid/study",
        room="study",
    )


def transcript_config() -> TranscriptEventsConfig:
    return TranscriptEventsConfig(
        enabled=True,
        source_id="study",
        pipeline_id="preferred-id",
        max_requests_per_minute=6,
        max_audio_seconds_per_hour=300,
        max_audio_seconds_per_day=1800,
    )


class FakeSource:
    instances: ClassVar[list[FakeSource]] = []

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.started = False
        self.closed = False
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def read_chunk(self) -> bytes:
        return b"\0" * 1024

    async def close(self) -> None:
        self.closed = True


class FakeBudget:
    def __init__(self, decision: BudgetDecision | None = None) -> None:
        self.decision = decision or BudgetDecision(True)
        self.durations: list[float] = []

    def consume(self, duration: float) -> BudgetDecision:
        self.durations.append(duration)
        return self.decision


def stt_factory(transcript: str, calls: list[tuple[str, bytes]]):
    class FakeStt:
        def __init__(self, pipeline_id: str) -> None:
            self.pipeline_id = pipeline_id

        async def transcribe(self, audio: bytes) -> str:
            calls.append((self.pipeline_id, audio))
            return transcript

    return FakeStt


def test_event_contract_is_generic_and_non_empty() -> None:
    encoded = encode_transcript_event(
        source(),
        "  これは観測された発話です  ",
        1.25,
        event_id="event-1",
        timestamp="2026-08-12T00:00:00+00:00",
    )
    assert encoded is not None
    payload = json.loads(encoded)
    assert payload == {
        "version": 1,
        "event": "transcript_observed",
        "event_id": "event-1",
        "timestamp": "2026-08-12T00:00:00+00:00",
        "source_id": "study",
        "room": "study",
        "backend": "ha_stt",
        "transcript": "これは観測された発話です",
        "duration_ms": 1250,
        "truncated": False,
    }
    forbidden = {"speaker", "url", "token", "pcm", "wake_word_id", "command"}
    assert forbidden.isdisjoint(payload)
    assert encode_transcript_event(source(), " \n ", 1.0) is None


def test_oversized_unicode_is_complete_json_within_fixed_limit() -> None:
    secret = "秘密の日本語🙂" * 5_000
    encoded = encode_transcript_event(
        source(),
        secret,
        15.0,
        event_id="stable-id",
        timestamp="2026-08-12T00:00:00+00:00",
    )
    assert encoded is not None
    assert len(encoded.encode("utf-8")) <= MAX_EVENT_BYTES
    payload = json.loads(encoded)
    assert payload["truncated"] is True
    assert payload["transcript"]
    assert secret.startswith(payload["transcript"])


async def test_publish_is_fixed_non_retained_and_retries_identical_payload() -> None:
    publisher = RecordingPublisher(failures=1)
    encoded = encode_transcript_event(source(), "テスト", 1.0, event_id="same-id")
    assert encoded is not None
    delivered = await TranscriptEventPublisher(
        publisher,
        attempts=2,
        initial_backoff=0,
    ).publish(encoded, asyncio.Event())
    assert delivered is True
    assert publisher.calls == [
        (TRANSCRIPT_TOPIC, encoded, 1, False),
        (TRANSCRIPT_TOPIC, encoded, 1, False),
    ]


async def test_publish_failure_is_bounded_and_does_not_log_content(caplog) -> None:
    secret = "家庭内だけの発話"
    publisher = RecordingPublisher(failures=10)
    encoded = encode_transcript_event(source(), secret, 1.0, event_id="drop-id")
    assert encoded is not None
    with caplog.at_level(logging.WARNING):
        delivered = await TranscriptEventPublisher(
            publisher,
            attempts=3,
            initial_backoff=0,
        ).publish(encoded, asyncio.Event())
    assert delivered is False
    assert len(publisher.calls) == 3
    assert secret not in caplog.text
    assert "drop-id" in caplog.text


async def test_standalone_worker_captures_and_publishes_one_segment(monkeypatch) -> None:
    audio = b"\0" * 32_000
    calls: list[tuple[str, bytes]] = []
    budget = FakeBudget()
    publisher = RecordingPublisher()

    async def fake_capture(_read_chunk, _detector, _stop_event):
        return audio

    monkeypatch.setattr(transcript_module, "capture_speech_segment", fake_capture)
    worker = AmbientTranscriptWorker(
        source(),
        transcript_config(),
        publisher,
        source_factory=FakeSource,
        detector_factory=lambda: object(),
        stt_factory=stt_factory("周辺の会話", calls),
        budget=budget,
    )
    result = await worker.run_once(asyncio.Event())
    assert result == (True, 0)
    assert calls == [("preferred-id", audio)]
    assert budget.durations == [1.0]
    assert len(publisher.calls) == 1
    assert publisher.calls[0][0] == TRANSCRIPT_TOPIC
    assert json.loads(publisher.calls[0][1])["transcript"] == "周辺の会話"
    assert FakeSource.instances[-1].closed is True


async def test_standalone_budget_blocks_before_stt(monkeypatch) -> None:
    audio = b"\0" * 32_000
    calls: list[tuple[str, bytes]] = []
    budget = FakeBudget(BudgetDecision(False, "audio_day_limit", 120))
    publisher = RecordingPublisher()

    async def fake_capture(_read_chunk, _detector, _stop_event):
        return audio

    monkeypatch.setattr(transcript_module, "capture_speech_segment", fake_capture)
    worker = AmbientTranscriptWorker(
        source(),
        transcript_config(),
        publisher,
        source_factory=FakeSource,
        detector_factory=lambda: object(),
        stt_factory=stt_factory("送信されない会話", calls),
        budget=budget,
    )
    assert await worker.run_once(asyncio.Event()) == (False, 120)
    assert calls == []
    assert publisher.calls == []


async def test_latest_queue_bounds_pcm_and_drops_only_pending_segment() -> None:
    release_first = asyncio.Event()
    recognized: list[bytes] = []

    class FakeProcessor:
        source = source()

        async def recognize(self, audio: bytes) -> TranscriptResult:
            recognized.append(audio)
            if audio == b"first":
                await release_first.wait()
            return TranscriptResult("text", '{"event_id":"id"}')

        async def publish(self, _result: TranscriptResult, _stop_event: asyncio.Event) -> bool:
            return True

    queue = LatestTranscriptQueue(FakeProcessor())
    stop_event = asyncio.Event()
    task = asyncio.create_task(queue.run(stop_event))
    first = queue.submit(b"first", 1.0)
    for _ in range(10):
        if recognized:
            break
        await asyncio.sleep(0)
    assert recognized == [b"first"]
    dropped = queue.submit(b"second", 2.0)
    latest = queue.submit(b"third", 3.0)
    assert await dropped.result is None
    assert await dropped.published is False
    assert queue.dropped_segments == 1

    release_first.set()
    assert (await first.result).transcript == "text"
    assert (await latest.result).transcript == "text"
    stop_event.set()
    await asyncio.wait_for(task, timeout=1)
    assert recognized == [b"first", b"third"]
