"""Alembic lineage locator for ``dotmac-analytics``."""

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed analytics Alembic versions directory."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
