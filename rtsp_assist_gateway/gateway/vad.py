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


class SpeechSegmentCollector:
    """Stateful, bounded VAD collector for a PCM stream.

    ``feed`` mirrors the segmentation contract used by
    :func:`capture_speech_segment`, but lets another consumer (such as a Wyoming
    wake detector) observe the same chunks while the current speech segment is
    retained only in memory.
    """

    def __init__(self, detector: Any) -> None:
        self.detector = detector
        self.prebuffer_count = max(
            1,
            math.ceil(PREBUFFER_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES),
        )
        self.silence_count = max(
            1,
            math.ceil(TRAILING_SILENCE_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES),
        )
        self.minimum_count = max(
            1,
            math.ceil(MIN_SEGMENT_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES),
        )
        self.minimum_speech_count = max(
            1,
            math.ceil(MIN_SPEECH_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES),
        )
        self.maximum_count = max(
            1,
            math.ceil(MAX_SEGMENT_SECONDS * PCM_RATE / VAD_CHUNK_SAMPLES),
        )
        self.prebuffer: deque[bytes] = deque(maxlen=self.prebuffer_count)
        self.segment: list[bytes] = []
        self.silence_chunks = 0
        self.speech_chunks = 0
        self.active = False

    def feed(self, chunk: bytes) -> bytes | None:
        """Consume one exact VAD chunk and return a completed segment, if any."""
        if len(chunk) != VAD_CHUNK_BYTES:
            raise VadError("PCM source returned a partial VAD chunk")
        try:
            is_speech = float(self.detector(chunk)) > VAD_THRESHOLD
        except Exception as exc:
            raise VadError(f"Local VAD processing failed: {type(exc).__name__}") from exc

        if not self.active:
            if not is_speech:
                self.prebuffer.append(chunk)
                return None
            self.segment = list(self.prebuffer)
            self.segment.append(chunk)
            self.prebuffer.clear()
            self.silence_chunks = 0
            self.speech_chunks = 1
            self.active = True
            return None

        self.segment.append(chunk)
        if is_speech:
            self.speech_chunks += 1
        self.silence_chunks = 0 if is_speech else self.silence_chunks + 1
        if len(self.segment) < self.maximum_count and self.silence_chunks < self.silence_count:
            return None

        completed = b"".join(self.segment)
        valid = (
            len(self.segment) >= self.minimum_count
            and self.speech_chunks >= self.minimum_speech_count
        )
        self._clear_active(reset=True)
        return completed if valid else None

    def reset(self) -> None:
        """Discard buffered audio and reset detector state."""
        self.prebuffer.clear()
        self._clear_active(reset=True)

    def _clear_active(self, *, reset: bool) -> None:
        self.segment.clear()
        self.silence_chunks = 0
        self.speech_chunks = 0
        self.active = False
        if reset:
            reset_detector(self.detector)


async def capture_speech_segment(
    read_chunk: Callable[[], Awaitable[bytes]],
    detector: Any,
    stop_event: asyncio.Event,
) -> bytes | None:
    """Wait for and return one bounded speech candidate, or None when stopping."""
    collector = SpeechSegmentCollector(detector)

    while not stop_event.is_set():
        chunk = await asyncio.wait_for(read_chunk(), timeout=READ_TIMEOUT_SECONDS)
        completed = collector.feed(chunk)
        if completed is not None:
            return completed

    collector.reset()
    return None
