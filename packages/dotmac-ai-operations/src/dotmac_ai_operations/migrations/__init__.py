"""Alembic lineage locator for ``dotmac-ai-operations``."""

from pathlib import Path


def versions_dir() -> Path:
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
