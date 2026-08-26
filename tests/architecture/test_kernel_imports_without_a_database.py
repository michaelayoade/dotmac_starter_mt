"""`import dotmac_kernel` must not require a database URL.

`dotmac_kernel.db` builds the engine from `settings.database_url` at MODULE
scope, so anything the package root re-exports drags that engine construction
into a bare `import dotmac_kernel`. A consumer then cannot import the kernel to
read `__version__`, register a manifest, or run a floor probe without a
parseable DSN — and the failure is an opaque
`sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL`, nowhere near
the import that caused it.

This is a REGRESSION TEST, not a hypothetical. `external_identity` gained a
module-level `from dotmac_kernel.db import conflict_savepoint` while fixing a
concurrent-binding race; because the package root re-exports
`ResolvedExternalIdentity`, that one line made the whole kernel un-importable
without a DSN. CI's `kernel-floors` job caught it — it builds the wheel into a
clean venv and does `import dotmac_kernel` — and `make check` did not, because a
developer environment usually has `DATABASE_URL` set.

The established fix is a function-local import. `errors.py` already does this
for `WebAuthRedirect`, with the same reason in its comment.

Runs the import in a SUBPROCESS with the variable removed, because the parent
pytest process has already imported the kernel and cached it in `sys.modules`;
an in-process check would pass regardless.
"""

from __future__ import annotations

import os
import subprocess
import sys

# The names a consumer is most likely to reach for before configuring anything:
# a version to log, a manifest type to register, and the identity contracts.
_PROBE = """
import dotmac_kernel
from dotmac_kernel import (
    ExternalIdentityBinding,
    ResolvedExternalIdentity,
    __version__,
)
assert __version__
print("ok")
"""

_TRANSACTION_PROBE = """
import sys
from dotmac_kernel.transactions import conflict_savepoint
assert conflict_savepoint
assert "dotmac_kernel.db" not in sys.modules
print("ok")
"""


def _import_without_database_url(source: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    # A parseable-but-absent DSN would let a lazy engine succeed and hide the
    # defect, so the variable is REMOVED rather than blanked.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_importing_the_kernel_needs_no_database_url() -> None:
    result = _import_without_database_url(_PROBE)
    assert result.returncode == 0, (
        "`import dotmac_kernel` failed without DATABASE_URL:\n"
        f"{result.stderr}\n"
        "Something reachable from the package root imports `dotmac_kernel.db`, "
        "which builds the engine at module scope. Move that import INSIDE the "
        "function that needs it (see `errors.py`'s `WebAuthRedirect` import)."
    )


def test_public_transaction_mechanic_needs_no_database_runtime() -> None:
    """A caller-owned Session can use a SAVEPOINT without a second engine."""
    result = _import_without_database_url(_TRANSACTION_PROBE)
    assert result.returncode == 0, (
        "`dotmac_kernel.transactions` imported the kernel DB runtime:\n"
        f"{result.stderr}"
    )


def test_the_probe_would_notice_a_module_level_db_import() -> None:
    """Sensitivity proof (ADR-0018).

    Importing `dotmac_kernel.db` directly is what the guarded modules must not
    do at module scope. If this stops failing, the engine has become lazy and
    the test above can no longer detect the regression it exists for — at which
    point this file should be re-aimed rather than deleted.
    """
    result = _import_without_database_url("import dotmac_kernel.db\n")
    assert result.returncode != 0, (
        "`import dotmac_kernel.db` now succeeds without DATABASE_URL, so the "
        "test above proves nothing. The engine construction has moved; re-aim "
        "this guard at whatever now carries the import-time cost."
    )
