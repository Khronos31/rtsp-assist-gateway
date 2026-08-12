from __future__ import annotations

import asyncio
import json
import logging
from typing import ClassVar

from gateway.budget import BudgetDecision
from gateway.config import (
    ACTIVATION_TOPIC,
    HA_STT_CANARY_TOPIC,
    TRANSCRIPT_TOPIC,
    HaSttCanaryConfig,
    SourceConfig,
    TranscriptEventsConfig,
    WakeWordConfig,
)
from gateway.stt_worker import HaSttCanaryWorker
from gateway.transcript import TranscriptEventPublisher


def configs(secret_url: str = "rtsp://user:password@example.invalid/study"):
    source = SourceConfig(id="study", url=secret_url, room="study")
    canary = HaSttCanaryConfig(
        enabled=True,
        source_id="study",
        pipeline_id="preferred-id",
        wake_words=(
            WakeWordConfig(
                id="computer",
                aliases=("ねえコンピューター", "ねえコンピュータ"),
            ),
        ),
        cooldown_seconds=3,
        max_requests_per_minute=6,
        max_audio_seconds_per_hour=300,
        max_audio_seconds_per_day=1800,
    )
    return source, canary


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


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, bool]] = []

    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        self.calls.append((topic, payload, qos, retain))


def stt_factory(transcript: str, calls: list[tuple[str, bytes]]):
    class FakeStt:
        def __init__(self, pipeline_id: str) -> None:
            self.pipeline_id = pipeline_id

        async def transcribe(self, audio: bytes) -> str:
            calls.append((self.pipeline_id, audio))
            return transcript

    return FakeStt


async def run_worker(
    monkeypatch,
    transcript: str,
    budget: FakeBudget | None = None,
    *,
    output_topic: str = HA_STT_CANARY_TOPIC,
    canary: bool = True,
    publisher: RecordingPublisher | None = None,
    transcript_events: bool = False,
    transcript_event_publisher: TranscriptEventPublisher | None = None,
):
    source, stt_config = configs()
    publisher = publisher or RecordingPublisher()
    calls: list[tuple[str, bytes]] = []
    audio = b"\0" * 32_000

    async def fake_capture(_read_chunk, _detector, _stop_event):
        return audio

    monkeypatch.setattr("gateway.stt_worker.capture_speech_segment", fake_capture)
    worker = HaSttCanaryWorker(
        source,
        stt_config,
        publisher,
        source_factory=FakeSource,
        detector_factory=lambda: object(),
        stt_factory=stt_factory(transcript, calls),
        budget=budget or FakeBudget(),
        output_topic=output_topic,
        canary=canary,
        transcript_config=(
            TranscriptEventsConfig(
                enabled=True,
                source_id="study",
                pipeline_id="preferred-id",
                max_requests_per_minute=6,
                max_audio_seconds_per_hour=300,
                max_audio_seconds_per_day=1800,
            )
            if transcript_events
            else None
        ),
        transcript_event_publisher=transcript_event_publisher,
    )
    result = await worker.run_once(asyncio.Event())
    return result, publisher, calls, audio


async def test_match_publishes_canonical_id_and_command(monkeypatch) -> None:
    result, publisher, calls, audio = await run_worker(
        monkeypatch,
        "ねえコンピューター、電気を消して。",
    )
    assert result == (True, 3)
    assert calls == [("preferred-id", audio)]
    assert len(publisher.calls) == 1
    topic, encoded, qos, retain = publisher.calls[0]
    payload = json.loads(encoded)
    assert topic == HA_STT_CANARY_TOPIC
    assert qos == 1
    assert retain is False
    assert payload["backend"] == "ha_stt"
    assert payload["wake_word_id"] == "computer"
    assert payload["command"] == "電気を消して"
    assert payload["canary"] is True
    assert FakeSource.instances[-1].closed is True


async def test_unmatched_transcript_is_not_logged_or_published(
    monkeypatch,
    caplog,
) -> None:
    secret_transcript = "家庭の秘密の会話"
    with caplog.at_level(logging.INFO):
        result, publisher, calls, _audio = await run_worker(monkeypatch, secret_transcript)
    assert result == (True, 3)
    assert len(calls) == 1
    assert publisher.calls == []
    assert secret_transcript not in caplog.text


async def test_budget_blocks_before_stt(monkeypatch, caplog) -> None:
    budget = FakeBudget(BudgetDecision(False, "audio_day_limit", 120))
    with caplog.at_level(logging.WARNING):
        result, publisher, calls, _audio = await run_worker(
            monkeypatch,
            "ねえコンピューター、テスト",
            budget,
        )
    assert result == (False, 120)
    assert calls == []
    assert publisher.calls == []
    assert budget.durations == [1.0]
    assert "audio_day_limit" in caplog.text


async def test_activation_payload_uses_fixed_topic_without_canary(monkeypatch) -> None:
    _result, publisher, _calls, _audio = await run_worker(
        monkeypatch,
        "ねえコンピューター、電気を消して。",
        output_topic=ACTIVATION_TOPIC,
        canary=False,
    )
    topic, encoded, qos, retain = publisher.calls[0]
    payload = json.loads(encoded)
    assert topic == ACTIVATION_TOPIC
    assert qos == 1
    assert retain is False
    assert "canary" not in payload
    assert payload["request_id"]
    assert payload["timestamp"]


async def test_oversized_command_is_rejected_without_text_logging(monkeypatch, caplog) -> None:
    secret_command = "秘" * 501
    with caplog.at_level(logging.WARNING):
        result, publisher, calls, _audio = await run_worker(
            monkeypatch,
            f"ねえコンピューター{secret_command}",
            output_topic=ACTIVATION_TOPIC,
            canary=False,
        )
    assert result == (True, 3)
    assert len(calls) == 1
    assert publisher.calls == []
    assert secret_command not in caplog.text
    assert "too_long" in caplog.text


async def test_publish_retry_reuses_identical_payload(monkeypatch) -> None:
    class RetryPublisher(RecordingPublisher):
        def __init__(self) -> None:
            super().__init__()
            self.attempts: list[tuple[str, str, int, bool]] = []

        async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
            self.attempts.append((topic, payload, qos, retain))
            if len(self.attempts) == 1:
                raise OSError("temporary")
            await super().publish(topic, payload, qos, retain)

    publisher = RetryPublisher()
    await run_worker(
        monkeypatch,
        "ねえコンピューター、電気を消して。",
        output_topic=ACTIVATION_TOPIC,
        canary=False,
        publisher=publisher,
    )
    assert len(publisher.attempts) == 2
    assert publisher.attempts[0] == publisher.attempts[1]


async def test_activation_and_transcript_share_one_stt_result(monkeypatch) -> None:
    result, publisher, calls, audio = await run_worker(
        monkeypatch,
        "ねえコンピューター、電気を消して。",
        output_topic=ACTIVATION_TOPIC,
        canary=False,
        transcript_events=True,
    )
    assert result == (True, 3)
    assert calls == [("preferred-id", audio)]
    assert [call[0] for call in publisher.calls] == [ACTIVATION_TOPIC, TRANSCRIPT_TOPIC]
    transcript = json.loads(publisher.calls[1][1])
    assert transcript["event"] == "transcript_observed"
    assert transcript["transcript"] == "ねえコンピューター、電気を消して。"


async def test_transcript_publish_failure_does_not_suppress_activation(monkeypatch) -> None:
    class TopicFailingPublisher(RecordingPublisher):
        async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
            self.calls.append((topic, payload, qos, retain))
            if topic == TRANSCRIPT_TOPIC:
                raise OSError("transcript broker failure")

    publisher = TopicFailingPublisher()
    transcript_publisher = TranscriptEventPublisher(
        publisher,
        attempts=2,
        initial_backoff=0,
    )
    result, _publisher, calls, _audio = await run_worker(
        monkeypatch,
        "ねえコンピューター、電気を消して。",
        output_topic=ACTIVATION_TOPIC,
        canary=False,
        publisher=publisher,
        transcript_events=True,
        transcript_event_publisher=transcript_publisher,
    )
    assert result == (True, 3)
    assert len(calls) == 1
    assert [call[0] for call in publisher.calls].count(ACTIVATION_TOPIC) == 1
    assert [call[0] for call in publisher.calls].count(TRANSCRIPT_TOPIC) == 2


async def test_session_failure_does_not_log_source_or_exception_secret(caplog) -> None:
    secret = "credential-that-must-not-appear"
    source, canary = configs(f"rtsp://user:{secret}@example.invalid/study")
    stop_event = asyncio.Event()

    class FailingSource(FakeSource):
        async def start(self) -> None:
            raise RuntimeError(f"ffmpeg leaked {secret} {self.config.url}")

    worker = HaSttCanaryWorker(
        source,
        canary,
        RecordingPublisher(),
        source_factory=FailingSource,
        detector_factory=lambda: object(),
        stt_factory=stt_factory("", []),
        budget=FakeBudget(),
        minimum_backoff=0.01,
        maximum_backoff=0.02,
    )
    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(worker.run_forever(stop_event))
        await asyncio.sleep(0.015)
        stop_event.set()
        await asyncio.wait_for(task, timeout=1)
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert source.url not in caplog.text
