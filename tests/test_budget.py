from __future__ import annotations

import json

import pytest
from gateway.budget import BudgetError, SubmissionBudget
from gateway.config import (
    HaSttCanaryConfig,
    MicroWakeWordActivationConfig,
    MicroWakeWordMapping,
    WakeWordConfig,
)


def config(
    *,
    requests: int = 2,
    hour_seconds: int = 10,
    day_seconds: int = 20,
) -> HaSttCanaryConfig:
    return HaSttCanaryConfig(
        enabled=True,
        source_id="study",
        pipeline_id="",
        wake_words=(WakeWordConfig(id="computer", aliases=("ねえコンピューター",)),),
        cooldown_seconds=0,
        max_requests_per_minute=requests,
        max_audio_seconds_per_hour=hour_seconds,
        max_audio_seconds_per_day=day_seconds,
    )


def test_request_budget_persists_across_instances(tmp_path) -> None:
    now = [1_000.0]
    path = tmp_path / "budget.json"
    assert SubmissionBudget(config(), path, lambda: now[0]).consume(1).allowed
    assert SubmissionBudget(config(), path, lambda: now[0]).consume(1).allowed
    blocked = SubmissionBudget(config(), path, lambda: now[0]).consume(1)
    assert blocked.allowed is False
    assert blocked.reason == "request_minute_limit"
    assert blocked.retry_after == 60
    now[0] += 61
    assert SubmissionBudget(config(), path, lambda: now[0]).consume(1).allowed
    assert path.stat().st_mode & 0o777 == 0o600


def test_budget_is_shared_when_switching_to_microwakeword_mode(tmp_path) -> None:
    now = [1_000.0]
    path = tmp_path / "shared-budget.json"
    ha_config = config(requests=1)
    micro_config = MicroWakeWordActivationConfig(
        enabled=True,
        source_id="study",
        wyoming_host="microwakeword",
        wyoming_port=10400,
        wake_words=(
            MicroWakeWordMapping(
                model="computer_v1",
                id="computer",
                aliases=("ねえコンピューター",),
            ),
        ),
        pipeline_id="",
        cooldown_seconds=0,
        max_requests_per_minute=1,
        max_audio_seconds_per_hour=10,
        max_audio_seconds_per_day=20,
    )
    assert SubmissionBudget(ha_config, path, lambda: now[0]).consume(1).allowed
    switched = SubmissionBudget(micro_config, path, lambda: now[0]).consume(1)
    assert switched.allowed is False
    assert switched.reason == "request_minute_limit"


def test_hourly_and_daily_audio_budgets(tmp_path) -> None:
    now = [5_000.0]
    hourly = SubmissionBudget(
        config(requests=60, day_seconds=100),
        tmp_path / "hourly.json",
        lambda: now[0],
    )
    assert hourly.consume(8).allowed
    assert hourly.consume(3).reason == "audio_hour_limit"

    daily = SubmissionBudget(config(requests=60), tmp_path / "daily.json", lambda: now[0])
    assert daily.consume(8).allowed
    now[0] += 3601
    assert daily.consume(8).allowed
    now[0] += 3601
    blocked = daily.consume(5)
    assert blocked.allowed is False
    assert blocked.reason == "audio_day_limit"
    now[0] += 86401
    assert daily.consume(5).allowed


def test_budget_state_contains_counters_only(tmp_path) -> None:
    path = tmp_path / "budget.json"
    SubmissionBudget(config(), path, lambda: 100.0).consume(2.5)
    state = json.loads(path.read_text(encoding="utf-8"))
    assert set(state) == {"version", "requests", "audio_seconds"}
    assert state["requests"] == [100.0]
    assert state["audio_seconds"] == [[100.0, 2.5]]
    encoded = json.dumps(state)
    for forbidden in ("transcript", "command", "pcm", "rtsp", "token"):
        assert forbidden not in encoded.lower()


def test_corrupt_state_and_invalid_duration_fail_closed(tmp_path) -> None:
    path = tmp_path / "budget.json"
    path.write_text("not-json", encoding="utf-8")
    budget = SubmissionBudget(config(), path, lambda: 100.0)
    with pytest.raises(BudgetError, match="state is invalid"):
        budget.consume(1)
    with pytest.raises(BudgetError, match="duration"):
        SubmissionBudget(config(), tmp_path / "new.json").consume(float("nan"))
