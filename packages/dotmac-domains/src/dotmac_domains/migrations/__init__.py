"""Alembic lineage for dotmac-domains."""

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed directory containing the Domains revisions."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
