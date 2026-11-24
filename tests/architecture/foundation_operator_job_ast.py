"""AST fingerprints for the Foundation operator-job additivity guard (T14).

DESIGN (S2) invariant I-1: the deploy, rollback, recovery, support-job, render
and existing spec-parse code paths stay byte-identical when the operator-job
entry point lands. "Byte-identical" is checked on the PARSED tree, so comments
and formatting cannot hide or fake a change, and a changed statement cannot hide
inside an unchanged file name.

This module is import-light on purpose (stdlib only) so the committed baseline
can be regenerated from a pre-change commit with plain `python3 -I`:

    git show <base>:<path> | ...   # see `fingerprint_tree`

The guard is INVERTED: every existing `.py` under the package is whole-file
hashed EXCEPT the three files that must change, and a new module is refused
unless it is in `NEW_MODULES`. The three files get structured checks:

* **plan.py** - one `StepKind` member (`OPERATOR_JOB`) may be added.
* **spec.py** - every top-level node except `ComposeJob` is unchanged
  (`ProductDeploymentSpec` is compared after removing the `if` statements whose
  test reads an `.operator` attribute: the one schema-gate refusal);
  `ComposeJob` is compared after removing the additive operator pieces; only
  the allow-listed new nodes may appear.
* **cli.py** - every top-level definition except `build_parser` is unchanged;
  `build_parser` is compared after removing the statements that mention
  `run_job`; only `cmd_run_job` may be added.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

#: The three existing files that must change; each has a structured check.
STRUCTURED_FILES: tuple[str, ...] = ("spec.py", "engine/plan.py", "cli.py")

#: New modules the change may add (relative to the package source directory).
NEW_MODULES: frozenset[str] = frozenset(
    {
        "operator_job.py",
        "operator_job_contract.py",
        "providers/compose_operator_job.py",
    }
)

#: The one StepKind member DESIGN section 9 allows to be added.
ADDED_STEP_KIND = "OPERATOR_JOB"

#: Identifier prefix marking an additive operator statement inside
#: `ComposeJob.parse` (DESIGN section 2: "the one optional field").
_OPERATOR_PREFIX = "operator"

#: In cli.py: the only function that may change, the statement-level names that
#: mark its one registration, and the only functions that may be added.
CLI_CHANGED_FUNCTION = "build_parser"
CLI_REGISTRATION_NAMES: frozenset[str] = frozenset({"run_job"})
CLI_NEW_FUNCTIONS: frozenset[str] = frozenset({"cmd_run_job"})


def _digest(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node).encode("utf-8")).hexdigest()


def whole_file_hash(source: str) -> str:
    return _digest(ast.parse(source))


def _assigns_name(stmt: ast.stmt, name: str) -> bool:
    if isinstance(stmt, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
    if isinstance(stmt, ast.AnnAssign):
        return isinstance(stmt.target, ast.Name) and stmt.target.id == name
    return False


def plan_hash(source: str) -> str:
    """Hash of engine/plan.py with the one added StepKind member removed."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "StepKind":
            node.body = [
                stmt for stmt in node.body if not _assigns_name(stmt, ADDED_STEP_KIND)
            ]
    return _digest(tree)


def _top_key(node: ast.stmt, ordinal: int) -> str:
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
    return f"{type(node).__name__}:{ordinal}"


def _mentions_operator(stmt: ast.stmt) -> bool:
    """Whether a statement ASSIGNS a name starting with the operator prefix."""
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


def _node_hashes(tree: ast.Module, *, skip: frozenset[str]) -> dict[str, str]:
    nodes: dict[str, str] = {}
    seen: dict[str, int] = {}
    for node in tree.body:
        # Unnamed statements are keyed by their ordinal AMONG THEIR KIND, so
        # inserting a function earlier in the file does not rename them.
        ordinal = seen.get(type(node).__name__, 0)
        seen[type(node).__name__] = ordinal + 1
        key = _top_key(node, ordinal)
        if key.split(":", 1)[-1] in skip:
            continue
        if key in nodes:
            key = f"{key}#{ordinal}"
        nodes[key] = _digest(node)
    return nodes


class _StripOperatorGates(ast.NodeTransformer):
    """Remove `if <test reading .operator>:` statements (the schema gate)."""

    def visit_If(self, node: ast.If) -> ast.AST | None:
        if any(
            isinstance(inner, ast.Attribute) and inner.attr == _OPERATOR_PREFIX
            for inner in ast.walk(node.test)
        ):
            return None
        return self.generic_visit(node)


def spec_fingerprint(source: str) -> dict[str, object]:
    tree = ast.parse(source)
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.ClassDef) and node.name == "ProductDeploymentSpec":
            tree.body[index] = _StripOperatorGates().visit(node)
    compose_job = ""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ComposeJob":
            compose_job = compose_job_hash(node)
    return {
        "nodes": _node_hashes(tree, skip=frozenset({"ComposeJob"})),
        "compose_job": compose_job,
    }


def _mentions_registration(stmt: ast.stmt) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id in CLI_REGISTRATION_NAMES
        for node in ast.walk(stmt)
    )


def build_parser_hash(node: ast.FunctionDef) -> str:
    """build_parser with only the one registration block removed."""
    node = copy.deepcopy(node)
    node.body = [s for s in node.body if not _mentions_registration(s)]
    return _digest(node)


def cli_fingerprint(source: str) -> dict[str, object]:
    tree = ast.parse(source)
    parser = ""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == CLI_CHANGED_FUNCTION:
            parser = build_parser_hash(node)
    return {
        "nodes": _node_hashes(
            tree, skip=frozenset({CLI_CHANGED_FUNCTION}) | CLI_NEW_FUNCTIONS
        ),
        "build_parser": parser,
    }


def package_files(src: Path) -> list[str]:
    return sorted(str(p.relative_to(src)) for p in src.rglob("*.py"))


def fingerprint_tree(src: Path) -> dict[str, object]:
    """The whole guard payload for the package source directory `src`."""
    whole: dict[str, str] = {}
    for relative in package_files(src):
        if relative in STRUCTURED_FILES or relative in NEW_MODULES:
            continue
        whole[relative] = whole_file_hash((src / relative).read_text(encoding="utf-8"))
    read = lambda name: (src / name).read_text(encoding="utf-8")  # noqa: E731
    return {
        "whole_file": whole,
        "plan_py": plan_hash(read("engine/plan.py")),
        "spec_py": spec_fingerprint(read("spec.py")),
        "cli_py": cli_fingerprint(read("cli.py")),
    }
