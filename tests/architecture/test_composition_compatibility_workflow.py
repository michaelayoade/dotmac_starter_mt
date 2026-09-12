"""The cross-product gate runs in a fresh, immutable reusable-workflow job."""

from __future__ import annotations

import ast
import copy
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/composition-compatibility.yml"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
TRUSTED_RUNNER_REVISION = "4fcdfff74c6d8098e5ebe9a4b1c63aa1140d4756"
TRUSTED_GATE_SHA256 = "17e0cb944518309dc68636cda697c2f50b762e0a30cf87cd427a29cb9f1eee45"
TRUSTED_RUNNER_PATH = ".github/actions/verify-composition-contract-sources/run_gate.py"
TRUSTED_ACTION = (
    "michaelayoade/dotmac_starter_mt/.github/actions/"
    "verify-composition-contract-sources@"
    f"{TRUSTED_RUNNER_REVISION}"
)
PYTHON_VERSIONS = "3.12\n3.11"
PRODUCTS = {
    "michaelayoade/dotmac_academy_app": (
        "ca1f9058a6483fe52207556fa2d17c56b1e237c7",
        ".compat-gate-clones/dotmac_academy_app",
    ),
    "michaelayoade/dotmac_erp": (
        "dca695a7d59179fe65577ba4e72a709ff0b32cde",
        ".compat-gate-clones/dotmac_erp",
    ),
    "michaelayoade/dotmac_sub": (
        "272a897b778899b110c5790fcaf43bbb54efe27c",
        ".compat-gate-clones/dotmac_sub",
    ),
}
SHA = re.compile(r"^[0-9a-f]{40}$")


def _document() -> dict[str, Any]:
    value = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _trusted_runner_source() -> str:
    git = shutil.which("git")
    assert git is not None
    result = subprocess.run(  # noqa: S603
        [git, "show", f"{TRUSTED_RUNNER_REVISION}:{TRUSTED_RUNNER_PATH}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _assigned_string(source: str, name: str) -> str | None:
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    return None


def _findings(document: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    triggers = document.get(True)
    if triggers != {"workflow_call": {}}:
        findings.append("workflow is not workflow_call-only")
    if document.get("permissions") != {"contents": "read"}:
        findings.append("workflow permissions are not contents: read")

    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != {"compatibility"}:
        findings.append("workflow does not own exactly one compatibility job")
        return findings
    job = jobs["compatibility"]
    if not isinstance(job, dict) or "uses" in job:
        findings.append("compatibility is not a locally-owned fresh job")
        return findings
    steps = job.get("steps")
    if not isinstance(steps, list):
        findings.append("compatibility job has no steps")
        return findings
    if any(not isinstance(step, dict) or "run" in step for step in steps):
        findings.append("candidate-authored shell runs before the trusted action")

    uses = [step.get("uses") for step in steps if isinstance(step, dict)]
    if uses != [CHECKOUT, CHECKOUT, CHECKOUT, CHECKOUT, SETUP_PYTHON, TRUSTED_ACTION]:
        findings.append("action order or immutable pins changed")

    setup_steps = [
        step
        for step in steps
        if isinstance(step, dict) and step.get("uses") == SETUP_PYTHON
    ]
    if len(setup_steps) != 1 or setup_steps[0].get("with") != {
        "python-version": PYTHON_VERSIONS
    }:
        findings.append("trusted host and product interpreter floors changed")

    checkout_steps = [
        step
        for step in steps
        if isinstance(step, dict) and step.get("uses") == CHECKOUT
    ]
    if len(checkout_steps) != 4:
        findings.append("workflow does not have exactly four checkouts")
        return findings
    starter_inputs = checkout_steps[0].get("with")
    if starter_inputs != {"fetch-depth": 0, "persist-credentials": False}:
        findings.append("Starter checkout does not preserve trusted history safely")

    actual_products: dict[str, tuple[str, str]] = {}
    for step in checkout_steps[1:]:
        inputs = step.get("with")
        if not isinstance(inputs, dict):
            findings.append("product checkout has no closed input mapping")
            continue
        if set(inputs) != {
            "repository",
            "ref",
            "path",
            "fetch-depth",
            "persist-credentials",
        }:
            findings.append("product checkout input shape changed")
            continue
        repository = inputs["repository"]
        revision = inputs["ref"]
        path = inputs["path"]
        if not all(isinstance(item, str) for item in (repository, revision, path)):
            findings.append("product checkout coordinate is not text")
            continue
        if not SHA.fullmatch(revision):
            findings.append(f"{repository}: product revision is not immutable")
        if inputs["fetch-depth"] != 0:
            findings.append(
                f"{repository}: checkout cannot prove protected-main ancestry"
            )
        if inputs["persist-credentials"] is not False:
            findings.append(f"{repository}: checkout retains excess authority")
        actual_products[repository] = (revision, path)
    if actual_products != PRODUCTS:
        findings.append("product checkout coordinates changed")
    return findings


def test_reusable_workflow_is_one_fresh_closed_job() -> None:
    assert _findings(_document()) == []


def test_trusted_action_is_the_reviewed_runner_successor() -> None:
    assert SHA.fullmatch(TRUSTED_RUNNER_REVISION)
    assert TRUSTED_ACTION.endswith(f"@{TRUSTED_RUNNER_REVISION}")
    assert (
        _assigned_string(_trusted_runner_source(), "GATE_SHA256") == TRUSTED_GATE_SHA256
    )


def test_guard_refuses_when_its_action_constant_lags_the_workflow(
    monkeypatch,
) -> None:
    old_action = TRUSTED_ACTION.rsplit("@", 1)[0] + (
        "@38eafe533332685c3df3f2d85fac10196d3e4431"
    )
    monkeypatch.setattr(
        "tests.architecture.test_composition_compatibility_workflow.TRUSTED_ACTION",
        old_action,
    )
    assert "action order or immutable pins changed" in _findings(_document())


def test_guard_refuses_candidate_shell_and_moving_product_refs() -> None:
    document = _document()
    planted_shell = copy.deepcopy(document)
    planted_shell["jobs"]["compatibility"]["steps"].insert(
        0, {"run": "echo poison >> $GITHUB_PATH"}
    )
    assert "candidate-authored shell" in " ".join(_findings(planted_shell))

    planted_ref = copy.deepcopy(document)
    planted_ref["jobs"]["compatibility"]["steps"][1]["with"]["ref"] = "main"
    findings = _findings(planted_ref)
    assert any("not immutable" in item for item in findings)

    shallow_product = copy.deepcopy(document)
    shallow_product["jobs"]["compatibility"]["steps"][1]["with"]["fetch-depth"] = 1
    assert "cannot prove protected-main ancestry" in " ".join(
        _findings(shallow_product)
    )


def test_guard_refuses_a_tagged_verifier_or_missing_product_checkout() -> None:
    document = _document()
    planted_action = copy.deepcopy(document)
    planted_action["jobs"]["compatibility"]["steps"][-1]["uses"] = (
        "michaelayoade/dotmac_starter_mt/.github/actions/"
        "verify-composition-contract-sources@main"
    )
    assert "immutable pins changed" in " ".join(_findings(planted_action))

    missing_product = copy.deepcopy(document)
    del missing_product["jobs"]["compatibility"]["steps"][2]
    assert "four checkouts" in " ".join(_findings(missing_product))


def test_guard_refuses_missing_product_floor_or_a_non_3_11_host() -> None:
    document = _document()
    missing_academy_floor = copy.deepcopy(document)
    missing_academy_floor["jobs"]["compatibility"]["steps"][-2]["with"][
        "python-version"
    ] = "3.11"
    assert "interpreter floors changed" in " ".join(_findings(missing_academy_floor))

    wrong_host = copy.deepcopy(document)
    wrong_host["jobs"]["compatibility"]["steps"][-2]["with"]["python-version"] = (
        "3.11\n3.12"
    )
    assert "interpreter floors changed" in " ".join(_findings(wrong_host))
