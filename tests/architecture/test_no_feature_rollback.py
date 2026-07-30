"""Feature services never call `db.rollback()` directly (F3 hard rule).

A bare `db.rollback()` inside a feature's request-scoped conflict handling
rolls back the ENTIRE transaction `dotmac_kernel.db.get_db` opened for the
request — including the `SET LOCAL app.current_tenant` it issued for RLS.
Any query the caller's exception handler runs afterwards then runs with no
tenant context, and FORCE ROW LEVEL SECURITY fails closed (see finding F3,
`docs/superpowers/plans/2026-07-18-phase2b1-sot-composability.md` Task 2,
and the canaries in `tests/test_conflict_rls_context.py`).

`dotmac_kernel.db.conflict_savepoint` is the one sanctioned pattern for an
expected conflict (`with conflict_savepoint(db): db.flush()` inside a
`try/except IntegrityError`): it rolls back only a SAVEPOINT, leaving the
outer transaction + its `SET LOCAL` intact.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISALLOWED = re.compile(r"\bdb\.rollback\(\)")

# Files that are allowed a bare `db.rollback()` despite living under
# app/features — each entry names a startup/maintenance task that owns its
# OWN top-level session end-to-end (open -> commit-or-rollback -> close),
# the same shape as dotmac_kernel.db.get_db/get_platform_db themselves, rather
# than a mid-request conflict site sharing the request's `get_db` session
# and its `SET LOCAL` tenant context. Nothing in this set translates the
# rollback into a `ConflictError` for a caller to keep going after — it's a
# whole-operation failure, logged and re-raised.
ALLOWLIST = {
    # `seed_platform_defaults` runs from the app lifespan (not a request),
    # opens its own `PlatformSessionLocal` session, and rolls back the WHOLE
    # seed operation on any failure — there is no request-scoped SET LOCAL
    # tenant context to preserve (it writes NULL-tenant platform rows), and
    # no ConflictError translation happens here (see module docstring).
    "app/features/settings/seed.py",
}


def _feature_py_files() -> list[Path]:
    features = PROJECT_ROOT / "app" / "features"
    return sorted(features.rglob("*.py"))


def test_no_bare_rollback_in_feature_services() -> None:
    violations: list[str] = []
    for path in _feature_py_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        for match in DISALLOWED.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            violations.append(f"{rel}:{line} -> bare db.rollback()")
    assert not violations, (
        "feature services must use dotmac_kernel.db.conflict_savepoint for "
        "expected conflicts, never a bare db.rollback() (F3):\n" + "\n".join(violations)
    )
