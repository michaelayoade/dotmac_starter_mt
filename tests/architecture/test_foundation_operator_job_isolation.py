"""T14: the operator-job entry point is additive; deploy bytes are unchanged.

DESIGN S2 invariant I-1. The baseline JSON was taken by
`foundation_operator_job_ast.fingerprint_tree` at the pre-change commit
(`base_commit` inside it). It hashes the PARSED tree, so formatting cannot fake
or hide a change. Sensitivity is proved on in-memory source, never on a plant
committed to disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.architecture import foundation_operator_job_ast as ast_guard

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
BASELINE = json.loads(
    (Path(__file__).parent / "foundation_operator_job_baseline.json").read_text(
        encoding="utf-8"
    )
)

#: The only top-level names the change may ADD to spec.py (keys as `_top_key`).
ALLOWED_NEW_SPEC_KEYS = {
    "Assign:_OPERATOR_COMMAND_NAME",
    "AnnAssign:OPERATOR_INPUTS_TARGET",
    "AnnAssign:OPERATOR_OUTPUTS_TARGET",
    "AnnAssign:OPERATOR_MAX_TIMEOUT_SECONDS",
    "ClassDef:OperatorJob",
    "FunctionDef:_parse_operator_job",
}


def test_t14_whole_files_are_byte_identical_to_the_baseline() -> None:
    current = ast_guard.fingerprint_tree(SRC)
    assert BASELINE["whole_file"], "baseline is empty; this would prove nothing"
    assert current["whole_file"] == BASELINE["whole_file"], (
        "a guarded file changed: engine/run.py, providers/compose_host.py, "
        "recovery*, render/*, execution_plan*, authorization*, bindings, "
        "toolchain or errors. The operator path must stay additive."
    )


def test_t14_plan_py_differs_only_by_the_one_step_kind_member() -> None:
    current = ast_guard.fingerprint_tree(SRC)
    assert current["plan_py"] == BASELINE["plan_py"]
    source = (SRC / "engine/plan.py").read_text(encoding="utf-8")
    assert 'OPERATOR_JOB = "operator_job"' in source


def test_t14_spec_py_existing_definitions_are_unchanged() -> None:
    current = ast_guard.fingerprint_tree(SRC)["spec_py"]
    baseline = BASELINE["spec_py"]
    changed = [
        key
        for key, digest in baseline["nodes"].items()
        if current["nodes"].get(key) != digest
    ]
    assert changed == []
    added = set(current["nodes"]) - set(baseline["nodes"])
    assert added == ALLOWED_NEW_SPEC_KEYS
    assert current["compose_job"] == baseline["compose_job"], (
        "ComposeJob changed beyond the one optional `operator` field and its "
        "parse statements"
    )


# ── sensitivity, on in-memory source ────────────────────────────────────────


def test_the_guard_bites_a_changed_executor_statement() -> None:
    before = "def run():\n    a()\n    b()\n"
    after = "def run():\n    a()\n    b()\n    c()\n"
    assert ast_guard.whole_file_hash(before) != ast_guard.whole_file_hash(after)
    assert ast_guard.whole_file_hash(before) == ast_guard.whole_file_hash(
        "def run():\n    # comment\n    a()\n\n    b()\n"
    )


def test_the_guard_bites_a_second_step_kind_member() -> None:
    base = "class StepKind:\n    A = 1\n"
    with_one = base + '    OPERATOR_JOB = "operator_job"\n'
    with_two = with_one + '    OTHER = "x"\n'
    assert ast_guard.plan_hash(base) == ast_guard.plan_hash(with_one)
    assert ast_guard.plan_hash(base) != ast_guard.plan_hash(with_two)


_COMPOSE = (
    "class ComposeJob:\n"
    "    code: str\n"
    "{field}"
    "    @classmethod\n"
    "    def parse(cls, table):\n"
    "        code = 1\n"
    "{extra}"
    "        return cls(code=code{kw})\n"
)


def _compose(*, operator: bool, tamper: bool = False) -> str:
    field = "    operator: OperatorJob | None = None\n\n" if operator else "\n"
    extra = "        operator = _parse_operator_job(code)\n" if operator else ""
    if tamper:
        extra += "        code = 2\n"
    return _COMPOSE.format(
        field=field, extra=extra, kw=", operator=operator" if operator else ""
    )


def test_the_guard_accepts_only_the_operator_additions_to_compose_job() -> None:
    base = ast_guard.spec_fingerprint(_compose(operator=False))["compose_job"]
    assert ast_guard.spec_fingerprint(_compose(operator=True))["compose_job"] == base
    tampered = ast_guard.spec_fingerprint(_compose(operator=True, tamper=True))
    assert tampered["compose_job"] != base


def test_the_guard_bites_a_changed_existing_spec_definition() -> None:
    one = ast_guard.spec_fingerprint("def f():\n    return 1\n")
    two = ast_guard.spec_fingerprint("def f():\n    return 2\n")
    assert one["nodes"] != two["nodes"]
