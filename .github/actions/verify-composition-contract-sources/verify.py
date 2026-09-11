"""Immutable bootstrap for the Kernel composition contract's helper bytes."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Final

TRUSTED_CONTRACT_REVISION: Final = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
TRUSTED_SEMANTIC_PATHS: Final = (
    "tools/composition_contract/composition_schema.py",
    "tools/composition_contract/observations.py",
    "tools/composition_contract/specs.py",
)


class TrustedSourceAcquisitionError(RuntimeError):
    """The pinned Git object or current source could not be read."""


class TrustedSourceDriftError(ValueError):
    """Current helper bytes differ from the pinned Git object."""


def read_regular_file(path: Path, *, label: str, limit: int = 2 * 1024 * 1024) -> bytes:
    """Read one non-symlink regular file once through an already-open fd."""

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise TrustedSourceAcquisitionError("this runner requires O_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
    except OSError as exc:
        raise TrustedSourceAcquisitionError(f"{label} is unreadable: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TrustedSourceAcquisitionError(f"{label} is not a regular file")
        if metadata.st_size > limit:
            raise TrustedSourceAcquisitionError(
                f"{label} exceeds the {limit}-byte bound"
            )
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise TrustedSourceAcquisitionError(
                    f"{label} exceeds the {limit}-byte bound"
                )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _git_show(repository: Path, revision: str, path: str) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repository), "show", f"{revision}:{path}"],  # noqa: S607
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise TrustedSourceAcquisitionError(f"could not launch git: {exc}") from exc
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise TrustedSourceAcquisitionError(
            f"git show failed for {revision}:{path}: "
            f"{diagnostic or '<no diagnostic>'}"
        )
    return result.stdout


def find_source_drift(
    repository: Path,
    *,
    revision: str = TRUSTED_CONTRACT_REVISION,
    paths: Sequence[str] = TRUSTED_SEMANTIC_PATHS,
) -> tuple[str, ...]:
    """Return semantic helpers whose current bytes differ from the Git object."""

    if tuple(paths) != TRUSTED_SEMANTIC_PATHS:
        raise ValueError("the protected helper set is closed")
    drift: list[str] = []
    for relative in TRUSTED_SEMANTIC_PATHS:
        current = read_regular_file(
            repository / relative,
            label=f"current source {relative!r}",
        )
        if current != _git_show(repository, revision, relative):
            drift.append(relative)
    return tuple(drift)


def read_verified_sources(repository: Path) -> MappingProxyType[str, bytes]:
    """Capture each helper once and return only bytes equal to the Git object."""

    captured: dict[str, bytes] = {}
    for relative in TRUSTED_SEMANTIC_PATHS:
        current = read_regular_file(
            repository / relative,
            label=f"current source {relative!r}",
        )
        if current != _git_show(repository, TRUSTED_CONTRACT_REVISION, relative):
            raise TrustedSourceDriftError(
                f"current helper {relative!r} differs from "
                f"{TRUSTED_CONTRACT_REVISION}"
            )
        captured[relative] = current
    return MappingProxyType(captured)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        drift = find_source_drift(options.repository)
    except TrustedSourceAcquisitionError as exc:
        print(f"TRUSTED SOURCE ACQUISITION FAILED: {exc}")
        return 2
    if drift:
        print(
            "TRUSTED SOURCE REFUSED: current helper bytes differ from "
            f"{TRUSTED_CONTRACT_REVISION}: {list(drift)!r}"
        )
        return 1
    print(f"TRUSTED SOURCE VERIFIED: {len(TRUSTED_SEMANTIC_PATHS)} helper files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
