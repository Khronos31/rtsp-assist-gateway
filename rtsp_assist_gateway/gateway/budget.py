"""Persistent aggregate privacy and provider-usage budget for HA STT."""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import HaSttCanaryConfig

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0
DEFAULT_STATE_PATH = Path("/data/ha_stt_budget.json")


class BudgetError(RuntimeError):
    """Raised when the privacy budget state cannot be trusted."""


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str = ""
    retry_after: float = 0.0


class SubmissionBudget:
    def __init__(
        self,
        config: HaSttCanaryConfig,
        path: Path = DEFAULT_STATE_PATH,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self.path = path
        self.clock = clock

    def consume(self, audio_seconds: float) -> BudgetDecision:
        if not math_is_finite_positive(audio_seconds):
            raise BudgetError("audio duration is invalid")
        try:
            now = _finite_number(self.clock())
        except ValueError as exc:
            raise BudgetError("system clock is invalid") from exc
        requests, audio = self._load()
        requests = sorted(stamp for stamp in requests if now - stamp < MINUTE)
        audio = sorted(
            ((stamp, duration) for stamp, duration in audio if now - stamp < DAY),
            key=lambda entry: entry[0],
        )

        failures: list[tuple[str, float]] = []
        if len(requests) >= self.config.max_requests_per_minute:
            failures.append(("request_minute_limit", requests[0] + MINUTE - now))
        hour_entries = [(stamp, duration) for stamp, duration in audio if now - stamp < HOUR]
        if sum(duration for _, duration in hour_entries) + audio_seconds > (
            self.config.max_audio_seconds_per_hour
        ):
            failures.append(
                (
                    "audio_hour_limit",
                    _retry_for_audio(
                        hour_entries,
                        audio_seconds,
                        self.config.max_audio_seconds_per_hour,
                        HOUR,
                        now,
                    ),
                )
            )
        if sum(duration for _, duration in audio) + audio_seconds > (
            self.config.max_audio_seconds_per_day
        ):
            failures.append(
                (
                    "audio_day_limit",
                    _retry_for_audio(
                        audio,
                        audio_seconds,
                        self.config.max_audio_seconds_per_day,
                        DAY,
                        now,
                    ),
                )
            )

        if failures:
            self._save(requests, audio)
            reason = "+".join(item[0] for item in failures)
            retry_after = max(1.0, max(item[1] for item in failures))
            return BudgetDecision(False, reason, retry_after)

        requests.append(now)
        audio.append((now, audio_seconds))
        self._save(requests, audio)
        return BudgetDecision(True)

    def _load(self) -> tuple[list[float], list[tuple[float, float]]]:
        if not self.path.exists():
            return [], []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError
            requests = raw.get("requests")
            audio = raw.get("audio_seconds")
            if not isinstance(requests, list) or not isinstance(audio, list):
                raise ValueError
            parsed_requests = [_finite_number(value) for value in requests]
            parsed_audio: list[tuple[float, float]] = []
            for entry in audio:
                if not isinstance(entry, list) or len(entry) != 2:
                    raise ValueError
                parsed_audio.append((_finite_number(entry[0]), _finite_number(entry[1])))
            return parsed_requests, parsed_audio
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise BudgetError("HA STT budget state is invalid") from exc

    def _save(self, requests: list[float], audio: list[tuple[float, float]]) -> None:
        payload = json.dumps(
            {"version": 1, "requests": requests, "audio_seconds": audio},
            separators=(",", ":"),
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                with suppress(OSError):
                    os.close(descriptor)
                raise
            os.replace(temporary, self.path)
        except OSError as exc:
            raise BudgetError("Unable to persist HA STT budget state") from exc


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    number = float(value)
    if not math_is_finite_positive(number, allow_zero=True):
        raise ValueError
    return number


def math_is_finite_positive(value: float, *, allow_zero: bool = False) -> bool:
    import math

    return math.isfinite(value) and (value >= 0 if allow_zero else value > 0)


def _retry_for_audio(
    entries: list[tuple[float, float]],
    new_duration: float,
    limit: float,
    window: float,
    now: float,
) -> float:
    total = sum(duration for _, duration in entries) + new_duration
    for stamp, duration in entries:
        total -= duration
        if total <= limit:
            return max(1.0, stamp + window - now)
    return window
