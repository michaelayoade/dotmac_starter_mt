"""`conflict_savepoint` is reached through its engine-free public owner —
scoped to what `app/assembly.py` actually composes, with the rest tracked as
a two-directional ratchet.

`dotmac_kernel.db` re-exports `conflict_savepoint` for backward
compatibility, but that module builds the reference assembly's engines
eagerly at import (`DatabaseRuntime.from_urls(...)` at module scope). Because
`app/assembly.py` constructs its `ProductAssemblySpec` at module level,
`load_manifests(FEATURE_MODULES)` imports every registered feature's
`service.py` the instant `app.assembly` is imported — well before
`create_app` runs. A module-scope `from dotmac_kernel.db import
conflict_savepoint` anywhere in that reachable graph fires the eager engine
construction early enough to defeat a `require_database_runtime=True` probe
checking the reference runtime is reachable.

`FEATURE_MODULES` (`app/features/__init__.py`) names zero `dotmac_` packages
— only `app/features/*` modules. That means the STRICT, zero-tolerance guard
below is scoped to `app/`: that is the entire reachable set a bare `import
app.assembly` walks. The 41 installable packages under `packages/` sit
outside that reachable graph; migrating their `src/` would make published,
tagged distributions ship bytes differing from their tags for an import-source
change that does not touch Starter's own composition. **This file (and the
predecessor PR, #678) closes Starter's eager-import chain — it is NOT a
fleet-wide migration of every package's `conflict_savepoint` import, and a
steady or shrinking package-backlog total must not be read as one.** Each
package's own migration is tracked instead as a two-directional ratchet
against `conflict_savepoint_package_backlog_baseline.json`, with one
retirement condition for every entry: migrate a package's imports when that
distribution next takes a legitimate version transition anyway, so the fix
is paid for out of a version bump already happening rather than costing its
own standalone `+dev` marker and publication-ledger row.

`dotmac_kernel.transactions` (`__all__ = ["conflict_savepoint"]`, re-exporting
from the internal `dotmac_kernel._transactions`) is the engine-free public
owner and the one sanctioned import source regardless of scope (see hard rule
9 in `AGENTS.md` and `tests/architecture/test_no_feature_rollback.py`). Every
scan below is source-derived — an AST sweep, not a hardcoded file list — so a
new or changed file is caught without anyone remembering to update a list.

`dotmac_kernel/db.py` itself is exempt from both the strict guard and the
backlog scan: it is the module that DEFINES the compatibility re-export
(`from dotmac_kernel._transactions import conflict_savepoint`), not a caller
reaching for it through `dotmac_kernel.db`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = (
    Path(__file__).parent / "conflict_savepoint_package_backlog_baseline.json"
)

#: The one file allowed to import `conflict_savepoint` from
#: `dotmac_kernel._transactions` under the `dotmac_kernel.db` name — it is the
#: module defining that compatibility re-export, not a caller of it.
DEFINING_MODULE = (
    PROJECT_ROOT / "packages" / "dotmac-kernel" / "src" / "dotmac_kernel" / "db.py"
)


def find_forbidden_imports(root: Path) -> list[str]:
    """Return `path:lineno` for every `from dotmac_kernel.db import
    conflict_savepoint` reachable via a real AST `ImportFrom` node under
    `root`. Deliberately AST-based, not a text/regex search, so a comment or
    docstring that merely NAMES the forbidden spelling does not trip it.
    """
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.resolve() == DEFINING_MODULE.resolve():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "dotmac_kernel.db":
                continue
            if any(alias.name == "conflict_savepoint" for alias in node.names):
                try:
                    display = path.relative_to(PROJECT_ROOT)
                except ValueError:
                    display = path
                violations.append(f"{display}:{node.lineno}")
    return violations


def test_no_conflict_savepoint_import_via_the_eager_db_module_in_app() -> None:
    """Strict, zero-tolerance: `app/` is the entire graph `app.assembly`'s
    module-level `load_manifests(FEATURE_MODULES)` actually reaches, so
    nothing here gets a backlog allowance."""
    violations = find_forbidden_imports(PROJECT_ROOT / "app")
    assert not violations, (
        "conflict_savepoint must be imported from dotmac_kernel.transactions "
        "(the engine-free public owner), never from dotmac_kernel.db (which "
        "builds engines eagerly at import) — app/ is on app.assembly's "
        "module-level import graph, so there is no backlog allowance here:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Package backlog: two-directional ratchet, not a strict gate.
#
# app/assembly.py's load_manifests(FEATURE_MODULES) does not reach any
# dotmac_ package, so a package importing conflict_savepoint from
# dotmac_kernel.db does not defeat Starter's own require_database_runtime
# probe. It is still debt (a product that installs the package and imports
# it eagerly inherits the same eager-engine problem) — tracked site-by-site
# rather than migrated here.
# ---------------------------------------------------------------------------


def scan_package_backlog() -> dict[str, int]:
    """`{relative_path: site_count}` for every `packages/` file still
    importing `conflict_savepoint` from `dotmac_kernel.db`. Counting sites
    (not just listing filenames) means a file gaining a SECOND forbidden
    import is visible as a count change, not hidden behind an unchanged
    filename.
    """
    counts: dict[str, int] = {}
    for violation in find_forbidden_imports(PROJECT_ROOT / "packages"):
        path = violation.rsplit(":", 1)[0]
        counts[path] = counts.get(path, 0) + 1
    return counts


def load_package_backlog_baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _describe_drift(live: dict[str, int], recorded: dict[str, int]) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(live) | set(recorded)):
        here, there = live.get(name, 0), recorded.get(name, 0)
        if here > there:
            problems.append(f"{name}: rose {there} -> {here} (new forbidden import)")
        elif here < there:
            problems.append(f"{name}: fell {there} -> {here} (lower the baseline)")
    return problems


def test_package_backlog_matches_the_frozen_ratchet() -> None:
    """Two-directional: fails if any package's site count rises (a NEW
    reach into the eager module) or falls (a package migrated without the
    baseline being regenerated in the same change) — see the module
    docstring for the retirement condition and the explicit scope statement
    this ratchet is NOT a completed fleet-wide migration.
    """
    recorded = load_package_backlog_baseline()["files"]
    assert isinstance(recorded, dict)
    problems = _describe_drift(scan_package_backlog(), recorded)
    assert not problems, (
        "the conflict_savepoint package backlog drifted from its frozen "
        "baseline (tests/architecture/conflict_savepoint_package_backlog_"
        "baseline.json):\n"
        + "\n".join(f"  {line}" for line in problems)
        + "\n\nA package migrates its own imports to "
        "dotmac_kernel.transactions.conflict_savepoint when it next takes a "
        "legitimate version transition, then lowers this baseline in that "
        "same change. Regenerate with generate_package_backlog_baseline() "
        "in this module."
    )


def test_package_backlog_baseline_total_agrees_with_its_entries() -> None:
    baseline = load_package_backlog_baseline()
    recorded: dict[str, int] = baseline["files"]  # type: ignore[assignment]
    assert baseline["total"] == sum(recorded.values()), (
        "baseline 'total' does not equal the sum of its entries; regenerate "
        "with generate_package_backlog_baseline() rather than editing it by "
        "hand"
    )


def test_package_backlog_baseline_states_the_narrowed_scope() -> None:
    """A reader of the baseline file alone (not this module) must not
    mistake the ratchet for a completed migration — the brief's explicit
    requirement. Pin the load-bearing phrases inside the baseline's own
    `_comment`."""
    comment = str(load_package_backlog_baseline()["_comment"])
    assert "does not migrate it" in comment
    assert "not fleet-wide" in comment
    assert "takes a legitimate version transition" in comment


def generate_package_backlog_baseline() -> None:
    """Regenerate the frozen baseline from the live tree. Not a test — call
    this (e.g. from a `python -c` one-liner) after a package's imports
    genuinely change, then review the diff."""
    counts = scan_package_backlog()
    data = {
        "_comment": (
            "Frozen backlog of conflict_savepoint imports still reaching the "
            "eager dotmac_kernel.db module, package by package, site by site. "
            "#678 closes the eager-import chain app/assembly.py's "
            "load_manifests(FEATURE_MODULES) actually reaches -- the five "
            "app/features/*/service.py imports. FEATURE_MODULES names zero "
            "dotmac_ packages, so this backlog is NOT part of that chain and "
            "#678 does not migrate it. #678 closes Starter's eager-import "
            "chain, not fleet-wide package debt -- do not read a shrinking or "
            "steady total here as a completed migration. Retirement "
            "condition, one rule for every entry: migrate a package's "
            "imports (to dotmac_kernel.transactions.conflict_savepoint) the "
            "next time that distribution takes a legitimate version "
            "transition anyway, so this costs zero standalone +dev markers "
            "or ledger rows. Two-directional ratchet: fails if a file's "
            "count rises OR falls without this baseline being regenerated in "
            "the same change. Regenerate with "
            "generate_package_backlog_baseline() in this module; do not "
            "hand-edit."
        ),
        "total": sum(counts.values()),
        "files": counts,
    }
    BASELINE_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Sensitivity proof, shared by both the strict app/ guard and the backlog
# scanner — both are built on find_forbidden_imports.
# ---------------------------------------------------------------------------


def test_guard_bites_a_planted_forbidden_import(tmp_path: Path) -> None:
    """Sensitivity plant: an actual forbidden import is named as a violation."""
    planted = tmp_path / "planted_offender.py"
    planted.write_text(
        "from dotmac_kernel.db import conflict_savepoint\n\n"
        "def use(db):\n"
        "    with conflict_savepoint(db):\n"
        "        db.flush()\n",
        encoding="utf-8",
    )
    violations = find_forbidden_imports(tmp_path)
    assert violations == [f"{planted}:1"]


def test_guard_does_not_bite_a_near_miss_comment_or_correct_import(
    tmp_path: Path,
) -> None:
    """Near miss: a comment/docstring naming the forbidden spelling, plus the
    correct import from `dotmac_kernel.transactions`, must not trip the guard
    — proving it reads the AST, not the text."""
    near_miss = tmp_path / "near_miss.py"
    near_miss.write_text(
        '"""Do not `from dotmac_kernel.db import conflict_savepoint` here."""\n'
        "\n"
        "# from dotmac_kernel.db import conflict_savepoint (forbidden, don't)\n"
        "from dotmac_kernel.transactions import conflict_savepoint\n"
        "\n"
        "def use(db):\n"
        "    with conflict_savepoint(db):\n"
        "        db.flush()\n",
        encoding="utf-8",
    )
    assert find_forbidden_imports(tmp_path) == []


def test_guard_exempts_only_the_defining_module() -> None:
    """`dotmac_kernel/db.py` itself defines the compatibility re-export via
    `dotmac_kernel._transactions`, not `dotmac_kernel.db` — so the exemption
    never actually needs to fire, and the real sweep still passes it in."""
    assert DEFINING_MODULE.exists()
    text = DEFINING_MODULE.read_text(encoding="utf-8")
    assert "from dotmac_kernel._transactions import conflict_savepoint" in text
    assert "from dotmac_kernel.db import conflict_savepoint" not in text


def test_backlog_ratchet_bites_a_planted_rise() -> None:
    """Sensitivity plant for the ratchet direction that matters most: a NEW
    site appearing in a package must be reported as a rise, not silently
    absorbed."""
    live = {"packages/dotmac-example/src/dotmac_example/service.py": 2}
    recorded = {"packages/dotmac-example/src/dotmac_example/service.py": 1}
    problems = _describe_drift(live, recorded)
    assert problems == [
        "packages/dotmac-example/src/dotmac_example/service.py: rose 1 -> 2 "
        "(new forbidden import)"
    ]


def test_backlog_ratchet_bites_a_planted_fall() -> None:
    """A file that genuinely migrated (falls to 0, or drops out of the live
    scan entirely) must still be reported — the fix is to regenerate the
    baseline in the same change, not to let it pass silently."""
    live: dict[str, int] = {}
    recorded = {"packages/dotmac-example/src/dotmac_example/service.py": 3}
    problems = _describe_drift(live, recorded)
    assert problems == [
        "packages/dotmac-example/src/dotmac_example/service.py: fell 3 -> 0 "
        "(lower the baseline)"
    ]


def test_backlog_ratchet_near_miss_unchanged_counts_do_not_trip() -> None:
    """Near miss: identical live and recorded counts must not be reported —
    proving the drift check is not merely "any entry present"."""
    live = {"packages/dotmac-example/src/dotmac_example/service.py": 5}
    recorded = dict(live)
    assert _describe_drift(live, recorded) == []
