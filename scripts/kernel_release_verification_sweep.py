#!/usr/bin/env python3
"""Require a bijection between facility-owned kernel tags and durable evidence."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from release_artifact_verification import canonical_json
from write_kernel_release_verification_record import validate_persisted_record

FIRST_FACILITY_ALPHA = 101
TAG = re.compile(r"dotmac-kernel-v(?P<version>0\.1\.0a(?P<alpha>[1-9][0-9]*))")
VERSION_FILE = re.compile(r"(?P<version>0\.1\.0a(?P<alpha>[1-9][0-9]*))\.json")


class SweepRefused(ValueError):
    """The tag and checked-in evidence oracles disagree."""


def git_output(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def facility_tags(repo: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    tags = git_output(repo, "tag", "--list", "dotmac-kernel-v*").splitlines()
    for tag in filter(None, tags):
        match = TAG.fullmatch(tag)
        if match is None or int(match["alpha"]) < FIRST_FACILITY_ALPHA:
            continue
        result[match["version"]] = tag
    return result


def evidence_paths(records: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not records.exists():
        return result
    for path in records.iterdir():
        if not path.is_file():
            raise SweepRefused(f"unexpected evidence entry {path.name}")
        match = VERSION_FILE.fullmatch(path.name)
        if match is None or int(match["alpha"]) < FIRST_FACILITY_ALPHA:
            raise SweepRefused(f"noncanonical evidence filename {path.name}")
        result[match["version"]] = path
    return result


def sweep(repo: Path, records: Path) -> None:
    tags = facility_tags(repo)
    evidence = evidence_paths(records)
    if set(tags) != set(evidence):
        raise SweepRefused(
            f"kernel tag/evidence versions differ: tags={sorted(tags)}, "
            f"evidence={sorted(evidence)}"
        )
    for version, tag in sorted(tags.items()):
        path = evidence[version]
        payload = path.read_bytes()
        record = json.loads(payload)
        if canonical_json(record) != payload:
            raise SweepRefused(f"{path.name} is not canonical JSON")
        validate_persisted_record(record, version=version)
        if git_output(repo, "cat-file", "-t", tag) != "tag":
            raise SweepRefused(f"{tag} is not annotated")
        if git_output(repo, "rev-parse", tag) != record["tag_object"]:
            raise SweepRefused(f"{tag} object differs from evidence")
        if git_output(repo, "rev-parse", f"{tag}^{{commit}}") != record["source_sha"]:
            raise SweepRefused(f"{tag} peel differs from evidence")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    records = root / "docs/inventories/kernel-release-verifications"
    try:
        sweep(root, records)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as failure:
        raise SystemExit(f"kernel release evidence REFUSED: {failure}") from failure
    print("kernel release tag/evidence oracle agrees")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
