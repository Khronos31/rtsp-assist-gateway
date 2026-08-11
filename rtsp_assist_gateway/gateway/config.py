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
HA_STT_CANARY_TOPIC = "rtsp_assist_gateway/canary/ha_stt"
ACTIVATION_TOPIC = "rtsp_assist_gateway/activation"
MIN_NORMALIZED_ALIAS_LENGTH = 3


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
class WakeWordConfig:
    id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class MicroWakeWordMapping:
    model: str
    id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class HaSttCanaryConfig:
    enabled: bool
    source_id: str
    pipeline_id: str
    wake_words: tuple[WakeWordConfig, ...]
    cooldown_seconds: int
    max_requests_per_minute: int
    max_audio_seconds_per_hour: int
    max_audio_seconds_per_day: int


@dataclass(frozen=True)
class MicroWakeWordActivationConfig:
    enabled: bool
    source_id: str
    wyoming_host: str
    wyoming_port: int
    wake_words: tuple[MicroWakeWordMapping, ...]
    pipeline_id: str
    cooldown_seconds: int
    max_requests_per_minute: int
    max_audio_seconds_per_hour: int
    max_audio_seconds_per_day: int

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(word.model for word in self.wake_words)


@dataclass(frozen=True)
class GatewayConfig:
    log_level: str
    sources: tuple[SourceConfig, ...]
    passive_canary: PassiveCanaryConfig
    ha_stt_canary: HaSttCanaryConfig
    ha_stt_activation: HaSttCanaryConfig
    microwakeword_activation: MicroWakeWordActivationConfig


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


def _parse_wyoming_uri(value: Any, field: str = "passive_canary") -> tuple[str, int]:
    uri = _require_string(value, f"{field}.wyoming_uri")
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{field}.wyoming_uri is invalid") from exc
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
        raise ConfigError(f"{field}.wyoming_uri must be tcp://host:port")
    if not 1 <= port <= 65535:
        raise ConfigError(f"{field}.wyoming_uri port is invalid")
    return parsed.hostname, port


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _parse_ha_stt_config(root: dict[str, Any], field: str) -> HaSttCanaryConfig:
    raw_stt = _require_mapping(root.get(field, {}), field)
    if "mqtt_topic" in raw_stt:
        raise ConfigError(f"{field}.mqtt_topic is fixed and must not be configured")
    enabled = raw_stt.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{field}.enabled must be a boolean")

    source_value = raw_stt.get("source_id", "")
    if not isinstance(source_value, str):
        raise ConfigError(f"{field}.source_id must be a string")
    source_id = source_value.strip()

    pipeline_value = raw_stt.get("pipeline_id", "")
    if not isinstance(pipeline_value, str):
        raise ConfigError(f"{field}.pipeline_id must be a string")
    pipeline_id = pipeline_value.strip()
    if len(pipeline_id) > 128 or any(ord(character) < 32 for character in pipeline_id):
        raise ConfigError(f"{field}.pipeline_id contains invalid characters")

    raw_wake_words = raw_stt.get(
        "wake_words",
        [{"id": "hey_jarvis", "aliases": ["hey jarvis"]}],
    )
    if not isinstance(raw_wake_words, list) or not raw_wake_words:
        raise ConfigError(f"{field}.wake_words must be a non-empty list")

    # Import here to keep matching normalization in one authoritative place without
    # introducing a config/matcher import cycle.
    from .matcher import normalize_for_match

    wake_words: list[WakeWordConfig] = []
    seen_word_ids: set[str] = set()
    seen_aliases: dict[str, str] = {}
    for word_index, raw_word in enumerate(raw_wake_words):
        word = _require_mapping(raw_word, f"{field}.wake_words[{word_index}]")
        word_id = _require_string(word.get("id"), f"{field}.wake_words[{word_index}].id")
        if not MODEL_RE.fullmatch(word_id):
            raise ConfigError(f"{field} wake-word ID at index {word_index} is invalid")
        if word_id in seen_word_ids:
            raise ConfigError(f"duplicate {field} wake-word ID: {word_id}")
        seen_word_ids.add(word_id)

        raw_aliases = word.get("aliases")
        if not isinstance(raw_aliases, list) or not raw_aliases:
            raise ConfigError(f"{field} aliases for {word_id} must be a non-empty list")
        aliases: list[str] = []
        for alias_index, raw_alias in enumerate(raw_aliases):
            alias = _require_label(
                raw_alias,
                f"{field}.wake_words[{word_index}].aliases[{alias_index}]",
            )
            normalized = normalize_for_match(alias)
            if len(normalized) < MIN_NORMALIZED_ALIAS_LENGTH:
                raise ConfigError(
                    f"{field} alias for {word_id} is shorter than "
                    f"{MIN_NORMALIZED_ALIAS_LENGTH} normalized characters"
                )
            previous = seen_aliases.get(normalized)
            if previous is not None:
                raise ConfigError(
                    f"{field} alias collision after normalization: {previous} and {word_id}"
                )
            seen_aliases[normalized] = word_id
            aliases.append(alias)
        wake_words.append(WakeWordConfig(id=word_id, aliases=tuple(aliases)))

    cooldown_seconds = _bounded_int(
        raw_stt.get("cooldown_seconds", 3),
        f"{field}.cooldown_seconds",
        0,
        300,
    )
    max_requests_per_minute = _bounded_int(
        raw_stt.get("max_requests_per_minute", 6),
        f"{field}.max_requests_per_minute",
        1,
        60,
    )
    max_audio_seconds_per_hour = _bounded_int(
        raw_stt.get("max_audio_seconds_per_hour", 300),
        f"{field}.max_audio_seconds_per_hour",
        16,
        3600,
    )
    max_audio_seconds_per_day = _bounded_int(
        raw_stt.get("max_audio_seconds_per_day", 1800),
        f"{field}.max_audio_seconds_per_day",
        16,
        86400,
    )
    if max_audio_seconds_per_day < max_audio_seconds_per_hour:
        raise ConfigError(f"{field}.max_audio_seconds_per_day must be at least the hourly limit")

    return HaSttCanaryConfig(
        enabled=enabled,
        source_id=source_id,
        pipeline_id=pipeline_id,
        wake_words=tuple(wake_words),
        cooldown_seconds=cooldown_seconds,
        max_requests_per_minute=max_requests_per_minute,
        max_audio_seconds_per_hour=max_audio_seconds_per_hour,
        max_audio_seconds_per_day=max_audio_seconds_per_day,
    )


def _parse_microwakeword_activation(
    root: dict[str, Any],
) -> MicroWakeWordActivationConfig:
    field = "microwakeword_activation"
    raw = _require_mapping(root.get(field, {}), field)
    if "mqtt_topic" in raw:
        raise ConfigError(f"{field}.mqtt_topic is fixed and must not be configured")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{field}.enabled must be a boolean")

    source_value = raw.get("source_id", "")
    if not isinstance(source_value, str):
        raise ConfigError(f"{field}.source_id must be a string")
    source_id = source_value.strip()

    pipeline_value = raw.get("pipeline_id", "")
    if not isinstance(pipeline_value, str):
        raise ConfigError(f"{field}.pipeline_id must be a string")
    pipeline_id = pipeline_value.strip()
    if len(pipeline_id) > 128 or any(ord(character) < 32 for character in pipeline_id):
        raise ConfigError(f"{field}.pipeline_id contains invalid characters")

    wyoming_uri = raw.get("wyoming_uri", "tcp://47701997-microwakeword:10400")
    wyoming_host, wyoming_port = _parse_wyoming_uri(wyoming_uri, field)

    raw_wake_words = raw.get(
        "wake_words",
        [{"model": "hey_jarvis", "id": "hey_jarvis", "aliases": ["hey jarvis"]}],
    )
    if not isinstance(raw_wake_words, list) or not raw_wake_words:
        raise ConfigError(f"{field}.wake_words must be a non-empty list")

    from .matcher import normalize_for_match

    wake_words: list[MicroWakeWordMapping] = []
    seen_models: set[str] = set()
    for index, raw_word in enumerate(raw_wake_words):
        word = _require_mapping(raw_word, f"{field}.wake_words[{index}]")
        model = _require_string(word.get("model"), f"{field}.wake_words[{index}].model")
        if not MODEL_RE.fullmatch(model):
            raise ConfigError(f"{field} model at index {index} is invalid")
        if model in seen_models:
            raise ConfigError(f"duplicate {field} model: {model}")
        seen_models.add(model)

        word_id = _require_string(word.get("id"), f"{field}.wake_words[{index}].id")
        if not MODEL_RE.fullmatch(word_id):
            raise ConfigError(f"{field} wake-word ID at index {index} is invalid")

        raw_aliases = word.get("aliases")
        if not isinstance(raw_aliases, list) or not raw_aliases:
            raise ConfigError(f"{field} aliases for {model} must be a non-empty list")
        aliases: list[str] = []
        seen_aliases: set[str] = set()
        for alias_index, raw_alias in enumerate(raw_aliases):
            alias = _require_label(
                raw_alias,
                f"{field}.wake_words[{index}].aliases[{alias_index}]",
            )
            normalized = normalize_for_match(alias)
            if len(normalized) < MIN_NORMALIZED_ALIAS_LENGTH:
                raise ConfigError(
                    f"{field} alias for {model} is shorter than "
                    f"{MIN_NORMALIZED_ALIAS_LENGTH} normalized characters"
                )
            if normalized in seen_aliases:
                raise ConfigError(f"duplicate {field} alias for model: {model}")
            seen_aliases.add(normalized)
            aliases.append(alias)
        wake_words.append(MicroWakeWordMapping(model=model, id=word_id, aliases=tuple(aliases)))

    cooldown_seconds = _bounded_int(
        raw.get("cooldown_seconds", 3),
        f"{field}.cooldown_seconds",
        0,
        300,
    )
    max_requests_per_minute = _bounded_int(
        raw.get("max_requests_per_minute", 6),
        f"{field}.max_requests_per_minute",
        1,
        60,
    )
    max_audio_seconds_per_hour = _bounded_int(
        raw.get("max_audio_seconds_per_hour", 300),
        f"{field}.max_audio_seconds_per_hour",
        16,
        3600,
    )
    max_audio_seconds_per_day = _bounded_int(
        raw.get("max_audio_seconds_per_day", 1800),
        f"{field}.max_audio_seconds_per_day",
        16,
        86400,
    )
    if max_audio_seconds_per_day < max_audio_seconds_per_hour:
        raise ConfigError(f"{field}.max_audio_seconds_per_day must be at least the hourly limit")

    return MicroWakeWordActivationConfig(
        enabled=enabled,
        source_id=source_id,
        wyoming_host=wyoming_host,
        wyoming_port=wyoming_port,
        wake_words=tuple(wake_words),
        pipeline_id=pipeline_id,
        cooldown_seconds=cooldown_seconds,
        max_requests_per_minute=max_requests_per_minute,
        max_audio_seconds_per_hour=max_audio_seconds_per_hour,
        max_audio_seconds_per_day=max_audio_seconds_per_day,
    )


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

    cooldown = _bounded_int(
        raw_canary.get("cooldown_seconds", 3),
        "passive_canary.cooldown_seconds",
        0,
        300,
    )

    ha_stt_canary = _parse_ha_stt_config(root, "ha_stt_canary")
    ha_stt_activation = _parse_ha_stt_config(root, "ha_stt_activation")
    microwakeword_activation = _parse_microwakeword_activation(root)

    if enabled:
        if len(sources) != 1:
            raise ConfigError("Phase 1 passive canary requires exactly one source")
        if not source_id:
            raise ConfigError("passive_canary.source_id is required when enabled")
        if source_id not in seen_ids:
            raise ConfigError("passive_canary.source_id must reference a configured source")
    if ha_stt_canary.enabled:
        if len(sources) != 1:
            raise ConfigError("Phase 2 HA STT canary requires exactly one source")
        if not ha_stt_canary.source_id:
            raise ConfigError("ha_stt_canary.source_id is required when enabled")
        if ha_stt_canary.source_id not in seen_ids:
            raise ConfigError("ha_stt_canary.source_id must reference a configured source")
    if ha_stt_activation.enabled:
        if len(sources) != 1:
            raise ConfigError("HA STT activation requires exactly one source")
        if not ha_stt_activation.source_id:
            raise ConfigError("ha_stt_activation.source_id is required when enabled")
        if ha_stt_activation.source_id not in seen_ids:
            raise ConfigError("ha_stt_activation.source_id must reference a configured source")
    if microwakeword_activation.enabled:
        if len(sources) != 1:
            raise ConfigError("microWakeWord activation requires exactly one source")
        if not microwakeword_activation.source_id:
            raise ConfigError("microwakeword_activation.source_id is required when enabled")
        if microwakeword_activation.source_id not in seen_ids:
            raise ConfigError(
                "microwakeword_activation.source_id must reference a configured source"
            )
    active_modes = sum(
        (
            enabled,
            ha_stt_canary.enabled,
            ha_stt_activation.enabled,
            microwakeword_activation.enabled,
        ),
    )
    if active_modes > 1:
        raise ConfigError(
            "passive_canary, ha_stt_canary, ha_stt_activation, and "
            "microwakeword_activation are mutually exclusive"
        )

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
        ha_stt_canary=ha_stt_canary,
        ha_stt_activation=ha_stt_activation,
        microwakeword_activation=microwakeword_activation,
    )


def load_options(path: str | Path = "/data/options.json") -> GatewayConfig:
    options_path = Path(path)
    try:
        options = json.loads(options_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("unable to read valid Supervisor options") from exc
    return parse_options(options)
