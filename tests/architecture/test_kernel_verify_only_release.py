from __future__ import annotations

import json
import subprocess
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
    canonical_json,
    verify_release_artifacts,
)
from tag_kernel_release_once import (  # noqa: E402
    TagRefused,
    reconcile_tag,
    require_live_authorization,
    validate_receipt,
)
from verify_kernel_release_artifacts import require_canonical_facility  # noqa: E402

WORKFLOW = ROOT / ".github/workflows/verify-kernel-release.yml"
PUBLISH_WORKFLOW = ROOT / ".github/workflows/release-kernel.yml"
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


def workflow_separates_readback_from_tag_authority(
    document: dict[str, object],
) -> bool:
    if document.get("permissions") != {"contents": "read", "actions": "read"}:
        return False
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != {"verify", "tag_and_record"}:
        return False
    verify = jobs["verify"]
    tag = jobs["tag_and_record"]
    if (
        not isinstance(verify, dict)
        or not isinstance(tag, dict)
        or "environment" in verify
        or "environment" in tag
        or tag.get("needs") != "verify"
        or tag.get("permissions") != {"contents": "write", "actions": "read"}
    ):
        return False
    verify_steps = verify.get("steps")
    tag_steps = tag.get("steps")
    if not isinstance(verify_steps, list) or not isinstance(tag_steps, list):
        return False
    verify_source = json.dumps(verify)
    tag_source = json.dumps(tag)
    whole_source = json.dumps(document)
    if any(
        term in verify_source
        for term in (
            "tag_kernel_release_once",
            "contents: write",
            "twine upload",
            "poetry build",
            "FORGEJO_PUBLISH_TOKEN",
        )
    ):
        return False
    if any(
        term in tag_source
        for term in (
            "FORGEJO_PUBLISH_TOKEN",
            "TWINE_PASSWORD",
            "twine upload",
            "poetry build",
            "collect_private_registry_files",
        )
    ):
        return False
    if "python scripts/tag_kernel_release_once.py" not in tag_source:
        return False
    if tag_source.index("tag_kernel_release_once.py") > tag_source.index(
        "open_release_record_pr.sh"
    ):
        return False
    if "${{ inputs." in "\n".join(
        str(step.get("run", ""))
        for job in jobs.values()
        for step in job.get("steps", [])
    ):
        return False
    if "registry-release" in whole_source:
        return False
    return True


def test_verifier_is_sole_tag_owner_and_cannot_publish_or_build() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow_separates_readback_from_tag_authority(document)
    steps = document["jobs"]["verify"]["steps"]
    checkout = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["persist-credentials"] is False

    permission_mutation = deepcopy(document)
    permission_mutation["permissions"]["contents"] = "write"
    assert not workflow_separates_readback_from_tag_authority(permission_mutation)
    publish_mutation = deepcopy(document)
    publish_mutation["jobs"]["verify"]["steps"][0]["run"] = "twine upload dist/*"
    assert not workflow_separates_readback_from_tag_authority(publish_mutation)
    secret_mutation = deepcopy(document)
    secret_mutation["jobs"]["tag_and_record"]["env"] = {"TWINE_PASSWORD": "writer"}
    assert not workflow_separates_readback_from_tag_authority(secret_mutation)

    publisher = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    assert publisher["permissions"] == {"contents": "read"}
    publisher_source = json.dumps(publisher)
    for forbidden in (
        "tag_kernel_release_once",
        "open_release_record_pr",
        "git tag",
        "git push",
        "contents: write",
    ):
        assert forbidden not in publisher_source

    tag_steps = document["jobs"]["tag_and_record"]["steps"]
    names = [step.get("name") for step in tag_steps]
    rederive = names.index(
        "Re-derive the historical release authorization before tagging"
    )
    final_main = names.index("Final protected-main reassert immediately before tagging")
    mutate = names.index("Create or reconcile the independently verified tag")
    assert (rederive, final_main, mutate) == (
        mutate - 2,
        mutate - 1,
        mutate,
    )
    assert "assert_current_main.sh" in tag_steps[final_main]["run"]


def _tag_fixture(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Tag Test")
    _git(repo, "config", "user.email", "tag@example.invalid")
    (repo / "subject").write_text("one\n")
    _git(repo, "add", "subject")
    _git(repo, "commit", "-m", "subject")
    source = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    return repo, source


def _git(repo: Path, *args: str) -> str:
    # The executable is fixed and all arguments are test literals or paths and
    # SHAs produced by this fixture.
    completed = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_tag_decision_create_already_and_refuse(tmp_path: Path) -> None:
    repo, source = _tag_fixture(tmp_path)
    tag = "dotmac-kernel-v0.1.0a101"
    assert reconcile_tag(repo, tag=tag, source_sha=source)["disposition"] == "CREATE"
    assert reconcile_tag(repo, tag=tag, source_sha=source)["disposition"] == "ALREADY"

    (repo / "subject").write_text("two\n")
    _git(repo, "commit", "-am", "other")
    other = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(TagRefused, match="different bytes"):
        reconcile_tag(repo, tag=tag, source_sha=other)


def test_lightweight_tag_is_refused(tmp_path: Path) -> None:
    repo, source = _tag_fixture(tmp_path)
    tag = "dotmac-kernel-v0.1.0a101"
    _git(repo, "tag", tag, source)
    _git(repo, "push", "origin", tag)
    with pytest.raises(TagRefused, match="lightweight"):
        reconcile_tag(repo, tag=tag, source_sha=source)


def test_tag_requires_the_current_runs_closed_verified_receipt(tmp_path: Path) -> None:
    source = "a" * 40
    facility = "b" * 40
    version = "0.1.0a101"
    tag = f"dotmac-kernel-v{version}"
    names = sorted(
        {
            f"dotmac_kernel-{version}-py3-none-any.whl",
            f"dotmac_kernel-{version}.tar.gz",
        }
    )
    files = [
        {
            "name": name,
            "size": 1,
            "build_sha256": "c" * 64,
            "registry_sha256": "c" * 64,
            "byte_equal": True,
            "clean_install": {
                "name": name,
                "distribution": "dotmac-kernel",
                "version": version,
                "dependencies_resolved": True,
                "metadata_matches": True,
                "import_passed": True,
            },
        }
        for name in names
    ]
    receipt = {
        "schema": "KernelReleaseVerificationReceipt.v1",
        "authorization": {
            "schema": "KernelReleaseSourceBinding.v1",
            "state": "allocated",
            "source_sha": source,
            "authorization_commit": "f" * 40,
            "authorization": {
                "latest_tag": "dotmac-kernel-v0.1.0a100",
                "latest_tag_object": "1" * 40,
                "latest_tag_commit": "2" * 40,
                "base_sha": "3" * 40,
                "target_version": version,
                "normalized_release_input_digest": f"sha256:{'4' * 64}",
            },
        },
        "facility": {
            "repository": "michaelayoade/dotmac_starter_mt",
            "ref": "refs/heads/main",
            "source_sha": facility,
            "run_id": 123,
            "run_attempt": 1,
        },
        "release": {
            "distribution": "dotmac-kernel",
            "version": version,
            "expected_tag": tag,
            "source_sha": source,
            "retained_build_observation": {
                "schema": "GitHubRetainedReleaseArtifactObservation.v1",
                "repository": "michaelayoade/dotmac_starter_mt",
                "workflow_path": ".github/workflows/release-kernel.yml",
                "head_branch": "main",
                "head_sha": source,
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "run_id": 100,
                "run_attempt": 1,
                "artifact_id": 200,
                "artifact_name": "dotmac-kernel-dist",
                "artifact_size_in_bytes": 2,
                "artifact_digest": None,
                "filenames": names,
            },
            "registry_observation": {
                "schema": "PrivateRegistryReadObservation.v1",
                "index_origin": "https://registry.dotmac.io",
                "observed_identity": {"login": "ci-reader", "is_admin": False},
                "facility_http_methods": ["GET"],
                "files": [{"name": name, "size": 1} for name in names],
            },
        },
        "verdict": "verified",
        "files": files,
    }
    validate_receipt(
        receipt,
        version=version,
        tag=tag,
        source_sha=source,
        facility_sha=facility,
        run_id=123,
    )
    live = tmp_path / "live-source-binding.json"
    live.write_bytes(canonical_json(receipt["authorization"]))
    require_live_authorization(receipt, live)
    for path, value in (
        (("authorization_commit",), "0" * 40),
        (("authorization", "base_sha"), "0" * 40),
        (("authorization", "latest_tag_object"), "0" * 40),
        (("authorization", "latest_tag_commit"), "0" * 40),
        (
            ("authorization", "normalized_release_input_digest"),
            f"sha256:{'0' * 64}",
        ),
    ):
        planted = deepcopy(receipt)
        target = planted["authorization"]
        for segment in path[:-1]:
            target = target[segment]
        target[path[-1]] = value
        with pytest.raises(TagRefused, match="live release authorization"):
            require_live_authorization(planted, live)
    wrong = deepcopy(receipt)
    wrong["facility"]["run_id"] = 124
    with pytest.raises(TagRefused, match="facility coordinates"):
        validate_receipt(
            wrong,
            version=version,
            tag=tag,
            source_sha=source,
            facility_sha=facility,
            run_id=123,
        )
    failed = deepcopy(receipt)
    failed["files"][0]["clean_install"]["import_passed"] = False
    with pytest.raises(TagRefused, match="file proof"):
        validate_receipt(
            failed,
            version=version,
            tag=tag,
            source_sha=source,
            facility_sha=facility,
            run_id=123,
        )
    extra = deepcopy(receipt)
    extra["files"][0]["clean_install"]["unexpected"] = True
    with pytest.raises(TagRefused, match="file proof"):
        validate_receipt(
            extra,
            version=version,
            tag=tag,
            source_sha=source,
            facility_sha=facility,
            run_id=123,
        )


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
