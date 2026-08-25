"""One transaction authority (control-plane security Task 4).

`dotmac_kernel/db.py` is the ONLY module that may construct DB sessions.
Everything else receives a session at its boundary (`get_db` /
`get_platform_db`, which own commit/rollback) and only mutates + flushes;
expected conflicts use `conflict_savepoint`. `UnitOfWork` — a second,
zero-consumer transaction authority — was DELETED this task (stronger SoT
rule: zero consumers → delete), and this test keeps a second authority from
ever growing back.

AST-based, not string-matching (module control-plane directive standard):
a comment or docstring mentioning `SessionLocal()` must not trip it, and a
call spelled `db.SessionLocal()` must. Scope is `app/` + the kernel package;
tests and operator scripts (`scripts/`, migration-trust-boundary CLIs) are
outside the request path this contract governs.

**The shipped test kit is scanned (changed 2026-08-11, ADR-0018).** It used to
be skipped by directory prefix on the premise that it "is a session authority
by design". The premise was true of exactly one function and one line —
`isolated_session`'s `sessionmaker` call in `testing/harness.py` — but the
exemption was written as a whole-tree prefix, so every file added under
`dotmac_kernel/testing/` inherited the blind spot without the exemption list
changing. That is the failure ADR-0018 names: an exempted region becomes the
lowest-friction place to put work.

The tree is now walked like any other, and the one justified call is pinned by
COUNT in `_JUSTIFIED_CALLS`. A second construction site anywhere under the kit
fails `test_justified_calls_ratchet_holds_in_both_directions`, and
`test_the_ratchet_catches_a_second_construction_site` proves that it does.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_ROOT = REPO_ROOT / "app"
# Session construction lives in the kernel package now (dotmac_kernel/db.py);
# both the kernel and the assembly are audited under this one contract.
KERNEL_ROOT = REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel"

# (root_dir, rel_prefix) pairs scanned by the audit.
_SCANNED_TREES = ((APP_ROOT, "app"), (KERNEL_ROOT, "dotmac_kernel"))

# The session factories/constructors nobody outside dotmac_kernel/db.py may CALL.
_FORBIDDEN_CALLS = {"SessionLocal", "PlatformSessionLocal", "sessionmaker", "Session"}

# The one module that owns session construction.
_AUTHORITY = "dotmac_kernel/db.py"

# Justified session-construction sites, pinned by EXACT COUNT — ADR-0018's
# two-directional ratchet. A file absent from this map must construct ZERO
# sessions; a file present must construct exactly the pinned number.
#
# Counts, not a bare allowlist, because a set-membership exemption permits any
# number of calls in a listed file: the file gets excused once and then becomes
# the cheapest place to add the next one. The count moving UP is new debt; the
# count moving DOWN is progress that must be recorded here; the file vanishing
# from the scan is a stale entry. All three fail.
#
# Additions require a matching justification in ARCHITECTURE.md's "Transaction
# authority" section.
#
# History worth keeping: this was an empty allowlist, and the entry it used to
# hold was `dotmac_kernel/middleware/tenant.py`, which opened a bare
# `SessionLocal()` because the resolver runs before any route dependency exists
# and nothing on the public surface named that need. `resolver_session()` names
# it, so the resolver uses a boundary like everything else and the exception
# disappeared rather than being documented — which is the outcome an exemption
# should be aiming for.
_JUSTIFIED_CALLS: dict[str, int] = {
    # The shipped test kit builds the savepoint-isolated session a consumer's
    # unit tests receive (`isolated_session`) — it IS a session authority, but
    # deliberately only here, and only once.
    #
    # Until 2026-08-11 the whole of `dotmac_kernel/testing/` was excluded from
    # this scan by directory prefix, so any file added under it inherited the
    # blind spot silently. The tree is now scanned like every other, and this
    # single pinned call is the entire justified surface.
    "dotmac_kernel/testing/harness.py": 1,
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
    or `sessionmaker` imports in one module's source.

    Reports EVERY site unconditionally — it does not consult `_JUSTIFIED_CALLS`.
    Justification is applied by the caller, against the count, so that the
    ratchet can see a pinned file's real number rather than a filtered one.
    """
    violations: list[str] = []
    tree = ast.parse(source)
    is_feature_module = rel_path.startswith("app/features/")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _FORBIDDEN_CALLS:
                violations.append(
                    f"{rel_path}:{node.lineno} calls {name}(...) — session "
                    "construction belongs to dotmac_kernel/db.py only"
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
    """Every module under the scanned trees except the one authority itself.

    There is deliberately no directory-prefix skip. `dotmac_kernel/testing/`
    used to be skipped wholesale; the exemption is now a per-file COUNT in
    `_JUSTIFIED_CALLS`, so a new file under that tree is scanned like any other
    instead of inheriting an existing blind spot (ADR-0018).
    """
    for root, prefix in _SCANNED_TREES:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel_path = f"{prefix}/" + str(path.relative_to(root)).replace("\\", "/")
            if rel_path == _AUTHORITY:
                continue
            yield rel_path, path.read_text()


def _scan_counts() -> dict[str, int]:
    """Every scanned module that constructs a session, and how many times."""
    counts: dict[str, int] = {}
    for rel_path, source in _iter_app_modules():
        found = find_session_authority_violations(rel_path, source)
        if found:
            counts[rel_path] = len(found)
    return counts


def test_only_core_db_constructs_sessions() -> None:
    """No UNJUSTIFIED session construction anywhere in the scanned trees."""
    counts = _scan_counts()
    unjustified: list[str] = []
    for rel_path, source in _iter_app_modules():
        if rel_path in _JUSTIFIED_CALLS:
            continue
        unjustified.extend(find_session_authority_violations(rel_path, source))
    assert not unjustified, (
        "Session-construction outside the one transaction authority "
        "(dotmac_kernel/db.py):\n" + "\n".join(unjustified)
    )
    # Non-vacuity: the scan must actually have walked the kit. If this tree
    # stops being scanned (a moved package, a changed root), the assertion
    # above would pass over nothing and report coverage it does not have.
    assert any(p.startswith("dotmac_kernel/testing/") for p in counts), (
        "the scan found no session construction under dotmac_kernel/testing/ — "
        "either the kit moved, or the scan root is wrong"
    )


def test_justified_calls_ratchet_holds_in_both_directions() -> None:
    """The pinned counts must match reality EXACTLY (ADR-0018 rule 3).

    Up means new debt behind an existing justification. Down means a site was
    removed and the pin must record it. A missing key means the entry is stale
    — the file no longer constructs anything and the exemption should be
    deleted, which is how the old `middleware/tenant.py` entry was caught.
    """
    actual = {p: n for p, n in _scan_counts().items() if p in _JUSTIFIED_CALLS}
    stale = sorted(set(_JUSTIFIED_CALLS) - set(actual))
    assert not stale, (
        f"stale _JUSTIFIED_CALLS entries — these files no longer construct a "
        f"session, so delete them: {stale}"
    )
    assert actual == _JUSTIFIED_CALLS, (
        "session-construction counts moved. Pinned "
        f"{_JUSTIFIED_CALLS}, found {actual}. Upward is new debt behind an "
        "existing justification; downward is progress that must be recorded "
        "here."
    )


def test_the_ratchet_catches_a_second_construction_site() -> None:
    """Sensitivity proof: pinning a count is only worth something if a SECOND
    call in an already-justified file fails.

    Uses the real harness source plus one extra call, so this proves the actual
    scanned file would trip the ratchet — not that a synthetic string does.
    """
    rel_path = "dotmac_kernel/testing/harness.py"
    harness = _resolve(rel_path).read_text()
    pinned = _JUSTIFIED_CALLS[rel_path]
    assert (
        len(find_session_authority_violations(rel_path, harness)) == pinned
    ), "the pin no longer matches the real file — fix _JUSTIFIED_CALLS"

    smuggled = harness + "\n\n_extra = sessionmaker(bind=None)\n"
    found = find_session_authority_violations(rel_path, smuggled)
    assert len(found) == pinned + 1, found


def test_checker_flags_a_violation() -> None:
    """Sensitivity self-test: the checker must actually bite. A synthetic
    feature module that builds its own session/factory is flagged on every
    forbidden form; a compliant module is not."""
    bad = (
        "from sqlalchemy.orm import Session, sessionmaker\n"
        "from dotmac_kernel.db import SessionLocal, engine\n"
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


def _resolve(rel_path: str) -> Path:
    """Map a `<prefix>/…` rel path back to its file under the scanned trees."""
    for root, prefix in _SCANNED_TREES:
        if rel_path.startswith(f"{prefix}/"):
            return root / rel_path[len(prefix) + 1 :]
    raise AssertionError(f"unknown tree for {rel_path}")


def test_every_justified_file_still_exists() -> None:
    """A pin naming a file that is gone is an exemption nobody can evaluate."""
    for rel_path in _JUSTIFIED_CALLS:
        assert _resolve(rel_path).is_file(), (
            f"{rel_path} is pinned in _JUSTIFIED_CALLS but does not exist — "
            "delete the entry"
        )
