"""Alembic lineage discovery for ``dotmac-durable-timers``."""

from pathlib import Path


def versions_dir() -> Path:
    """Return the package's Alembic versions directory."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
