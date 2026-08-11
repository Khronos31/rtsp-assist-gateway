# Changelog

Notable changes to RTSP Assist Gateway are recorded here.

## [Unreleased]

## [0.4.0] - 2026-08-11

### Added

- Added a disabled-by-default `microwakeword_activation` mode. A configured Wyoming model is the wake
  authority, and only the same bounded utterance associated with a detection is submitted to Home Assistant
  STT for command transcription.
- Added model-to-canonical-ID mapping, optional best-effort alias stripping, the existing fixed version 1
  activation topic, and the same persistent aggregate STT privacy budget used by HA STT modes.

### Safety

- All four modes are mutually exclusive. No later unrelated utterance is accepted after a wake detection.
- Raw PCM and transcripts remain in bounded memory only and are never written or included in MQTT payloads.
- The public add-on remains independent from Embodied HA and contains no household routing or resident data.

## [0.3.0] - 2026-08-11

- Added the disabled-by-default generic HA STT production activation event.

## [0.2.0] - 2026-08-11

- Added the bounded HA STT wake-prefix canary with persistent privacy budgets.

## [0.1.0] - 2026-08-10

- Added the passive RTSP-to-Wyoming microWakeWord diagnostic canary.
