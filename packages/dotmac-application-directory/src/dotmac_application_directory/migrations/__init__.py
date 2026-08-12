"""Locating this module's Alembic lineage from a consuming assembly.

A consumer composes `version_locations` from INSTALLED packages, whose paths are
environment-specific — a virtualenv, a wheel, a container layer. The starter can
hard-code `packages/dotmac-application-directory/...` in its `alembic.ini`
because the package sits in the same checkout; a cross-repository consumer like
`dotmac_workspace` cannot, and must not reach into `__file__` to guess.

So this mirrors `dotmac_kernel.migrations.versions_dir()`: one public function
that says where the lineage is, which keeps the layout an implementation detail
this module is free to change.
"""

from __future__ import annotations

from pathlib import Path


def versions_dir() -> Path:
    """Absolute path to this module's Alembic versions directory.

    Compose it into the consuming assembly's `version_locations` alongside
    `dotmac_kernel.migrations.versions_dir()` and the assembly's own lineage.
    """
    return Path(__file__).resolve().parent / "versions"


__all__ = ["versions_dir"]
