"""The opt-in's actual-framework suite must remain enforced in hosted CI."""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
SUITE = "tests/compatibility/test_browser_document_policy.py"


def _enforce(workflow: str) -> None:
    jobs = yaml.safe_load(workflow)["jobs"]
    assert "console-document-policy" in jobs, "missing actual-framework workload"
    job = jobs["console-document-policy"]
    assert "if" not in job, "compatibility job may not silently skip"
    runs = [step for step in job["steps"] if "run" in step]
    install = next(step for step in runs if "pip install" in step["run"])
    assert all(
        flag in install["run"]
        for flag in ("--require-hashes", "--no-deps", "--target", "--only-binary")
    ), "framework overlay must remain isolated and hash-locked"
    workload = next(step for step in runs if step.get("id") == "compatibility")
    assert "if" not in workload, "workload may not silently skip"
    assert SUITE in workload["run"] and "-m pytest" in workload["run"]
    assert "CONSOLE_FRAMEWORK_OVERLAY" in workload["run"]
    evidence = next(
        step for step in runs if "COMPATIBILITY_OUTCOME" in step.get("env", {})
    )
    assert evidence.get("if") == "always()"
    source = (
        evidence["run"].split("<<'PYEVIDENCE'\n", 1)[1].rsplit("\nPYEVIDENCE", 1)[0]
    )
    assertions = [
        node for node in ast.parse(source).body if isinstance(node, ast.Assert)
    ]
    checks = [ast.unparse(node.test) for node in assertions]
    assert any(
        "COMPATIBILITY_OUTCOME" in check and "success" in check for check in checks
    )
    assert any("required" in check and "collected" in check for check in checks)
    assert any("cases" in check and "refused" in check for check in checks)
    assert "console-document-policy" in jobs["docker-build"]["needs"]


def test_actual_framework_suite_is_enforced_by_the_live_workflow() -> None:
    _enforce(WORKFLOW.read_text())


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_job",
        "skip_job",
        "wrong_suite",
        "unlocked_overlay",
        "missing_outcome",
        "ungated_build",
    ],
)
def test_workflow_detector_refuses_each_temporary_violation(mutation: str) -> None:
    document = deepcopy(yaml.safe_load(WORKFLOW.read_text()))
    jobs = document["jobs"]
    job = jobs["console-document-policy"]
    if mutation == "missing_job":
        del jobs["console-document-policy"]
    elif mutation == "skip_job":
        job["if"] = False
    elif mutation == "wrong_suite":
        step = next(step for step in job["steps"] if step.get("id") == "compatibility")
        step["run"] = step["run"].replace(SUITE, "tests/unit")
    elif mutation == "unlocked_overlay":
        step = next(
            step for step in job["steps"] if "pip install" in step.get("run", "")
        )
        step["run"] = step["run"].replace("--require-hashes", "")
    elif mutation == "missing_outcome":
        step = next(
            step
            for step in job["steps"]
            if "COMPATIBILITY_OUTCOME" in step.get("env", {})
        )
        step["run"] = "\n".join(
            line for line in step["run"].splitlines() if "assert os.environ" not in line
        )
    else:
        jobs["docker-build"]["needs"].remove("console-document-policy")
    with pytest.raises(AssertionError):
        _enforce(yaml.safe_dump(document))
