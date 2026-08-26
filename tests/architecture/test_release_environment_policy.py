"""Package-release environments are credential boundaries, not review queues.

GitHub environment settings are mutable external state and cannot truthfully be
proved by an offline repository test.  This file pins the checked-in policy
that the release captain reads back after a settings change, and proves every
release job that holds registry credentials still names that boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
POLICY = REPO / ".github" / "release-environments.json"
WORKFLOWS = REPO / ".github" / "workflows"
PACKAGE_ENVIRONMENTS = {"pypi-release", "registry-release"}


def _load_policy(path: Path = POLICY) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _policy_problems(policy: dict) -> list[str]:
    problems: list[str] = []
    if policy.get("schema_version") != 1:
        problems.append("schema_version must be 1")

    environments = policy.get("package_release_environments", {})
    if set(environments) != PACKAGE_ENVIRONMENTS:
        problems.append(
            "package release environments must be exactly pypi-release and "
            "registry-release"
        )
    for name in sorted(PACKAGE_ENVIRONMENTS & set(environments)):
        contract = environments[name]
        if contract.get("allowed_branches") != ["main"]:
            problems.append(f"{name} must allow exactly main")
        if contract.get("required_reviewer_count") != 0:
            problems.append(f"{name} must require zero reviewers")
        if contract.get("wait_timer_minutes") != 0:
            problems.append(f"{name} must have a zero-minute wait timer")

    production = policy.get("production_deployments", {})
    if production.get("human_approval_required") is not True:
        problems.append("production deployments must remain human-approved")
    return problems


def _release_environment_jobs() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for path in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            environment = job.get("environment")
            if environment in PACKAGE_ENVIRONMENTS:
                found.append((path.name, job_name, environment))
    return found


def test_package_release_environment_contract_is_automatic_and_main_only() -> None:
    assert _policy_problems(_load_policy()) == []


def test_policy_guard_detects_a_reintroduced_release_reviewer() -> None:
    """Sensitivity: one reviewer recreates the human wait this change retires."""
    planted = _load_policy()
    planted["package_release_environments"]["registry-release"][
        "required_reviewer_count"
    ] = 1
    assert _policy_problems(planted) == ["registry-release must require zero reviewers"]


def test_production_approval_is_not_removed_with_package_publication() -> None:
    """Sensitivity: the speed decision does not silently expand to production."""
    planted = _load_policy()
    planted["production_deployments"]["human_approval_required"] = False
    assert _policy_problems(planted) == [
        "production deployments must remain human-approved"
    ]


def test_release_workflows_keep_credentials_inside_declared_environments() -> None:
    jobs = _release_environment_jobs()
    assert jobs, "no release jobs name a declared package environment"
    assert {environment for _path, _job, environment in jobs} == {
        "registry-release"
    }, "pypi-release is retained as policy but has no active workflow in this repo"
