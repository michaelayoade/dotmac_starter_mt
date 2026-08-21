"""Alembic versions shipped by dotmac-fx-policy."""

from importlib.resources import files
from pathlib import Path


def versions_dir() -> Path:
    return Path(str(files("dotmac_fx_policy.migrations.versions")))


__all__ = ["versions_dir"]
