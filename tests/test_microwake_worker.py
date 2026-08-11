from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import ClassVar

import gateway.microwake_worker as microwake_worker
from gateway.budget import BudgetDecision
from gateway.config import (
    ACTIVATION_TOPIC,
    MicroWakeWordActivationConfig,
    MicroWakeWordMapping,
    SourceConfig,
)
from gateway.microwake_worker import (
    MicroWakeWordActivationWorker,
    command_from_transcript,
)
from gateway.vad import VAD_CHUNK_BYTES
from wyoming.wake import Detection


def configs(secret_url: str = "rtsp://user:password@example.invalid/study"):
    source = SourceConfig(id="study", url=secret_url, room="study")
    activation = MicroWakeWordActivationConfig(
        enabled=True,
        source_id="study",
        wyoming_host="microwakeword",
        wyoming_port=10400,
        wake_words=(
            MicroWakeWordMapping(
                model="computer_v1",
                id="computer",
                aliases=("ねえコンピューター", "ねえコンピュータ"),
            ),
        ),
        pipeline_id="preferred-id",
        cooldown_seconds=3,
        max_requests_per_minute=6,
        max_audio_seconds_per_hour=300,
        max_audio_seconds_per_day=1800,
    )
    return source, activation


class FakeSource:
    instances: ClassVar[list[FakeSource]] = []
    chunks: ClassVar[list[bytes]] = []

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.remaining = deque(self.__class__.chunks)
        self.start_count = 0
        self.closed = False
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.start_count += 1

    async def read_chunk(self) -> bytes:
        return self.remaining.popleft()

    async def close(self) -> None:
        self.closed = True


class SequenceVad:
    values: ClassVar[list[float]] = []

    def __init__(self) -> None:
        self.remaining = deque(self.__class__.values)
        self.reset_count = 0

    def __call__(self, _chunk: bytes) -> float:
        return self.remaining.popleft()

    def reset(self) -> None:
        self.reset_count += 1


class FakeWakeDetector:
    reads_before_detection: ClassVar[int] = 4
    detection_name: ClassVar[str] = "computer_v1"

    def __init__(self, host: str, port: int, models: tuple[str, ...]) -> None:
        assert (host, port, models) == ("microwakeword", 10400, ("computer_v1",))

    async def detect(self, read_chunk, _stop_event: asyncio.Event) -> Detection:
        for _ in range(self.__class__.reads_before_detection):
            await read_chunk()
        return Detection(name=self.__class__.detection_name)


class FakeBudget:
    def __init__(self, decision: BudgetDecision | None = None) -> None:
        self.decision = decision or BudgetDecision(True)
        self.durations: list[float] = []

    def consume(self, duration: float) -> BudgetDecision:
        self.durations.append(duration)
        return self.decision


class RecordingPublisher:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.attempts: list[tuple[str, str, int, bool]] = []

    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        self.attempts.append((topic, payload, qos, retain))
        if len(self.attempts) <= self.failures:
            raise OSError("temporary broker failure")


def stt_factory(transcript: str, calls: list[tuple[str, bytes]]):
    class FakeStt:
        def __init__(self, pipeline_id: str) -> None:
            self.pipeline_id = pipeline_id

        async def transcribe(self, audio: bytes) -> str:
            calls.append((self.pipeline_id, audio))
            return transcript

    return FakeStt


def prepare_stream() -> list[bytes]:
    speech_chunks = 4
    trailing_silence_chunks = 25
    values = [0.9] * speech_chunks + [0.0] * trailing_silence_chunks
    SequenceVad.values = values
    chunks = [bytes([index + 1]) * VAD_CHUNK_BYTES for index in range(len(values))]
    FakeSource.chunks = chunks
    return chunks


async def run_worker(
    transcript: str,
    *,
    budget: FakeBudget | None = None,
    publisher: RecordingPublisher | None = None,
    clock=None,
    grace: float = 1.5,
):
    source, activation = configs()
    calls: list[tuple[str, bytes]] = []
    publisher = publisher or RecordingPublisher()
    worker = MicroWakeWordActivationWorker(
        source,
        activation,
        publisher,
        source_factory=FakeSource,
        wake_detector_factory=FakeWakeDetector,
        vad_detector_factory=SequenceVad,
        stt_factory=stt_factory(transcript, calls),
        budget=budget or FakeBudget(),
        clock=clock,
        association_grace_seconds=grace,
        minimum_backoff=0,
    )
    result = await worker.run_once(asyncio.Event())
    return result, publisher, calls


async def test_detection_and_vad_share_one_source_then_publish_command() -> None:
    chunks = prepare_stream()
    FakeSource.instances.clear()
    FakeWakeDetector.reads_before_detection = 4
    FakeWakeDetector.detection_name = "computer_v1"

    result, publisher, calls = await run_worker("ねえコンピューター、今日の予定を教えて")

    assert result == (True, 3)
    assert calls == [("preferred-id", b"".join(chunks))]
    assert len(FakeSource.instances) == 1
    assert FakeSource.instances[0].start_count == 1
    assert FakeSource.instances[0].closed is True
    assert len(publisher.attempts) == 1
    topic, encoded, qos, retain = publisher.attempts[0]
    payload = json.loads(encoded)
    assert (topic, qos, retain) == (ACTIVATION_TOPIC, 1, False)
    assert payload["event"] == "wake_command_detected"
    assert payload["backend"] == "microwakeword"
    assert payload["wake_word_id"] == "computer"
    assert payload["command"] == "今日の予定を教えて"
    assert "canary" not in payload
    assert "model" not in payload


async def test_just_completed_segment_is_associated_without_new_capture() -> None:
    chunks = prepare_stream()
    FakeWakeDetector.reads_before_detection = len(chunks)
    result, publisher, calls = await run_worker("今日の予定を教えて", clock=lambda: 100.0)
    assert result == (True, 3)
    assert calls == [("preferred-id", b"".join(chunks))]
    assert len(publisher.attempts) == 1


async def test_detection_without_associated_segment_does_not_wait_or_call_stt() -> None:
    prepare_stream()
    FakeWakeDetector.reads_before_detection = 0
    result, publisher, calls = await run_worker("後から来た無関係な発話")
    assert result == (True, 3)
    assert calls == []
    assert publisher.attempts == []


async def test_stale_completed_segment_is_not_submitted() -> None:
    chunks = prepare_stream()
    FakeWakeDetector.reads_before_detection = len(chunks)
    times = iter([100.0, 103.0])
    result, publisher, calls = await run_worker(
        "古い発話",
        clock=lambda: next(times),
        grace=1.5,
    )
    assert result == (True, 3)
    assert calls == []
    assert publisher.attempts == []


async def test_unknown_model_is_rejected_before_stt() -> None:
    prepare_stream()
    FakeWakeDetector.reads_before_detection = 4
    FakeWakeDetector.detection_name = "unknown_model"
    result, publisher, calls = await run_worker("何かの発話")
    assert result == (True, 3)
    assert calls == []
    assert publisher.attempts == []
    FakeWakeDetector.detection_name = "computer_v1"


async def test_budget_blocks_before_stt(caplog) -> None:
    prepare_stream()
    FakeWakeDetector.reads_before_detection = 4
    budget = FakeBudget(BudgetDecision(False, "audio_day_limit", 120))
    with caplog.at_level(logging.WARNING):
        result, publisher, calls = await run_worker(
            "ねえコンピューター、秘密の発話",
            budget=budget,
        )
    assert result == (False, 120)
    assert calls == []
    assert publisher.attempts == []
    assert len(budget.durations) == 1
    assert "秘密の発話" not in caplog.text


async def test_empty_and_oversized_commands_never_publish(caplog) -> None:
    for transcript in ("ねえコンピューター", "秘" * 501):
        prepare_stream()
        FakeWakeDetector.reads_before_detection = 4
        with caplog.at_level(logging.INFO):
            result, publisher, calls = await run_worker(transcript)
        assert result == (True, 3)
        assert len(calls) == 1
        assert publisher.attempts == []
        assert transcript not in caplog.text


async def test_publish_retry_reuses_identical_payload() -> None:
    prepare_stream()
    FakeWakeDetector.reads_before_detection = 4
    publisher = RecordingPublisher(failures=1)
    await run_worker("ねえコンピューター、テスト", publisher=publisher)
    assert len(publisher.attempts) == 2
    assert publisher.attempts[0] == publisher.attempts[1]


def test_command_fallback_does_not_require_stt_to_recognize_wake_alias() -> None:
    word = configs()[1].wake_words[0]
    assert command_from_transcript("今日の予定を教えて", word) == "今日の予定を教えて"
    assert command_from_transcript("ねえ、コンピューター。今日の予定を教えて", word) == (
        "今日の予定を教えて"
    )
    assert command_from_transcript("ねえコンピューター", word) == ""


async def test_source_failure_log_does_not_expose_url_credentials(caplog) -> None:
    source, activation = configs()
    stop_event = asyncio.Event()

    def failing_source_factory(_config: SourceConfig):
        stop_event.set()
        raise OSError("source unavailable")

    worker = MicroWakeWordActivationWorker(
        source,
        activation,
        RecordingPublisher(),
        source_factory=failing_source_factory,
        minimum_backoff=0,
    )
    with caplog.at_level(logging.WARNING):
        await worker.run_forever(stop_event)

    assert "source_id=study" in caplog.text
    assert "rtsp://" not in caplog.text
    assert "user:password" not in caplog.text


async def test_provider_failures_close_source_and_use_bounded_backoff(
    monkeypatch,
    caplog,
) -> None:
    source, activation = configs()
    FakeSource.instances.clear()
    FakeSource.chunks = []
    stop_event = asyncio.Event()
    waits: list[float] = []

    class FailingWakeDetector:
        def __init__(self, _host: str, _port: int, _models: tuple[str, ...]) -> None:
            pass

        async def detect(self, _read_chunk, _stop_event: asyncio.Event) -> Detection:
            raise OSError("provider credential=private")

    async def record_wait(event: asyncio.Event, seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 3:
            event.set()

    monkeypatch.setattr(microwake_worker, "_wait_or_stop", record_wait)
    worker = MicroWakeWordActivationWorker(
        source,
        activation,
        RecordingPublisher(),
        source_factory=FakeSource,
        wake_detector_factory=FailingWakeDetector,
        minimum_backoff=1,
        maximum_backoff=4,
    )
    with caplog.at_level(logging.WARNING):
        await worker.run_forever(stop_event)

    assert waits == [1, 2, 4]
    assert len(FakeSource.instances) == 3
    assert all(instance.closed for instance in FakeSource.instances)
    assert "error_type=OSError" in caplog.text
    assert "credential=private" not in caplog.text


async def test_orderly_stop_closes_source_without_stt_or_publish() -> None:
    source, activation = configs()
    FakeSource.instances.clear()
    FakeSource.chunks = []
    stop_event = asyncio.Event()
    calls: list[tuple[str, bytes]] = []
    publisher = RecordingPublisher()

    class StoppingWakeDetector:
        def __init__(self, _host: str, _port: int, _models: tuple[str, ...]) -> None:
            pass

        async def detect(self, _read_chunk, event: asyncio.Event) -> None:
            event.set()
            return None

    worker = MicroWakeWordActivationWorker(
        source,
        activation,
        publisher,
        source_factory=FakeSource,
        wake_detector_factory=StoppingWakeDetector,
        stt_factory=stt_factory("unreachable", calls),
    )
    result = await worker.run_once(stop_event)

    assert result == (False, 0)
    assert calls == []
    assert publisher.attempts == []
    assert len(FakeSource.instances) == 1
    assert FakeSource.instances[0].closed is True
