"""Public locator for the ``dotmac-tax`` Alembic lineage."""

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed directory containing Tax revisions."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
