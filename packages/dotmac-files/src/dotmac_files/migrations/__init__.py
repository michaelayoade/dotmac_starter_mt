"""Public locator for the installed ``dotmac-files`` Alembic lineage."""

from __future__ import annotations

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed directory containing the ``fi`` revisions."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
