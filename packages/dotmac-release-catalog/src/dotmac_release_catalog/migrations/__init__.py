"""Locate the Release Catalog Alembic lineage from an installed package.

Consuming assemblies compose migration locations from installed distributions,
whose paths vary between virtualenvs, wheels and container layers.  This public
locator keeps the package layout owned here instead of making every consumer
reconstruct it from ``__file__``.
"""

from __future__ import annotations

from pathlib import Path


def versions_dir() -> Path:
    """Return the absolute directory containing this module's revisions."""
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
