from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, ClassVar

from gateway.config import CANARY_TOPIC, PassiveCanaryConfig, SourceConfig
from gateway.worker import PassiveCanaryWorker, build_detection_payload
from wyoming.wake import Detection


def configs(secret_url: str = "rtsp://user:password@example.invalid:8554/study"):
    source = SourceConfig(id="study", url=secret_url, room="study")
    canary = PassiveCanaryConfig(
        enabled=True,
        source_id="study",
        wyoming_host="microwakeword",
        wyoming_port=10400,
        models=("hey_jarvis",),
        cooldown_seconds=0,
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
        return b"\0" * 2048

    async def close(self) -> None:
        self.closed = True


class FakeDetector:
    def __init__(self, _host: str, _port: int, _models: tuple[str, ...]) -> None: ...

    async def detect(self, read_chunk, _stop_event: asyncio.Event) -> Detection:
        assert len(await read_chunk()) == 2048
        return Detection(name="hey_jarvis")


class RecordingPublisher:
    def __init__(self, failures: int = 0, stop_after_success: asyncio.Event | None = None) -> None:
        self.failures = failures
        self.stop_after_success = stop_after_success
        self.calls: list[tuple[str, str, int, bool]] = []

    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        self.calls.append((topic, payload, qos, retain))
        if len(self.calls) <= self.failures:
            raise RuntimeError("broker failed with password=do-not-log")
        if self.stop_after_success is not None:
            self.stop_after_success.set()


async def test_one_detection_publishes_only_fixed_diagnostic_payload() -> None:
    FakeSource.instances.clear()
    source, canary = configs()
    publisher = RecordingPublisher()
    worker = PassiveCanaryWorker(
        source,
        canary,
        publisher,
        source_factory=FakeSource,
        detector_factory=FakeDetector,
    )
    assert await worker.run_once(asyncio.Event()) is True
    assert len(publisher.calls) == 1
    topic, encoded, qos, retain = publisher.calls[0]
    payload = json.loads(encoded)
    assert topic == CANARY_TOPIC
    assert qos == 1
    assert retain is False
    assert payload["version"] == 1
    assert payload["event"] == "wake_detected"
    assert payload["source_id"] == "study"
    assert payload["room"] == "study"
    assert payload["backend"] == "microwakeword"
    assert payload["model"] == "hey_jarvis"
    assert payload["canary"] is True
    assert set(payload) == {
        "version",
        "event",
        "request_id",
        "timestamp",
        "source_id",
        "room",
        "backend",
        "model",
        "canary",
    }
    assert FakeSource.instances[-1].closed is True


async def test_publish_retry_reuses_request_id_and_payload(caplog) -> None:
    source, canary = configs()
    publisher = RecordingPublisher(failures=1)
    worker = PassiveCanaryWorker(
        source,
        canary,
        publisher,
        source_factory=FakeSource,
        detector_factory=FakeDetector,
        minimum_backoff=0,
    )
    with caplog.at_level(logging.WARNING):
        assert await worker.run_once(asyncio.Event()) is True
    assert len(publisher.calls) == 2
    assert publisher.calls[0][1] == publisher.calls[1][1]
    assert (
        json.loads(publisher.calls[0][1])["request_id"]
        == json.loads(publisher.calls[1][1])["request_id"]
    )
    assert "do-not-log" not in caplog.text


async def test_session_failure_uses_bounded_backoff_without_secret_logs(caplog) -> None:
    secret = "credential-that-must-not-appear"
    source, canary = configs(f"rtsp://user:{secret}@example.invalid/audio")
    stop_event = asyncio.Event()

    class FailingSource(FakeSource):
        async def start(self) -> None:
            raise RuntimeError(f"ffmpeg stderr leaked {secret} {self.config.url}")

    worker = PassiveCanaryWorker(
        source,
        canary,
        RecordingPublisher(),
        source_factory=FailingSource,
        detector_factory=FakeDetector,
        minimum_backoff=0.01,
        maximum_backoff=0.02,
    )
    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(worker.run_forever(stop_event))
        await asyncio.sleep(0.035)
        stop_event.set()
        await asyncio.wait_for(task, timeout=1)
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert source.url not in caplog.text
    messages = [record.getMessage() for record in caplog.records]
    assert "retry_seconds=0.010" in messages[0]
    assert "retry_seconds=0.020" in messages[1]
    assert "retry_seconds=0.020" in messages[2]


def test_payload_has_no_private_fields() -> None:
    source, _ = configs()
    payload: dict[str, Any] = build_detection_payload(source, Detection(name="hey_jarvis"))
    encoded = json.dumps(payload)
    for forbidden in ("rtsp", "password", "audio", "transcript", "command", "eha", "token"):
        assert forbidden not in encoded.lower()
