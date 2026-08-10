# Conductor specification: passive wake-word canary

## Objective

Build the first safe increment of RTSP Assist Gateway as a public Home Assistant OS add-on. It reads one configured RTSP audio source, converts it to 16 kHz mono signed 16-bit PCM, sends it to a Wyoming microWakeWord provider with the stock `hey_jarvis` model, and publishes detections only to a dedicated diagnostic MQTT topic.

## Scope and constraints

- No Web UI, Ingress, custom integration, host port, `/config` mount, or Home Assistant Core API.
- No production command, chat, Home Assistant event, or Embodied HA target.
- No raw-audio recording or transcript processing.
- Configuration comes only from Supervisor add-on options.
- Each RTSP source has a bounded, independently restartable worker.
- Secrets, complete RTSP URLs, ffmpeg stderr, audio bytes, and Supervisor credentials never appear in logs or MQTT payloads.
- The passive-canary topic is a hard-coded diagnostic namespace, not user-configurable.
- A detection closes the current provider/source session, publishes one event, observes a bounded cooldown, and reconnects. Persistent-session optimization is deferred.
- Do not install or restart this add-on, Embodied HA, go2rtc, or Home Assistant as part of this increment.

## Acceptance criteria

1. `python -m pytest -q` exits zero and covers validation, Wyoming protocol exchange, MQTT payload privacy, one-detection/one-publish behavior, reconnect/backoff, and clean process shutdown.
2. `ruff check .` and `python -m compileall rtsp_assist_gateway tests scripts` exit zero.
3. `python scripts/verify_packaging.py` exits zero and confirms the required add-on files, executable entrypoint, internally consistent manifests, and absence of Ingress, host ports, `/config` maps, audio devices, and Home Assistant Core API permission. This self-check is not treated as Supervisor validation.
4. Invalid configuration fails before an RTSP or MQTT connection: duplicate source IDs, non-RTSP URLs, unknown canary source, malformed Wyoming URI, empty model list, wildcard MQTT topic, and out-of-range cooldown are rejected.
5. A fake Wyoming server receives `Detect`, `AudioStart`, and PCM chunks, returns `Detection(name="hey_jarvis")`, and causes one publisher call with QoS 1 and retain false. The contract explicitly permits broker-level duplicate delivery; a retry of one logical event keeps the same request ID.
6. The published payload contains the version, event, request ID, UTC timestamp, source ID, room, backend, model, and `canary: true`; it contains no URL, credentials, token, audio, transcript, command, or EHA target.
7. Provider EOF or source failure triggers bounded backoff and recovery without a busy loop, zombie child process, or impact outside that source worker.
8. A credential-bearing test URL and simulated ffmpeg stderr never appear in captured logs, exceptions exposed by the add-on, or MQTT output.
9. A repository scan finds no household-specific entity IDs, LAN addresses, names, credentials, generated audio, or private training data.
10. Before public push, Supervisor accepts and builds the exact local commit, and an authorized disposable installation processes a real RTSP source through the stock microWakeWord add-on and emits the diagnostic MQTT event.

## Increments

1. Add-on manifests, public documentation, configuration model, and validator.
2. RTSP/ffmpeg, Wyoming, Supervisor MQTT, and diagnostic-payload adapters with unit tests.
3. One-source worker, retry/cooldown state machine, integration tests, and packaging audit.
4. Evidence review and local commit, then stop for installation approval.
5. Supervisor validation/build and a real RTSP canary on the exact commit.
6. Public push only after the real canary passes.

## Rollback

This increment does not connect to production routes. Before public push, remove the uncommitted new repository files. After push, revert the initial commit or stop/uninstall the add-on. No Embodied HA or go2rtc setting is changed.

## Deferred work

- HA STT plus prefix matching.
- Selectable production backends and the common production event contract.
- Multiple sources, acoustic arbitration, pre-roll, MQTT Discovery diagnostics, and EHA automation examples.
- Custom Japanese wake-word models.

## Evidence as of 2026-08-10

- `20 passed` on the local unit/integration suite.
- `ruff check`, `ruff format --check`, `compileall`, `git diff --check`, and the static packaging self-check pass.
- The implemented Wyoming client connected to a real microWakeWord 2.1.0 add-on and detected stock `hey_jarvis` from a saved 16 kHz mono signed-16-bit fixture. The fixture is not part of this repository.
- The live Supervisor MQTT service response was checked without printing values; host, username, and password are strings and port is numeric as expected.
- Home Assistant Supervisor accepted and built exact commit `dd50525` on amd64. The resulting Python 3.11 container started with manual boot.
- At 2026-08-11 00:09:01 JST, a real `camera_study` RTSP source produced stock `hey_jarvis` Detection and one MQTT delivery on the fixed topic with QoS 1 and retain false. The payload contained no URL, credentials, audio, transcript, command, or EHA target.
- After detection, ffmpeg stopped, the three-second cooldown elapsed, and the source reconnected while the add-on remained healthy.
- GitHub Actions passed on `dd50525` after public push.
- Not yet verified: aarch64 container build, long-duration stability, or live failure-injection recovery. These remain later canary gates rather than Phase 1 publication gates.
