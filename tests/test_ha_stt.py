from __future__ import annotations

import json

import pytest
from gateway.ha_stt import HaSttClient, HaSttError
from websockets.asyncio.server import serve


async def test_stt_only_websocket_contract_sends_exact_pcm() -> None:
    observed: dict = {}
    handler_id = 7

    async def handler(websocket) -> None:
        await websocket.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(await websocket.recv())
        observed["authenticated"] = auth == {"type": "auth", "access_token": "secret"}
        await websocket.send(json.dumps({"type": "auth_ok"}))
        request = json.loads(await websocket.recv())
        observed["request"] = request
        await websocket.send(json.dumps({"id": 1, "type": "result", "success": True}))
        await websocket.send(
            json.dumps(
                {
                    "id": 1,
                    "type": "event",
                    "event": {
                        "type": "run-start",
                        "data": {"runner_data": {"stt_binary_handler_id": handler_id}},
                    },
                }
            )
        )
        await websocket.send(
            json.dumps({"id": 1, "type": "event", "event": {"type": "stt-start", "data": {}}})
        )
        chunks: list[bytes] = []
        while True:
            message = await websocket.recv()
            assert isinstance(message, bytes)
            assert message[0] == handler_id
            if len(message) == 1:
                break
            chunks.append(message[1:])
        observed["audio"] = b"".join(chunks)
        await websocket.send(
            json.dumps(
                {
                    "id": 1,
                    "type": "event",
                    "event": {
                        "type": "stt-end",
                        "data": {"stt_output": {"text": "ねえコンピューター、電気を消して"}},
                    },
                }
            )
        )
        await websocket.send(
            json.dumps({"id": 1, "type": "event", "event": {"type": "run-end", "data": {}}})
        )

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = HaSttClient(
            "pipeline-id",
            token="secret",
            websocket_uri=f"ws://127.0.0.1:{port}",
        )
        audio = bytes(range(256)) * 20
        assert await client.transcribe(audio) == "ねえコンピューター、電気を消して"

    assert observed["authenticated"] is True
    assert observed["audio"] == audio
    request = observed["request"]
    assert request["start_stage"] == "stt"
    assert request["end_stage"] == "stt"
    assert request["input"] == {"sample_rate": 16000, "no_vad": True}
    assert request["pipeline"] == "pipeline-id"


async def test_server_error_does_not_expose_response_or_token() -> None:
    async def handler(websocket) -> None:
        await websocket.send(json.dumps({"type": "auth_required"}))
        await websocket.recv()
        await websocket.send(json.dumps({"type": "auth_ok"}))
        await websocket.recv()
        await websocket.send(
            json.dumps(
                {
                    "id": 1,
                    "type": "result",
                    "success": False,
                    "error": {"message": "provider leaked household-secret"},
                }
            )
        )

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = HaSttClient(
            "",
            token="supervisor-secret",
            websocket_uri=f"ws://127.0.0.1:{port}",
        )
        with pytest.raises(HaSttError) as caught:
            await client.transcribe(b"\0\0")
    assert "household-secret" not in str(caught.value)
    assert "supervisor-secret" not in str(caught.value)


async def test_invalid_audio_and_missing_token_fail_before_connect() -> None:
    client = HaSttClient("", token="")
    with pytest.raises(HaSttError, match="token"):
        await client.transcribe(b"\0\0")
    with pytest.raises(HaSttError, match="signed 16-bit"):
        await HaSttClient("", token="x").transcribe(b"odd")
