"""T14: the operator-job entry point is additive; deploy bytes are unchanged.

DESIGN S2 invariant I-1. The baseline JSON was taken by
`foundation_operator_job_ast.fingerprint_tree` over a `git archive` of the
pre-change commit (`base_commit` inside it). It hashes the PARSED tree, so
formatting cannot fake or hide a change. The guard is INVERTED: every existing
module is whole-file hashed except `spec.py`, `engine/plan.py` and `cli.py`,
which get structured checks, so a module outside any list cannot slip through.
Sensitivity is proved on in-memory source, never on a plant committed to disk.
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


def test_t14_every_existing_module_is_byte_identical_except_the_three() -> None:
    current = ast_guard.fingerprint_tree(SRC)
    assert len(BASELINE["whole_file"]) > 50, "baseline is thin; this proves nothing"
    assert set(BASELINE["whole_file"]) >= {
        "engine/run.py",
        "providers/compose_host.py",
        "recovery.py",
        "render/compose.py",
        "execution_plan.py",
        "authorization_v3.py",
    }
    assert current["whole_file"] == BASELINE["whole_file"], (
        "an existing module changed, or a module outside the allow-list was "
        "added. Only spec.py, engine/plan.py and cli.py may change, and only "
        "operator_job.py, operator_job_contract.py and "
        "providers/compose_operator_job.py may be added."
    )


def test_t14_the_new_modules_are_exactly_the_allowed_ones() -> None:
    present = set(ast_guard.package_files(SRC))
    assert ast_guard.NEW_MODULES <= present
    assert present - set(BASELINE["whole_file"]) - set(
        ast_guard.STRUCTURED_FILES
    ) == set(ast_guard.NEW_MODULES)


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
    assert set(current["nodes"]) - set(baseline["nodes"]) == ALLOWED_NEW_SPEC_KEYS
    assert current["compose_job"] == baseline["compose_job"], (
        "ComposeJob changed beyond the one optional `operator` field and its "
        "parse statements"
    )


def test_t14_cli_py_only_build_parser_registration_and_one_handler() -> None:
    current = ast_guard.fingerprint_tree(SRC)["cli_py"]
    baseline = BASELINE["cli_py"]
    assert current["nodes"] == baseline["nodes"], (
        "a cli.py definition other than build_parser changed or was added "
        "(deploy, rollback and recovery handlers must be untouched)"
    )
    assert current["build_parser"] == baseline["build_parser"]
    source = (SRC / "cli.py").read_text(encoding="utf-8")
    for name in ast_guard.CLI_NEW_FUNCTIONS:
        assert f"def {name}(" in source


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
    kw = ", operator=operator" if operator else ""
    return _COMPOSE.format(field=field, extra=extra, kw=kw)


def test_the_guard_accepts_only_the_operator_additions_to_compose_job() -> None:
    base = ast_guard.spec_fingerprint(_compose(operator=False))["compose_job"]
    assert ast_guard.spec_fingerprint(_compose(operator=True))["compose_job"] == base
    tampered = ast_guard.spec_fingerprint(_compose(operator=True, tamper=True))
    assert tampered["compose_job"] != base


_SPEC = (
    "class ProductDeploymentSpec:\n"
    "    def __post_init__(self):\n"
    "        check()\n"
    "{gate}"
    "        done()\n"
)
_GATE = (
    "        if self.schema != 'v5' and any(j.operator for j in self.jobs):\n"
    "            raise SpecError('gate')\n"
)


def test_the_guard_accepts_only_the_operator_schema_gate_in_the_spec_class() -> None:
    base = ast_guard.spec_fingerprint(_SPEC.format(gate=""))["nodes"]
    gated = ast_guard.spec_fingerprint(_SPEC.format(gate=_GATE))["nodes"]
    assert gated == base
    other = _GATE.replace("j.operator", "j.other")
    assert ast_guard.spec_fingerprint(_SPEC.format(gate=other))["nodes"] != base
    extra = _GATE + "        sneaky()\n"
    assert ast_guard.spec_fingerprint(_SPEC.format(gate=extra))["nodes"] != base


def test_the_guard_bites_a_changed_existing_spec_definition() -> None:
    one = ast_guard.spec_fingerprint("def f():\n    return 1\n")
    two = ast_guard.spec_fingerprint("def f():\n    return 2\n")
    assert one["nodes"] != two["nodes"]


_CLI = (
    "def cmd_deploy(args):\n    return 1\n"
    "{extra_fn}"
    "def build_parser():\n"
    "    deploy = add('deploy')\n"
    "{registration}"
    "    return deploy\n"
)


def _cli(*, registration: bool = False, extra_fn: str = "", edit: bool = False) -> str:
    reg = (
        "    run_job = add('run-job')\n    run_job.add_argument('--execute')\n"
        if registration
        else ""
    )
    source = _CLI.format(extra_fn=extra_fn, registration=reg)
    return source.replace("return 1", "return 2") if edit else source


def test_the_guard_accepts_only_the_one_cli_addition() -> None:
    base = ast_guard.cli_fingerprint(_cli())
    added = ast_guard.cli_fingerprint(
        _cli(registration=True, extra_fn="def cmd_run_job(args):\n    return 0\n")
    )
    assert added == base


def test_the_guard_bites_a_changed_deploy_handler_in_cli() -> None:
    base = ast_guard.cli_fingerprint(_cli())
    assert ast_guard.cli_fingerprint(_cli(edit=True))["nodes"] != base["nodes"]


def test_the_guard_bites_an_unlisted_new_cli_function() -> None:
    base = ast_guard.cli_fingerprint(_cli())
    sneaky = ast_guard.cli_fingerprint(
        _cli(extra_fn="def cmd_deploy_fast(args):\n    return 0\n")
    )
    assert sneaky["nodes"] != base["nodes"]


def test_the_guard_bites_an_unregistered_build_parser_edit() -> None:
    base = ast_guard.cli_fingerprint(_cli())
    edited = _cli().replace("return deploy", "deploy.x = 1\n    return deploy")
    assert ast_guard.cli_fingerprint(edited)["build_parser"] != base["build_parser"]
