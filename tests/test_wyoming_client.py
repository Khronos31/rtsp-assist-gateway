from __future__ import annotations

import asyncio

from gateway.wyoming_client import WyomingDetector
from wyoming.audio import AudioChunk, AudioStart
from wyoming.event import async_read_event, async_write_event
from wyoming.wake import Detect, Detection


async def test_real_wyoming_wire_exchange() -> None:
    received: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while event := await async_read_event(reader):
                received.append(event.type)
                if AudioChunk.is_type(event.type):
                    chunk = AudioChunk.from_event(event)
                    assert chunk.rate == 16_000
                    assert chunk.width == 2
                    assert chunk.channels == 1
                    assert chunk.audio == b"\x01\x02" * 1024
                    await async_write_event(Detection(name="hey_jarvis").event(), writer)
                    return
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    stop_event = asyncio.Event()

    async def read_chunk() -> bytes:
        await asyncio.sleep(0.01)
        return b"\x01\x02" * 1024

    try:
        detection = await WyomingDetector("127.0.0.1", port, ("hey_jarvis",)).detect(
            read_chunk, stop_event
        )
    finally:
        server.close()
        await server.wait_closed()

    assert detection is not None
    assert detection.name == "hey_jarvis"
    assert Detect(names=["hey_jarvis"]).event().type in received
    assert AudioStart(rate=16_000, width=2, channels=1).event().type in received
    assert AudioChunk(rate=16_000, width=2, channels=1, audio=b"").event().type in received
