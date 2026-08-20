"""Public locator for the installed Web Analytics Alembic lineage."""

from __future__ import annotations

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed directory containing Web Analytics revisions."""

    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
