"""Supervisor MQTT discovery and diagnostic publishing."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import urllib.request
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt


class MqttError(RuntimeError):
    """Raised for MQTT discovery or publishing failures."""


@dataclass(frozen=True)
class MqttCredentials:
    host: str
    port: int
    username: str = ""
    password: str = ""
    ssl: bool = False


def fetch_mqtt_credentials(
    token: str | None = None,
    url: str = "http://supervisor/services/mqtt",
    timeout: float = 10,
) -> MqttCredentials:
    supervisor_token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
    if not supervisor_token:
        raise MqttError("Supervisor token is unavailable")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {supervisor_token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except Exception as exc:
        raise MqttError("Unable to obtain MQTT service configuration") from exc
    data = body.get("data", {}) if isinstance(body, dict) else {}
    host = data.get("host")
    port = data.get("port", 1883)
    ssl_enabled = data.get("ssl", False)
    if (
        not isinstance(host, str)
        or not host
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        or not isinstance(ssl_enabled, bool)
    ):
        raise MqttError("MQTT service configuration is invalid")
    username = data.get("username", "")
    password = data.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str):
        raise MqttError("MQTT service configuration is invalid")
    return MqttCredentials(
        host=host,
        port=port,
        username=username,
        password=password,
        ssl=ssl_enabled,
    )


class PahoPublisher:
    def __init__(self, credentials: MqttCredentials, connect_timeout: float = 10) -> None:
        self.credentials = credentials
        self.connect_timeout = connect_timeout
        self._connected = threading.Event()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
        if credentials.username:
            self._client.username_pw_set(credentials.username, credentials.password)
        if credentials.ssl:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._started = False

    def _on_connect(
        self,
        _client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _props: Any,
    ) -> None:
        if reason_code == 0:
            self._connected.set()

    def _on_disconnect(
        self,
        _client: Any,
        _userdata: Any,
        _disconnect_flags: Any,
        _reason_code: Any,
        _props: Any,
    ) -> None:
        self._connected.clear()

    def _connect_sync(self) -> None:
        self._client.connect(self.credentials.host, self.credentials.port, keepalive=30)
        self._client.loop_start()
        self._started = True
        if not self._connected.wait(self.connect_timeout):
            self.close_sync()
            raise MqttError("MQTT connection timed out")

    async def connect(self) -> None:
        await asyncio.to_thread(self._connect_sync)

    def _publish_sync(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        if not self._connected.is_set():
            raise MqttError("MQTT publisher is disconnected")
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=10)
        if not info.is_published():
            raise MqttError("MQTT publish was not acknowledged")

    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        await asyncio.to_thread(self._publish_sync, topic, payload, qos, retain)

    def close_sync(self) -> None:
        if self._started:
            self._client.disconnect()
            self._client.loop_stop()
            self._started = False
        self._connected.clear()

    async def close(self) -> None:
        await asyncio.to_thread(self.close_sync)
