# RTSP Assist Gateway project instructions

This repository contains a public Home Assistant OS add-on that converts configured RTSP audio streams into generic Home Assistant-facing activation events.

- Keep the gateway independent from Embodied HA. Do not add characters, memories, speakers, agent prefixes, or household-specific routing.
- Do not add a Web UI or Ingress unless a later decision explicitly approves it. Use Supervisor options and logs.
- Never persist raw audio. Never log RTSP URLs, credentials, Supervisor tokens, MQTT passwords, PCM, or unmatched transcripts.
- Passive canaries may publish only to their hard-coded diagnostic namespace and must never target command/chat topics.
- Use bounded memory and isolate failures per source worker.
- Add executable tests for protocol, privacy, validation, and recovery changes.
- Public commits must end with `Co-Authored-By: Codex <noreply@openai.com>` when Codex authored the change.
