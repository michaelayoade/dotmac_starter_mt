"""`conflict_savepoint` is reached through its engine-free public owner.

`dotmac_kernel.db` re-exports `conflict_savepoint` for backward
compatibility, but that module builds the reference assembly's engines
eagerly at import (`DatabaseRuntime.from_urls(...)` at module scope). Because
`app/assembly.py` constructs its `ProductAssemblySpec` at module level,
`load_manifests` imports every feature/package service module the instant
`app.assembly` is imported — well before `create_app` runs. A module-scope
`from dotmac_kernel.db import conflict_savepoint` anywhere reachable from
that import graph fires the eager engine construction early enough to defeat
a `require_database_runtime=True` probe checking the reference runtime is
reachable.

`dotmac_kernel.transactions` (`__all__ = ["conflict_savepoint"]`, re-exporting
from the internal `dotmac_kernel._transactions`) is the engine-free public
owner and the one sanctioned import source (see hard rule 9 in `AGENTS.md`
and `tests/architecture/test_no_feature_rollback.py`). This guard is
source-derived — an AST sweep over `app/` and every installable package under
`packages/` — rather than a hardcoded file list, so a new file that
regresses is caught without anyone remembering to add it to a list.

`dotmac_kernel/db.py` itself is exempt: it is the module that DEFINES the
compatibility re-export (`from dotmac_kernel._transactions import
conflict_savepoint`), not a caller reaching for it through `dotmac_kernel.db`.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

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


def test_no_conflict_savepoint_import_via_the_eager_db_module() -> None:
    violations: list[str] = []
    for subroot in (PROJECT_ROOT / "app", PROJECT_ROOT / "packages"):
        violations.extend(find_forbidden_imports(subroot))
    assert not violations, (
        "conflict_savepoint must be imported from dotmac_kernel.transactions "
        "(the engine-free public owner), never from dotmac_kernel.db (which "
        "builds engines eagerly at import):\n" + "\n".join(violations)
    )


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
