from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_versions import VersionError, validate_versions
from scripts.release_tag import github_repository


def write_versions(tmp_path: Path, manifest: str, python: str) -> tuple[Path, Path]:
    config_path = tmp_path / "config.yaml"
    python_path = tmp_path / "__init__.py"
    config_path.write_text(f'name: Test\nversion: "{manifest}"\n', encoding="utf-8")
    python_path.write_text(f'__version__ = "{python}"\n', encoding="utf-8")
    return config_path, python_path


def test_repository_versions_and_tag_match() -> None:
    versions = validate_versions(tag="v0.3.0")
    assert versions.manifest == "0.3.0"
    assert versions.python == versions.manifest


def test_mismatch_is_rejected(tmp_path: Path) -> None:
    config_path, python_path = write_versions(tmp_path, "1.2.3", "1.2.4")
    with pytest.raises(VersionError, match="must equal"):
        validate_versions(config_path=config_path, python_path=python_path)


@pytest.mark.parametrize("version", ["01.2.3", "1.2", "v1.2.3", "1.2.3-"])
def test_invalid_semver_is_rejected(tmp_path: Path, version: str) -> None:
    config_path, python_path = write_versions(tmp_path, version, version)
    with pytest.raises(VersionError, match="Semantic Versioning"):
        validate_versions(config_path=config_path, python_path=python_path)


def test_wrong_tag_is_rejected() -> None:
    with pytest.raises(VersionError, match=r"Git tag must be v0\.3\.0"):
        validate_versions(tag="v0.1.0")


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:Khronos31/rtsp-assist-gateway.git", "Khronos31/rtsp-assist-gateway"),
        ("https://github.com/Khronos31/rtsp-assist-gateway.git", "Khronos31/rtsp-assist-gateway"),
    ],
)
def test_github_repository_parsing(monkeypatch, remote: str, expected: str) -> None:
    monkeypatch.setattr("scripts.release_tag.run", lambda _command: remote)
    assert github_repository() == expected
