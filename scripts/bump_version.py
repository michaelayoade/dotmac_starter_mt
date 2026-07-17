#!/usr/bin/env python3
"""Bump dotmac_starter_mt's app version across package metadata.

Ported from dotmac_sub's scripts/bump_version.py (the org's infra SoT), trimmed
to this repo's actual version surface: VERSION and pyproject.toml. Sub also
carries package.json/package-lock.json, a Flutter mobile app, and CHANGELOG
auto-inserts — none of that exists here, so it is intentionally dropped rather
than ported dead. See docs/superpowers/sdd/task-12-report.md for the full
port-delta.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
PYPROJECT_FILE = ROOT / "pyproject.toml"

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(version.strip())
    if not match:
        raise ValueError(f"Expected semantic version like 1.2.3, got {version!r}")
    return tuple(int(part) for part in match.groups())


def current_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def next_version(current: str, bump: str | None, explicit: str | None) -> str:
    if explicit:
        parse_version(explicit)
        return explicit

    major, minor, patch = parse_version(current)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"

    raise ValueError("Choose a bump type or pass --set VERSION")


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        rel = path.relative_to(ROOT)
        raise RuntimeError(f"Expected exactly one match in {rel} for {pattern!r}")
    path.write_text(new_text, encoding="utf-8")


def update_files(version: str) -> None:
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    replace_once(
        PYPROJECT_FILE,
        r'^version = "[^"]+"',
        f'version = "{version}"',
    )


def run_git(args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump dotmac_starter_mt's semantic app version."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("bump", nargs="?", choices=("major", "minor", "patch"))
    group.add_argument("--set", dest="explicit_version", metavar="VERSION")
    parser.add_argument(
        "--tag",
        action="store_true",
        help="Create an annotated Git tag like v1.2.3 after updating files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print 'current -> target' without changing files.",
    )
    args = parser.parse_args()

    current = current_version()
    version = next_version(current, args.bump, args.explicit_version)

    if args.dry_run:
        print(f"{current} -> {version}")
        return 0

    update_files(version)
    print(f"Bumped version: {current} -> {version}")
    print("Updated VERSION and pyproject.toml.")

    if args.tag:
        run_git(["tag", "-a", f"v{version}", "-m", f"Release v{version}"])
        print(f"Created Git tag v{version}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
