from __future__ import annotations

import sys
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from collect_github_release_artifact import (  # noqa: E402
    GitHubRedirect,
    require_canonical_workflow,
)
from collect_private_registry_files import SameOriginRedirect  # noqa: E402
from release_artifact_verification import (  # noqa: E402
    CleanInstallObservation,
    RegistryFile,
    ReleaseArtifactRefused,
    RetainedBuildFile,
    verify_release_artifacts,
)
from verify_kernel_release_artifacts import require_canonical_facility  # noqa: E402

WORKFLOW = ROOT / ".github/workflows/verify-kernel-release.yml"
NAMES = frozenset(
    {
        "dotmac_kernel-0.1.0a100-py3-none-any.whl",
        "dotmac_kernel-0.1.0a100.tar.gz",
    }
)


def build(name: str, digest: str = "a" * 64) -> RetainedBuildFile:
    return RetainedBuildFile(name=name, size=12, sha256=digest)


def registry(name: str, digest: str = "a" * 64) -> RegistryFile:
    return RegistryFile(name=name, size=12, sha256=digest)


def install(name: str, *, passed: bool = True) -> CleanInstallObservation:
    return CleanInstallObservation(
        name=name,
        distribution="dotmac-kernel",
        version="0.1.0a100",
        dependencies_resolved=passed,
        metadata_matches=passed,
        import_passed=passed,
    )


def decide(
    *,
    retained: list[RetainedBuildFile] | None = None,
    read_back: list[RegistryFile] | None = None,
    installs: list[CleanInstallObservation] | None = None,
):
    return verify_release_artifacts(
        expected_names=NAMES,
        retained=retained or [build(name) for name in NAMES],
        registry=read_back or [registry(name) for name in NAMES],
        installs=installs or [install(name) for name in NAMES],
        distribution="dotmac-kernel",
        version="0.1.0a100",
    )


def test_wheel_and_sdist_must_both_match_and_clean_install() -> None:
    assert decide()["verdict"] == "verified"
    for missing in NAMES:
        with pytest.raises(ReleaseArtifactRefused, match="names"):
            decide(read_back=[registry(name) for name in NAMES - {missing}])
        with pytest.raises(ReleaseArtifactRefused, match="names"):
            decide(installs=[install(name) for name in NAMES - {missing}])


def test_a_byte_mismatch_or_failed_import_refuses() -> None:
    changed = next(iter(NAMES))
    with pytest.raises(ReleaseArtifactRefused, match="registry bytes differ"):
        decide(
            read_back=[
                registry(name, digest="b" * 64 if name == changed else "a" * 64)
                for name in NAMES
            ]
        )
    with pytest.raises(ReleaseArtifactRefused, match="clean install did not pass"):
        decide(installs=[install(name, passed=name != changed) for name in NAMES])


def test_noncanonical_versions_are_refused_before_collection() -> None:
    from release_artifact_verification import canonical_kernel_filenames

    for value in ("0.1.0a0", "00.1.0a100", "0.01.0a100", "0.1.00a100", "0.1.0a0100"):
        with pytest.raises(ReleaseArtifactRefused, match="canonical public prerelease"):
            canonical_kernel_filenames(value)


def test_registry_redirect_never_carries_auth_cross_origin() -> None:
    request = urllib.request.Request(
        "https://registry.dotmac.io/simple", headers={"Authorization": "Basic redacted"}
    )
    handler = SameOriginRedirect("registry.dotmac.io")
    with pytest.raises(urllib.error.HTTPError, match="unsafe redirect"):
        handler.redirect_request(
            request, None, 302, "found", {}, "https://objects.example/artifact"
        )


def test_github_signed_download_redirect_strips_bearer_token() -> None:
    request = urllib.request.Request(
        "https://api.github.com/artifact", headers={"Authorization": "Bearer redacted"}
    )
    redirected = GitHubRedirect().redirect_request(
        request, None, 302, "found", {}, "https://objects.example/signed"
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None


def workflow_is_verify_only(document: dict[str, object]) -> bool:
    if document.get("permissions") != {"contents": "read", "actions": "read"}:
        return False
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != {"verify"}:
        return False
    job = jobs["verify"]
    if not isinstance(job, dict) or "environment" in job:
        return False
    steps = job.get("steps")
    if not isinstance(steps, list):
        return False
    expected_names = [
        None,
        None,
        "Bind the verification facility to current protected main",
        "Collect the retained build bytes from the original run",
        "Collect the named bytes from the private registry",
        "Re-assert current protected main before the decision",
        "Compare every byte and clean-install both artifacts",
        "Retain the typed verification receipt",
    ]
    if [step.get("name") for step in steps] != expected_names:
        return False
    if steps[3].get("run") != "python scripts/collect_github_release_artifact.py":
        return False
    if steps[4].get("run") != "python scripts/collect_private_registry_files.py":
        return False
    if steps[6].get("run") != "python scripts/verify_kernel_release_artifacts.py":
        return False
    receipt = steps[7].get("with", {}).get("path")
    if receipt != "${{ runner.temp }}/receipt/KernelReleaseVerificationReceipt.v1.json":
        return False
    allowed_uses = {
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    }
    forbidden = (
        "twine",
        "poetry build",
        "git tag",
        "git push",
        "FORGEJO_PUBLISH_TOKEN",
        "registry-release",
    )
    for step in steps:
        if not isinstance(step, dict):
            return False
        if "uses" in step and step["uses"] not in allowed_uses:
            return False
        run = str(step.get("run", ""))
        if "${{ inputs." in run or any(term in run for term in forbidden):
            return False
    return True


def test_verify_only_workflow_cannot_publish_build_or_tag() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow_is_verify_only(document)
    steps = document["jobs"]["verify"]["steps"]
    checkout = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["persist-credentials"] is False

    permission_mutation = deepcopy(document)
    permission_mutation["permissions"]["contents"] = "write"
    assert not workflow_is_verify_only(permission_mutation)
    publish_mutation = deepcopy(document)
    publish_mutation["jobs"]["verify"]["steps"][0]["run"] = "twine upload dist/*"
    assert not workflow_is_verify_only(publish_mutation)
    order_mutation = deepcopy(document)
    order_mutation["jobs"]["verify"]["steps"][4:6] = reversed(
        order_mutation["jobs"]["verify"]["steps"][4:6]
    )
    assert not workflow_is_verify_only(order_mutation)
    source_mutation = deepcopy(document)
    source_mutation["jobs"]["verify"]["steps"][3]["run"] = "curl https://elsewhere"
    assert not workflow_is_verify_only(source_mutation)
    receipt_mutation = deepcopy(document)
    receipt_mutation["jobs"]["verify"]["steps"][7]["with"]["path"] = "elsewhere"
    assert not workflow_is_verify_only(receipt_mutation)


def test_provider_neutral_decision_core_has_no_transport_identity() -> None:
    source = (
        (SCRIPTS / "release_artifact_verification.py")
        .read_text(encoding="utf-8")
        .lower()
    )
    for forbidden in ("github", "forgejo", "registry.dotmac.io", "token", "workflow"):
        assert forbidden not in source


def test_clean_install_uses_only_distribution_declared_dependencies() -> None:
    source = (SCRIPTS / "verify_kernel_release_artifacts.py").read_text(
        encoding="utf-8"
    )
    assert "psycopg[binary]" not in source
    assert '"--isolated"' in source
    assert '"https://pypi.org/simple"' in source
    assert '[str(python), "-m", "pip", "check"]' in source


def test_provider_adapters_hard_bind_release_coordinates() -> None:
    github = (SCRIPTS / "collect_github_release_artifact.py").read_text(
        encoding="utf-8"
    )
    registry = (SCRIPTS / "collect_private_registry_files.py").read_text(
        encoding="utf-8"
    )
    verifier = (SCRIPTS / "verify_kernel_release_artifacts.py").read_text(
        encoding="utf-8"
    )
    for source in (github, verifier):
        assert 'CANONICAL_REPOSITORY = "michaelayoade/dotmac_starter_mt"' in source
        assert (
            'CANONICAL_WORKFLOW_PATH = ".github/workflows/release-kernel.yml"' in source
        )
        assert 'CANONICAL_ARTIFACT_NAME = "dotmac-kernel-dist"' in source
    assert 'REGISTRY_ORIGIN = "https://registry.dotmac.io"' in registry
    assert 'REGISTRY_LOGIN = "ci-reader"' in registry
    for forbidden in (
        "ORIGINAL_REPOSITORY",
        "ORIGINAL_WORKFLOW_PATH",
        "ORIGINAL_ARTIFACT_NAME",
        "REGISTRY_INDEX_URL",
        "REGISTRY_IDENTITY_URL",
        "REGISTRY_USERNAME",
        "REGISTRY_EXPECTED_ORIGIN",
    ):
        assert forbidden not in WORKFLOW.read_text(encoding="utf-8")


def test_forked_or_non_main_verification_facility_is_refused() -> None:
    require_canonical_facility("michaelayoade/dotmac_starter_mt", "refs/heads/main")
    with pytest.raises(SystemExit, match="repository is not canonical"):
        require_canonical_facility("outsider/fork", "refs/heads/main")
    with pytest.raises(SystemExit, match="not dispatched from main"):
        require_canonical_facility(
            "michaelayoade/dotmac_starter_mt", "refs/heads/feature"
        )


def test_workflow_binding_uses_run_path_not_numeric_workflow_url() -> None:
    require_canonical_workflow(
        {
            "path": ".github/workflows/release-kernel.yml",
            "workflow_url": (
                "https://api.github.com/repos/michaelayoade/"
                "dotmac_starter_mt/actions/workflows/323775252"
            ),
        }
    )
    with pytest.raises(SystemExit, match="workflow path differs"):
        require_canonical_workflow(
            {
                "path": ".github/workflows/not-the-release.yml",
                "workflow_url": (
                    "https://api.github.com/repos/michaelayoade/"
                    "dotmac_starter_mt/actions/workflows/323775252"
                ),
            }
        )
