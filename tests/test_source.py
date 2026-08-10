from __future__ import annotations

import asyncio
import logging

from gateway.config import SourceConfig
from gateway.source import FfmpegPcmSource


class FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_eof()

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


async def test_ffmpeg_boundary_does_not_log_or_forward_stderr(monkeypatch, caplog) -> None:
    secret = "camera-password"
    url = f"rtsp://user:{secret}@example.invalid:8554/audio"
    captured: dict = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    source = FfmpegPcmSource(SourceConfig(id="study", url=url, room="study"))
    with caplog.at_level(logging.INFO):
        await source.start()
        await source.close()
    assert url in captured["args"]
    assert captured["kwargs"]["stderr"] is asyncio.subprocess.DEVNULL
    assert url not in caplog.text
    assert secret not in caplog.text
