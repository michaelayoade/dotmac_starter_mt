"""Alembic lineage shipped by ``dotmac-ticketing``."""

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed directory containing this module's revisions."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
