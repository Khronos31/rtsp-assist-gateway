"""Minimal Wyoming wake-word client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import async_read_event, async_write_event
from wyoming.wake import Detect, Detection

from .source import PCM_CHANNELS, PCM_RATE, PCM_WIDTH


class ProviderDisconnected(ConnectionError):
    """Raised when the Wyoming provider closes unexpectedly."""


class WyomingDetector:
    def __init__(
        self,
        host: str,
        port: int,
        models: tuple[str, ...],
        connect_timeout: float = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.models = models
        self.connect_timeout = connect_timeout

    async def detect(
        self,
        read_chunk: Callable[[], Awaitable[bytes]],
        stop_event: asyncio.Event,
    ) -> Detection | None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.connect_timeout
        )
        event_task: asyncio.Task | None = None
        chunk_task: asyncio.Task | None = None
        stop_task: asyncio.Task | None = None
        try:
            await async_write_event(Detect(names=list(self.models)).event(), writer)
            await async_write_event(
                AudioStart(rate=PCM_RATE, width=PCM_WIDTH, channels=PCM_CHANNELS).event(), writer
            )
            event_task = asyncio.create_task(async_read_event(reader))
            while True:
                chunk_task = asyncio.create_task(read_chunk())
                stop_task = asyncio.create_task(stop_event.wait())
                done, _ = await asyncio.wait(
                    {event_task, chunk_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if stop_task in done and stop_task.result():
                    return None
                if event_task in done:
                    event = event_task.result()
                    if event is None:
                        raise ProviderDisconnected("Wyoming provider ended")
                    if Detection.is_type(event.type):
                        detection = Detection.from_event(event)
                        if detection.name in self.models:
                            return detection
                    event_task = asyncio.create_task(async_read_event(reader))
                if chunk_task in done:
                    chunk = chunk_task.result()
                    await async_write_event(
                        AudioChunk(
                            rate=PCM_RATE,
                            width=PCM_WIDTH,
                            channels=PCM_CHANNELS,
                            audio=chunk,
                        ).event(),
                        writer,
                    )
                    chunk_task = None
                else:
                    chunk_task.cancel()
                    await asyncio.gather(chunk_task, return_exceptions=True)
                    chunk_task = None
                if stop_task is not None:
                    stop_task.cancel()
                    await asyncio.gather(stop_task, return_exceptions=True)
                    stop_task = None
        finally:
            for task in (event_task, chunk_task, stop_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (event_task, chunk_task, stop_task) if task is not None),
                return_exceptions=True,
            )
            with suppress(ConnectionError, RuntimeError):
                await async_write_event(AudioStop().event(), writer)
            writer.close()
            with suppress(ConnectionError, OSError, RuntimeError):
                await writer.wait_closed()
