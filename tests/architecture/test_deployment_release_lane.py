"""The deployment-foundation release lane is built, and it actually closes the
circularity Michael found in review.

Three defects motivated this file, and each gets its own section below:

1. **The release path was circular.** `dotmac-deployment-foundation` is
   `EXTRACTION.toml`'s `universal-facility` classification, and no existing
   release lane accepted it — `release-modules.json` wants a
   `db_schema`/`manifest_attr`/`kernel_floor` it does not have, and
   `release-adapters.json` proves an IMPORT surface for a package that is
   never imported by a product process. `.github/release-facilities.json` +
   `.github/workflows/release-facility.yml` is the fix: a package can now
   actually be published.
2. **`deployment-conformance.yml` installed from public PyPI with no
   credential.** The distribution does not exist there — at best a hard
   failure, at worst a name-squat installing arbitrary code under the real
   package's name. It now installs ONLY from the private Forgejo index,
   authenticated as the READ-only `ci-reader` identity, and never references
   the PUBLISH credential or a public index.
3. **Conformance validated the parse, not the descriptor.** It now calls
   `dotmac_deployment_foundation.conformance.check_all` and fails on any
   finding.

## Why this file walks PARSED YAML/JSON rather than grepping

A grep for `FORGEJO_PUBLISH_TOKEN` or `pypi.org` over the raw file text would
also match the file's own PROSE explaining why those things must be absent —
`deployment-conformance.yml`'s header discusses both at length precisely to
justify the rule it enforces. `test_the_lane_shares_conventions_with_its_
siblings` in `test_adapter_release_lane.py` already established the fix for
workflow-level checks (strip `#` comment lines before scanning); this file
goes one step further for the two negative-space rules, because a `#`-comment
strip is fragile against a token that only ever appears in RUN-SCRIPT
comments, YAML block-scalar prose, or a multi-line description string —
none of which start with a bare `#`. Walking the values PyYAML/json actually
produced is the version of that fix which cannot be fooled by comment shape:
a YAML `#` comment never survives parsing into the data structure at all, so
a predicate over the parsed tree is blind to it by construction, not by a
heuristic that has to remember to strip it.
"""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FACILITY_ALLOWLIST = PROJECT_ROOT / ".github" / "release-facilities.json"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-facility.yml"
CONFORMANCE_WORKFLOW = (
    PROJECT_ROOT / ".github" / "workflows" / "deployment-conformance.yml"
)
ADOPTER_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "deployment-adopter.yml"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _string_values(node: Any) -> list[str]:
    """Every string VALUE (and key) reachable in a parsed YAML/JSON tree.

    Deliberately structural, not textual: a YAML `#` comment is discarded by
    the parser before this function ever sees the tree, so a needle that only
    ever appears in a comment cannot show up here — which is the whole point
    of preferring this over `needle in raw_text`.
    """
    found: list[str] = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_string_values(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_string_values(item))
    return found


def _references(path: Path, needle: str) -> bool:
    """Does the PARSED document contain `needle` in any string value?"""
    tree = _load_yaml(path)
    return any(needle in value for value in _string_values(tree))


# ── 1. the facility allowlist parses and is exactly one entry ───────────────


def test_the_facility_allowlist_parses_and_lists_exactly_one_entry() -> None:
    data = _load_json(FACILITY_ALLOWLIST)
    facilities = data["facilities"]
    assert set(facilities) == {"dotmac-deployment-foundation"}, (
        "the facility allowlist changed. Adding a second universal facility "
        "or removing the one listed is a reviewed diff, not an incidental "
        "edit — see the file's own $comment for why it is populated at all."
    )


def test_the_listed_facilitys_package_directory_exists_and_matches() -> None:
    entry = _load_json(FACILITY_ALLOWLIST)["facilities"]["dotmac-deployment-foundation"]
    package_dir = PROJECT_ROOT / entry["package_dir"]
    assert package_dir.is_dir(), f"{package_dir} does not exist"

    pyproject_path = package_dir / "pyproject.toml"
    assert pyproject_path.is_file(), f"{pyproject_path} does not exist"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    assert (
        pyproject["tool"]["poetry"]["name"] == "dotmac-deployment-foundation"
    ), "the allowlisted package_dir's pyproject.toml declares a different name"

    dossier_path = package_dir / "EXTRACTION.toml"
    assert dossier_path.is_file(), f"{dossier_path} does not exist"
    dossier = tomllib.loads(dossier_path.read_text(encoding="utf-8"))
    assert dossier["classification"] == "universal-facility"


def test_the_facility_entry_carries_no_stateful_facts() -> None:
    """The same ratchet `test_adapter_release_lane.py` holds for the adapter
    lane: a schema, a manifest attribute or a kernel floor means the package
    is a module and belongs in `release-modules.json` instead."""
    entry = _load_json(FACILITY_ALLOWLIST)["facilities"]["dotmac-deployment-foundation"]
    for field in ("db_schema", "manifest_attr", "kernel_floor"):
        assert field not in entry, field


def test_the_facility_tag_prefix_matches_the_distribution_name() -> None:
    entry = _load_json(FACILITY_ALLOWLIST)["facilities"]["dotmac-deployment-foundation"]
    assert entry["tag_prefix"] == "dotmac-deployment-foundation-v"


def test_the_facility_declares_the_exact_controller_receipt_and_launcher() -> None:
    entry = _load_json(FACILITY_ALLOWLIST)["facilities"]["dotmac-deployment-foundation"]
    assert entry["controller_receipt_schema"] == "DeploymentControllerReleaseReceipt.v1"
    assert entry["controller_launcher"] == "scripts/run_deployment_controller.py"
    assert entry["controller_generic_package"] == "dotmac-deployment-controller"


# ── 2. release-facility.yml publishes with the PUBLISH credential ───────────


def test_release_facility_workflow_has_the_three_job_shape() -> None:
    workflow = _load_yaml(RELEASE_WORKFLOW)
    assert set(workflow["jobs"]) == {"build", "publish", "verify"}


def _release_concurrency_problems(workflow: dict[str, Any]) -> list[str]:
    concurrency = workflow.get("concurrency", {})
    problems: list[str] = []
    if concurrency.get("group") != (
        "release-facility-${{ inputs.facility }}-${{ inputs.version }}"
    ):
        problems.append("concurrency is not keyed to the facility and version")
    if concurrency.get("cancel-in-progress") is not False:
        problems.append("an in-progress release can be cancelled by another dispatch")
    return problems


def test_release_facility_serializes_each_version_without_cancelling_it() -> None:
    assert _release_concurrency_problems(_load_yaml(RELEASE_WORKFLOW)) == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("group", "release-facility"),
        ("cancel-in-progress", True),
    ],
)
def test_release_facility_concurrency_guard_is_sensitive(
    field: str, replacement: object
) -> None:
    mutated = copy.deepcopy(_load_yaml(RELEASE_WORKFLOW))
    mutated["concurrency"][field] = replacement
    assert _release_concurrency_problems(mutated)


def test_release_facility_publish_job_uses_the_publish_token_and_environment() -> None:
    workflow = _load_yaml(RELEASE_WORKFLOW)
    publish_job = workflow["jobs"]["publish"]
    assert publish_job["environment"] == "registry-release"
    assert "FORGEJO_PUBLISH_TOKEN" in "\n".join(_string_values(publish_job)), (
        "the publish job must reference secrets.FORGEJO_PUBLISH_TOKEN — that "
        "is the whole point of the protected registry-release environment"
    )


def test_release_facility_dispatch_choice_matches_the_allowlist() -> None:
    """The same ratchet `test_adapter_release_lane.py` holds for `adapter`:
    the workflow's convenience `choice` list must match the enforced
    allowlist exactly, so the UI cannot drift from the gate."""
    workflow = _load_yaml(RELEASE_WORKFLOW)
    offered = set(workflow[True]["workflow_dispatch"]["inputs"]["facility"]["options"])
    allowlisted = set(_load_json(FACILITY_ALLOWLIST)["facilities"])
    assert offered == allowlisted


def test_release_facility_reasserts_freshness_before_publish_and_before_verify() -> (
    None
):
    workflow = _load_yaml(RELEASE_WORKFLOW)
    for job_name in ("build", "publish"):
        steps = workflow["jobs"][job_name]["steps"]
        blob = "\n".join(str(step.get("run", "")) for step in steps)
        assert "assert_current_main.sh" in blob, job_name


def _named_steps(workflow: dict[str, Any], job: str) -> dict[str, dict[str, Any]]:
    return {
        str(step["name"]): step
        for step in workflow["jobs"][job]["steps"]
        if "name" in step
    }


def _controller_release_lane_problems(workflow: dict[str, Any]) -> list[str]:
    """Structural guard for build-once receipt transport and verification."""
    problems: list[str] = []
    build = _named_steps(workflow, "build")
    publish = _named_steps(workflow, "publish")
    verify = _named_steps(workflow, "verify")

    required_runs = {
        ("build", "Bind the controller release receipt to the exact built bytes"): (
            "create-controller-receipt",
            '--source-revision "${GITHUB_SHA}"',
            '--release-run-id "${GITHUB_RUN_ID}"',
        ),
        ("publish", "Re-verify the receipt before publishing"): (
            "verify-controller-receipt",
            "DeploymentControllerReleaseReceipt.v1.json",
            "dist/*.whl",
            "controller-release/launcher/run_deployment_controller.py",
        ),
        ("publish", "Publish durable controller launcher and receipt"): (
            "put_controller_asset()",
            "get_controller_asset()",
            "publish_controller_asset()",
            "--request PUT",
            "--request GET",
            '--upload-file "${source_path}"',
            "controller-release/launcher/run_deployment_controller.py",
            "controller-release/DeploymentControllerReleaseReceipt.v1.json",
            '[[ "${status}" == "409" ]]',
            '[[ "${status}" != "201" ]]',
            '[[ "${duplicate_status}" != "409" ]]',
            "cmp --silent",
        ),
        ("verify", "Re-verify the downloaded controller release bundle"): (
            "verify-controller-receipt",
            "controller-release/wheel/*.whl",
        ),
        ("verify", "Download and compare durable controller assets"): (
            "--request GET",
            "registry-controller/run_deployment_controller.py",
            "registry-controller/DeploymentControllerReleaseReceipt.v1.json",
            "cmp --silent",
        ),
        ("verify", "Verify registry wheel bytes against the build receipt"): (
            "pip download",
            "--only-binary=:all:",
            "registry-dist/*.whl",
            "verify-controller-receipt",
            "--receipt registry-controller/DeploymentControllerReleaseReceipt.v1.json",
            "--launcher registry-controller/run_deployment_controller.py",
        ),
    }
    jobs = {"build": build, "publish": publish, "verify": verify}
    for (job, name), needles in required_runs.items():
        step = jobs[job].get(name)
        if step is None:
            problems.append(f"{job}: missing {name!r}")
            continue
        run = str(step.get("run", ""))
        for needle in needles:
            if needle not in run:
                problems.append(f"{job}/{name}: missing {needle!r}")

    build_output = (
        workflow["jobs"]["build"].get("outputs", {}).get("controller_artifact_name")
    )
    if build_output != "${{ steps.resolve.outputs.controller_artifact_name }}":
        problems.append("build: controller artifact name is not exported")
    generic_output = (
        workflow["jobs"]["build"].get("outputs", {}).get("controller_generic_package")
    )
    if generic_output != "${{ steps.resolve.outputs.controller_generic_package }}":
        problems.append("build: controller generic package name is not exported")

    upload = build.get("Upload controller provenance and recovery bundle")
    if upload is None:
        problems.append("build: controller release bundle is not uploaded")
    else:
        settings = upload.get("with", {})
        if (
            settings.get("name")
            != "${{ steps.resolve.outputs.controller_artifact_name }}"
        ):
            problems.append("build: controller artifact name is not the resolved name")
        if settings.get("path") != "controller-release/":
            problems.append("build: controller release directory is not uploaded")
        if settings.get("if-no-files-found") != "error":
            problems.append("build: missing controller files do not fail the upload")
        if settings.get("retention-days") != 90:
            problems.append("build: controller release retention changed")

    generic_base = (
        "https://registry.dotmac.io/api/packages/dotmac/generic/"
        "${{ needs.build.outputs.controller_generic_package }}/"
        "${{ inputs.version }}"
    )
    generic_publish = publish.get("Publish durable controller launcher and receipt")
    if generic_publish is None:
        problems.append("publish: durable controller assets are not published")
    else:
        command = str(generic_publish.get("run", ""))
        generic_env = generic_publish.get("env", {})
        if generic_env.get("GENERIC_BASE") != generic_base:
            problems.append("publish: generic package endpoint drifted")
        if generic_env.get("GENERIC_TOKEN") != ("${{ secrets.FORGEJO_PUBLISH_TOKEN }}"):
            problems.append("publish: generic assets use a different credential")
        if command.count("--upload-file") != 1:
            problems.append("publish: generic PUT is not centralized in one helper")
        if command.count("--request PUT") != 1:
            problems.append("publish: generic PUT bypasses the one checked helper")
        if ".whl" in command or "dist/" in command:
            problems.append("publish: wheel is being republished generically")
        if '--write-out "%{http_code}"' not in command:
            problems.append("publish: generic PUT response status is not observed")

        retry_requirements = (
            'if [[ "${status}" == "409" ]]; then',
            'if ! get_controller_asset "${target_name}" "${remote_copy}"; then',
            'if ! cmp --silent "${source_path}" "${remote_copy}"; then',
            'elif [[ "${status}" != "201" ]]; then',
            'if [[ "${duplicate_status}" != "409" ]]; then',
        )
        for requirement in retry_requirements:
            start = command.find(requirement)
            line_start = command.rfind("\n", 0, start) + 1
            indentation = command[line_start:start]
            end = command.find(f"\n{indentation}fi", start)
            if start < 0 or end < 0 or "exit 1" not in command[start:end]:
                problems.append(
                    f"publish: retry/create-only guard lacks {requirement!r}"
                )
        if command.count("$(put_controller_asset ") != 2:
            problems.append("publish: each asset does not receive PUT plus probe")
        if command.count("publish_controller_asset \\\n") != 2:
            problems.append("publish: launcher and receipt are not each published once")
        launcher_position = command.rfind(
            "controller-release/launcher/run_deployment_controller.py"
        )
        receipt_position = command.rfind(
            "controller-release/DeploymentControllerReleaseReceipt.v1.json"
        )
        if launcher_position < 0 or receipt_position <= launcher_position:
            problems.append("publish: receipt is not the final durable asset")

    durable_download = verify.get("Download and compare durable controller assets")
    if durable_download is None:
        problems.append("verify: durable controller assets are not downloaded")
    else:
        durable_env = durable_download.get("env", {})
        durable_command = str(durable_download.get("run", ""))
        if durable_env.get("GENERIC_BASE") != generic_base:
            problems.append("verify: generic package endpoint differs from publish")
        if durable_env.get("GENERIC_TOKEN") != ("${{ secrets.FORGEJO_PUBLISH_TOKEN }}"):
            problems.append("verify: durable assets use a different credential")
        if durable_command.count("--request GET") != 2:
            problems.append("verify: durable download is not exactly two files")
        if durable_command.count("cmp --silent") != 2:
            problems.append("verify: both durable files are not byte-compared")
        if ".whl" in durable_command:
            problems.append("verify: wheel is being fetched from the generic registry")

    expected_artifact = "${{ needs.build.outputs.controller_artifact_name }}"
    for job, steps in (("publish", publish), ("verify", verify)):
        download = steps.get("Download the exact controller release bundle")
        if (
            download is None
            or download.get("with", {}).get("name") != expected_artifact
        ):
            problems.append(f"{job}: exact controller artifact is not downloaded")

    publish_order = list(publish)
    if (
        "Re-verify the receipt before publishing" in publish_order
        and "Publish to the Forgejo private index" in publish_order
        and publish_order.index("Re-verify the receipt before publishing")
        > publish_order.index("Publish to the Forgejo private index")
    ):
        problems.append("publish: receipt verification happens after publication")
    verify_order = list(verify)
    if (
        "Download and compare durable controller assets" in verify_order
        and "Tag the verified release" in verify_order
        and verify_order.index("Download and compare durable controller assets")
        > verify_order.index("Tag the verified release")
    ):
        problems.append("verify: durable asset proof happens after tagging")
    if (
        "Verify registry wheel bytes against the build receipt" in verify_order
        and "Tag the verified release" in verify_order
        and verify_order.index("Verify registry wheel bytes against the build receipt")
        > verify_order.index("Tag the verified release")
    ):
        problems.append("verify: registry byte proof happens after tagging")
    return problems


def test_controller_release_is_carried_and_verified_as_one_build() -> None:
    assert _controller_release_lane_problems(_load_yaml(RELEASE_WORKFLOW)) == []


@pytest.mark.parametrize(
    ("job", "step", "needle"),
    [
        (
            "build",
            "Bind the controller release receipt to the exact built bytes",
            '--release-run-id "${GITHUB_RUN_ID}"',
        ),
        (
            "publish",
            "Re-verify the receipt before publishing",
            "verify-controller-receipt",
        ),
        (
            "publish",
            "Publish durable controller launcher and receipt",
            "put_controller_asset()",
        ),
        (
            "verify",
            "Download and compare durable controller assets",
            "cmp --silent",
        ),
        (
            "verify",
            "Verify registry wheel bytes against the build receipt",
            "--only-binary=:all:",
        ),
        (
            "verify",
            "Verify registry wheel bytes against the build receipt",
            "verify-controller-receipt",
        ),
    ],
)
def test_controller_release_lane_guard_is_sensitive(
    job: str, step: str, needle: str
) -> None:
    """Removing each load-bearing leg makes the same structural guard fail."""
    mutated = copy.deepcopy(_load_yaml(RELEASE_WORKFLOW))
    target = _named_steps(mutated, job)[step]
    target["run"] = str(target["run"]).replace(needle, "planted-gap")

    assert _controller_release_lane_problems(mutated)


def test_controller_release_upload_guard_is_sensitive() -> None:
    mutated = copy.deepcopy(_load_yaml(RELEASE_WORKFLOW))
    upload = _named_steps(mutated, "build")[
        "Upload controller provenance and recovery bundle"
    ]
    upload["with"]["path"] = "wheel-only/"

    assert _controller_release_lane_problems(mutated)


def test_controller_generic_publication_refuses_a_second_wheel_copy() -> None:
    mutated = copy.deepcopy(_load_yaml(RELEASE_WORKFLOW))
    publish = _named_steps(mutated, "publish")[
        "Publish durable controller launcher and receipt"
    ]
    publish["run"] += "\ncurl --upload-file dist/controller.whl generic/wheel.whl\n"

    assert _controller_release_lane_problems(mutated)


@pytest.mark.parametrize(
    "needle",
    [
        '[[ "${status}" == "409" ]]',
        'get_controller_asset "${target_name}" "${remote_copy}"',
        'cmp --silent "${source_path}" "${remote_copy}"',
        '[[ "${status}" != "201" ]]',
        '[[ "${duplicate_status}" != "409" ]]',
    ],
)
def test_controller_generic_create_only_guard_is_sensitive(needle: str) -> None:
    mutated = copy.deepcopy(_load_yaml(RELEASE_WORKFLOW))
    publish = _named_steps(mutated, "publish")[
        "Publish durable controller launcher and receipt"
    ]
    publish["run"] = str(publish["run"]).replace(needle, "[[ planted-gap ]]")

    assert _controller_release_lane_problems(mutated)


def test_controller_generic_unexpected_status_must_exit_guard_is_sensitive() -> None:
    mutated = copy.deepcopy(_load_yaml(RELEASE_WORKFLOW))
    publish = _named_steps(mutated, "publish")[
        "Publish durable controller launcher and receipt"
    ]
    command = str(publish["run"])
    condition = 'if [[ "${duplicate_status}" != "409" ]]; then'
    condition_start = command.index(condition)
    condition_end = command.index("\nfi", condition_start)
    refusal = command[condition_start:condition_end]
    publish["run"] = (
        command[:condition_start]
        + refusal.replace("exit 1", "true", 1)
        + command[condition_end:]
    )

    assert _controller_release_lane_problems(mutated)


def test_controller_generic_receipt_last_guard_is_sensitive() -> None:
    mutated = copy.deepcopy(_load_yaml(RELEASE_WORKFLOW))
    publish = _named_steps(mutated, "publish")[
        "Publish durable controller launcher and receipt"
    ]
    command = str(publish["run"])
    launcher = (
        "controller-release/launcher/run_deployment_controller.py \\\n"
        "  run_deployment_controller.py \\\n"
        "  launcher"
    )
    receipt = (
        "controller-release/DeploymentControllerReleaseReceipt.v1.json \\\n"
        "  DeploymentControllerReleaseReceipt.v1.json \\\n"
        "  receipt"
    )
    publish["run"] = (
        command.replace(launcher, "swapped-status")
        .replace(receipt, launcher)
        .replace("swapped-status", receipt)
    )

    assert _controller_release_lane_problems(mutated)


# ── 3 & 4. deployment-conformance.yml: no publish token, no public PyPI ─────


def test_deployment_conformance_never_references_the_publish_token() -> None:
    assert not _references(CONFORMANCE_WORKFLOW, "FORGEJO_PUBLISH_TOKEN")


def test_deployment_conformance_never_references_public_pypi() -> None:
    assert not _references(CONFORMANCE_WORKFLOW, "pypi.org")


def test_deployment_conformance_installs_from_the_private_index_as_ci_reader() -> None:
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    blob = "\n".join(_string_values(workflow))
    assert "registry.dotmac.io" in blob
    assert "ci-reader" in blob
    assert "FORGEJO_READ_TOKEN" in blob


def test_deployment_conformance_runs_check_all() -> None:
    """Problem 3: the gate must run the conformance CHECKS, not merely parse
    the descriptor. `check_all` is `conformance.py`'s aggregate — the
    function whose whole reason to return a list rather than raise is that a
    CI job can report every finding in one pass."""
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    blob = "\n".join(_string_values(workflow))
    assert "from dotmac_deployment_foundation.conformance import (" in blob
    assert "check_all," in blob
    assert "check_all(spec)" in blob


def test_deployment_conformance_declares_the_read_token_as_a_required_secret() -> None:
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    secrets = workflow[True]["workflow_call"]["secrets"]
    assert secrets["FORGEJO_READ_TOKEN"]["required"] is True


def test_deployment_conformance_has_a_require_real_digests_input_default_true() -> None:
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    inputs = workflow[True]["workflow_call"]["inputs"]
    assert inputs["require-real-digests"]["default"] is True
    assert inputs["require-real-digests"]["type"] == "boolean"


def _descriptor_conformance_script() -> str:
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    step = next(
        item
        for item in workflow["jobs"]["descriptor"]["steps"]
        if item.get("name") == "Run the descriptor conformance checks"
    )
    wrapper = step["run"]
    prefix = "python <<'PY'\n"
    assert wrapper.startswith(prefix)
    assert wrapper.endswith("\nPY\n")
    return wrapper.removeprefix(prefix).removesuffix("\nPY\n")


def _execute_descriptor_conformance_script() -> None:
    resolved_workflow = CONFORMANCE_WORKFLOW.resolve()
    assert resolved_workflow.is_relative_to(PROJECT_ROOT.resolve())
    # This sensitivity test intentionally executes the exact checked-in body;
    # the path assertion above is the enforceable premise for the exemption.
    exec(  # noqa: S102
        compile(_descriptor_conformance_script(), resolved_workflow, "exec")
    )


def test_non_strict_digest_mode_removes_only_placeholder_findings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Execute the workflow's actual script against its real placeholder.

    This is the control the original implementation lacked: setting the input
    false must change the verdict of the aggregate check, not merely skip a
    later duplicate after ``check_all`` has already failed.
    """

    monkeypatch.setenv("DESCRIPTOR", str(PROJECT_ROOT / "deploy" / "product.toml"))
    monkeypatch.setenv("REQUIRE_REAL_DIGESTS", "false")
    monkeypatch.syspath_prepend(
        str(PROJECT_ROOT / "packages" / "dotmac-deployment-foundation" / "src")
    )

    _execute_descriptor_conformance_script()

    assert "0 conformance findings" in capsys.readouterr().out


def test_strict_digest_mode_still_refuses_the_same_real_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The opposite verdict proves non-strict mode did not weaken the check."""

    monkeypatch.setenv("DESCRIPTOR", str(PROJECT_ROOT / "deploy" / "product.toml"))
    monkeypatch.setenv("REQUIRE_REAL_DIGESTS", "true")
    monkeypatch.syspath_prepend(
        str(PROJECT_ROOT / "packages" / "dotmac-deployment-foundation" / "src")
    )

    with pytest.raises(SystemExit, match="1"):
        _execute_descriptor_conformance_script()

    output = capsys.readouterr().out
    assert "image.reference is pinned to the placeholder" in output
    assert "assembly.manifest_digest is the placeholder" in output


def test_deployment_conformance_starters_own_descriptor_is_still_all_zero() -> None:
    """Sensitivity proof for `require-real-digests`, run against the REAL
    descriptor this repository ships today: `deploy/product.toml` still
    carries the placeholder, so the new step must have something real to
    fail on rather than being proven only in the abstract."""
    descriptor = (PROJECT_ROOT / "deploy" / "product.toml").read_text(encoding="utf-8")
    assert "0" * 64 in descriptor, (
        "deploy/product.toml no longer carries an all-zeros placeholder — "
        "if this is because real digests were wired in, this test (and the "
        "adopter workflow's dispatch-only header) should be updated together"
    )


def test_the_non_strict_image_audit_skip_is_unmistakable() -> None:
    """Michael's finding: a non-strict audit could 'return success for the
    impossible image'. The skip path must warn LOUDLY (not merely notice,
    which a quiet green run can bury) and must write to the job summary,
    so the run's summary page — not just the log a nobody reads — says
    SKIPPED rather than passed."""
    workflow = _load_yaml(CONFORMANCE_WORKFLOW)
    image_steps = workflow["jobs"]["image"]["steps"]
    blob = "\n".join(str(step.get("run", "")) for step in image_steps)
    assert "::warning::" in blob
    assert "GITHUB_STEP_SUMMARY" in blob
    assert "SKIPPED" in blob


# ── the sensitivity proof: a planted token IS caught, a comment is NOT ──────


def test_a_planted_publish_token_is_caught_by_the_same_predicate() -> None:
    """SENSITIVITY PROOF. `test_deployment_conformance_never_references_the_
    publish_token` passing could mean the rule works, or it could mean the
    predicate is vacuous. Prove the difference: mutate a real copy of the
    parsed workflow so a string value actually carries the forbidden token,
    and show the exact same predicate now fails."""
    tree = _load_yaml(CONFORMANCE_WORKFLOW)
    mutated = copy.deepcopy(tree)
    mutated["jobs"]["descriptor"]["steps"].append(
        {
            "name": "planted violation",
            "env": {"TWINE_PASSWORD": "${{ secrets.FORGEJO_PUBLISH_TOKEN }}"},
            "run": "echo hi",
        }
    )
    assert any("FORGEJO_PUBLISH_TOKEN" in v for v in _string_values(mutated))


def test_a_comment_mentioning_the_publish_token_does_not_trip_the_predicate() -> None:
    """NEGATIVE control for the sensitivity proof above. A `#`-prefixed YAML
    comment mentioning the forbidden token — exactly the shape this
    workflow's own header comment uses to EXPLAIN the rule — must not trip
    the guard, because a guard that flags its own documentation gets
    disabled by the next person who hits it. `raw_text` finding the needle
    while the parsed-tree walk does not is the demonstration that this
    predicate is immune to the false positive a `needle in raw_text` check
    would produce.
    """
    synthetic = (
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "# This workflow must NEVER reference FORGEJO_PUBLISH_TOKEN or "
        "pypi.org.\n"
        "jobs:\n"
        "  x:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo hi\n"
    )
    assert "FORGEJO_PUBLISH_TOKEN" in synthetic  # the raw text DOES contain it
    assert "pypi.org" in synthetic

    tree = yaml.safe_load(synthetic)
    values = _string_values(tree)
    assert not any("FORGEJO_PUBLISH_TOKEN" in v for v in values), (
        "a YAML comment survived parsing into the data structure — it should "
        "have been discarded before this predicate ever ran"
    )
    assert not any("pypi.org" in v for v in values)


def test_real_conformance_workflow_is_the_negative_control() -> None:
    """The real file must pass both rules — proving the predicate above is
    not merely capable of failing, it correctly does not fire on the
    document this repository actually ships."""
    assert not _references(CONFORMANCE_WORKFLOW, "FORGEJO_PUBLISH_TOKEN")
    assert not _references(CONFORMANCE_WORKFLOW, "pypi.org")


# ── 5. deployment-adopter.yml is workflow_dispatch only ─────────────────────


def test_deployment_adopter_is_workflow_dispatch_only() -> None:
    workflow = _load_yaml(ADOPTER_WORKFLOW)
    triggers = workflow[True]
    assert set(triggers) == {"workflow_dispatch"}, (
        "deployment-adopter.yml must stay dispatch-only until "
        "dotmac-deployment-foundation is published AND deploy/product.toml's "
        "placeholder digests are replaced — see the file's own header for "
        "the exact re-enable condition. Running it on every push/PR before "
        "then teaches reviewers to ignore a check that cannot pass."
    )


def test_deployment_adopter_still_calls_the_real_reusable_workflow() -> None:
    """Specificity for the test above: dispatch-only must not have been
    achieved by disconnecting the job from the gate it exists to exercise."""
    workflow = _load_yaml(ADOPTER_WORKFLOW)
    deployment_job = workflow["jobs"]["deployment"]
    assert deployment_job["uses"] == "./.github/workflows/deployment-conformance.yml"
    assert "FORGEJO_READ_TOKEN" in deployment_job["secrets"]


def test_deployment_adopter_is_not_yet_wired_into_required_checks() -> None:
    """A guard that only checks `on:` could pass while the job is still
    reachable through some other trigger this file does not model (a
    `workflow_run`, say). Specificity: there must be exactly the one trigger
    key, holding no branch filters that would make it push/PR-shaped."""
    workflow = _load_yaml(ADOPTER_WORKFLOW)
    triggers = workflow[True]
    assert triggers == {"workflow_dispatch": {}} or triggers == {
        "workflow_dispatch": None
    }
