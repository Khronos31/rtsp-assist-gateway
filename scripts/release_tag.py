#!/usr/bin/env python3
"""Validate and atomically publish an immutable release tag."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

if __package__:
    from .check_versions import ROOT, validate_versions
else:
    from check_versions import ROOT, validate_versions

PROTECTION_RULESET_NAME = "Protect release tags"


class ReleaseError(RuntimeError):
    """Raised when a release precondition is not satisfied."""


def run(command: list[str], *, capture: bool = True) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() if capture else "command failed"
        raise ReleaseError(f"{command[0]} failed: {detail}")
    return result.stdout.strip() if capture else ""


def github_repository() -> str:
    remote = run(["git", "remote", "get-url", "origin"])
    match = re.fullmatch(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?", remote)
    if match is None:
        match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?", remote)
    if match is None:
        raise ReleaseError("origin must be a GitHub SSH or HTTPS repository")
    return match.group(1)


def ensure_tag_protection(repository: str) -> None:
    raw = run(["gh", "api", f"repos/{repository}/rulesets"])
    summaries = json.loads(raw)
    for summary in summaries:
        if (
            summary.get("name") != PROTECTION_RULESET_NAME
            or summary.get("target") != "tag"
            or summary.get("enforcement") != "active"
        ):
            continue
        details = json.loads(run(["gh", "api", f"repos/{repository}/rulesets/{summary['id']}"]))
        includes = details.get("conditions", {}).get("ref_name", {}).get("include", [])
        rule_types = {rule.get("type") for rule in details.get("rules", [])}
        if "refs/tags/v*" in includes and {"update", "deletion"}.issubset(rule_types):
            return
    raise ReleaseError("active release-tag update/deletion protection was not found")


def ensure_successful_ci(repository: str, head: str) -> None:
    raw = run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repository,
            "--workflow",
            "test",
            "--commit",
            head,
            "--limit",
            "10",
            "--json",
            "status,conclusion,headSha",
        ]
    )
    runs: list[dict[str, Any]] = json.loads(raw)
    if not any(
        item.get("headSha") == head
        and item.get("status") == "completed"
        and item.get("conclusion") == "success"
        for item in runs
    ):
        raise ReleaseError("the exact target commit does not have a successful test workflow")


def run_local_checks(tag: str) -> None:
    commands = [
        [sys.executable, "scripts/check_versions.py", "--tag", tag],
        [sys.executable, "-m", "pytest", "-q"],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "rtsp_assist_gateway",
            "tests",
            "scripts",
        ],
        [sys.executable, "scripts/verify_packaging.py"],
    ]
    for command in commands:
        run(command, capture=False)


def validate_release(tag: str, *, require_ci: bool) -> tuple[str, str]:
    versions = validate_versions(tag=tag)
    if run(["git", "status", "--porcelain"]):
        raise ReleaseError("worktree must be clean")
    if run(["git", "branch", "--show-current"]) != "main":
        raise ReleaseError("release tags may be created only from main")

    head = run(["git", "rev-parse", "HEAD"])
    live_main_lines = run(["git", "ls-remote", "--heads", "origin", "refs/heads/main"]).splitlines()
    if len(live_main_lines) != 1:
        raise ReleaseError("unable to resolve exactly one live origin/main")
    live_main = live_main_lines[0].split()[0]
    if head != live_main:
        raise ReleaseError("HEAD must equal live origin/main")
    if run(["git", "tag", "--list", tag]):
        raise ReleaseError(f"local tag already exists: {tag}")
    if run(["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"]):
        raise ReleaseError(f"remote tag already exists: {tag}")

    repository = github_repository()
    ensure_tag_protection(repository)
    if require_ci:
        ensure_successful_ci(repository, head)
    run_local_checks(tag)
    return head, versions.manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="Exact release tag, for example v0.1.0")
    parser.add_argument(
        "--push",
        action="store_true",
        help="Create and atomically push the tag after all remote gates pass",
    )
    args = parser.parse_args()
    try:
        head, version = validate_release(args.tag, require_ci=args.push)
        if not args.push:
            print(json.dumps({"result": "ok", "dry_run": True, "tag": args.tag, "target": head}))
            return
        run(["git", "tag", "-a", args.tag, "-m", f"RTSP Assist Gateway {version}"])
        run(
            [
                "git",
                "push",
                "--atomic",
                "origin",
                "HEAD:refs/heads/main",
                f"refs/tags/{args.tag}",
            ],
            capture=False,
        )
        print(json.dumps({"result": "ok", "tag": args.tag, "target": head}))
    except (OSError, ValueError, json.JSONDecodeError, ReleaseError) as exc:
        raise SystemExit(f"release failed: {exc}") from None


if __name__ == "__main__":
    main()
