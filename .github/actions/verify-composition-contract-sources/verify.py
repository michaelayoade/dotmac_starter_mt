"""Immutable bootstrap for the Kernel composition contract's helper bytes."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

TRUSTED_CONTRACT_REVISION: Final = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
TRUSTED_SEMANTIC_PATHS: Final = (
    "tools/composition_contract/composition_schema.py",
    "tools/composition_contract/observations.py",
    "tools/composition_contract/specs.py",
)


class TrustedSourceAcquisitionError(RuntimeError):
    """The pinned Git object or current source could not be read."""


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
        try:
            current = (repository / relative).read_bytes()
        except OSError as exc:
            raise TrustedSourceAcquisitionError(
                f"current source {relative!r} is unreadable: {exc}"
            ) from exc
        if current != _git_show(repository, revision, relative):
            drift.append(relative)
    return tuple(drift)


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
