"""Bounded local speech segmentation for the HA STT canary."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from .source import PCM_RATE, PCM_WIDTH

VAD_CHUNK_SAMPLES = 512
VAD_CHUNK_BYTES = VAD_CHUNK_SAMPLES * PCM_WIDTH
PREBUFFER_SECONDS = 0.3
TRAILING_SILENCE_SECONDS = 0.8
MIN_SEGMENT_SECONDS = 0.5
MIN_SPEECH_SECONDS = 0.1
MAX_SEGMENT_SECONDS = 15.0
VAD_THRESHOLD = 0.5
READ_TIMEOUT_SECONDS = 10.0


class VadError(RuntimeError):
    """Raised when local voice activity detection cannot run safely."""


def new_silero_detector() -> Any:
    try:
        from pysilero_vad import SileroVoiceActivityDetector

        return SileroVoiceActivityDetector()
    except Exception as exc:
        raise VadError(f"Unable to initialize local VAD: {type(exc).__name__}") from exc


def reset_detector(detector: Any) -> None:
    reset = getattr(detector, "reset", None)
    if callable(reset):
        reset()


async def capture_speech_segment(
    read_chunk: Callable[[], Awaitable[bytes]],
    detector: Any,
    stop_event: asyncio.Event,
) -> bytes | None:
    """Wait for and return one bounded speech candidate, or None when stopping."""
    prebuffer_count = max(1, math.ceil(PREBUFFER_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES))
    silence_count = max(
        1,
        math.ceil(TRAILING_SILENCE_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES),
    )
    minimum_count = max(1, math.ceil(MIN_SEGMENT_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES))
    minimum_speech_count = max(
        1,
        math.ceil(MIN_SPEECH_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES),
    )
    maximum_count = max(1, math.ceil(MAX_SEGMENT_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES))
    prebuffer: deque[bytes] = deque(maxlen=prebuffer_count)
    segment: list[bytes] = []
    silence_chunks = 0
    speech_chunks = 0
    active = False

    while not stop_event.is_set():
        chunk = await asyncio.wait_for(read_chunk(), timeout=READ_TIMEOUT_SECONDS)
        if len(chunk) != VAD_CHUNK_BYTES:
            raise VadError("PCM source returned a partial VAD chunk")
        try:
            is_speech = float(detector(chunk)) > VAD_THRESHOLD
        except Exception as exc:
            raise VadError(f"Local VAD processing failed: {type(exc).__name__}") from exc

        if not active:
            if not is_speech:
                prebuffer.append(chunk)
                continue
            segment = list(prebuffer)
            segment.append(chunk)
            prebuffer.clear()
            silence_chunks = 0
            speech_chunks = 1
            active = True
            continue

        segment.append(chunk)
        if is_speech:
            speech_chunks += 1
        silence_chunks = 0 if is_speech else silence_chunks + 1
        if len(segment) < maximum_count and silence_chunks < silence_count:
            continue

        if len(segment) >= minimum_count and speech_chunks >= minimum_speech_count:
            reset_detector(detector)
            return b"".join(segment)
        segment.clear()
        silence_chunks = 0
        speech_chunks = 0
        active = False
        reset_detector(detector)

    reset_detector(detector)
    return None
