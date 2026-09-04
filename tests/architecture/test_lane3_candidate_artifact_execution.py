"""Lane 3 executes the recorded candidate, never checkout Foundation source.

The candidate receipt is the only source of artifact coordinates.  Both the
rehearsal runner and the publication-side receipt verifier must therefore use
the wheel fetched and verified against that receipt.  A dispatch-provided
digest or a ``sys.path`` insertion pointing at ``packages/`` would let a green
run attest different bytes from the ones later published.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
LANE3 = ROOT / ".github/workflows/exposure-rehearsal.yml"
RELEASE = ROOT / ".github/workflows/release-facility.yml"
RUNNER = ROOT / "scripts/exposure_rehearsal_runner.py"
CHECKER = ROOT / "scripts/require_rehearsal.py"


def _document(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dispatch_inputs(document: dict[str, Any]) -> dict[str, Any]:
    # PyYAML 1.1 parses the unquoted YAML key ``on`` as True.
    trigger = document.get("on", document.get(True, {}))
    return trigger["workflow_dispatch"]["inputs"]


def _run_steps(document: dict[str, Any], job: str) -> list[str]:
    return [
        str(step.get("run", ""))
        for step in document["jobs"][job]["steps"]
        if step.get("run")
    ]


def _ordered_findings(runs: list[str], markers: tuple[str, ...]) -> list[str]:
    positions: list[int] = []
    findings: list[str] = []
    for marker in markers:
        matches = [index for index, body in enumerate(runs) if marker in body]
        if len(matches) != 1:
            findings.append(f"{marker!r} occurs {len(matches)} times")
            continue
        positions.append(matches[0])
    if len(positions) == len(markers) and positions != sorted(positions):
        findings.append(f"candidate execution order is {positions}, not monotonic")
    return findings


LANE3_ORDER = (
    "resolve-candidate",
    "gh api",
    "verify-candidate",
    "pip install --no-deps",
    "scripts/exposure_rehearsal_runner.py",
)

RELEASE_ORDER = (
    "resolve-candidate",
    "gh api",
    "verify-candidate",
    "pip install --no-deps",
    "scripts/require_rehearsal.py",
)


def _lane3_findings(document: dict[str, Any]) -> list[str]:
    findings = _ordered_findings(_run_steps(document, "rehearse"), LANE3_ORDER)
    inputs = _dispatch_inputs(document)
    if "foundation_artifact" in inputs:
        findings.append("the dispatch can supply a candidate digest")
    expected_inputs = {
        "authorization_run",
        "authorization_doc_digest",
        "facility",
        "controller_identity",
        "target",
        "vm_slot",
        "candidate_version",
    }
    if set(inputs) != expected_inputs:
        findings.append("the dispatch input set is not the closed expected set")
    permissions = document["jobs"]["rehearse"].get("permissions", {})
    if permissions.get("actions") != "read":
        findings.append("the rehearsal job cannot fetch the recorded Actions artifact")
    execute = next(
        (
            body
            for body in _run_steps(document, "rehearse")
            if "scripts/exposure_rehearsal_runner.py" in body
        ),
        "",
    )
    candidate_runner = (
        ".lane3-foundation/bin/python scripts/exposure_rehearsal_runner.py"
    )
    if candidate_runner not in execute:
        findings.append("the rehearsal runner is not driven by the candidate venv")
    if "steps.candidate.outputs.candidate_sha256" not in execute:
        findings.append("the receipt digest is not derived from the candidate receipt")
    return findings


def _release_findings(document: dict[str, Any]) -> list[str]:
    findings = _ordered_findings(_run_steps(document, "build"), RELEASE_ORDER)
    verify = next(
        (
            body
            for body in _run_steps(document, "build")
            if "scripts/require_rehearsal.py" in body
        ),
        "",
    )
    if ".foundation-candidate/bin/python scripts/require_rehearsal.py" not in verify:
        findings.append("the receipt verifier is not driven by the candidate venv")
    return findings


def _checkout_imports(source: str) -> list[int]:
    tree = ast.parse(source)
    findings: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "insert" or not isinstance(node.func.value, ast.Attribute):
            continue
        if node.func.value.attr != "path" or not isinstance(
            node.func.value.value, ast.Name
        ):
            continue
        if node.func.value.value.id != "sys":
            continue
        rendered = ast.dump(node)
        if "dotmac-deployment-foundation" in rendered and "packages" in rendered:
            findings.append(node.lineno)
    return findings


def test_lane3_executes_the_digest_verified_candidate_wheel() -> None:
    assert _lane3_findings(_document(LANE3)) == []


def test_publication_verifies_the_receipt_with_the_same_candidate_wheel() -> None:
    assert _release_findings(_document(RELEASE)) == []


def test_neither_executable_can_import_foundation_from_checkout() -> None:
    assert _checkout_imports(RUNNER.read_text(encoding="utf-8")) == []
    assert _checkout_imports(CHECKER.read_text(encoding="utf-8")) == []


def test_the_lane3_guard_bites_on_each_identity_regression() -> None:
    original = _document(LANE3)

    missing_verify = copy.deepcopy(original)
    missing_verify["jobs"]["rehearse"]["steps"] = [
        step
        for step in missing_verify["jobs"]["rehearse"]["steps"]
        if "verify-candidate" not in str(step.get("run", ""))
    ]
    assert _lane3_findings(missing_verify)

    checkout_python = copy.deepcopy(original)
    for step in checkout_python["jobs"]["rehearse"]["steps"]:
        if "scripts/exposure_rehearsal_runner.py" in str(step.get("run", "")):
            step["run"] = str(step["run"]).replace(
                ".lane3-foundation/bin/python", "python"
            )
    assert _lane3_findings(checkout_python)

    supplied_digest = copy.deepcopy(original)
    inputs = _dispatch_inputs(supplied_digest)
    inputs["foundation_artifact"] = {"required": True, "type": "string"}
    assert _lane3_findings(supplied_digest)


def test_the_release_guard_bites_when_checkout_python_verifies_the_receipt() -> None:
    mutated = _document(RELEASE)
    for step in mutated["jobs"]["build"]["steps"]:
        if "scripts/require_rehearsal.py" in str(step.get("run", "")):
            step["run"] = str(step["run"]).replace(
                ".foundation-candidate/bin/python", "python"
            )
    assert _release_findings(mutated)
