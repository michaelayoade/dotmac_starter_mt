"""Alembic lineage locator for consuming assemblies."""

from pathlib import Path


def versions_dir() -> Path:
    return Path(__file__).parent / "versions"


__all__ = ["versions_dir"]
