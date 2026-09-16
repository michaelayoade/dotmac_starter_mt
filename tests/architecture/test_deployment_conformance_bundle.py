"""The additive deployment-conformance successor is secret-free by shape.

This guard covers the reusable workflow's structure.  It does not prove that a
calling product derived its expected-file from its real dependency manifest;
that remains a caller-side adoption premise until ERP and Workspace migrate.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deployment-conformance-bundle.yml"
ACTION = (
    "michaelayoade/dotmac_starter_mt/"
    ".github/actions/verified-dependency-bundle@"
    "7ec614c7b8051e7d399d80381ffdb6344d50dbc6"
)
REQUIRED_BUNDLE_INPUTS = {
    "foundation-version",
    "bundle-plan-digest",
    "bundle-wheel-filename",
    "bundle-wheel-sha256",
    "bundle-run-id",
    "bundle-archive-artifact-id",
    "bundle-sidecar-artifact-id",
    "bundle-producer-workflow-path",
    "bundle-archive-artifact-name",
    "bundle-sidecar-artifact-name",
}
EXPECTED_ACTION_INPUTS = {
    "operation": "verify-and-index",
    "expected-file": "${{ steps.expected.outputs.path }}",
    "producer-repository": "${{ github.repository }}",
    "producer-repository-id": "${{ github.repository_id }}",
    "producer-workflow-path": "${{ inputs.bundle-producer-workflow-path }}",
    "archive-artifact-name": "${{ inputs.bundle-archive-artifact-name }}",
    "sidecar-artifact-name": "${{ inputs.bundle-sidecar-artifact-name }}",
    "archive-artifact-id": "${{ inputs.bundle-archive-artifact-id }}",
    "run-id": "${{ inputs.bundle-run-id }}",
    "sidecar-artifact-id": "${{ inputs.bundle-sidecar-artifact-id }}",
    "github-token": "${{ github.token }}",
}


def _load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
    ]


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _string_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in _string_values(child)]
    return []


def _problems(workflow: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    triggers = workflow.get(True, {})
    if not isinstance(triggers, dict) or set(triggers) != {"workflow_call"}:
        problems.append("workflow is not workflow_call-only")
    call = triggers.get("workflow_call", {})
    inputs = call.get("inputs", {})
    missing = sorted(
        name
        for name in REQUIRED_BUNDLE_INPUTS
        if inputs.get(name, {}).get("required") is not True
    )
    if missing:
        problems.append(f"required bundle inputs missing: {missing}")
    if call.get("secrets"):
        problems.append("workflow_call accepts a secret")
    strings = _string_values(workflow)
    if any(
        "secrets." in text.lower()
        or "secrets[" in text.lower()
        or "forgejo_" in text.lower()
        for text in strings
    ):
        problems.append("workflow references a registry or GitHub secret")
    if workflow.get("permissions") != {"contents": "read", "actions": "read"}:
        problems.append("permissions are not the exact read-only pair")

    jobs = workflow.get("jobs", {})
    if set(jobs) != {"descriptor", "image"}:
        problems.append("descriptor/image job set changed")
    steps = _steps(workflow)
    checkouts = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    if len(checkouts) != 2 or any(
        step.get("with", {}).get("persist-credentials") is not False
        for step in checkouts
    ):
        problems.append("candidate checkouts do not disable persisted credentials")

    bundle_steps = [
        step
        for step in steps
        if "verified-dependency-bundle" in str(step.get("uses", ""))
    ]
    if len(bundle_steps) != 2 or any(
        step.get("uses") != ACTION for step in bundle_steps
    ):
        problems.append("bundle action is not used twice at the exact trusted SHA")
    for step in bundle_steps:
        supplied = step.get("with", {})
        if supplied != EXPECTED_ACTION_INPUTS:
            problems.append("bundle action inputs do not preserve the trusted dataflow")
    for job in jobs.values():
        job_steps = job.get("steps", [])
        positions = {
            name: next(
                (
                    index
                    for index, step in enumerate(job_steps)
                    if step.get("name") == step_name
                ),
                None,
            )
            for name, step_name in {
                "materialize": "Materialize the one-wheel expected artifact set",
                "install": (
                    "Install the pinned foundation from the verified offline index"
                ),
                "checkout": "Check out the evaluated product revision",
            }.items()
        }
        positions["verify"] = next(
            (
                index
                for index, step in enumerate(job_steps)
                if "verified-dependency-bundle" in str(step.get("uses", ""))
            ),
            None,
        )
        if any(position is None for position in positions.values()) or not (
            positions["materialize"]
            < positions["verify"]
            < positions["install"]
            < positions["checkout"]
        ):
            problems.append(
                "job does not materialize, verify, install, then checkout in order"
            )

    runs = [str(step.get("run", "")) for step in steps]
    if any("${{" in run for run in runs):
        problems.append("a run body interpolates a GitHub expression")
    joined_runs = "\n".join(runs)
    if "secrets." in joined_runs or "FORGEJO_" in joined_runs:
        problems.append("a run body references a registry credential")
    installs = [run for run in runs if "pip install" in run]
    if len(installs) != 2 or any(
        "--no-index" not in run
        or "--no-deps" not in run
        or "--find-links" not in run
        or "--index-url" in run
        or "--extra-index-url" in run
        for run in installs
    ):
        problems.append("foundation installs are not offline-only")
    install_steps = [
        step
        for step in steps
        if step.get("name")
        == "Install the pinned foundation from the verified offline index"
    ]
    if len(install_steps) != 2 or any(
        step.get("env")
        != {
            "FOUNDATION_VERSION": "${{ inputs.foundation-version }}",
            "BUNDLE_INDEX_ROOT": "${{ steps.bundle.outputs.index-root }}",
        }
        for step in install_steps
    ):
        problems.append("offline installs do not consume the verified index output")

    binders = [
        str(step.get("run", ""))
        for step in steps
        if step.get("name") == "Materialize the one-wheel expected artifact set"
    ]
    required_binder_fragments = (
        '"package_normalised_name": "dotmac-deployment-foundation"',
        '"schema_version": 1',
        '"artifacts": [{',
        "re.escape(version)",
        "dotmac_deployment_foundation-",
        're.fullmatch(r"[0-9a-f]{64}", plan_digest)',
        're.fullmatch(r"[0-9a-f]{64}", wheel_sha256)',
    )
    if len(binders) != 2 or any(
        fragment not in binder
        for binder in binders
        for fragment in required_binder_fragments
    ):
        problems.append("expected-file is not bound to one exact foundation wheel")

    names = {step.get("name") for step in steps}
    if "Run the descriptor conformance checks" not in names:
        problems.append("descriptor conformance execution is absent")
    if "Audit the image the descriptor pins" not in names:
        problems.append("image conformance execution is absent")
    return problems


def test_the_real_bundle_conformance_workflow_is_secret_free_and_complete() -> None:
    assert _problems(_load()) == []


def test_the_guard_refuses_each_trust_boundary_regression() -> None:
    original = _load()

    secret = copy.deepcopy(original)
    secret[True]["workflow_call"]["secrets"] = {"TOKEN": {"required": True}}
    assert "workflow_call accepts a secret" in _problems(secret)

    secret_env = copy.deepcopy(original)
    secret_env["jobs"]["descriptor"]["env"] = {"TOKEN": "${{ secrets.SOME_TOKEN }}"}
    assert "workflow references a registry or GitHub secret" in _problems(secret_env)

    local_action = copy.deepcopy(original)
    next(
        step
        for step in _steps(local_action)
        if "verified-dependency-bundle" in str(step.get("uses", ""))
    )["uses"] = "./.github/actions/verified-dependency-bundle"
    assert "bundle action is not used twice at the exact trusted SHA" in _problems(
        local_action
    )

    decoy_expected = copy.deepcopy(original)
    next(
        step
        for step in _steps(decoy_expected)
        if "verified-dependency-bundle" in str(step.get("uses", ""))
    )["with"]["expected-file"] = "caller-supplied.json"
    assert "bundle action inputs do not preserve the trusted dataflow" in _problems(
        decoy_expected
    )

    early_checkout = copy.deepcopy(original)
    descriptor_steps = early_checkout["jobs"]["descriptor"]["steps"]
    checkout = next(
        step
        for step in descriptor_steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    descriptor_steps.remove(checkout)
    descriptor_steps.insert(0, checkout)
    assert (
        "job does not materialize, verify, install, then checkout in order"
        in _problems(early_checkout)
    )

    online = copy.deepcopy(original)
    install = next(
        step for step in _steps(online) if "pip install" in str(step.get("run", ""))
    )
    install["run"] = str(install["run"]).replace(
        "--no-index", "--index-url https://example.invalid/simple"
    )
    assert "foundation installs are not offline-only" in _problems(online)

    unverified_index = copy.deepcopy(original)
    install_step = next(
        step
        for step in _steps(unverified_index)
        if step.get("name")
        == "Install the pinned foundation from the verified offline index"
    )
    install_step["env"]["BUNDLE_INDEX_ROOT"] = "lookalike-index"
    assert "offline installs do not consume the verified index output" in _problems(
        unverified_index
    )

    weak_binding = copy.deepcopy(original)
    binder = next(
        step
        for step in _steps(weak_binding)
        if step.get("name") == "Materialize the one-wheel expected artifact set"
    )
    binder["run"] = str(binder["run"]).replace(
        '"package_normalised_name": "dotmac-deployment-foundation"',
        '"package_normalised_name": "anything"',
    )
    assert "expected-file is not bound to one exact foundation wheel" in _problems(
        weak_binding
    )

    no_descriptor = copy.deepcopy(original)
    no_descriptor["jobs"]["descriptor"]["steps"] = [
        step
        for step in no_descriptor["jobs"]["descriptor"]["steps"]
        if step.get("name") != "Run the descriptor conformance checks"
    ]
    assert "descriptor conformance execution is absent" in _problems(no_descriptor)

    no_image = copy.deepcopy(original)
    no_image["jobs"]["image"]["steps"] = [
        step
        for step in no_image["jobs"]["image"]["steps"]
        if step.get("name") != "Audit the image the descriptor pins"
    ]
    assert "image conformance execution is absent" in _problems(no_image)
