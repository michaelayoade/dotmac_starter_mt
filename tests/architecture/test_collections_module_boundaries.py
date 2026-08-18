"""Sensitivity-proven architecture gate for ``dotmac-collections``.

The live package assertion intentionally starts RED while the active Billing
worktree owns the shared allocation surfaces.  The scanner is independently
testable against planted package trees, so a missing package cannot masquerade
as a clean result.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "packages/dotmac-collections/src/dotmac_collections"

ALLOWED_DOTMAC_IMPORTS = frozenset({"dotmac_collections", "dotmac_kernel"})
FORBIDDEN_IO_IMPORTS = frozenset(
    {
        "apscheduler",
        "boto3",
        "celery",
        "httpx",
        "requests",
        "sched",
        "stripe",
        "threading",
    }
)
AMBIENT_CLOCK_CALLS = frozenset(
    {
        "date.today",
        "datetime.date.today",
        "datetime.now",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.utcnow",
        "time.monotonic",
        "time.time",
    }
)
TIME_QUANTITY_TOKENS = (
    "age",
    "days",
    "elapsed",
    "hours",
    "offset",
    "overdue",
)
MONEY_NAME_TOKENS = (
    "amount",
    "fee",
    "floor",
    "minimum",
    "threshold",
    "tolerance",
)
SWEEP_NAME_TOKENS = (
    "fire_due",
    "scan_due",
    "scanner",
    "sweep",
)


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _imported_roots(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    return (node.module or "",)


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = f"{node.module}.{alias.name}"
    return aliases


def _canonical_name(dotted: str, aliases: dict[str, str]) -> str:
    head, separator, tail = dotted.partition(".")
    target = aliases.get(head, head)
    return f"{target}.{tail}" if separator else target


def _forbidden_import(module_name: str) -> bool:
    root = module_name.split(".", 1)[0]
    if root == "app":
        return True
    if root.startswith("dotmac_") and root not in ALLOWED_DOTMAC_IMPORTS:
        return True
    return root in FORBIDDEN_IO_IMPORTS


def _timing_specific(identifier: str) -> bool:
    lowered = identifier.lower()
    if lowered == "collection_timing":
        return False
    return (
        "prepaid" in lowered
        or "postpaid" in lowered
        or lowered.startswith(("advance_", "arrears_"))
        or lowered.endswith(("_advance", "_arrears"))
    )


def _numeric_constant(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _nonzero_numeric_constant(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value != 0
    )


def _assigned_names(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
) -> tuple[str, ...]:
    targets: tuple[ast.expr, ...]
    if isinstance(node, ast.Assign):
        targets = tuple(node.targets)
    else:
        targets = (node.target,)
    return tuple(name for target in targets if (name := _dotted_name(target)))


def _issue(relative: str, node: ast.AST, kind: str, detail: str) -> str:
    return f"{relative}:{getattr(node, 'lineno', 0)}:{kind}:{detail}"


def scan_collections_boundaries(package_root: Path) -> tuple[str, ...]:
    """Return stable violations; absence is a violation, never a clean scan."""

    if not package_root.is_dir():
        return ("package-missing:packages/dotmac-collections/src/dotmac_collections",)

    issues: set[str] = set()
    paths = sorted(path for path in package_root.rglob("*.py") if path.is_file())
    if not paths:
        return ("package-empty:packages/dotmac-collections/src/dotmac_collections",)

    for path in paths:
        relative = path.relative_to(package_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for imported in _imported_roots(node):
                    if _forbidden_import(imported):
                        issues.add(_issue(relative, node, "forbidden-import", imported))

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if _timing_specific(node.name):
                    issues.add(
                        _issue(relative, node, "timing-specific-symbol", node.name)
                    )
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lowered = node.name.lower()
                    if any(token in lowered for token in SWEEP_NAME_TOKENS):
                        issues.add(_issue(relative, node, "sweep-owner", node.name))
                    for argument in (*node.args.posonlyargs, *node.args.args):
                        if _timing_specific(argument.arg):
                            issues.add(
                                _issue(
                                    relative,
                                    argument,
                                    "timing-specific-symbol",
                                    argument.arg,
                                )
                            )

            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for name in _assigned_names(node):
                    leaf = name.rsplit(".", 1)[-1]
                    if _timing_specific(leaf):
                        issues.add(
                            _issue(relative, node, "timing-specific-symbol", leaf)
                        )
                    value = node.value
                    if (
                        value is not None
                        and _numeric_constant(value)
                        and any(token in leaf.lower() for token in MONEY_NAME_TOKENS)
                    ):
                        issues.add(
                            _issue(relative, node, "hardcoded-money-threshold", leaf)
                        )

            if isinstance(node, ast.Call):
                callee = _canonical_name(_dotted_name(node.func), aliases)
                if callee in AMBIENT_CLOCK_CALLS:
                    issues.add(_issue(relative, node, "ambient-clock", callee))
                if callee.endswith(".sleep") or callee == "sleep":
                    issues.add(_issue(relative, node, "scheduler-sleep", callee))
                if callee in {"Decimal", "Money"} or callee.endswith(
                    (".Decimal", ".Money", "Money.of")
                ):
                    if any(
                        isinstance(argument, ast.Constant) for argument in node.args
                    ):
                        issues.add(_issue(relative, node, "hardcoded-money", callee))

            if isinstance(node, ast.Compare):
                expressions = (node.left, *node.comparators)
                names = tuple(
                    _dotted_name(expression).lower() for expression in expressions
                )
                if any(
                    token in name for name in names for token in TIME_QUANTITY_TOKENS
                ) and any(
                    _nonzero_numeric_constant(expression) for expression in expressions
                ):
                    issues.add(
                        _issue(relative, node, "hardcoded-time-threshold", "compare")
                    )

            if isinstance(node, ast.While):
                if isinstance(node.test, ast.Constant) and node.test.value is True:
                    issues.add(_issue(relative, node, "scheduler-loop", "while-true"))

    return tuple(sorted(issues))


def _write_package(tmp_path: Path, source: str) -> Path:
    package = tmp_path / "dotmac_collections"
    package.mkdir()
    (package / "probe.py").write_text(source, encoding="utf-8")
    return package


def test_collections_package_exists_and_has_no_forbidden_boundary() -> None:
    assert scan_collections_boundaries(PACKAGE_ROOT) == ()


@pytest.mark.parametrize(
    ("source", "expected_kind"),
    [
        ("from app.models import Invoice\n", "forbidden-import:app.models"),
        ("import dotmac_billing\n", "forbidden-import:dotmac_billing"),
        ("import dotmac_durable_timers\n", "forbidden-import:dotmac_durable_timers"),
        ("import requests\n", "forbidden-import:requests"),
        (
            "from datetime import datetime\nNOW = datetime.now()\n",
            "ambient-clock:datetime.datetime.now",
        ),
        (
            "import datetime as clock\nNOW = clock.datetime.now()\n",
            "ambient-clock:datetime.datetime.now",
        ),
        (
            "def plan_prepaid_consequence():\n    return None\n",
            "timing-specific-symbol:plan_prepaid_consequence",
        ),
        (
            "def run_due_cases_sweep():\n    while True:\n        sleep(1)\n",
            "sweep-owner:run_due_cases_sweep",
        ),
        (
            "from decimal import Decimal\nFLOOR = Decimal('0.01')\n",
            "hardcoded-money:decimal.Decimal",
        ),
        (
            "from time import sleep as wait\ndef retry():\n    wait(1)\n",
            "scheduler-sleep:time.sleep",
        ),
        (
            "def due(days_overdue):\n    return days_overdue > 30\n",
            "hardcoded-time-threshold:compare",
        ),
    ],
)
def test_boundary_scanner_detects_planted_violations(
    tmp_path: Path,
    source: str,
    expected_kind: str,
) -> None:
    issues = scan_collections_boundaries(_write_package(tmp_path, source))
    assert any(expected_kind in issue for issue in issues), issues


def test_boundary_scanner_accepts_injected_time_and_published_kernel_seams(
    tmp_path: Path,
) -> None:
    package = _write_package(
        tmp_path,
        """
from datetime import datetime

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

def assess(
    scope: Scope,
    position: Money,
    as_of: datetime,
) -> tuple[Scope, Money, datetime]:
    return scope, position, as_of
""".lstrip(),
    )
    assert scan_collections_boundaries(package) == ()
