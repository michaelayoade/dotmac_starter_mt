"""AST fingerprints for the Foundation operator-job additivity guard (T14).

DESIGN (S2) invariant I-1: the deploy, rollback, recovery, support-job, render
and existing spec-parse code paths stay byte-identical when the operator-job
entry point lands. "Byte-identical" is checked on the PARSED tree, so comments
and formatting cannot hide or fake a change, and a changed statement cannot hide
inside an unchanged file name.

This module is import-light on purpose (stdlib only) so the committed baseline
can be regenerated from a pre-change commit with plain `python3 -I`:

    git show <base>:<path> | ...   # see `fingerprint_tree`

Three shapes:

* **whole-file** - the file must not change at all (executor, compose host,
  recovery, render, execution plan, authorization, bindings).
* **plan.py** - one `StepKind` member (`OPERATOR_JOB`) may be added; nothing
  else may change.
* **spec.py** - every top-level node except `ComposeJob` must be unchanged;
  `ComposeJob` is compared after removing exactly the additive operator
  pieces; the only new top-level names allowed are `_NEW_SPEC_NODES`.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

#: Files that must be byte-identical in the parsed tree.
WHOLE_FILE_GLOBS: tuple[str, ...] = (
    "engine/run.py",
    "engine/lock.py",
    "providers/compose_host.py",
    "providers/exposure_host.py",
    "recovery*.py",
    "render/*.py",
    "execution_plan*.py",
    "authorization.py",
    "authorization_v3.py",
    "execution_bindings.py",
    "toolchain.py",
    "errors.py",
)

#: The one StepKind member DESIGN section 9 allows to be added.
ADDED_STEP_KIND = "OPERATOR_JOB"

#: Top-level names the operator change may ADD to spec.py. A new name outside
#: this set fails the guard, so the additive surface cannot quietly widen.
_NEW_SPEC_NODES: frozenset[str] = frozenset({"OperatorJob", "_parse_operator_job"})

#: Identifier prefix marking an additive operator statement inside
#: `ComposeJob.parse` (DESIGN section 2: "the one optional field").
_OPERATOR_PREFIX = "operator"


def _digest(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node).encode("utf-8")).hexdigest()


def whole_file_hash(source: str) -> str:
    return _digest(ast.parse(source))


def plan_hash(source: str) -> str:
    """Hash of engine/plan.py with the one added StepKind member removed."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "StepKind":
            node.body = [
                stmt for stmt in node.body if not _assigns_name(stmt, ADDED_STEP_KIND)
            ]
    return _digest(tree)


def _assigns_name(stmt: ast.stmt, name: str) -> bool:
    if isinstance(stmt, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
    if isinstance(stmt, ast.AnnAssign):
        return isinstance(stmt.target, ast.Name) and stmt.target.id == name
    return False


def _top_key(node: ast.stmt, index: int) -> str:
    if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return f"{type(node).__name__}:{node.name}"
    if isinstance(node, ast.Assign):
        names = ",".join(
            t.id if isinstance(t, ast.Name) else ast.dump(t) for t in node.targets
        )
        return f"Assign:{names}"
    if isinstance(node, ast.AnnAssign):
        target = node.target
        label = target.id if isinstance(target, ast.Name) else ast.dump(target)
        return f"AnnAssign:{label}"
    return f"{type(node).__name__}:{index}"


def _mentions_operator(stmt: ast.stmt) -> bool:
    """Whether a statement is an additive operator statement.

    True only when the statement ASSIGNS a name starting with the operator
    prefix. Reads of such names elsewhere would not qualify, so the removal
    cannot swallow an unrelated existing statement.
    """
    targets: list[ast.expr] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
    return any(
        isinstance(t, ast.Name) and t.id.startswith(_OPERATOR_PREFIX) for t in targets
    )


def _strip_operator_keyword(stmt: ast.stmt) -> ast.stmt:
    """Drop `operator=...` keyword arguments from `cls(...)` calls."""
    stmt = copy.deepcopy(stmt)
    for node in ast.walk(stmt):
        if isinstance(node, ast.Call):
            node.keywords = [k for k in node.keywords if k.arg != "operator"]
    return stmt


def compose_job_hash(node: ast.ClassDef) -> str:
    """ComposeJob with only the additive operator pieces removed."""
    node = copy.deepcopy(node)
    body: list[ast.stmt] = []
    for stmt in node.body:
        if _assigns_name(stmt, "operator"):
            continue
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "parse":
            stmt.body = [
                _strip_operator_keyword(inner)
                for inner in stmt.body
                if not _mentions_operator(inner)
            ]
        body.append(stmt)
    node.body = body
    return _digest(node)


def spec_fingerprint(source: str) -> dict[str, object]:
    tree = ast.parse(source)
    nodes: dict[str, str] = {}
    compose_job = ""
    for index, node in enumerate(tree.body):
        key = _top_key(node, index)
        if isinstance(node, ast.ClassDef) and node.name == "ComposeJob":
            compose_job = compose_job_hash(node)
            continue
        if key in nodes:
            key = f"{key}#{index}"
        nodes[key] = _digest(node)
    return {"nodes": nodes, "compose_job": compose_job}


def spec_new_nodes(source: str) -> set[str]:
    """Top-level definition names present in `source` (for the allow-list)."""
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
    return names


def fingerprint_tree(src: Path) -> dict[str, object]:
    """The whole guard payload for the package source directory `src`."""
    whole: dict[str, str] = {}
    for pattern in WHOLE_FILE_GLOBS:
        for path in sorted(src.glob(pattern)):
            whole[str(path.relative_to(src))] = whole_file_hash(
                path.read_text(encoding="utf-8")
            )
    return {
        "whole_file": whole,
        "plan_py": plan_hash((src / "engine/plan.py").read_text(encoding="utf-8")),
        "spec_py": spec_fingerprint((src / "spec.py").read_text(encoding="utf-8")),
        "spec_definitions": sorted(
            spec_new_nodes((src / "spec.py").read_text(encoding="utf-8"))
        ),
    }
