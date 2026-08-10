"""Bounded ffmpeg PCM source with secret-safe diagnostics."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass

from .config import SourceConfig

LOGGER = logging.getLogger(__name__)
PCM_RATE = 16_000
PCM_WIDTH = 2
PCM_CHANNELS = 1
PCM_CHUNK_BYTES = 2_048


class SourceDisconnected(ConnectionError):
    """Raised when ffmpeg stops yielding PCM."""


@dataclass
class FfmpegPcmSource:
    config: SourceConfig
    ffmpeg_binary: str = "ffmpeg"
    chunk_bytes: int = PCM_CHUNK_BYTES

    def __post_init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None

    def command(self) -> tuple[str, ...]:
        return (
            self.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            self.config.url,
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            str(PCM_CHANNELS),
            "-ar",
            str(PCM_RATE),
            "-f",
            "s16le",
            "pipe:1",
        )

    async def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("PCM source is already started")
        LOGGER.info("Starting RTSP source source_id=%s", self.config.id)
        self._process = await asyncio.create_subprocess_exec(
            *self.command(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def read_chunk(self) -> bytes:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError("PCM source is not started")
        try:
            return await process.stdout.readexactly(self.chunk_bytes)
        except asyncio.IncompleteReadError as exc:
            raise SourceDisconnected("RTSP source ended") from exc

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        else:
            await process.wait()
        LOGGER.info("Stopped RTSP source source_id=%s", self.config.id)
