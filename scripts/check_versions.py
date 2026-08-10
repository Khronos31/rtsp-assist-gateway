#!/usr/bin/env python3
"""Validate the manifest, Python, and optional Git-tag versions."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "rtsp_assist_gateway" / "config.yaml"
PYTHON_VERSION_PATH = ROOT / "rtsp_assist_gateway" / "gateway" / "__init__.py"
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class VersionError(ValueError):
    """Raised when version sources are invalid or inconsistent."""


@dataclass(frozen=True)
class Versions:
    manifest: str
    python: str

    @property
    def expected_tag(self) -> str:
        return f"v{self.manifest}"


def read_manifest_version(path: Path = CONFIG_PATH) -> str:
    matches = re.findall(
        r'^version:\s*["\']?([^"\'\s#]+)["\']?\s*(?:#.*)?$',
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        raise VersionError("config.yaml must contain exactly one top-level version")
    return matches[0]


def read_python_version(path: Path = PYTHON_VERSION_PATH) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "__version__":
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                values.append(value)
    if len(values) != 1:
        raise VersionError("gateway/__init__.py must assign __version__ exactly once")
    return values[0]


def validate_versions(
    tag: str | None = None,
    config_path: Path = CONFIG_PATH,
    python_path: Path = PYTHON_VERSION_PATH,
) -> Versions:
    versions = Versions(
        manifest=read_manifest_version(config_path),
        python=read_python_version(python_path),
    )
    if not SEMVER_RE.fullmatch(versions.manifest):
        raise VersionError("config.yaml version must be valid Semantic Versioning")
    if versions.python != versions.manifest:
        raise VersionError("Python __version__ must equal config.yaml version")
    if tag is not None and tag != versions.expected_tag:
        raise VersionError(f"Git tag must be {versions.expected_tag}")
    return versions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Tag to validate, for example v0.1.0")
    args = parser.parse_args()
    tag = args.tag
    if tag is None and os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME")
    try:
        versions = validate_versions(tag=tag)
    except (OSError, SyntaxError, ValueError) as exc:
        raise SystemExit(f"version check failed: {exc}") from None
    print(
        json.dumps(
            {
                "result": "ok",
                "version": versions.manifest,
                "expected_tag": versions.expected_tag,
                "checked_tag": tag,
            }
        )
    )


if __name__ == "__main__":
    main()
