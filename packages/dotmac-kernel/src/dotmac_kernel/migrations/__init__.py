"""Kernel base migrations, shipped as package data.

`versions_dir()` returns the directory holding the kernel's base Alembic
revisions (0001-0007). A product assembly composes this with its OWN migration
directory via `version_locations` in its Alembic config (see the reference
assembly's `alembic.ini` / `alembic/env.py`), so the kernel base lineage and
the assembly lineage share one revision graph while staying separately owned.
"""

from __future__ import annotations

from pathlib import Path


def versions_dir() -> Path:
    return Path(__file__).resolve().parent / "versions"
