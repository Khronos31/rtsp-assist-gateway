from __future__ import annotations

from gateway.config import parse_options
from gateway.main import build_worker
from gateway.microwake_worker import MicroWakeWordActivationWorker
from gateway.stt_worker import HaSttCanaryWorker
from gateway.transcript import AmbientTranscriptWorker


class FakePublisher:
    pass


def base_options() -> dict:
    return {
        "sources": [
            {
                "id": "study",
                "url": "rtsp://example.invalid/study",
                "room": "study",
            }
        ],
        "transcript_events": {
            "enabled": True,
            "source_id": "study",
            "pipeline_id": "preferred-id",
            "max_requests_per_minute": 6,
            "max_audio_seconds_per_hour": 300,
            "max_audio_seconds_per_day": 1800,
        },
    }


def test_builds_standalone_transcript_worker() -> None:
    config = parse_options(base_options())
    worker = build_worker(config, config.sources[0], FakePublisher())
    assert isinstance(worker, AmbientTranscriptWorker)


def test_builds_ha_stt_worker_with_transcript_overlay() -> None:
    options = base_options()
    options["ha_stt_activation"] = {
        "enabled": True,
        "source_id": "study",
        "pipeline_id": "preferred-id",
        "wake_words": [{"id": "computer", "aliases": ["hey computer"]}],
        "cooldown_seconds": 3,
        "max_requests_per_minute": 6,
        "max_audio_seconds_per_hour": 300,
        "max_audio_seconds_per_day": 1800,
    }
    config = parse_options(options)
    worker = build_worker(config, config.sources[0], FakePublisher())
    assert isinstance(worker, HaSttCanaryWorker)
    assert worker.transcript_processor is not None
    assert worker.transcript_processor.budget is worker.budget


def test_builds_microwake_worker_with_transcript_overlay() -> None:
    options = base_options()
    options["microwakeword_activation"] = {
        "enabled": True,
        "source_id": "study",
        "wyoming_uri": "tcp://microwakeword:10400",
        "wake_words": [{"model": "computer_v1", "id": "computer", "aliases": ["hey computer"]}],
        "pipeline_id": "preferred-id",
        "cooldown_seconds": 3,
        "max_requests_per_minute": 6,
        "max_audio_seconds_per_hour": 300,
        "max_audio_seconds_per_day": 1800,
    }
    config = parse_options(options)
    worker = build_worker(config, config.sources[0], FakePublisher())
    assert isinstance(worker, MicroWakeWordActivationWorker)
    assert worker.transcript_processor is not None
    assert worker.transcript_processor.budget is worker.budget
