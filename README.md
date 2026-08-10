# RTSP Assist Gateway

Experimental Home Assistant OS add-on for turning RTSP audio sources into generic activation events.

The first increment is deliberately a passive canary. It sends one configured RTSP audio source to an existing Wyoming microWakeWord provider and publishes stock-model detections to a fixed diagnostic MQTT topic. It cannot invoke Home Assistant services, Embodied HA, chat, or command topics.

## Current status

This repository is under development and has no release tag yet. The Phase 1 passive canary was accepted and built by Home Assistant Supervisor on amd64, then detected stock `hey_jarvis` over a real RTSP source and delivered the fixed diagnostic MQTT event. It is not a production voice-command route.

## Phase 1 configuration

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
```

Detections are published to `rtsp_assist_gateway/canary/detection` with QoS 1 and retain disabled. QoS 1 is at-least-once: subscribers must tolerate duplicates and may deduplicate using `request_id`.

RTSP credentials may be included in the URL when required, but the add-on deliberately never prints the URL or forwards ffmpeg stderr. Raw audio is held only while streaming and is never written to disk.

## Deliberate non-features

- No Web UI or Ingress
- No raw-audio recording
- No Home Assistant service calls
- No production command or chat output
- No Embodied HA-specific routing
- No HA STT backend yet
- No multi-source arbitration yet

See [CONDUCTOR.md](CONDUCTOR.md) for the executable acceptance criteria and release gate.
