"""The controller release is signed only after its release run completes."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FINALIZER = ROOT / ".github/workflows/finalize-controller-release-evidence.yml"
RELEASE = ROOT / ".github/workflows/release-facility.yml"
SCRIPT = ROOT / "scripts/finalize_controller_release_evidence.py"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(FINALIZER.read_text(encoding="utf-8"))


def _steps(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(step["name"]): step
        for step in workflow["jobs"]["finalize"]["steps"]
        if "name" in step
    }


def _lane_problems(workflow: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    triggers = workflow.get(True, {})
    if triggers != {
        "workflow_run": {
            "workflows": ["Release facility"],
            "types": ["completed"],
        }
    }:
        problems.append("finalizer is not triggered only by completed Release facility")
    permissions = workflow.get("permissions", {})
    if permissions != {"actions": "read", "contents": "read"}:
        problems.append("finalizer permissions are not exact read-only authority")
    concurrency = workflow.get("concurrency", {})
    group = str(concurrency.get("group", ""))
    if (
        "github.event.workflow_run.id" not in group
        or "github.event.workflow_run.run_attempt" not in group
        or concurrency.get("cancel-in-progress") is not False
    ):
        problems.append("finalizer concurrency does not preserve one run attempt")

    job = workflow.get("jobs", {}).get("finalize", {})
    if job.get("if") != "${{ github.event.workflow_run.conclusion == 'success' }}":
        problems.append("non-successful release runs can reach the signer")
    if job.get("environment") != "registry-release":
        problems.append("signer is not inside the protected release environment")
    steps = _steps(workflow)

    provision = steps.get("Refuse unprovisioned signing authority", {})
    provision_blob = "\n".join(str(value) for value in provision.values())
    for needle in (
        "DEPLOYMENT_RELEASE_EVIDENCE_PRIVATE_KEY_PEM",
        "DEPLOYMENT_RELEASE_EVIDENCE_KEY_ID",
        '[[ -z "${RELEASE_EVIDENCE_KEY_PEM}" ]]',
        '[[ -z "${RELEASE_EVIDENCE_KEY_ID}" ]]',
        "umask 077",
        "chmod 600",
    ):
        if needle not in provision_blob:
            problems.append(f"signing authority provision lacks {needle!r}")

    sign = steps.get("Re-read, bind, and sign the completed release", {})
    sign_blob = "\n".join(str(value) for value in sign.values())
    for needle in (
        "finalize_controller_release_evidence.py",
        "github.event.workflow_run.repository.id",
        "github.event.workflow_run.repository.full_name",
        "github.event.workflow_run.head_repository.id",
        "github.event.workflow_run.head_repository.full_name",
        "github.event.workflow_run.workflow_id",
        "github.event.workflow_run.id",
        "github.event.workflow_run.run_attempt",
        "github.event.workflow_run.event",
        "github.event.workflow_run.head_sha",
        "github.event.workflow_run.status",
        "github.event.workflow_run.conclusion",
        "GITHUB_TOKEN",
        "FORGEJO_TOKEN",
        "--private-key-path",
        "--key-id",
    ):
        if needle not in sign_blob:
            problems.append(f"completed-run binding lacks {needle!r}")

    publish = steps.get("Publish signed evidence create-only", {})
    command = str(publish.get("run", ""))
    if command.count("--request PUT") != 1 or command.count("--upload-file") != 1:
        problems.append("evidence publication bypasses its one PUT helper")
    for needle in (
        "get_evidence_asset()",
        "publish_evidence_asset()",
        "--request GET",
        'if [[ "${status}" == "409" ]]; then',
        'get_evidence_asset "${target_name}" "${remote_copy}"',
        'cmp --silent "${source_path}" "${remote_copy}"',
        'elif [[ "${status}" != "201" ]]; then',
        'if [[ "${duplicate_status}" != "409" ]]; then',
    ):
        if needle not in command:
            problems.append(f"retry-safe evidence publication lacks {needle!r}")

    if command.count("$(put_evidence_asset ") != 2:
        problems.append("evidence asset does not receive PUT plus conflict probe")
    if command.count("publish_evidence_asset \\\n") != 2:
        problems.append("evidence and signature are not each published once")

    ordered = (
        "controller-release-evidence/DeploymentControllerReleaseEvidence.v1.json",
        '"release evidence"',
        "controller-release-evidence/DetachedEvidenceSignature.v1.json",
        '"release signature"',
    )
    positions = [command.find(needle) for needle in ordered]
    if any(position < 0 for position in positions):
        problems.append("evidence retry-safe publication calls are incomplete")
    elif positions != sorted(positions):
        problems.append("signature is not the final create-only publication")
    refusal_conditions = (
        'if [[ "${status}" == "409" ]]; then',
        'if ! get_evidence_asset "${target_name}" "${remote_copy}"; then',
        'if ! cmp --silent "${source_path}" "${remote_copy}"; then',
        'elif [[ "${status}" != "201" ]]; then',
        'if [[ "${duplicate_status}" != "409" ]]; then',
    )
    for condition in refusal_conditions:
        start = command.find(condition)
        line_start = command.rfind("\n", 0, start) + 1
        indentation = command[line_start:start]
        end = command.find(f"\n{indentation}fi", start)
        if start < 0 or end < 0 or "exit 1" not in command[start:end]:
            problems.append(f"evidence publication guard lacks {condition!r}")

    cleanup = steps.get("Delete projected private key", {})
    if cleanup.get("if") != "always()" or "rm -f" not in str(cleanup.get("run", "")):
        problems.append("projected signing key is not removed on every outcome")
    return problems


def test_post_completion_release_evidence_lane_is_closed() -> None:
    assert _lane_problems(_workflow()) == []


@pytest.mark.parametrize(
    "needle",
    [
        '[[ "${status}" == "409" ]]',
        'get_evidence_asset "${target_name}" "${remote_copy}"',
        'cmp --silent "${source_path}" "${remote_copy}"',
        '[[ "${status}" != "201" ]]',
        '[[ "${duplicate_status}" != "409" ]]',
    ],
)
def test_every_create_only_status_check_is_load_bearing(needle: str) -> None:
    mutated = copy.deepcopy(_workflow())
    step = _steps(mutated)["Publish signed evidence create-only"]
    step["run"] = str(step["run"]).replace(needle, "[[ planted-gap ]]")
    assert _lane_problems(mutated)


def test_success_only_trigger_guard_is_load_bearing() -> None:
    mutated = copy.deepcopy(_workflow())
    mutated["jobs"]["finalize"]["if"] = "always()"
    assert _lane_problems(mutated)


def test_signing_is_not_attempted_inside_the_running_release_workflow() -> None:
    release = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    assert "finalize_controller_release_evidence.py" not in "\n".join(
        str(value) for value in release.values()
    )


def test_key_provisioning_and_public_trust_install_are_explicit_blockers() -> None:
    source = FINALIZER.read_text(encoding="utf-8")
    assert "PRE-RELEASE OPERATIONAL BLOCKER" in source
    assert "DEPLOYMENT_RELEASE_EVIDENCE_PRIVATE_KEY_PEM" in source
    assert "DEPLOYMENT_RELEASE_EVIDENCE_KEY_ID" in source
    assert "root-owned" in source and "DeploymentEvidenceTrustPolicy.v1" in source
    assert "BEGIN PRIVATE KEY" not in source
    assert "BEGIN PRIVATE KEY" not in SCRIPT.read_text(encoding="utf-8")
