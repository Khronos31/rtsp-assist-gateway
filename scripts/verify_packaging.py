#!/usr/bin/env python3
"""Static self-check for the add-on package.

This intentionally does not claim to replace Supervisor validation or a real build.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import yaml

if __package__:
    from .check_versions import validate_versions
else:
    from check_versions import validate_versions

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "rtsp_assist_gateway"
REQUIRED = {
    ROOT / "repository.yaml",
    ADDON / "config.yaml",
    ADDON / "Dockerfile",
    ADDON / "requirements.txt",
    ADDON / "run.sh",
    ADDON / "gateway" / "main.py",
}
FORBIDDEN_MANIFEST_KEYS = {
    "audio",
    "apparmor",
    "devices",
    "full_access",
    "host_dbus",
    "host_network",
    "ingress",
    "map",
    "ports",
    "privileged",
}


def main() -> None:
    validate_versions()
    missing = sorted(str(path.relative_to(ROOT)) for path in REQUIRED if not path.is_file())
    if missing:
        raise SystemExit(f"Missing required package files: {', '.join(missing)}")

    config = yaml.safe_load((ADDON / "config.yaml").read_text(encoding="utf-8"))
    repository = yaml.safe_load((ROOT / "repository.yaml").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(repository, dict):
        raise SystemExit("YAML manifests must contain objects")
    if config.get("slug") != "rtsp_assist_gateway":
        raise SystemExit("Unexpected add-on identity")
    if config.get("boot") != "manual" or config.get("stage") != "experimental":
        raise SystemExit("Initial canary must be experimental and manual-start")
    if config.get("hassio_api") is not True:
        raise SystemExit("MQTT service discovery requires hassio_api")
    if config.get("homeassistant_api") is not True:
        raise SystemExit("HA STT mode requires homeassistant_api")
    forbidden = sorted(FORBIDDEN_MANIFEST_KEYS.intersection(config))
    if forbidden:
        raise SystemExit(f"Forbidden add-on permissions or surfaces: {', '.join(forbidden)}")
    if config.get("services") != ["mqtt:need"]:
        raise SystemExit("The only required Supervisor service must be mqtt:need")
    if config.get("arch") != ["amd64", "aarch64"]:
        raise SystemExit("Unexpected architecture declaration")
    if "passive_canary" not in config.get("options", {}):
        raise SystemExit("Missing passive_canary defaults")
    if "passive_canary" not in config.get("schema", {}):
        raise SystemExit("Missing passive_canary schema")
    if "ha_stt_canary" not in config.get("options", {}):
        raise SystemExit("Missing ha_stt_canary defaults")
    if "ha_stt_canary" not in config.get("schema", {}):
        raise SystemExit("Missing ha_stt_canary schema")
    if "ha_stt_activation" not in config.get("options", {}):
        raise SystemExit("Missing ha_stt_activation defaults")
    if "ha_stt_activation" not in config.get("schema", {}):
        raise SystemExit("Missing ha_stt_activation schema")
    if "microwakeword_activation" not in config.get("options", {}):
        raise SystemExit("Missing microwakeword_activation defaults")
    if "microwakeword_activation" not in config.get("schema", {}):
        raise SystemExit("Missing microwakeword_activation schema")

    run_sh = ADDON / "run.sh"
    if not run_sh.stat().st_mode & stat.S_IXUSR:
        raise SystemExit("run.sh is not executable")
    if "exec python3 -m gateway.main" not in run_sh.read_text(encoding="utf-8"):
        raise SystemExit("run.sh does not exec the gateway entry point")

    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    if "ffmpeg" not in dockerfile or "requirements.txt" not in dockerfile:
        raise SystemExit("Dockerfile is missing runtime dependencies")
    requirements = (ADDON / "requirements.txt").read_text(encoding="utf-8")
    for dependency in ("paho-mqtt==", "pysilero-vad==", "websockets==", "wyoming=="):
        if dependency not in requirements:
            raise SystemExit(f"Missing pinned runtime dependency: {dependency.removesuffix('==')}")
    if "SileroVoiceActivityDetector()" not in dockerfile:
        raise SystemExit("Dockerfile does not validate the VAD runtime during build")

    print(json.dumps({"result": "ok", "note": "static self-check only"}))


if __name__ == "__main__":
    main()
