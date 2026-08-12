# RTSP Assist Gateway

Experimental Home Assistant OS add-on for turning RTSP audio sources into generic activation events.

## Installation

Add [`https://github.com/Khronos31/embodied-ha-addons`](https://github.com/Khronos31/embodied-ha-addons)
to the Home Assistant add-on store, then install **RTSP Assist Gateway** from it. This repository holds
the source and is not itself an add-on repository.

It provides four mutually exclusive one-source activation modes. The microWakeWord routes stream PCM to a Wyoming provider. The HA STT routes segment speech locally, submits bounded candidates to a selected Home Assistant Assist STT pipeline, and performs deterministic wake-word prefix matching. The two canary modes publish only diagnostics; the opt-in HA STT and microWakeWord activation modes publish generic activation events for Home Assistant automations to route. A disabled-by-default transcript output can run alone or reuse a compatible production activation stream. The gateway never invokes Home Assistant services, Embodied HA, or chat directly.

## Current status

Tag `v0.1.0` contains the accepted Phase 1 microWakeWord canary. Version `0.2.0` adds the accepted Phase 2 HA STT canary after passing an exact Supervisor build, a positive Japanese utterance, a five-minute zero-activation room-audio soak, and the privacy-budget checks. Version `0.3.0` adds the disabled-by-default generic HA STT activation event used by Home Assistant automations. Version `0.4.0` adds the disabled-by-default microWakeWord-gated activation mode after its Study-only production canary passed. Version `0.5.0` adds disabled-by-default generic transcript events, released after an exact Supervisor build and a six-hour single-source live run in which 76 utterances were published with no duplicate, malformed, or out-of-order event, no transcript text in logs or status, and the privacy budget blocking submissions on both its per-minute and per-hour limits as designed.

## microWakeWord canary

```yaml
log_level: info
sources:
  - id: study
    url: rtsp://go2rtc-host:8554/study_audio
    room: study
passive_canary:
  enabled: true
  source_id: study
  wyoming_uri: tcp://47701997-microwakeword:10400
  models:
    - hey_jarvis
  cooldown_seconds: 3
ha_stt_canary:
  enabled: false
  source_id: ""
  pipeline_id: ""
  wake_words:
    - id: hey_jarvis
      aliases:
        - hey jarvis
  cooldown_seconds: 3
  max_requests_per_minute: 6
  max_audio_seconds_per_hour: 300
  max_audio_seconds_per_day: 1800
ha_stt_activation:
  enabled: false
  source_id: ""
  pipeline_id: ""
  wake_words:
    - id: hey_jarvis
      aliases:
        - hey jarvis
  cooldown_seconds: 3
  max_requests_per_minute: 6
  max_audio_seconds_per_hour: 300
  max_audio_seconds_per_day: 1800
```

Detections are published to `rtsp_assist_gateway/canary/detection` with QoS 1 and retain disabled. QoS 1 is at-least-once: subscribers must tolerate duplicates and may deduplicate using `request_id`.

## microWakeWord activation

```yaml
microwakeword_activation:
  enabled: true
  source_id: study
  wyoming_uri: tcp://47701997-microwakeword:10400
  wake_words:
    - model: computer_v1
      id: computer
      aliases:
        - ねえコンピューター
        - ねえコンピュータ
  pipeline_id: ""
  cooldown_seconds: 3
  max_requests_per_minute: 6
  max_audio_seconds_per_hour: 300
  max_audio_seconds_per_day: 1800
```

This mode sends the same bounded in-memory PCM stream to the Wyoming wake detector and local VAD. Home Assistant STT is not contacted before a configured model is detected. After detection, only the currently active or just-completed speech segment inside a fixed 1.5-second association window is eligible for STT; the worker does not wait for unrelated later speech. The RTSP source is not reopened between detection and command capture.

`model` is the exact name reported by the Wyoming provider. `id` is the generic canonical ID published for household automation routing; multiple model variants may map to the same ID. `aliases` are used only to remove a recognized wake prefix from the STT result. microWakeWord remains the wake authority, so a non-empty transcript is still accepted when STT does not recognize the alias. This avoids making wake reliability depend on STT spelling, but it also means a microWakeWord false positive can submit and route its associated speech segment. Run a representative passive-canary soak before enabling this mode.

Successful commands use the existing fixed `rtsp_assist_gateway/activation` topic with `backend: microwakeword`, QoS 1, and retain disabled. Raw audio and transcripts are never persisted or logged. The aggregate HA STT request/audio budgets are shared with the HA STT modes across ordinary restarts and mode switches.

## HA STT canary

```yaml
log_level: info
sources:
  - id: study
    url: rtsp://go2rtc-host:8554/study_audio
    room: study
passive_canary:
  enabled: false
  source_id: ""
  wyoming_uri: tcp://47701997-microwakeword:10400
  models:
    - hey_jarvis
  cooldown_seconds: 3
ha_stt_canary:
  enabled: true
  source_id: study
  pipeline_id: ""
  wake_words:
    - id: computer
      aliases:
        - ねえコンピューター
        - ねえコンピュータ
  cooldown_seconds: 3
  max_requests_per_minute: 6
  max_audio_seconds_per_hour: 300
  max_audio_seconds_per_day: 1800
ha_stt_activation:
  enabled: false
  source_id: ""
  pipeline_id: ""
  wake_words:
    - id: hey_jarvis
      aliases:
        - hey jarvis
  cooldown_seconds: 3
  max_requests_per_minute: 6
  max_audio_seconds_per_hour: 300
  max_audio_seconds_per_day: 1800
```

`pipeline_id` may be empty to use Home Assistant's preferred Assist pipeline. `wake_words` is an array, and every canonical wake-word has an `id` plus one or more explicit `aliases`; this is how STT spelling variants are absorbed. Matching applies Unicode NFKC, case folding, and whitespace/punctuation removal, then chooses the longest prefix. Kana conversion, MeCab, fuzzy matching, and AI Tasks are not performed.

Only a transcript beginning with an alias and containing a non-empty remaining command is published, to `rtsp_assist_gateway/canary/ha_stt`. Unmatched transcripts are neither logged, published, nor persisted. Matched payloads contain the canonical wake-word ID and command for diagnostic inspection.

HA STT mode necessarily sends household speech candidates to the selected Assist STT provider, which may be remote or metered. The request/minute and audio/hour/day limits are enforced before provider contact and survive ordinary add-on restarts. Disable `ha_stt_canary.enabled` or stop the add-on to end submissions.

## HA STT activation

Set `ha_stt_canary.enabled: false` and configure `ha_stt_activation` with the same fields to opt into generic activation output. Matching commands are published to the fixed topic `rtsp_assist_gateway/activation` with QoS 1 and retain disabled. The version 1 payload contains `request_id`, `timestamp`, `source_id`, configured `room`, `backend`, canonical `wake_word_id`, and `command`; it does not contain the diagnostic `canary` field. Commands longer than 500 Unicode characters are rejected without logging their text.

The fixed topic is intentionally not an agent command topic. A Home Assistant automation must validate and route events for the intended consumer. MQTT remains an at-least-once transport, so consumers must persistently deduplicate `request_id` before causing side effects.

RTSP credentials may be included in the URL when required, but the add-on deliberately never prints the URL or forwards ffmpeg stderr. Raw audio is held only in memory while streaming and is never written to disk.

## Ambient transcript events (development)

```yaml
transcript_events:
  enabled: false
  source_id: study
  pipeline_id: ""
  max_requests_per_minute: 6
  max_audio_seconds_per_hour: 300
  max_audio_seconds_per_day: 1800
```

When explicitly enabled, recognized speech is published to the fixed
`rtsp_assist_gateway/transcript` topic with QoS 1 and retain disabled. The version-1 JSON event contains
`event_id`, UTC timestamp, source ID, configured room, `backend: ha_stt`, transcript, duration in
milliseconds, and a truncation flag. Consumers must deduplicate by `event_id` because QoS 1 is
at-least-once. Every complete encoded event is limited to 16 KiB; longer Unicode text is safely truncated
and marked without splitting JSON.

Non-retained MQTT is not access control. Enabling this option makes ordinary household transcripts visible
to MQTT broker clients that can subscribe to the fixed topic. The Gateway stores neither transcript text
nor raw audio, but another subscriber may retain what it receives. The selected Home Assistant STT provider
may also be remote or metered, so the persistent request/audio budgets apply before provider contact.

Transcript output may run by itself, or alongside `ha_stt_activation` or `microwakeword_activation` when
the source ID, pipeline ID, and all STT budgets are identical. Combined mode uses one RTSP reader, one VAD,
and one STT request for a shared speech segment. It cannot run with either canary mode. A bounded queue keeps
only one in-flight and one latest pending PCM segment; older pending speech may be dropped when STT is slow.
Transcript delivery also uses bounded retry and is allowed to drop rather than block wake activation.

## Deliberate non-features

- No Web UI or Ingress
- No raw-audio recording
- No transcript retention or speaker inference
- No Home Assistant service calls
- No direct command or chat output
- No Embodied HA-specific routing
- No multi-source arbitration yet
- No automatic kana, morphological, or fuzzy wake-word matching

See [CONDUCTOR.md](CONDUCTOR.md) for the executable acceptance criteria and release gate.

## Version and release tags

`rtsp_assist_gateway/config.yaml` is the canonical version source. The Python `__version__` and a release tag must match it exactly; version `0.2.0` therefore uses tag `v0.2.0`.

CI runs `python scripts/check_versions.py` on every branch and tag. A tag build fails unless `GITHUB_REF_NAME` is exactly `v` plus the manifest version.

For a release, first commit and push the version changes, wait for the exact main commit's `test` workflow to succeed, and then run:

```bash
python scripts/release_tag.py --tag v0.2.0
python scripts/release_tag.py --tag v0.2.0 --push
```

The first command is a dry run. `--push` additionally requires active GitHub rules that prevent updates and deletions under `refs/tags/v*`, verifies successful CI for the exact target SHA, creates an annotated tag, and atomically pushes unchanged `main` plus the new tag. It never edits versions or force-updates a tag.
