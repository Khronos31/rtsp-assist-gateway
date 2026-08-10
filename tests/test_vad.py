from __future__ import annotations

import asyncio
from collections import deque

import pytest
from gateway.vad import VAD_CHUNK_BYTES, VadError, capture_speech_segment


class SequenceDetector:
    def __init__(self, values: list[float]) -> None:
        self.values = deque(values)
        self.reset_count = 0

    def __call__(self, _chunk: bytes) -> float:
        return self.values.popleft()

    def reset(self) -> None:
        self.reset_count += 1


def reader(chunks: list[bytes]):
    remaining = deque(chunks)

    async def read_chunk() -> bytes:
        return remaining.popleft()

    return read_chunk


async def test_capture_returns_bounded_candidate_with_prebuffer_and_tail() -> None:
    silence_before = 10
    speech = 4
    silence_after = 25
    values = [0.0] * silence_before + [0.9] * speech + [0.0] * silence_after
    chunks = [bytes([index % 251]) * VAD_CHUNK_BYTES for index in range(len(values))]
    detector = SequenceDetector(values)
    result = await capture_speech_segment(reader(chunks), detector, asyncio.Event())
    assert result is not None
    assert len(result) == (silence_before + speech + silence_after) * VAD_CHUNK_BYTES
    assert result.startswith(chunks[0])
    assert result.endswith(chunks[-1])
    assert detector.reset_count == 1


async def test_single_false_positive_is_discarded() -> None:
    # One speech-positive frame followed by the full tail is below MIN_SPEECH_SECONDS.
    first = [0.9] + [0.0] * 25
    second = [0.9] * 4 + [0.0] * 25
    detector = SequenceDetector(first + second)
    chunks = [b"x" * VAD_CHUNK_BYTES for _ in range(len(first + second))]
    result = await capture_speech_segment(reader(chunks), detector, asyncio.Event())
    assert result is not None
    assert detector.reset_count == 2


async def test_partial_chunk_fails_closed() -> None:
    detector = SequenceDetector([0.0])
    with pytest.raises(VadError, match="partial"):
        await capture_speech_segment(reader([b"short"]), detector, asyncio.Event())


async def test_stop_before_read_returns_none_and_resets() -> None:
    detector = SequenceDetector([])
    stop_event = asyncio.Event()
    stop_event.set()
    assert await capture_speech_segment(reader([]), detector, stop_event) is None
    assert detector.reset_count == 1
