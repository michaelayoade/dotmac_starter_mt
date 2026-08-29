"""Adversarial canaries for portable post-completion authorization evidence."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "finalize_deployment_authorization_evidence.py"

AUTHORIZER_REVISION = "a" * 40
APPLICATION_FROM = "b" * 40
APPLICATION_TO = "c" * 40
CONTROLLER_REVISION = "d" * 40
WHEEL_DIGEST = "sha256:" + "1" * 64
LAUNCHER_DIGEST = "sha256:" + "2" * 64
RECEIPT_DIGEST = "sha256:" + "3" * 64
PLAN_DIGEST = "sha256:" + "4" * 64
IMAGE_FROM = "sha256:" + "5" * 64
IMAGE_TO = "sha256:" + "6" * 64
CONFIG_FROM = "sha256:" + "7" * 64
CONFIG_TO = "sha256:" + "8" * 64
MANIFEST_FROM = "sha256:" + "9" * 64
MANIFEST_TO = "sha256:" + "a" * 64
VERSION = "0.3.0a1"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "deployment_authorization_finalizer", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


finalizer = _load()

from dotmac_deployment_foundation.authenticity import (  # noqa: E402
    DeploymentControllerReleaseArtifactV1,
)
from dotmac_deployment_foundation.execution import (  # noqa: E402
    ApplicationReleaseIdentityV1,
    AuthorizerProvenanceV1,
    ControllerProvenanceV1,
    RevisionEvidenceV1,
    RevisionRelation,
)


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _expected(**overrides: object):
    values = {
        "server_origin": "https://github.com",
        "api_url": "https://api.github.com",
        "repository_id": 101,
        "repository": "michaelayoade/dotmac_deploy_owner",
        "head_repository_id": 101,
        "head_repository": "michaelayoade/dotmac_deploy_owner",
        "workflow_id": 202,
        "workflow_name": "Authorize production deployment",
        "workflow_path": ".github/workflows/authorize-production.yml",
        "run_id": 303,
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "head_sha": AUTHORIZER_REVISION,
        "status": "completed",
        "conclusion": "success",
    }
    values.update(overrides)
    return finalizer.ExpectedRun(**values)


def _run_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "id": 303,
        "run_attempt": 1,
        "workflow_id": 202,
        "event": "workflow_dispatch",
        "head_sha": AUTHORIZER_REVISION,
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "referenced_workflows": [],
        "path": ".github/workflows/authorize-production.yml",
        "repository": {
            "id": 101,
            "full_name": "michaelayoade/dotmac_deploy_owner",
        },
        "head_repository": {
            "id": 101,
            "full_name": "michaelayoade/dotmac_deploy_owner",
        },
    }
    document.update(overrides)
    return document


def _workflow_run(
    *,
    branch_document: dict[str, object] | None = None,
    referenced_workflows: tuple[object, ...] = (),
    **run_overrides: object,
):
    return finalizer.validate_authorizer_run(
        run_document=_run_document(**run_overrides),
        repository_document={
            "id": 101,
            "full_name": "michaelayoade/dotmac_deploy_owner",
        },
        workflow_document={
            "id": 202,
            "name": "Authorize production deployment",
            "path": ".github/workflows/authorize-production.yml",
            "state": "active",
        },
        workflow_blob=b"exact authorizing workflow",
        branch_document=branch_document
        or {
            "name": "main",
            "protected": True,
            "commit": {"sha": APPLICATION_TO},
        },
        referenced_workflows=referenced_workflows,
        expected=_expected(),
    )


def _application():
    return finalizer.ExpectedApplicationRepository(
        server_origin="https://github.com",
        api_url="https://api.github.com",
        repository_id=404,
        repository="michaelayoade/dotmac_sub",
    )


def _snapshot(bundle: bytes = b"complete application history"):
    return finalizer.ApplicationHistorySnapshotV1(
        server_origin="https://github.com",
        api_origin="https://api.github.com",
        repository_id=404,
        repository="michaelayoade/dotmac_sub",
        object_format="sha1",
        from_revision=APPLICATION_FROM,
        to_revision=APPLICATION_TO,
        bundle_name=finalizer.HISTORY_NAME,
        bundle_size=len(bundle),
        bundle_sha256=_digest(bundle),
    )


def _release(**overrides: object):
    workflow = finalizer.GitHubWorkflowRunV1(
        server_origin="https://github.com",
        api_origin="https://api.github.com",
        repository_id=505,
        repository="michaelayoade/dotmac_starter_mt",
        head_repository_id=505,
        head_repository="michaelayoade/dotmac_starter_mt",
        workflow_id=606,
        workflow_path=".github/workflows/release-facility.yml",
        workflow_revision=CONTROLLER_REVISION,
        workflow_blob_sha256="sha256:" + "b" * 64,
        head_ref="refs/heads/main",
        referenced_workflows=(),
        run_id=707,
        run_attempt=1,
        event="workflow_dispatch",
        head_sha=CONTROLLER_REVISION,
        status="completed",
        conclusion="success",
    )
    values: dict[str, object] = {
        "workflow_run": workflow,
        "distribution": "dotmac-deployment-foundation",
        "exact_version": VERSION,
        "tag": f"dotmac-deployment-foundation-v{VERSION}",
        "source_revision": CONTROLLER_REVISION,
        "artifacts": (
            DeploymentControllerReleaseArtifactV1(
                name="DeploymentControllerReleaseReceipt.v1.json",
                media_type="application/json",
                size=128,
                sha256=RECEIPT_DIGEST,
            ),
            DeploymentControllerReleaseArtifactV1(
                name=("dotmac_deployment_foundation-0.3.0a1-py3-none-any.whl"),
                media_type="application/zip",
                size=4096,
                sha256=WHEEL_DIGEST,
            ),
            DeploymentControllerReleaseArtifactV1(
                name="run_deployment_controller.py",
                media_type="text/x-python",
                size=2048,
                sha256=LAUNCHER_DIGEST,
            ),
        ),
    }
    values.update(overrides)
    return finalizer.DeploymentControllerReleaseEvidenceV1(**values)


def _envelope(snapshot=None):
    history = snapshot or _snapshot()
    current = ApplicationReleaseIdentityV1(
        image_digest=IMAGE_FROM,
        source_revision=APPLICATION_FROM,
        configuration_digest=CONFIG_FROM,
        manifest_digest=MANIFEST_FROM,
    )
    candidate = ApplicationReleaseIdentityV1(
        image_digest=IMAGE_TO,
        source_revision=APPLICATION_TO,
        configuration_digest=CONFIG_TO,
        manifest_digest=MANIFEST_TO,
    )
    return finalizer.DeploymentExecutionEnvelopeV1(
        execution_id="production-authorization-303",
        product="dotmac_sub",
        target_ref="production:dotmac-sub",
        plan_digest=PLAN_DIGEST,
        required_controller=ControllerProvenanceV1(
            distribution="dotmac-deployment-foundation",
            exact_version=VERSION,
            artifact_sha256=WHEEL_DIGEST,
            launcher_sha256=LAUNCHER_DIGEST,
            source_revision=CONTROLLER_REVISION,
            release_run_id=707,
            tag=f"dotmac-deployment-foundation-v{VERSION}",
        ),
        authorizer=AuthorizerProvenanceV1(
            repository="michaelayoade/dotmac_deploy_owner",
            workflow_path=".github/workflows/authorize-production.yml",
            workflow_revision=AUTHORIZER_REVISION,
            run_id=303,
        ),
        candidate=candidate,
        expected_current=current,
        relation_evidence=RevisionEvidenceV1(
            relation=RevisionRelation.FORWARD,
            from_revision=APPLICATION_FROM,
            to_revision=APPLICATION_TO,
            history_snapshot_digest=history.snapshot_digest,
        ),
        override=None,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("id", 999),
        ("run_attempt", 2),
        ("workflow_id", 999),
        ("event", "push"),
        ("head_sha", "f" * 40),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("path", ".github/workflows/foreign.yml"),
    ],
)
def test_every_terminal_authorizer_coordinate_is_requeried(
    field: str, replacement: object
) -> None:
    with pytest.raises(finalizer.FinalizationRefused):
        _workflow_run(**{field: replacement})  # type: ignore[arg-type]


def test_foreign_authorizer_repository_is_refused_even_when_green() -> None:
    with pytest.raises(finalizer.FinalizationRefused):
        _workflow_run(
            repository={"id": 999, "full_name": "attacker/foreign"},
            head_repository={"id": 999, "full_name": "attacker/foreign"},
        )


def test_exact_authorizer_workflow_blob_is_bound() -> None:
    first = _workflow_run()
    second = finalizer.validate_authorizer_run(
        run_document=_run_document(),
        repository_document={
            "id": 101,
            "full_name": "michaelayoade/dotmac_deploy_owner",
        },
        workflow_document={
            "id": 202,
            "name": "Authorize production deployment",
            "path": ".github/workflows/authorize-production.yml",
            "state": "active",
        },
        workflow_blob=b"substituted authorizing workflow",
        branch_document={"name": "main", "protected": True},
        referenced_workflows=(),
        expected=_expected(),
    )
    assert first.workflow_blob_sha256 != second.workflow_blob_sha256


def test_authorizer_path_suffix_and_protected_branch_are_bound() -> None:
    workflow = _workflow_run(
        path=".github/workflows/authorize-production.yml@refs/heads/main",
        branch_document={
            "name": "main",
            "protected": True,
            "commit": {"sha": APPLICATION_TO},
        },
    )
    assert workflow.head_ref == "refs/heads/main"
    assert workflow.head_sha == AUTHORIZER_REVISION
    assert (
        _workflow_run(path=".github/workflows/authorize-production.yml@main").head_ref
        == "refs/heads/main"
    )

    with pytest.raises(finalizer.FinalizationRefused):
        _workflow_run(
            path=".github/workflows/authorize-production.yml@refs/heads/foreign"
        )
    with pytest.raises(finalizer.FinalizationRefused):
        _workflow_run(branch_document={"name": "main", "protected": False})


def test_authorization_api_origin_is_refused_before_credentials_are_installed() -> None:
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.HttpReader(
            server_origin="https://github.com",
            api_url="https://foreign.example/api/v3",
            github_token="not-a-real-token",
        )


class _ReferencedWorkflowReader:
    def github_blob(self, url: str, *, name: str) -> bytes:
        del name
        assert url == (
            "https://api.github.com/repos/dotmac/shared/contents/"
            ".github/workflows/authorize.yml?ref=" + CONTROLLER_REVISION
        )
        return b"exact reusable authorizer"


def test_reusable_authorizer_ref_revision_and_blob_are_all_bound() -> None:
    run = _run_document(
        referenced_workflows=[
            {
                "path": "dotmac/shared/.github/workflows/authorize.yml@stable",
                "ref": "refs/heads/stable",
                "sha": CONTROLLER_REVISION,
            }
        ]
    )
    observed = finalizer._referenced_workflows(
        reader=_ReferencedWorkflowReader(),
        expected=_expected(),
        run_document=run,
    )
    assert len(observed) == 1
    assert observed[0].workflow_ref == "stable"
    assert observed[0].workflow_revision == CONTROLLER_REVISION
    assert observed[0].workflow_blob_sha256 == _digest(b"exact reusable authorizer")

    with pytest.raises(finalizer.FinalizationRefused):
        finalizer._referenced_workflows(
            reader=_ReferencedWorkflowReader(),
            expected=_expected(),
            run_document=_run_document(
                referenced_workflows=[
                    {
                        "path": (
                            "dotmac/shared/.github/workflows/authorize.yml@foreign"
                        ),
                        "ref": "refs/heads/stable",
                        "sha": CONTROLLER_REVISION,
                    }
                ]
            ),
        )
    missing = _run_document()
    missing.pop("referenced_workflows")
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer._referenced_workflows(
            reader=_ReferencedWorkflowReader(),
            expected=_expected(),
            run_document=missing,
        )


def test_application_repository_and_commit_are_independently_pinned() -> None:
    expected = _application()
    finalizer.validate_application_repository(
        {"id": 404, "full_name": "michaelayoade/dotmac_sub"},
        expected=expected,
    )
    finalizer.validate_application_commit(
        {"sha": APPLICATION_TO},
        revision=APPLICATION_TO,
    )

    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.validate_application_repository(
            {"id": 999, "full_name": "attacker/fork"},
            expected=expected,
        )
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.validate_application_commit(
            {"sha": "f" * 40},
            revision=APPLICATION_TO,
        )


def _git_binary() -> Path:
    found = shutil.which("git")
    if found is None:
        pytest.skip("Git is required for the history bundle canary")
    return Path(found).resolve()


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed Git binary and test argv
        [str(_git_binary()), *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _history_bundle(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "application"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Authorization Canary")
    _git(repository, "config", "user.email", "canary@example.invalid")
    payload = repository / "payload.txt"
    payload.write_text("from\n", encoding="utf-8")
    _git(repository, "add", "payload.txt")
    _git(repository, "commit", "-m", "from")
    from_revision = _git(repository, "rev-parse", "HEAD")
    payload.write_text("to\n", encoding="utf-8")
    _git(repository, "commit", "-am", "to")
    to_revision = _git(repository, "rev-parse", "HEAD")
    bundle = tmp_path / "source-history.bundle"
    _git(repository, "bundle", "create", str(bundle), "--all")
    return bundle, from_revision, to_revision


def test_history_bundle_contains_the_exact_transition(tmp_path: Path) -> None:
    bundle, from_revision, to_revision = _history_bundle(tmp_path)

    snapshot = finalizer.verify_history_bundle(
        bundle=bundle,
        git_binary=_git_binary(),
        application=_application(),
        from_revision=from_revision,
        to_revision=to_revision,
    )

    assert snapshot.from_revision == from_revision
    assert snapshot.to_revision == to_revision
    assert snapshot.bundle_sha256 == _digest(bundle.read_bytes())


def test_corrupt_or_incomplete_history_bundle_is_refused(tmp_path: Path) -> None:
    bundle = tmp_path / "hostile.bundle"
    bundle.write_bytes(b"not a Git bundle")

    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.verify_history_bundle(
            bundle=bundle,
            git_binary=_git_binary(),
            application=_application(),
            from_revision=APPLICATION_FROM,
            to_revision=APPLICATION_TO,
        )


def test_history_or_controller_rebinding_is_refused() -> None:
    healthy_history = _snapshot()
    envelope = _envelope(healthy_history)
    controller_release = _release()
    healthy = finalizer.build_authorization_evidence(
        workflow_run=_workflow_run(),
        envelope=envelope,
        controller_release=controller_release,
        application_history=healthy_history,
    )
    assert healthy.application_history == healthy_history
    assert healthy.execution_envelope_digest == envelope.envelope_digest
    assert (
        healthy.controller_release_evidence_digest == controller_release.evidence_digest
    )

    substituted_history = replace(
        healthy_history,
        bundle_sha256=_digest(b"foreign bundle"),
    )
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.build_authorization_evidence(
            workflow_run=_workflow_run(),
            envelope=_envelope(healthy_history),
            controller_release=_release(),
            application_history=substituted_history,
        )
    hostile_artifacts = tuple(
        replace(item, sha256="sha256:" + "f" * 64)
        if item.name.endswith(".whl")
        else item
        for item in _release().artifacts
    )
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.build_authorization_evidence(
            workflow_run=_workflow_run(),
            envelope=_envelope(healthy_history),
            controller_release=_release(artifacts=hostile_artifacts),
            application_history=healthy_history,
        )


def test_signer_receives_only_domain_separated_authorization_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "authorization.pem"
    key.write_text("not inspected by Python", encoding="utf-8")
    key.chmod(0o600)
    seen: list[bytes] = []

    def fake_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        payload = kwargs.get("input")
        assert isinstance(payload, bytes)
        seen.append(payload)
        return subprocess.CompletedProcess(argv, 0, stdout=b"s" * 64)

    monkeypatch.setattr(finalizer.subprocess, "run", fake_run)
    history = _snapshot()
    evidence = finalizer.build_authorization_evidence(
        workflow_run=_workflow_run(),
        envelope=_envelope(history),
        controller_release=_release(),
        application_history=history,
    )
    signature = finalizer.sign_evidence(
        evidence,
        key_id="authorization-2026-a",
        private_key_path=key,
        openssl_path=Path("/bin/echo"),
    )
    expected = finalizer.signing_payload_bytes(
        purpose=finalizer.EvidencePurpose.AUTHORIZATION,
        key_id="authorization-2026-a",
        payload_schema=finalizer.AUTHORIZATION_EVIDENCE_SCHEMA,
        document=evidence.to_document(),
    )

    assert seen == [expected]
    assert signature.purpose is finalizer.EvidencePurpose.AUTHORIZATION
    assert signature.purpose is not finalizer.EvidencePurpose.RELEASE


def test_outputs_are_exclusive_and_bundle_bound(tmp_path: Path) -> None:
    bundle_bytes = b"complete application history"
    source_bundle = tmp_path / "source.bundle"
    source_bundle.write_bytes(bundle_bytes)
    history = _snapshot(bundle_bytes)
    evidence = finalizer.build_authorization_evidence(
        workflow_run=_workflow_run(),
        envelope=_envelope(history),
        controller_release=_release(),
        application_history=history,
    )
    signature = finalizer.DetachedEvidenceSignatureV1(
        purpose=finalizer.EvidencePurpose.AUTHORIZATION,
        key_id="authorization-2026-a",
        payload_schema=finalizer.AUTHORIZATION_EVIDENCE_SCHEMA,
        payload_sha256=_digest(finalizer.canonical_json_bytes(evidence.to_document())),
        signature_b64=base64.b64encode(b"s" * 64).decode("ascii"),
    )
    output = tmp_path / "authorization-output"

    evidence_path, signature_path, bundle_path = finalizer.write_outputs(
        output=output,
        source_bundle=source_bundle,
        evidence=evidence,
        signature=signature,
    )

    assert evidence_path.read_bytes() == finalizer.canonical_json_bytes(
        evidence.to_document()
    )
    assert signature_path.exists()
    assert bundle_path.read_bytes() == bundle_bytes
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.write_outputs(
            output=output,
            source_bundle=source_bundle,
            evidence=evidence,
            signature=signature,
        )
