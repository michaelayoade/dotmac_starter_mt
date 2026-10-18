"""Independent canonical learning migration lineage (branch: learning)."""

from pathlib import Path


def versions_dir() -> Path:
    """Return the installed directory containing the ``lr`` revisions."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
