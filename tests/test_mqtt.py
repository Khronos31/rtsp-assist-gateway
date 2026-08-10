from __future__ import annotations

import io
import json

import pytest
from gateway.mqtt import MqttError, fetch_mqtt_credentials


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_supervisor_mqtt_discovery(monkeypatch) -> None:
    body = {
        "result": "ok",
        "data": {
            "host": "broker",
            "port": 1883,
            "username": "u",
            "password": "p",
            "ssl": True,
        },
    }

    def fake_urlopen(request, timeout):
        assert request.headers["Authorization"] == "Bearer test-token"
        assert timeout == 10
        return Response(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    credentials = fetch_mqtt_credentials(token="test-token")
    assert credentials.host == "broker"
    assert credentials.password == "p"
    assert credentials.ssl is True


def test_supervisor_failure_hides_underlying_secret(monkeypatch) -> None:
    def fake_urlopen(_request, timeout):
        raise RuntimeError(f"token leaked at timeout={timeout}: super-secret")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(MqttError) as error:
        fetch_mqtt_credentials(token="super-secret")
    assert "super-secret" not in str(error.value)
