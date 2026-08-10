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

---

# Conductor specification: Phase 2 HA STT canary

## Objective

Add a mutually exclusive one-source HA STT activation canary. It detects bounded speech locally, sends only candidate speech to a configured Home Assistant Assist STT pipeline, recognizes one of several configured wake-word aliases at the beginning of the transcript, and publishes matched commands only to a fixed diagnostic MQTT topic.

## Acceptance criteria

1. A live microWakeWord provider restart causes the installed Phase 1 gateway to log a bounded failure and reconnect without restarting the gateway; EHA, go2rtc, and Home Assistant remain untouched.
2. `python -m pytest -q`, `ruff check .`, `ruff format --check .`, `compileall`, `check_versions.py`, and `verify_packaging.py` all exit zero.
3. Configuration accepts an array of canonical wake-word objects, each with a non-empty alias array; duplicate IDs, empty aliases, and aliases that collide after normalization fail before network access.
4. Matching applies Unicode NFKC, case folding, whitespace folding, and leading/following separator normalization; the longest normalized prefix wins and returns the canonical ID plus the remaining command.
5. A fake Assist WebSocket integration proves that only bounded 16 kHz mono signed-16-bit candidate PCM is submitted, a matched transcript causes one fixed-topic diagnostic publish, and unmatched text causes no publish.
6. Captured logs and MQTT payloads contain no RTSP URL, credential, Supervisor token, unmatched transcript, or raw PCM. No audio or transcript file is written.
7. `passive_canary.enabled` and `ha_stt_canary.enabled` cannot both be true. Neither can target a configurable MQTT topic, HA service, EHA chat, or command route.
8. A real HA STT canary recognizes at least one configured Japanese alias from a real RTSP source and publishes the canonical wake-word ID plus matched command to the fixed diagnostic topic. This is unverified until an authorized add-on build/start and user utterance.
9. Before release, the exact candidate commit passes GitHub CI, Supervisor build, and the existing version/tag release contract. This is unverified until implementation is complete.
10. Candidate STT submissions are blocked by configurable per-minute request and hourly/daily audio budgets. Budget state contains counters only, survives ordinary add-on restarts, and never contains audio or transcripts.
11. Aliases shorter than the conservative normalized minimum are rejected, an alias without a remaining command never publishes, and a representative negative corpus plus a live room-audio soak must meet the declared zero-false-activation canary gate before release.
12. Phase 2 explicitly adds `homeassistant_api: true`, updates the permission-aware packaging contract, and bumps the add-on version before an installed canary. The exact built add-on must prove its Supervisor token can open the Core WebSocket API.

## Non-goals

- MeCab, morphological reading conversion, fuzzy edit-distance matching, or AI Tasks.
- Production EHA chat dispatch, Home Assistant service calls, multiple-source arbitration, custom Japanese microWakeWord training, or Web UI.
- Persisting raw audio, matched or unmatched transcripts, or household recordings.

## Constraints

- `config.yaml` remains the canonical release version source.
- Do not edit `secrets.yaml`, `.ssh/`, or `.storage/`.
- Do not restart EHA, go2rtc, or Home Assistant.
- Keep the existing Phase 1 microWakeWord options backward compatible.
- Unmatched STT text may exist only in memory long enough to decide no-match; it is not logged, published, or persisted.

## Increments

1. Close the live Phase 1 provider-restart gate and record evidence.
2. Verify the installed Home Assistant Assist pipeline/WebSocket STT contract with a disposable client.
3. Run architecture red-team and incorporate required privacy, permission, and false-activation gates.
4. Implement alias configuration, deterministic normalization, collision rejection, and tests.
5. Implement bounded VAD capture, aggregate submission budgets, and HA STT adapter against fakes.
6. Add the mutually exclusive HA STT canary worker, privacy tests, packaging checks, and documentation.
7. Commit and stop before the permission-changing installed build. After explicit authorization, run CI, Supervisor build, positive and negative one-source canaries; release only if every gate passes.

## Evidence as of 2026-08-11

- Restarting the live microWakeWord provider caused the installed Phase 1 gateway to back off for 1, 2, and 4 seconds and recover without restarting the gateway, EHA, go2rtc, or Home Assistant.
- The preferred Assist pipeline is Home Assistant Cloud with Japanese STT. A disposable client explicitly ran `start_stage: stt` and `end_stage: stt`, submitted saved private 16 kHz mono signed-16-bit PCM, received the expected Japanese transcription, and observed no intent or TTS stage. The audio and transcript were not added to the repository.
- Independent red-team judgment was REVISE. Aggregate STT budgets, explicit Home Assistant API permission review, and negative-audio false-activation evidence were added as release gates.
- The candidate implementation passes 58 tests plus Ruff, format, compileall, version, packaging, and diff checks. Tests cover multiple canonical wake words and aliases, normalization collisions, longest-prefix matching, bounded VAD, persisted aggregate budgets, the STT-only WebSocket wire contract, no-match privacy, and fail-closed retries.
- The actual candidate VAD and WebSocket adapter processed a saved private recording in memory: Silero selected a 1.824-second segment, Home Assistant returned non-empty STT, and a wake-word-only transcript was correctly rejected because no command remained. No recording or transcript was added to the repository.

## Rollback

Stop the Gateway, select the existing microWakeWord canary or reinstall tagged `v0.1.0`, and leave the production EHA listening path unchanged. No Home Assistant or go2rtc rollback is required.
