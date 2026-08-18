"""Every shipped distribution must import without a database URL.

`dotmac_kernel.db` builds its engine from `settings.database_url` at MODULE
scope. So a single module-level `from dotmac_kernel.db import ...` anywhere in a
package makes `import <that package>` require a parseable DSN — and the failure
is an opaque `sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL`,
raised nowhere near the import that caused it.

This has now bitten **three times**, for the same reason:

- `dotmac_kernel.external_identity` gained one while a concurrent-binding race
  was being fixed. CI's `kernel-floors` job caught it — it installs the kernel
  wheel into a clean venv and does `import dotmac_kernel`.
- `dotmac_application_directory.service` had one from the day it was written.
  **Nothing caught it for weeks**, because `kernel-floors` imports only the
  kernel, the module had never been published (so the release wheel smoke had
  never run), and every developer and CI lane that imports it has `DATABASE_URL`
  set. It surfaced on the module's first-ever publish attempt, inside the
  release smoke, one step before the artifact would have been uploaded.
- `dotmac_template_studio` legitimately imports its routers while constructing
  its manifest, but those routers reached the eager DB owner through
  `dotmac_kernel.deps`. Deferring that adapter's entry into the transaction
  owner until FastAPI resolves a request removed the last package exemption
  without moving transaction authority.

Repeated instances are a class. This test is the class: it walks `packages/*` and
imports each distribution with the variable REMOVED, so the next one fails here
— on a normal PR, in the fast lane — rather than in a release dispatch.

The established fix is a function-local import; `errors.py` in the kernel
carries the same comment for `WebAuthRedirect`.

Each import runs in a SUBPROCESS: the parent pytest process has already imported
these packages and cached them in `sys.modules`, so an in-process check would
pass regardless of what the module does at import time.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PACKAGES_DIR = Path(__file__).resolve().parents[2] / "packages"


def _distributions() -> list[tuple[str, str]]:
    """(directory name, import name) for every package that ships code."""
    found: list[tuple[str, str]] = []
    for package_dir in sorted(PACKAGES_DIR.iterdir()):
        pyproject = package_dir / "pyproject.toml"
        if not pyproject.is_file():
            continue
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        packages = data["tool"]["poetry"].get("packages") or []
        for entry in packages:
            include = entry.get("include")
            if include:
                found.append((package_dir.name, include))
    return found


def _import_without_database_url(source: str) -> subprocess.CompletedProcess[str]:
    # REMOVED, not blanked: a parseable-but-empty DSN could let a lazy engine
    # succeed and hide the defect this test exists to catch.
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_there_are_distributions_to_check() -> None:
    """Non-vacuity: a walk that found nothing would pass silently forever."""
    found = _distributions()
    assert len(found) >= 8, f"only found {found}"
    assert ("dotmac-kernel", "dotmac_kernel") in found


@pytest.mark.parametrize(
    ("directory", "import_name"), _distributions(), ids=lambda v: str(v)
)
def test_the_distribution_imports_without_a_database_url(
    directory: str, import_name: str
) -> None:
    result = _import_without_database_url(f"import {import_name}\n")
    assert result.returncode == 0, (
        f"`import {import_name}` fails without DATABASE_URL:\n{result.stderr}\n"
        f"Something in {directory} imports `dotmac_kernel.db` at module scope, "
        "which builds the engine on import. Move that import INSIDE the function "
        "that needs it — see `dotmac_kernel.errors` for the pattern. A consumer "
        "must be able to import this package to read its version or register its "
        "manifest without configuring a database, and the release wheel smoke "
        "does exactly that."
    )


def test_the_probe_would_notice_a_module_level_db_import() -> None:
    """Sensitivity proof (ADR-0018).

    Importing `dotmac_kernel.db` directly is precisely what the packages must not
    do at module scope. If this stops failing, the engine has become lazy and the
    parametrized test above can no longer detect the regression it exists for —
    at which point re-aim this file rather than delete it.
    """
    result = _import_without_database_url("import dotmac_kernel.db\n")
    assert result.returncode != 0, (
        "`import dotmac_kernel.db` now succeeds without DATABASE_URL, so the "
        "checks above prove nothing. Engine construction has moved; re-aim this "
        "guard at whatever now carries the import-time cost."
    )
