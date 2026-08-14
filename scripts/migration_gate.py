#!/usr/bin/env python3
"""Composed migration gate for THIS assembly (ADR-0006 D1, item 6).

Loads every version location the assembly's Alembic config selects, attributes
each to a registered migration owner, and refuses the composition on a
duplicate revision, prefix, branch label, schema claim or table ownership —
plus the lineage and full-qualification rules D1 states alongside them.

Runs before an image can be built (`make check`, and the `migration-gate`
matrix entry in CI): a composition fault must fail here, in a cheap static
step, and never during a deploy against a real database.

Everything-by-config: `ALEMBIC_INI` overrides the config it reads (default
`<repo>/alembic.ini`) and `GATE_DATABASE_URL` the placeholder URL below, so a
deployment composing a different set of version locations gates the set it
actually ships.

Exit codes: 0 = composable, 1 = violations (printed), 2 = bad invocation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Reading the manifests means importing the feature packages, and
# `dotmac_kernel.db` builds the SQLAlchemy engine at import. This gate is
# STATIC — it never connects — so pin a well-formed but unroutable placeholder
# exactly as `tests/conftest.py` and `scripts/consumer_boot_check.sh` do: the
# engine constructs, and any accidental connection fails fast instead of
# reaching a real database from a CI lint step. Must run before the first
# `dotmac_kernel` import, which freezes `settings`.
_PLACEHOLDER_DB_URL = os.getenv(
    "GATE_DATABASE_URL",
    "postgresql+psycopg://migration-gate:migration-gate@127.0.0.1:59999/gate",
)
os.environ.setdefault("DATABASE_URL", _PLACEHOLDER_DB_URL)
os.environ.setdefault("PLATFORM_DATABASE_URL", _PLACEHOLDER_DB_URL)

from dotmac_kernel.migrations.gate import (  # noqa: E402
    run_gate,
    version_locations_from_ini,
)

# Gate the SAME composition the app boots — `app.assembly`'s spec, not
# `load_manifests(FEATURE_MODULES)`. That earlier spelling covered the
# assembly's own features only, so the first INSTALLED MODULE's lineage was
# unattributable: its branch label was "not registered" because the module
# declaring it was never in the set the gate built its registry from. A gate
# that checks a different composition than the one that ships is not a gate.
from app.assembly import assembly  # noqa: E402

# Gate the composition the app boots INCLUDING its prerequisite answers. A
# composed module declares the database effects it needs; this assembly's
# binding of those effects to revisions is part of the composition, so a gate
# run without it would report every requirement as unbound and check nothing
# useful. Same source `alembic/env.py` installs at runtime.
from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS  # noqa: E402


def main() -> int:
    ini_path = Path(os.getenv("ALEMBIC_INI") or (REPO_ROOT / "alembic.ini"))
    if not ini_path.is_file():
        print(f"alembic config not found: {ini_path}", file=sys.stderr)
        return 2
    locations = version_locations_from_ini(ini_path)
    if not locations:
        print(f"{ini_path} selects no version_locations", file=sys.stderr)
        return 2
    report = run_gate(
        assembly.modules, locations, bindings=ASSEMBLY_PREREQUISITE_BINDINGS
    )
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
