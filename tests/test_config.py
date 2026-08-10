from __future__ import annotations

import pytest
from gateway.config import CANARY_TOPIC, ConfigError, parse_options


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
