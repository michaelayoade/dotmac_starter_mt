"""Every shipped distribution must import without a database URL.

`dotmac_kernel.db` builds its engine from `settings.database_url` at MODULE
scope. So a single module-level `from dotmac_kernel.db import ...` anywhere in a
package makes `import <that package>` require a parseable DSN — and the failure
is an opaque `sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL`,
raised nowhere near the import that caused it.

This has now bitten **twice**, in two packages, for the same reason:

- `dotmac_kernel.external_identity` gained one while a concurrent-binding race
  was being fixed. CI's `kernel-floors` job caught it — it installs the kernel
  wheel into a clean venv and does `import dotmac_kernel`.
- `dotmac_application_directory.service` had one from the day it was written.
  **Nothing caught it for weeks**, because `kernel-floors` imports only the
  kernel, the module had never been published (so the release wheel smoke had
  never run), and every developer and CI lane that imports it has `DATABASE_URL`
  set. It surfaced on the module's first-ever publish attempt, inside the
  release smoke, one step before the artifact would have been uploaded.

Two instances is a class. This test is the class: it walks `packages/*` and
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

# EXEMPT, with an enforceable premise (ADR-0018) and a two-directional ratchet.
#
# `dotmac_template_studio.__init__` exports a FastAPI router. A router's
# `Depends(get_db)` defaults are evaluated when the function is DEFINED, so
# `dotmac_kernel.deps` — and through it the eager engine in `dotmac_kernel.db` —
# is genuinely needed at module scope. That is not a careless import that a
# function-local one would fix; it is the ROOT CAUSE showing through:
# `dotmac_kernel/db.py` builds `engine = create_engine(settings.database_url)` at
# import.
#
# So this is exempt because the premise is structural, not because it is
# inconvenient — and the premise is ASSERTED below rather than asserted in prose.
# The real fix is a lazy kernel engine, which is its own change with its own
# risk; until then this package cannot be published through a lane whose smoke
# imports it with no database, and the release allowlist does not list it.
_IMPORT_REQUIRES_DB: frozenset[str] = frozenset({"dotmac_template_studio"})


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
    source_roots = [
        str(package / "src")
        for package in sorted(PACKAGES_DIR.iterdir())
        if (package / "src").is_dir()
    ]
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        [*source_roots, *([inherited] if inherited else [])]
    )
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
    if import_name in _IMPORT_REQUIRES_DB:
        pytest.skip(f"{import_name} is exempt; see the ratchet test below")
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


# ── The exemption's own guards ──────────────────────────────────────────────


@pytest.mark.parametrize("import_name", sorted(_IMPORT_REQUIRES_DB))
def test_an_exempt_package_still_actually_needs_the_database(
    import_name: str,
) -> None:
    """The ratchet, and it runs in BOTH directions.

    If an exempt package starts importing cleanly, the exemption is stale and the
    set must be lowered — otherwise the list quietly grows into a place where
    real defects hide. `_IMPORT_REQUIRES_DB` may only ever shrink.
    """
    result = _import_without_database_url(f"import {import_name}\n")
    assert result.returncode != 0, (
        f"{import_name} now imports without DATABASE_URL — remove it from "
        "_IMPORT_REQUIRES_DB. An exemption nobody re-checks is how a guard "
        "becomes decoration."
    )


@pytest.mark.parametrize("import_name", sorted(_IMPORT_REQUIRES_DB))
def test_the_exemptions_premise_holds(import_name: str) -> None:
    """The premise, asserted rather than asserted-in-prose.

    Two halves, and BOTH must hold or the exemption is inherited rather than
    earned:

    1. importing the package pulls `dotmac_kernel.deps` into `sys.modules` —
       that is the actual mechanism, since `deps` imports `dotmac_kernel.db` at
       module scope and `db` builds the engine;
    2. the package's own import surface includes a FastAPI router, which is WHY
       it needs `deps` at all: a router's `Depends(get_db)` defaults are
       evaluated when the function is defined.

    The chain is `__init__` -> `manifest` -> `router` -> `dotmac_kernel.deps` ->
    `dotmac_kernel.db`, and the first hop is what makes it structural: a
    `ModuleManifest` that declares `api_routers` must import the router to
    reference it.

    The first version of this test looked for an `APIRouter` attribute on the
    package itself and failed — the router lives in a SUBMODULE
    (`<pkg>.router`), not re-exported on `__init__`. The guard caught the
    imprecision in the premise, which is the point of asserting one.
    """
    probe = (
        "import os, sys\n"
        # Set unconditionally: `setdefault` would keep an unparseable value
        # the caller already had, and the probe would fail for the wrong
        # reason.
        "os.environ['DATABASE_URL'] = 'postgresql+psycopg://u:p@h/d'\n"
        "import fastapi\n"
        f"import {import_name}\n"
        "\n"
        "# Half 1 — the mechanism.\n"
        "assert 'dotmac_kernel.deps' in sys.modules, 'deps was never imported'\n"
        "\n"
        "# Half 2 — the reason. A router anywhere in this package's own graph.\n"
        "routers = [\n"
        "    name\n"
        "    for name, mod in list(sys.modules.items())\n"
        f"    if name.startswith('{import_name}') and mod is not None\n"
        "    for attr in dir(mod)\n"
        "    if isinstance(getattr(mod, attr, None), fastapi.APIRouter)\n"
        "]\n"
        "assert routers, 'no FastAPI router in this package'\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, (
        f"the stated premise for exempting {import_name} no longer holds, so the "
        "exemption must be re-argued rather than inherited:\n"
        f"{result.stderr}"
    )
