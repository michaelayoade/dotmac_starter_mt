"""Installed Network Inventory Alembic lineage."""

from pathlib import Path


def versions_dir() -> Path:
    return Path(__file__).with_name("versions")


__all__ = ["versions_dir"]
