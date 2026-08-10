"""Home Assistant Assist WebSocket adapter restricted to the STT stage."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect

from .source import PCM_RATE

DEFAULT_WEBSOCKET_URI = "ws://supervisor/core/websocket"
SEND_CHUNK_BYTES = 3_200


class HaSttError(RuntimeError):
    """Raised when the STT-only Assist request fails."""


class HaSttClient:
    def __init__(
        self,
        pipeline_id: str,
        *,
        token: str | None = None,
        websocket_uri: str = DEFAULT_WEBSOCKET_URI,
        connect_factory: Callable[..., Any] = connect,
        timeout: float = 45,
    ) -> None:
        self.pipeline_id = pipeline_id
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.websocket_uri = websocket_uri
        self.connect_factory = connect_factory
        self.timeout = timeout

    async def transcribe(self, audio: bytes) -> str:
        if not self.token:
            raise HaSttError("Supervisor token is unavailable")
        if not audio or len(audio) % 2:
            raise HaSttError("STT audio must be non-empty signed 16-bit PCM")

        try:
            async with asyncio.timeout(self.timeout):
                async with self.connect_factory(
                    self.websocket_uri,
                    max_size=2**20,
                    open_timeout=self.timeout,
                    close_timeout=5,
                ) as websocket:
                    await self._authenticate(websocket)
                    return await self._run_stt(websocket, audio)
        except HaSttError:
            raise
        except Exception as exc:
            raise HaSttError(f"Home Assistant STT failed: {type(exc).__name__}") from exc

    async def _authenticate(self, websocket: Any) -> None:
        hello = await self._receive_json(websocket)
        if hello.get("type") != "auth_required":
            raise HaSttError("Home Assistant WebSocket did not request authentication")
        await websocket.send(json.dumps({"type": "auth", "access_token": self.token}))
        auth = await self._receive_json(websocket)
        if auth.get("type") != "auth_ok":
            raise HaSttError("Home Assistant WebSocket authentication failed")

    async def _run_stt(self, websocket: Any, audio: bytes) -> str:
        request: dict[str, Any] = {
            "id": 1,
            "type": "assist_pipeline/run",
            "start_stage": "stt",
            "end_stage": "stt",
            "input": {"sample_rate": PCM_RATE, "no_vad": True},
        }
        if self.pipeline_id:
            request["pipeline"] = self.pipeline_id
        await websocket.send(json.dumps(request))

        handler_id: int | None = None
        while handler_id is None:
            message = await self._receive_json(websocket)
            self._raise_for_error(message)
            event = message.get("event", {})
            if event.get("type") == "run-start":
                value = event.get("data", {}).get("runner_data", {}).get("stt_binary_handler_id")
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
                    raise HaSttError("Home Assistant returned an invalid STT binary handler")
                handler_id = value

        for offset in range(0, len(audio), SEND_CHUNK_BYTES):
            await websocket.send(bytes([handler_id]) + audio[offset : offset + SEND_CHUNK_BYTES])
        await websocket.send(bytes([handler_id]))

        transcript: str | None = None
        while True:
            message = await self._receive_json(websocket)
            self._raise_for_error(message)
            event = message.get("event", {})
            event_type = event.get("type")
            if event_type == "stt-end":
                value = event.get("data", {}).get("stt_output", {}).get("text")
                if not isinstance(value, str):
                    raise HaSttError("Home Assistant returned an invalid STT result")
                transcript = value
            elif event_type == "run-end":
                break
        if transcript is None:
            raise HaSttError("Home Assistant ended the Assist run without an STT result")
        return transcript

    @staticmethod
    async def _receive_json(websocket: Any) -> dict[str, Any]:
        raw = await websocket.recv()
        if not isinstance(raw, str):
            raise HaSttError("Home Assistant returned an unexpected binary message")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HaSttError("Home Assistant returned invalid JSON") from exc
        if not isinstance(message, dict):
            raise HaSttError("Home Assistant returned an invalid message")
        return message

    @staticmethod
    def _raise_for_error(message: dict[str, Any]) -> None:
        if message.get("type") == "result" and message.get("success") is False:
            raise HaSttError("Home Assistant rejected the STT-only pipeline request")
        if message.get("event", {}).get("type") == "error":
            raise HaSttError("Home Assistant STT pipeline emitted an error")
