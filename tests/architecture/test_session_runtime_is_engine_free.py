"""`dotmac_kernel.session_runtime` must cost nothing to import.

The whole point of extracting the runtime is that a product can instantiate it
with ITS configuration. That is only true if importing the module does not
first read the KERNEL's configuration and build the kernel's engines — which is
exactly what `dotmac_kernel.db` does, deliberately and eagerly.

So the two modules have opposite import contracts, and both are asserted here
together because the pair only makes sense as a pair:

* importing `session_runtime` must succeed with no `DATABASE_URL` in the
  environment, and must not drag `dotmac_kernel.db` in behind it;
* importing `dotmac_kernel.db` must still FAIL without one.

The second is not a wart to be fixed later. `test_kernel_imports_without_a_
database.py` and `test_packages_import_without_a_database.py` both rely on
entering the owner costing a DSN — that is how they can tell a module-level
`from dotmac_kernel.db import ...` from a deferred one. If the owner ever goes
lazy, those guards start passing for the wrong reason, and this file is where
that shows up first.

Runs in a SUBPROCESS with the variable REMOVED (not blanked — a
parseable-but-absent DSN would let a lazy engine succeed and hide the defect),
because the parent pytest process has already imported the kernel and cached it
in `sys.modules`.
"""

from __future__ import annotations

import os
import subprocess
import sys

_IMPORT_THE_RUNTIME = """
import sys

from dotmac_kernel.session_runtime import (
    CANONICAL_TENANT_SETTING,
    DatabaseRuntime,
)

assert CANONICAL_TENANT_SETTING == "app.current_tenant"
assert DatabaseRuntime is not None

# The module may not reach the eager owner, directly or through a helper it
# calls at import. A product instantiating its own runtime must not end up
# holding the reference assembly's engines as a side effect.
assert "dotmac_kernel.db" not in sys.modules, sorted(
    m for m in sys.modules if m.startswith("dotmac_kernel")
)
print("ok")
"""


def _run_without_database_url(source: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_the_runtime_imports_without_a_database_url() -> None:
    result = _run_without_database_url(_IMPORT_THE_RUNTIME)
    assert result.returncode == 0, (
        "`dotmac_kernel.session_runtime` could not be imported without "
        f"DATABASE_URL:\n{result.stderr}\n"
        "It must not import `dotmac_kernel.config` or `dotmac_kernel.db` at "
        "module scope — a product instantiating its own runtime would then be "
        "forced to satisfy the kernel's configuration first, which is the "
        "coupling this module was extracted to remove."
    )


def test_the_eager_owner_still_costs_a_dsn() -> None:
    """Sensitivity proof (ADR-0018).

    The test above proves the runtime is free to import. It would prove that
    equally well if NOTHING in the kernel needed a DSN any more — at which
    point it, and the two package-root import guards that share its premise,
    would all be measuring an environment rather than a contract.
    """
    result = _run_without_database_url("import dotmac_kernel.db\n")
    assert result.returncode != 0, (
        "`import dotmac_kernel.db` now succeeds without DATABASE_URL. The "
        "reference assembly's runtime has become lazy, so this file and the "
        "package-root import guards no longer detect what they were written "
        "for. Re-aim them at whatever now carries the import-time cost."
    )
