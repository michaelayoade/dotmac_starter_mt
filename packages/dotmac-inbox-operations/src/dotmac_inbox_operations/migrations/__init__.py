"""Alembic lineage exported by dotmac-inbox-operations."""

from pathlib import Path


def versions_dir() -> Path:
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
