"""Supervisor options parsing and fail-closed validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ALLOWED_LOG_LEVELS = {"debug", "info", "warning", "error"}
CANARY_TOPIC = "rtsp_assist_gateway/canary/detection"


class ConfigError(ValueError):
    """Raised when add-on options violate the runtime contract."""


@dataclass(frozen=True)
class SourceConfig:
    id: str
    url: str
    room: str


@dataclass(frozen=True)
class PassiveCanaryConfig:
    enabled: bool
    source_id: str
    wyoming_host: str
    wyoming_port: int
    models: tuple[str, ...]
    cooldown_seconds: int


@dataclass(frozen=True)
class GatewayConfig:
    log_level: str
    sources: tuple[SourceConfig, ...]
    passive_canary: PassiveCanaryConfig


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be an object")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _require_label(value: Any, field: str) -> str:
    label = _require_string(value, field)
    if len(label) > 128 or any(ord(character) < 32 for character in label):
        raise ConfigError(f"{field} contains invalid characters")
    return label


def _parse_rtsp_url(value: Any, source_id: str) -> str:
    url = _require_string(value, f"source {source_id} URL")
    if any(ord(character) < 32 for character in url):
        raise ConfigError(f"source {source_id} URL contains control characters")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"source {source_id} URL is invalid") from exc
    if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
        raise ConfigError(f"source {source_id} URL must be an absolute RTSP URL")
    if port is not None and not 1 <= port <= 65535:
        raise ConfigError(f"source {source_id} URL port is invalid")
    if parsed.fragment:
        raise ConfigError(f"source {source_id} URL must not contain a fragment")
    return url


def _parse_wyoming_uri(value: Any) -> tuple[str, int]:
    uri = _require_string(value, "passive_canary.wyoming_uri")
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("passive_canary.wyoming_uri is invalid") from exc
    if (
        parsed.scheme != "tcp"
        or not parsed.hostname
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigError("passive_canary.wyoming_uri must be tcp://host:port")
    if not 1 <= port <= 65535:
        raise ConfigError("passive_canary.wyoming_uri port is invalid")
    return parsed.hostname, port


def parse_options(options: Any) -> GatewayConfig:
    root = _require_mapping(options, "options")
    log_level = str(root.get("log_level", "info")).lower()
    if log_level not in ALLOWED_LOG_LEVELS:
        raise ConfigError("log_level is invalid")

    raw_sources = root.get("sources", [])
    if not isinstance(raw_sources, list):
        raise ConfigError("sources must be a list")

    sources: list[SourceConfig] = []
    seen_ids: set[str] = set()
    for index, raw_source in enumerate(raw_sources):
        source = _require_mapping(raw_source, f"sources[{index}]")
        source_id = _require_string(source.get("id"), f"sources[{index}].id")
        if not SOURCE_ID_RE.fullmatch(source_id):
            raise ConfigError(f"source ID at index {index} is invalid")
        if source_id in seen_ids:
            raise ConfigError(f"duplicate source ID: {source_id}")
        seen_ids.add(source_id)
        room = _require_label(source.get("room"), f"source {source_id} room")
        sources.append(
            SourceConfig(id=source_id, url=_parse_rtsp_url(source.get("url"), source_id), room=room)
        )

    raw_canary = _require_mapping(root.get("passive_canary", {}), "passive_canary")
    if "mqtt_topic" in raw_canary:
        raise ConfigError("passive_canary.mqtt_topic is fixed and must not be configured")
    enabled = raw_canary.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("passive_canary.enabled must be a boolean")

    source_id_value = raw_canary.get("source_id", "")
    if not isinstance(source_id_value, str):
        raise ConfigError("passive_canary.source_id must be a string")
    source_id = source_id_value.strip()

    wyoming_uri = raw_canary.get("wyoming_uri", "tcp://47701997-microwakeword:10400")
    wyoming_host, wyoming_port = _parse_wyoming_uri(wyoming_uri)

    raw_models = raw_canary.get("models", ["hey_jarvis"])
    if not isinstance(raw_models, list) or not raw_models:
        raise ConfigError("passive_canary.models must be a non-empty list")
    models: list[str] = []
    for index, raw_model in enumerate(raw_models):
        model = _require_string(raw_model, f"passive_canary.models[{index}]")
        if not MODEL_RE.fullmatch(model):
            raise ConfigError(f"passive_canary model at index {index} is invalid")
        if model in models:
            raise ConfigError(f"duplicate passive_canary model: {model}")
        models.append(model)

    cooldown = raw_canary.get("cooldown_seconds", 3)
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or not 0 <= cooldown <= 300:
        raise ConfigError("passive_canary.cooldown_seconds must be an integer from 0 to 300")

    if enabled:
        if len(sources) != 1:
            raise ConfigError("Phase 1 passive canary requires exactly one source")
        if not source_id:
            raise ConfigError("passive_canary.source_id is required when enabled")
        if source_id not in seen_ids:
            raise ConfigError("passive_canary.source_id must reference a configured source")

    return GatewayConfig(
        log_level=log_level,
        sources=tuple(sources),
        passive_canary=PassiveCanaryConfig(
            enabled=enabled,
            source_id=source_id,
            wyoming_host=wyoming_host,
            wyoming_port=wyoming_port,
            models=tuple(models),
            cooldown_seconds=cooldown,
        ),
    )


def load_options(path: str | Path = "/data/options.json") -> GatewayConfig:
    options_path = Path(path)
    try:
        options = json.loads(options_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("unable to read valid Supervisor options") from exc
    return parse_options(options)
