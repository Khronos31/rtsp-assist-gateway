# RTSP Assist Gateway

Experimental Home Assistant OS add-on for turning RTSP audio sources into generic activation events.

It provides two mutually exclusive one-source canaries. The microWakeWord route streams PCM to a Wyoming provider. The HA STT route segments speech locally, submits bounded candidates to a selected Home Assistant Assist STT pipeline, and performs deterministic wake-word prefix matching. Neither route invokes Home Assistant services, Embodied HA, chat, or production command topics.

## Current status

Tag `v0.1.0` contains the accepted Phase 1 microWakeWord canary. The HA STT route is a Phase 2 candidate and remains unaccepted until an exact Supervisor build, positive utterance, negative room-audio soak, and privacy-budget checks pass. This is not a production voice-command route.

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
```

Detections are published to `rtsp_assist_gateway/canary/detection` with QoS 1 and retain disabled. QoS 1 is at-least-once: subscribers must tolerate duplicates and may deduplicate using `request_id`.

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
```

`pipeline_id` may be empty to use Home Assistant's preferred Assist pipeline. `wake_words` is an array, and every canonical wake-word has an `id` plus one or more explicit `aliases`; this is how STT spelling variants are absorbed. Matching applies Unicode NFKC, case folding, and whitespace/punctuation removal, then chooses the longest prefix. Kana conversion, MeCab, fuzzy matching, and AI Tasks are not performed.

Only a transcript beginning with an alias and containing a non-empty remaining command is published, to `rtsp_assist_gateway/canary/ha_stt`. Unmatched transcripts are neither logged, published, nor persisted. Matched payloads contain the canonical wake-word ID and command for diagnostic inspection.

HA STT mode necessarily sends household speech candidates to the selected Assist STT provider, which may be remote or metered. The request/minute and audio/hour/day limits are enforced before provider contact and survive ordinary add-on restarts. Disable `ha_stt_canary.enabled` or stop the add-on to end submissions.

RTSP credentials may be included in the URL when required, but the add-on deliberately never prints the URL or forwards ffmpeg stderr. Raw audio is held only in memory while streaming and is never written to disk.

## Deliberate non-features

- No Web UI or Ingress
- No raw-audio recording
- No Home Assistant service calls
- No production command or chat output
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
