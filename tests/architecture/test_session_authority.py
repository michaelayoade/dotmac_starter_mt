"""One transaction authority (control-plane security Task 4).

`app/core/db.py` is the ONLY module that may construct DB sessions.
Everything else receives a session at its boundary (`get_db` /
`get_platform_db`, which own commit/rollback) and only mutates + flushes;
expected conflicts use `conflict_savepoint`. `UnitOfWork` — a second,
zero-consumer transaction authority — was DELETED this task (stronger SoT
rule: zero consumers → delete), and this test keeps a second authority from
ever growing back.

AST-based, not string-matching (module control-plane directive standard):
a comment or docstring mentioning `SessionLocal()` must not trip it, and a
call spelled `db.SessionLocal()` must. Scope is `app/` — tests and
operator scripts (`scripts/`, migration-trust-boundary CLIs) are outside
the request path this contract governs.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent.parent / "app"

# The session factories/constructors nobody outside app/core/db.py may CALL.
_FORBIDDEN_CALLS = {"SessionLocal", "PlatformSessionLocal", "sessionmaker", "Session"}

# The one module that owns session construction.
_AUTHORITY = "app/core/db.py"

# Explicit, justified exceptions to the CALL rule (module-path suffix →
# reason). Additions require a matching justification in ARCHITECTURE.md's
# "Transaction authority" section.
_CALL_ALLOWLIST = {
    # The tenant resolver runs BEFORE any route dependency exists, so no
    # get_db-provided session can reach it; it opens a short read-only
    # session via `with SessionLocal() as db:` and owns that boundary
    # itself (same contract, different entry point). It never mutates.
    "app/core/middleware/tenant.py",
}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def find_session_authority_violations(rel_path: str, source: str) -> list[str]:
    """Return 'path:line message' entries for forbidden session construction
    or `sessionmaker` imports in one module's source."""
    violations: list[str] = []
    tree = ast.parse(source)
    is_feature_module = rel_path.startswith("app/features/")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _FORBIDDEN_CALLS and rel_path not in _CALL_ALLOWLIST:
                violations.append(
                    f"{rel_path}:{node.lineno} calls {name}(...) — session "
                    "construction belongs to app/core/db.py only"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module and "sqlalchemy" in node.module and is_feature_module:
                for alias in node.names:
                    if alias.name == "sessionmaker":
                        violations.append(
                            f"{rel_path}:{node.lineno} imports sessionmaker — "
                            "feature modules receive sessions, never build them"
                        )
    return violations


def _iter_app_modules():
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel_path = "app/" + str(path.relative_to(APP_ROOT)).replace("\\", "/")
        if rel_path == _AUTHORITY:
            continue
        yield rel_path, path.read_text()


def test_only_core_db_constructs_sessions() -> None:
    violations: list[str] = []
    for rel_path, source in _iter_app_modules():
        violations.extend(find_session_authority_violations(rel_path, source))
    assert not violations, (
        "Session-construction outside the one transaction authority "
        "(app/core/db.py):\n" + "\n".join(violations)
    )


def test_checker_flags_a_violation() -> None:
    """Sensitivity self-test: the checker must actually bite. A synthetic
    feature module that builds its own session/factory is flagged on every
    forbidden form; a compliant module is not."""
    bad = (
        "from sqlalchemy.orm import Session, sessionmaker\n"
        "from app.core.db import SessionLocal, engine\n"
        "db = SessionLocal()\n"
        "factory = sessionmaker(bind=engine)\n"
        "s = Session(engine)\n"
    )
    flagged = find_session_authority_violations("app/features/fake/service.py", bad)
    assert len(flagged) == 4, flagged  # 3 calls + 1 sessionmaker import

    good = (
        "from sqlalchemy.orm import Session\n"
        "def list_things(db: Session):\n"
        "    # SessionLocal() in a comment must not trip an AST checker\n"
        "    return db.scalars('...')\n"
    )
    assert find_session_authority_violations("app/features/fake/service.py", good) == []


def test_allowlist_is_still_needed() -> None:
    """Every allowlisted module must still contain the call it is excused
    for — a stale allowlist entry is a hole waiting for a regression."""
    for rel_path in _CALL_ALLOWLIST:
        source = (APP_ROOT.parent / rel_path).read_text()
        assert find_session_authority_violations("app/_probe_.py", source), (
            f"{rel_path} no longer constructs a session — remove it from "
            "_CALL_ALLOWLIST"
        )
