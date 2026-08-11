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
- Home Assistant Supervisor accepted and installed exact candidate commit `4c647a3` as a disposable, manually booted `0.2.0` add-on with `homeassistant_api: true`; the stopped official `0.1.0` installation remained unchanged as rollback.
- At 2026-08-11 01:37:49 JST, the live `camera_study` RTSP source recognized the configured Japanese alias `あかねちゃん`, mapped it to canonical ID `akane`, extracted `テストメッセージを送って`, and published one QoS 1, non-retained diagnostic event on the fixed HA STT topic. This proves the installed add-on token could complete the STT-only Core WebSocket exchange.
- After the match, the source stopped for the configured three-second cooldown and reconnected without an add-on restart. A subsequent five-minute live room-audio soak produced zero diagnostic events and no RTSP, Home Assistant authentication, or reconnect errors.
- The live add-on used roughly 40–75 MB of memory. A sampled CPU value reached about 38%, so performance optimization remains follow-up work even though the bounded functional and privacy canary gates passed.
- Supervisor and live-room gates passed; the release helper independently requires successful GitHub CI for the exact target SHA and the protected `v0.2.0` version/tag contract before publication.

## Rollback

Stop the Gateway, select the existing microWakeWord canary or reinstall tagged `v0.1.0`, and leave the production EHA listening path unchanged. No Home Assistant or go2rtc rollback is required.

---

# Conductor specification: minimal production activation routing

## Objective

Create the smallest generic path from a matched HA STT wake command to a versioned MQTT activation event, then let a Home Assistant automation route the canonical wake-word ID to an Embodied HA instance. The Gateway remains independent of Embodied HA and household-specific topic names.

## Acceptance criteria

1. In this repository, `python -m pytest -q`, `ruff check .`, `ruff format --check .`, `python -m compileall rtsp_assist_gateway tests scripts`, `python scripts/check_versions.py`, and `python scripts/verify_packaging.py` all exit zero.
2. Supervisor options expose a disabled-by-default HA STT activation mode. Parsing rejects any configuration with more than one of passive canary, HA STT canary, and HA STT activation enabled, and rejects an enabled activation whose source is missing or unknown before network access.
3. A fake STT integration proves that a matched activation publishes exactly once to the fixed `rtsp_assist_gateway/activation` topic with QoS 1 and retain false. Its version-1 payload contains request ID, timestamp, source ID, room, backend, canonical wake-word ID, and command; it contains no `canary`, URL, credential, token, PCM, or transcript field.
4. Unmatched text still produces no MQTT event and never appears in logs. Publish retry for one logical activation retains the same request ID. Oversized commands are rejected before publish without logging their contents.
5. In `/config/GitHub/embodied-ha`, the focused MQTT-envelope tests and the full test suite exit zero. Legacy plain-text and legacy `{message, source}` chat payloads remain accepted. A valid version-1 Gateway envelope runs chat once in `voice` mode, passes its allowlisted room directly to that chat subprocess, and atomically updates the user's room belief. Malformed, unsupported, oversized, stale, and duplicate request IDs run no chat. Replay protection is bounded, persisted atomically under `EHA_DATA_DIR`, and rejects the same request ID after a simulated daemon restart.
6. The EHA MQTT chat listener handles messages sequentially. A valid fresh Gateway envelope received while chat is busy waits for the bounded chat lock and runs once after release instead of being silently skipped; expiry/failure is logged without the command text.
7. `/config/automations.yaml` contains one disabled-by-default or otherwise non-live-until-deploy automation that accepts only the fixed activation topic, allowlists canonical IDs and source IDs, derives room from a household-owned source map instead of trusting the payload room, republishes a bounded version-1 envelope to the mapped per-instance `chat/set` topic with retain false, and preserves request ID. `ha core check` exits zero after the YAML edit.
8. `git diff --check` exits zero in the Gateway, Embodied HA, and `/config` repositories, and repository scans find no household entity IDs or individual EHA MQTT prefixes in the public Gateway repository.
9. **Unverified until separately authorized deployment:** before enabling the study activation, continuous STT for that same source is disabled in every EHA instance that could independently observe it. An exact versioned Gateway build plus the HA automation then delivers one real study utterance through a common route into one EHA chat receiver; a repeated identical request ID, including after an EHA restart, causes no second chat. Stopping Gateway, disabling the automation, restoring prior EHA source settings, and restoring Gateway options returns to the pre-change path without restarting Home Assistant or go2rtc.

## Non-goals

- Multi-source arbitration, all-room rollout, or replacement/removal of EHA's current continuous STT.
- Wake-only followed by a delayed command; this increment requires wake prefix and command in one utterance.
- Speaker acknowledgements, TTS changes, microWakeWord production routing, custom-model training, Web UI, or Ingress.
- Gateway knowledge of Akane, Sora, Midori, their MQTT prefixes, character data, or EHA-specific chat semantics.

## Constraints

- Do not touch `secrets.yaml`, `.ssh/`, or `.storage/`.
- Never persist raw audio or unmatched transcripts, and never log credentials, RTSP URLs, tokens, PCM, or unmatched text.
- The production topic is fixed, non-retained, and unavailable to either canary mode.
- MQTT broker membership remains the existing trust boundary: broker clients can already publish directly to EHA `chat/set`. This increment does not claim publisher authentication. The automation must still prevent payload-controlled room selection by deriving room from its own source map.
- Edit only the new automation block in the already-dirty `/config/automations.yaml`; preserve all unrelated user changes.
- Run `ha core check` after editing Home Assistant YAML. Do not run `ha core restart`, add-on rebuild/update/restart, version bump, release, or push in this implementation phase.
- Embodied HA frontend files are out of scope.

## Rollback

Before deployment, revert the two feature branches and remove only the new automation block. After a later deployment, stop the Gateway, disable the activation automation, and restore the prior add-on options. The legacy EHA chat payload contract remains present, so rollback does not require data migration. No Home Assistant or go2rtc restart is required for the stop/disable rollback itself.

## Increments

1. Freeze the production topic/payload, EHA envelope, automation mapping boundary, and rollback through independent red-team review.
2. Add Gateway activation configuration and generic publish mode with fake-STT contract/privacy tests.
3. Add bounded EHA versioned-envelope validation, persistent request-ID deduplication, direct per-chat voice-room binding, and sequential busy handling with compatibility tests.
4. Add the household-only HA automation and pass `ha core check` without restarting Home Assistant.
5. Run full repository checks, review diffs, and stop before version bump or deployment.

# Conductor specification: microWakeWord-gated activation

## Objective

Add a production activation mode in which a configured microWakeWord model detects the wake phrase locally, and only the bounded utterance associated with that detection is submitted to the selected Home Assistant STT pipeline for command transcription. Publish the resulting generic command event through the existing fixed MQTT contract without teaching the Gateway about Embodied HA or household residents.

## Acceptance criteria

1. `python -m pytest -q`, `ruff check .`, `ruff format --check .`, `python -m compileall rtsp_assist_gateway tests scripts`, `python scripts/check_versions.py`, and `python scripts/verify_packaging.py` all exit zero in this repository.
2. Supervisor options expose a disabled-by-default `microwakeword_activation` mode. Parsing validates one source, a credential-free Wyoming URI, unique model-to-canonical-ID mappings, optional aliases, the STT pipeline ID, cooldown, and the existing persistent STT privacy budgets. Exactly one of passive canary, HA STT canary, HA STT activation, and microWakeWord activation may be enabled.
3. A fake continuous PCM source proves that microWakeWord and local VAD observe the same bounded stream: no HA STT call occurs before a configured model detection; after detection, only the currently active or just-completed temporally associated utterance is eligible, without reopening the RTSP source; exactly one bounded PCM segment is submitted to HA STT. If no associated segment exists within the fixed grace window, nothing is submitted and the worker does not wait for an unrelated later utterance.
4. The detected model, not STT recognition of the wake phrase, authorizes activation. When STT begins with a configured alias, that alias is stripped; when it does not, the non-empty transcript remains the command. Empty or over-500-character commands publish nothing and their text is never logged.
5. A successful command publishes exactly once to `rtsp_assist_gateway/activation` with QoS 1 and retain false. Its version-1 payload contains `event=wake_command_detected`, `backend=microwakeword`, the mapped canonical `wake_word_id`, source/room/request/timestamp, and command; it contains no canary flag, model name, URL, credential, token, PCM, or separate transcript field. Publish retry reuses the identical payload and request ID.
6. Budget exhaustion blocks HA STT before provider contact, provider/source failures use bounded backoff, raw audio is held only in bounded memory, and logs contain neither matched/unmatched transcripts nor source secrets.
7. In `/config/GitHub/embodied-ha`, focused and full tests accept only the allowlisted `microwakeword` backend in addition to `ha_stt`; unknown backends remain rejected and the existing replay/room binding behavior is unchanged. `/config/automations.yaml` allowlists those same two backends and `ha core check` exits zero.
8. `git diff --check` exits zero in the Gateway, Embodied HA, and `/config` repositories. A repository scan finds no household entity IDs, personal names, or EHA MQTT prefixes in the public Gateway repository.
9. **Unverified until separately authorized deployment:** after a fresh six-hour passive-canary interval with zero unintended detections from the configured custom model, an exact versioned add-on build detects one real wake utterance, sends only its associated bounded utterance to HA STT, publishes one correlated activation, and produces one downstream assistant chat/reply. A duplicate request ID causes no second chat. Restoring the saved Gateway options returns to passive-canary mode without restarting Home Assistant or go2rtc.

## Non-goals

- Training or improving microWakeWord models, changing the Wyoming provider, or claiming that the observed 2/3 positive rate is sufficient for general release.
- Multi-source arbitration, simultaneous activation modes, speaker acknowledgement/chimes, TTS changes, Web UI, or Ingress.
- Removing the existing HA STT canary/activation modes or making HA STT optional for command transcription.
- Direct Gateway calls to Embodied HA, resident-specific routing, or configurable MQTT output topics.

## Constraints

- Do not touch `secrets.yaml`, `.ssh/`, or `.storage/`.
- Never persist raw audio or transcripts. Never log RTSP URLs, credentials, Supervisor tokens, MQTT passwords, PCM, or transcript/command content.
- Use one bounded ffmpeg PCM stream per active source. microWakeWord is the wake authority; HA STT is contacted only after detection and only for the associated bounded utterance.
- Keep the fixed generic MQTT boundary and existing household automation routing. The public Gateway must not contain household identities, entity IDs, or EHA topic names.
- Run `ha core check` after editing Home Assistant YAML. Do not run `ha core restart`, add-on rebuild/update/restart, version bump, release, push, or production option changes in this implementation phase.
- Embodied HA frontend files are out of scope.

## Rollback

Before deployment, abandon or revert the Gateway and Embodied HA feature branches and revert only the backend allowlist change in the household automation. After a later deployment, stop the Gateway, restore the exact saved Supervisor options with passive canary enabled and microWakeWord activation disabled, then restart only the Gateway. No HA Core or go2rtc restart is required.

## Increments

1. Freeze the configuration, continuous-PCM/VAD handoff, payload, privacy, and rollback contracts through red-team review.
2. Add a stateful bounded VAD collector and prove that wake detection and command capture share one PCM source.
3. Add microWakeWord activation configuration and worker with fake Wyoming/STT/MQTT contract tests.
4. Extend the EHA backend allowlist and household automation, then pass focused tests and `ha core check`.
5. Run full checks across all affected repositories, review diffs, and stop before version bump or deployment.

## Evidence report (2026-08-11)

- Criteria 1–6: PASS. Gateway has 89 passing tests; Ruff lint/format, compileall, version consistency,
  packaging verification, and `git diff --check` all pass. Contract tests cover one shared PCM source,
  temporal association/no-later-wait behavior, model authority, bounded commands, identical publish retry,
  persistent budget blocking before STT, and transcript/source-secret log redaction.
- Criterion 7: PASS before deployment. Embodied HA has 1,050 passing tests plus 186 passing subtests; the
  focused backend tests pass, and `ha core check` accepts the household automation change. No HA reload or
  restart was performed.
- Criterion 8: PASS. Diff checks pass in all three affected trees. Newly added public Gateway production
  code, tests, schema, and documentation use generic model/source identities and contain no household MQTT
  prefixes or entity IDs.
- Criterion 9: PENDING. The private custom-model provider/live-path check detected 2 of 3 intentional calls,
  which proves path liveness but is not a general recall claim. Because those calls intentionally interrupted
  the passive interval, the fresh zero-unintended-detection soak runs from 2026-08-11 16:20:23 JST through at
  least 22:20:23 JST. Version bump, commit, build, option switch, and production E2E remain separately gated.

## Increment log

- Increment 1: COMPLETE — specification and rollback frozen; red-team REVISE findings incorporated.
- Increment 2: COMPLETE — bounded stateful VAD collector and same-stream tests implemented.
- Increment 3: COMPLETE — disabled-by-default configuration, worker, MQTT contract, privacy, and budget tests implemented.
- Increment 4: COMPLETE — EHA/automation backend allowlists implemented and verified without reload/restart.
- Increment 5: COMPLETE for pre-deployment evidence; live criterion 9 remains intentionally pending.
