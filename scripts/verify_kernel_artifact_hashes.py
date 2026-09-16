#!/usr/bin/env python3
"""Verify that a downloaded kernel distribution is exactly the build output."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from release_artifact_verification import canonical_kernel_filenames

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_digests(dist: Path, version: str) -> dict[str, str]:
    try:
        expected_names = canonical_kernel_filenames(version)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not dist.is_dir():
        raise SystemExit(f"distribution directory does not exist: {dist}")

    entries = list(dist.iterdir())
    if any(path.is_symlink() for path in entries):
        raise SystemExit("artifact set contains a symlink")
    actual_files = {path.name for path in entries if path.is_file()}
    if len(actual_files) != len(entries):
        raise SystemExit("artifact set contains a non-file entry")
    expected_files = set(expected_names)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise SystemExit(f"artifact set mismatch: missing={missing}, extra={extra}")
    return {filename: _digest(dist / filename) for filename in sorted(expected_files)}


def record(dist: Path, version: str, output: Path) -> None:
    digests = _artifact_digests(dist, version)
    wheel = next(name for name in digests if name.endswith(".whl"))
    sdist = next(name for name in digests if name.endswith(".tar.gz"))
    with output.open("a", encoding="utf-8") as stream:
        stream.write(f"wheel_sha256={digests[wheel]}\n")
        stream.write(f"sdist_sha256={digests[sdist]}\n")


def verify(dist: Path, version: str, wheel_sha256: str, sdist_sha256: str) -> None:
    if any(not _SHA256.fullmatch(value) for value in (wheel_sha256, sdist_sha256)):
        raise SystemExit("build artifact digest is not a canonical SHA-256")
    actual = _artifact_digests(dist, version)
    expected = {
        next(name for name in actual if name.endswith(".whl")): wheel_sha256,
        next(name for name in actual if name.endswith(".tar.gz")): sdist_sha256,
    }

    for filename, expected_digest in expected.items():
        actual_digest = actual[filename]
        if actual_digest != expected_digest:
            raise SystemExit(
                f"artifact digest mismatch for {filename}: "
                f"expected {expected_digest}, got {actual_digest}"
            )
    print("kernel artifact hashes match build outputs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--dist", type=Path, required=True)
    record_parser.add_argument("--version", required=True)
    record_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--dist", type=Path, required=True)
    verify_parser.add_argument("--version", required=True)
    verify_parser.add_argument("--wheel-sha256", required=True)
    verify_parser.add_argument("--sdist-sha256", required=True)
    args = parser.parse_args()
    if args.command == "record":
        record(args.dist, args.version, args.output)
    else:
        verify(args.dist, args.version, args.wheel_sha256, args.sdist_sha256)


if __name__ == "__main__":
    main()
