from __future__ import annotations

import pytest
from gateway.config import (
    ACTIVATION_TOPIC,
    CANARY_TOPIC,
    HA_STT_CANARY_TOPIC,
    TRANSCRIPT_TOPIC,
    ConfigError,
    parse_options,
)


def valid_options() -> dict:
    return {
        "log_level": "info",
        "sources": [
            {
                "id": "study",
                "url": "rtsp://user:password@example.invalid:8554/study_audio",
                "room": "study",
            }
        ],
        "passive_canary": {
            "enabled": True,
            "source_id": "study",
            "wyoming_uri": "tcp://microwakeword:10400",
            "models": ["hey_jarvis"],
            "cooldown_seconds": 3,
        },
    }


def test_valid_options_and_fixed_topic() -> None:
    config = parse_options(valid_options())
    assert config.passive_canary.enabled is True
    assert config.passive_canary.models == ("hey_jarvis",)
    assert config.passive_canary.wyoming_host == "microwakeword"
    assert CANARY_TOPIC == "rtsp_assist_gateway/canary/detection"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda o: o["sources"].append(dict(o["sources"][0])), "duplicate source ID"),
        (
            lambda o: o["sources"].append(
                {"id": "other", "url": "rtsp://example.invalid/other", "room": "other"}
            ),
            "exactly one source",
        ),
        (lambda o: o["sources"][0].update(url="https://example.invalid/audio"), "absolute RTSP"),
        (lambda o: o["passive_canary"].update(source_id="missing"), "configured source"),
        (lambda o: o["passive_canary"].update(wyoming_uri="http://host:10400"), "tcp://host:port"),
        (lambda o: o["passive_canary"].update(models=[]), "non-empty list"),
        (lambda o: o["passive_canary"].update(models=["bad/#"]), "model at index"),
        (
            lambda o: o["passive_canary"].update(mqtt_topic="embodied_ha/chat/set"),
            "fixed and must not be configured",
        ),
        (lambda o: o["passive_canary"].update(cooldown_seconds=301), "integer from 0 to 300"),
        (lambda o: o["sources"][0].update(room="study\nforged"), "invalid characters"),
    ],
)
def test_invalid_options_fail_closed(mutate, match: str) -> None:
    options = valid_options()
    mutate(options)
    with pytest.raises(ConfigError, match=match):
        parse_options(options)


def test_disabled_canary_allows_no_sources() -> None:
    config = parse_options(
        {
            "sources": [],
            "passive_canary": {
                "enabled": False,
                "source_id": "",
                "wyoming_uri": "tcp://microwakeword:10400",
                "models": ["hey_jarvis"],
                "cooldown_seconds": 3,
            },
        }
    )
    assert config.sources == ()


def stt_options() -> dict:
    options = valid_options()
    options["passive_canary"]["enabled"] = False
    options["ha_stt_canary"] = {
        "enabled": True,
        "source_id": "study",
        "pipeline_id": "",
        "wake_words": [
            {
                "id": "computer",
                "aliases": ["ねえコンピューター", "ねえコンピュータ"],
            },
            {"id": "jarvis", "aliases": ["ヘイジャービス", "ヘイジャーヴィス"]},
        ],
        "cooldown_seconds": 3,
        "max_requests_per_minute": 6,
        "max_audio_seconds_per_hour": 300,
        "max_audio_seconds_per_day": 1800,
    }
    return options


def test_valid_ha_stt_options_accept_multiple_aliases() -> None:
    config = parse_options(stt_options())
    assert config.ha_stt_canary.enabled is True
    assert config.ha_stt_canary.pipeline_id == ""
    assert config.ha_stt_canary.wake_words[0].aliases == (
        "ねえコンピューター",
        "ねえコンピュータ",
    )
    assert HA_STT_CANARY_TOPIC == "rtsp_assist_gateway/canary/ha_stt"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda o: o["passive_canary"].update(enabled=True), "mutually exclusive"),
        (lambda o: o["ha_stt_canary"].update(source_id="missing"), "configured source"),
        (lambda o: o["ha_stt_canary"].update(wake_words=[]), "non-empty list"),
        (
            lambda o: o["ha_stt_canary"]["wake_words"].append(
                {"id": "computer", "aliases": ["コンピューター"]}
            ),
            "duplicate .* ID",
        ),
        (
            lambda o: o["ha_stt_canary"]["wake_words"].append(
                {"id": "other", "aliases": ["ねえ、コンピューター"]}
            ),
            "collision after normalization",
        ),
        (
            lambda o: o["ha_stt_canary"]["wake_words"].append({"id": "short", "aliases": ["ねえ"]}),
            "shorter than 3",
        ),
        (
            lambda o: o["ha_stt_canary"].update(mqtt_topic="embodied_ha/chat/set"),
            "fixed and must not be configured",
        ),
        (
            lambda o: o["ha_stt_canary"].update(max_audio_seconds_per_day=100),
            "at least the hourly limit",
        ),
    ],
)
def test_invalid_ha_stt_options_fail_closed(mutate, match: str) -> None:
    options = stt_options()
    mutate(options)
    with pytest.raises(ConfigError, match=match):
        parse_options(options)


def activation_options() -> dict:
    options = stt_options()
    options["ha_stt_canary"]["enabled"] = False
    options["ha_stt_activation"] = dict(options["ha_stt_canary"])
    options["ha_stt_activation"]["enabled"] = True
    return options


def test_valid_activation_uses_fixed_topic() -> None:
    config = parse_options(activation_options())
    assert config.ha_stt_activation.enabled is True
    assert config.ha_stt_canary.enabled is False
    assert ACTIVATION_TOPIC == "rtsp_assist_gateway/activation"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda o: o["ha_stt_canary"].update(enabled=True), "mutually exclusive"),
        (lambda o: o["passive_canary"].update(enabled=True), "mutually exclusive"),
        (lambda o: o["ha_stt_activation"].update(source_id="missing"), "configured source"),
        (
            lambda o: o["ha_stt_activation"].update(mqtt_topic="eha_sora/chat/set"),
            "fixed and must not be configured",
        ),
    ],
)
def test_invalid_activation_options_fail_closed(mutate, match: str) -> None:
    options = activation_options()
    mutate(options)
    with pytest.raises(ConfigError, match=match):
        parse_options(options)


def microwakeword_activation_options() -> dict:
    options = valid_options()
    options["passive_canary"]["enabled"] = False
    options["microwakeword_activation"] = {
        "enabled": True,
        "source_id": "study",
        "wyoming_uri": "tcp://microwakeword:10400",
        "wake_words": [
            {
                "model": "computer_v1",
                "id": "computer",
                "aliases": ["ねえコンピューター", "ねえコンピュータ"],
            },
            {
                "model": "computer_v2",
                "id": "computer",
                "aliases": ["ヘイコンピューター"],
            },
        ],
        "pipeline_id": "preferred-id",
        "cooldown_seconds": 3,
        "max_requests_per_minute": 6,
        "max_audio_seconds_per_hour": 300,
        "max_audio_seconds_per_day": 1800,
    }
    return options


def test_valid_microwakeword_activation_maps_multiple_models_to_one_id() -> None:
    config = parse_options(microwakeword_activation_options())
    activation = config.microwakeword_activation
    assert activation.enabled is True
    assert activation.models == ("computer_v1", "computer_v2")
    assert tuple(word.id for word in activation.wake_words) == ("computer", "computer")
    assert activation.pipeline_id == "preferred-id"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda o: o["passive_canary"].update(enabled=True), "mutually exclusive"),
        (
            lambda o: o["microwakeword_activation"].update(source_id="missing"),
            "configured source",
        ),
        (
            lambda o: o["microwakeword_activation"].update(
                wyoming_uri="http://microwakeword:10400"
            ),
            "tcp://host:port",
        ),
        (
            lambda o: o["microwakeword_activation"]["wake_words"].append(
                {
                    "model": "computer_v1",
                    "id": "other",
                    "aliases": ["別のコンピューター"],
                }
            ),
            "duplicate .* model",
        ),
        (
            lambda o: o["microwakeword_activation"].update(mqtt_topic="assistant/chat/set"),
            "fixed and must not be configured",
        ),
        (
            lambda o: o["microwakeword_activation"]["wake_words"][0].update(aliases=[]),
            "non-empty list",
        ),
        (
            lambda o: o["microwakeword_activation"].update(max_audio_seconds_per_day=100),
            "at least the hourly limit",
        ),
    ],
)
def test_invalid_microwakeword_activation_fails_closed(mutate, match: str) -> None:
    options = microwakeword_activation_options()
    mutate(options)
    with pytest.raises(ConfigError, match=match):
        parse_options(options)


def transcript_options(*, with_mode: str | None = None) -> dict:
    if with_mode == "ha_stt":
        options = activation_options()
        pipeline_id = options["ha_stt_activation"]["pipeline_id"]
    elif with_mode == "microwakeword":
        options = microwakeword_activation_options()
        pipeline_id = options["microwakeword_activation"]["pipeline_id"]
    else:
        options = valid_options()
        options["passive_canary"]["enabled"] = False
        pipeline_id = "preferred-id"
    options["transcript_events"] = {
        "enabled": True,
        "source_id": "study",
        "pipeline_id": pipeline_id,
        "max_requests_per_minute": 6,
        "max_audio_seconds_per_hour": 300,
        "max_audio_seconds_per_day": 1800,
    }
    return options


def test_transcript_events_standalone_and_production_overlay_are_valid() -> None:
    standalone = parse_options(transcript_options())
    ha_stt = parse_options(transcript_options(with_mode="ha_stt"))
    microwake = parse_options(transcript_options(with_mode="microwakeword"))
    assert standalone.transcript_events.enabled is True
    assert ha_stt.transcript_events.enabled is True
    assert microwake.transcript_events.enabled is True
    assert TRANSCRIPT_TOPIC == "rtsp_assist_gateway/transcript"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda o: o["transcript_events"].update(source_id="missing"), "configured source"),
        (lambda o: o["passive_canary"].update(enabled=True), "cannot run with a canary"),
        (
            lambda o: o["transcript_events"].update(mqtt_topic="household/private"),
            "fixed and must not be configured",
        ),
        (
            lambda o: o["transcript_events"].update(max_event_bytes=65_536),
            "fixed and must not be configured",
        ),
        (
            lambda o: o["transcript_events"].update(max_audio_seconds_per_day=100),
            "at least the hourly limit",
        ),
    ],
)
def test_invalid_transcript_options_fail_closed(mutate, match: str) -> None:
    options = transcript_options()
    mutate(options)
    with pytest.raises(ConfigError, match=match):
        parse_options(options)


@pytest.mark.parametrize(
    "field",
    [
        "pipeline_id",
        "max_requests_per_minute",
        "max_audio_seconds_per_hour",
        "max_audio_seconds_per_day",
    ],
)
def test_combined_transcript_contract_must_exactly_match_activation(field: str) -> None:
    options = transcript_options(with_mode="microwakeword")
    if field == "pipeline_id":
        options["transcript_events"][field] = "different"
    else:
        options["transcript_events"][field] += 1
    with pytest.raises(ConfigError, match="must use identical"):
        parse_options(options)
