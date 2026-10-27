"""Every audit action Learning writes is declared on its manifest.

The hosted bank-lifecycle canaries failed with
``UndeclaredAuditActionError`` because nothing local proved the
write-site literals and the manifest declarations agree: the registry an
adopter composes from ``dotmac_learning.manifest.module`` must accept
every action the package can pass to ``write_audit_event``. The scan is
AST-based over the installed package source so a new write site that
invents an action fails here, in the unit lane, not on hosted PG.
"""

import ast
import importlib.util
from pathlib import Path

from dotmac_kernel.audit_actions import AuditActionRegistry
from dotmac_learning.manifest import module


def _package_sources() -> list[Path]:
    spec = importlib.util.find_spec("dotmac_learning")
    assert spec is not None and spec.submodule_search_locations is not None
    return sorted(
        path
        for root in spec.submodule_search_locations
        for path in Path(root).rglob("*.py")
    )


def _guarded_values(function: ast.FunctionDef, name: str) -> set[str]:
    """String literals ``name`` is compared against with ``==`` in ``function``."""
    values: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq):
            continue
        operands = [node.left, *node.comparators]
        if not any(isinstance(o, ast.Name) and o.id == name for o in operands):
            continue
        values.update(
            o.value
            for o in operands
            if isinstance(o, ast.Constant) and isinstance(o.value, str)
        )
    return values


def _written_actions() -> set[str]:
    written: set[str] = set()
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def enclosing_function(
            node: ast.AST, parents: dict[ast.AST, ast.AST] = parents
        ) -> ast.FunctionDef | None:
            current: ast.AST | None = node
            while current is not None:
                if isinstance(current, ast.FunctionDef):
                    return current
                current = parents.get(current)
            return None

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "action":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    written.add(value.value)
                elif isinstance(value, ast.JoinedStr):
                    # Dynamic action (e.g. f"success_queue.{action}"): the
                    # interpolated name must be pinned by == guards in the
                    # same function, else the written set is unknowable.
                    parts = value.values
                    assert (
                        len(parts) == 2
                        and isinstance(parts[0], ast.Constant)
                        and isinstance(parts[0].value, str)
                        and isinstance(parts[1], ast.FormattedValue)
                        and isinstance(parts[1].value, ast.Name)
                    ), (
                        f"{path}:{value.lineno}: unresolvable dynamic audit "
                        "action; write a literal or a guarded f-string prefix"
                    )
                    function = enclosing_function(value)
                    assert function is not None
                    guarded = _guarded_values(function, parts[1].value.id)
                    assert guarded, (
                        f"{path}:{value.lineno}: dynamic audit action "
                        f"{parts[1].value.id!r} has no == guards to expand"
                    )
                    written.update(f"{parts[0].value}{name}" for name in guarded)
    return written


def test_every_written_audit_action_is_declared_on_the_manifest() -> None:
    written = _written_actions()
    # Scan sanity: the bank lifecycle alone writes ten question_bank.*
    # actions; an empty or partial scan must not pass vacuously.
    assert {a for a in written if a.startswith("question_bank.")} >= {
        "question_bank.draft_created",
        "question_bank.imported",
    }
    declared = set(module.audit_actions)
    undeclared = sorted(written - declared)
    assert not undeclared, (
        "Audit action(s) written by dotmac_learning but not declared on "
        f"its manifest: {undeclared}. Declare each on "
        "dotmac_learning.manifest.module (audit_actions=(...))."
    )
    registry = AuditActionRegistry.from_manifests([module])
    for action in sorted(written):
        registry.require(action)
