"""Adversarial canaries for authenticated deployment evidence.

No test signer or private key is needed: the verifier boundary is replaced by
a fake which accepts exactly one 64-byte test signature.  Digest, purpose,
identity and history-binding failures occur before that boundary, proving they
are properties of the evidence protocol rather than of the fake.
"""

from __future__ import annotations

import base64
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from dotmac_deployment_foundation import authenticity as authenticity_module
from dotmac_deployment_foundation.authenticity import (
    AUTHORIZATION_EVIDENCE_DOMAIN,
    AUTHORIZATION_EVIDENCE_SCHEMA,
    RELEASE_EVIDENCE_DOMAIN,
    RELEASE_EVIDENCE_SCHEMA,
    ApplicationHistorySnapshotV1,
    ApplicationRepositoryAuthorityV1,
    DeploymentAuthorizationEvidenceV1,
    DeploymentControllerReleaseArtifactV1,
    DeploymentControllerReleaseEvidenceV1,
    DeploymentEvidenceTrustPolicyV1,
    DetachedEvidenceSignatureV1,
    EvidencePurpose,
    GitHubReferencedWorkflowV1,
    GitHubWorkflowRunV1,
    TrustedEvidenceKeyV1,
    WorkflowAuthorityV1,
    canonical_json_bytes,
    signing_payload_bytes,
    verify_detached_evidence,
)
from dotmac_deployment_foundation.errors import SpecError, UnknownFieldError

REVISION = "1" * 40
WORKFLOW_REVISION = "2" * 40
OTHER_REVISION = "3" * 40
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
ENVELOPE_DIGEST = "sha256:" + "c" * 64
RELEASE_DIGEST = "sha256:" + "d" * 64
SIGNATURE = b"s" * 64
OPENSSL_BYTES = b"pinned openssl test bytes"
GIT_BYTES = b"pinned git test bytes"
DOCKER_BYTES = b"pinned docker test bytes"
RELEASE_PUBLIC_KEY_BYTES = b"pinned release public test key bytes"
AUTHORIZATION_PUBLIC_KEY_BYTES = b"pinned authorization public test key bytes"
RELEASE_PUBLIC_KEY_SPKI = b"canonical release SubjectPublicKeyInfo"
AUTHORIZATION_PUBLIC_KEY_SPKI = b"canonical authorization SubjectPublicKeyInfo"


def _sha256(data: bytes) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _run(
    *,
    server_origin: str = "https://github.com",
    api_origin: str = "https://api.github.com",
    repository_id: int = 101,
    repository: str = "michaelayoade/dotmac_starter_mt",
    head_repository_id: int | None = None,
    head_repository: str | None = None,
    workflow_id: int = 202,
    workflow_path: str = ".github/workflows/release-facility.yml",
    workflow_revision: str = WORKFLOW_REVISION,
    run_id: int = 303,
    run_attempt: int = 1,
    event: str = "workflow_dispatch",
    head_sha: str = REVISION,
    head_ref: str = "refs/heads/main",
    referenced_workflows: tuple[GitHubReferencedWorkflowV1, ...] = (),
    status: str = "completed",
    conclusion: str = "success",
) -> GitHubWorkflowRunV1:
    return GitHubWorkflowRunV1(
        server_origin=server_origin,
        api_origin=api_origin,
        repository_id=repository_id,
        repository=repository,
        head_repository_id=(
            repository_id if head_repository_id is None else head_repository_id
        ),
        head_repository=repository if head_repository is None else head_repository,
        workflow_id=workflow_id,
        workflow_path=workflow_path,
        workflow_revision=workflow_revision,
        workflow_blob_sha256=DIGEST,
        run_id=run_id,
        run_attempt=run_attempt,
        event=event,
        head_sha=head_sha,
        head_ref=head_ref,
        referenced_workflows=referenced_workflows,
        status=status,
        conclusion=conclusion,
    )


def _release() -> DeploymentControllerReleaseEvidenceV1:
    return DeploymentControllerReleaseEvidenceV1(
        workflow_run=_run(),
        distribution="dotmac-deployment-foundation",
        exact_version="0.3.0a1",
        tag="dotmac-deployment-foundation-v0.3.0a1",
        source_revision=REVISION,
        artifacts=(
            DeploymentControllerReleaseArtifactV1(
                name="dotmac_deployment_foundation-0.3.0a1-py3-none-any.whl",
                media_type="application/octet-stream",
                size=4096,
                sha256=DIGEST,
            ),
            DeploymentControllerReleaseArtifactV1(
                name="run_deployment_controller.py",
                media_type="text/x-python",
                size=2048,
                sha256=OTHER_DIGEST,
            ),
        ),
    )


def _history() -> ApplicationHistorySnapshotV1:
    return ApplicationHistorySnapshotV1(
        server_origin="https://github.com",
        api_origin="https://api.github.com",
        repository_id=404,
        repository="michaelayoade/dotmac_sub",
        object_format="sha1",
        from_revision=OTHER_REVISION,
        to_revision=REVISION,
        bundle_name="dotmac-sub-release-history.bundle",
        bundle_size=8192,
        bundle_sha256=OTHER_DIGEST,
    )


def _authorization() -> DeploymentAuthorizationEvidenceV1:
    return DeploymentAuthorizationEvidenceV1(
        workflow_run=_authorization_run(),
        execution_envelope_digest=ENVELOPE_DIGEST,
        controller_release_evidence_digest=RELEASE_DIGEST,
        application_history=_history(),
    )


def _authorization_run(**overrides: object) -> GitHubWorkflowRunV1:
    values: dict[str, object] = {
        "repository_id": 505,
        "repository": "michaelayoade/dotmac_deployment_control",
        "workflow_id": 707,
        "workflow_path": ".github/workflows/authorize-deployment.yml",
        "run_id": 606,
    }
    values.update(overrides)
    return _run(**values)  # type: ignore[arg-type]


def _signature(
    evidence: DeploymentControllerReleaseEvidenceV1 | DeploymentAuthorizationEvidenceV1,
    *,
    purpose: EvidencePurpose,
    key_id: str,
    raw_signature: bytes = SIGNATURE,
) -> DetachedEvidenceSignatureV1:
    document = evidence.to_document()
    return DetachedEvidenceSignatureV1(
        purpose=purpose,
        key_id=key_id,
        payload_schema=str(document["schema"]),
        payload_sha256=_sha256(canonical_json_bytes(document)),
        signature_b64=base64.b64encode(raw_signature).decode("ascii"),
    )


def _policy() -> DeploymentEvidenceTrustPolicyV1:
    return DeploymentEvidenceTrustPolicyV1(
        openssl_path=Path("/usr/bin/openssl"),
        openssl_sha256=_sha256(OPENSSL_BYTES),
        git_path=Path("/usr/bin/git"),
        git_sha256=_sha256(GIT_BYTES),
        docker_path=Path("/usr/bin/docker"),
        docker_sha256=_sha256(DOCKER_BYTES),
        keys=(
            TrustedEvidenceKeyV1(
                key_id="release-2026-a",
                purpose=EvidencePurpose.RELEASE,
                public_key_path=Path("/etc/dotmac/release-ed25519.pem"),
                public_key_sha256=_sha256(RELEASE_PUBLIC_KEY_BYTES),
                public_key_spki_sha256=_sha256(RELEASE_PUBLIC_KEY_SPKI),
            ),
            TrustedEvidenceKeyV1(
                key_id="authorization-2026-a",
                purpose=EvidencePurpose.AUTHORIZATION,
                public_key_path=Path("/etc/dotmac/authorization-ed25519.pem"),
                public_key_sha256=_sha256(AUTHORIZATION_PUBLIC_KEY_BYTES),
                public_key_spki_sha256=_sha256(AUTHORIZATION_PUBLIC_KEY_SPKI),
            ),
        ),
        release_authorities=(
            WorkflowAuthorityV1(
                server_origin="https://github.com",
                api_origin="https://api.github.com",
                repository_id=101,
                repository="michaelayoade/dotmac_starter_mt",
                workflow_id=202,
                workflow_path=".github/workflows/release-facility.yml",
                event="workflow_dispatch",
                protected_ref="refs/heads/main",
                referenced_workflows=(),
            ),
        ),
        authorization_authorities=(
            WorkflowAuthorityV1(
                server_origin="https://github.com",
                api_origin="https://api.github.com",
                repository_id=505,
                repository="michaelayoade/dotmac_deployment_control",
                workflow_id=707,
                workflow_path=".github/workflows/authorize-deployment.yml",
                event="workflow_dispatch",
                protected_ref="refs/heads/main",
                referenced_workflows=(),
            ),
        ),
        application_repositories=(
            ApplicationRepositoryAuthorityV1(
                server_origin="https://github.com",
                api_origin="https://api.github.com",
                repository_id=404,
                repository="michaelayoade/dotmac_sub",
            ),
        ),
    )


@pytest.fixture
def verifier_boundary(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    verified_payloads: list[bytes] = []

    def fake_trusted_file(path: Path, *, executable: bool) -> bytes:
        if executable:
            assert path == Path("/usr/bin/openssl")
            return OPENSSL_BYTES
        if path == Path("/etc/dotmac/release-ed25519.pem"):
            return RELEASE_PUBLIC_KEY_BYTES
        assert path == Path("/etc/dotmac/authorization-ed25519.pem")
        return AUTHORIZATION_PUBLIC_KEY_BYTES

    def fake_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        assert argv[0] == "/usr/bin/openssl"
        signature_path = Path(argv[argv.index("-sigfile") + 1])
        input_bytes = kwargs["input"]
        assert isinstance(input_bytes, bytes)
        verified_payloads.append(input_bytes)
        return subprocess.CompletedProcess(
            argv, 0 if signature_path.read_bytes() == SIGNATURE else 1
        )

    monkeypatch.setattr(
        authenticity_module, "_require_root_owned_regular_file", fake_trusted_file
    )
    monkeypatch.setattr(
        authenticity_module,
        "_public_key_spki_sha256",
        lambda *, openssl_path, public_key_path: (
            _sha256(RELEASE_PUBLIC_KEY_SPKI)
            if public_key_path == Path("/etc/dotmac/release-ed25519.pem")
            else _sha256(AUTHORIZATION_PUBLIC_KEY_SPKI)
        ),
    )
    monkeypatch.setattr(authenticity_module.subprocess, "run", fake_run)
    return verified_payloads


def test_release_and_authorization_signatures_use_distinct_domains() -> None:
    release = _release()
    authorization = _authorization()
    release_bytes = signing_payload_bytes(
        purpose=EvidencePurpose.RELEASE,
        key_id="release-2026-a",
        payload_schema=RELEASE_EVIDENCE_SCHEMA,
        document=release.to_document(),
    )
    authorization_bytes = signing_payload_bytes(
        purpose=EvidencePurpose.AUTHORIZATION,
        key_id="authorization-2026-a",
        payload_schema=AUTHORIZATION_EVIDENCE_SCHEMA,
        document=authorization.to_document(),
    )

    assert RELEASE_EVIDENCE_DOMAIN in release_bytes
    assert AUTHORIZATION_EVIDENCE_DOMAIN not in release_bytes
    assert AUTHORIZATION_EVIDENCE_DOMAIN in authorization_bytes
    assert RELEASE_EVIDENCE_DOMAIN not in authorization_bytes
    assert release_bytes != authorization_bytes


def test_both_evidence_purposes_verify_with_their_exact_keys(
    verifier_boundary: list[bytes],
) -> None:
    release = _release()
    authorization = _authorization()

    verify_detached_evidence(
        release,
        _signature(release, purpose=EvidencePurpose.RELEASE, key_id="release-2026-a"),
        policy=_policy(),
        expected_purpose=EvidencePurpose.RELEASE,
    )
    verify_detached_evidence(
        authorization,
        _signature(
            authorization,
            purpose=EvidencePurpose.AUTHORIZATION,
            key_id="authorization-2026-a",
        ),
        policy=_policy(),
        expected_purpose=EvidencePurpose.AUTHORIZATION,
    )

    assert len(verifier_boundary) == 2


def test_forged_or_replaced_release_artifact_is_refused_before_openssl(
    verifier_boundary: list[bytes],
) -> None:
    release = _release()
    signature = _signature(
        release, purpose=EvidencePurpose.RELEASE, key_id="release-2026-a"
    )
    forged = replace(
        release,
        artifacts=(
            replace(release.artifacts[0], sha256=OTHER_DIGEST),
            release.artifacts[1],
        ),
    )

    with pytest.raises(SpecError, match="digest does not match"):
        verify_detached_evidence(
            forged,
            signature,
            policy=_policy(),
            expected_purpose=EvidencePurpose.RELEASE,
        )
    assert verifier_boundary == []


def test_wrong_purpose_and_wrong_key_are_independently_refused(
    verifier_boundary: list[bytes],
) -> None:
    release = _release()
    valid = _signature(
        release, purpose=EvidencePurpose.RELEASE, key_id="release-2026-a"
    )

    with pytest.raises(SpecError, match="wrong signer purpose"):
        verify_detached_evidence(
            release,
            valid,
            policy=_policy(),
            expected_purpose=EvidencePurpose.AUTHORIZATION,
        )
    with pytest.raises(SpecError, match="untrusted release evidence key"):
        verify_detached_evidence(
            release,
            replace(valid, key_id="untrusted-release-key"),
            policy=_policy(),
            expected_purpose=EvidencePurpose.RELEASE,
        )
    assert verifier_boundary == []


def test_invalid_ed25519_signature_fails_closed(
    verifier_boundary: list[bytes],
) -> None:
    release = _release()
    forged_signature = _signature(
        release,
        purpose=EvidencePurpose.RELEASE,
        key_id="release-2026-a",
        raw_signature=b"x" * 64,
    )

    with pytest.raises(SpecError, match="verification failed"):
        verify_detached_evidence(
            release,
            forged_signature,
            policy=_policy(),
            expected_purpose=EvidencePurpose.RELEASE,
        )
    assert len(verifier_boundary) == 1


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [("in_progress", "success"), ("completed", "failure"), ("completed", "")],
)
def test_non_successful_workflow_runs_are_unrepresentable(
    status: str, conclusion: str
) -> None:
    with pytest.raises(SpecError, match="completed successfully"):
        _run(status=status, conclusion=conclusion)


@pytest.mark.parametrize(
    "foreign",
    [
        {"repository_id": 999},
        {"repository": "attacker/foreign"},
        {"api_origin": "https://api.foreign.invalid"},
        {"workflow_id": 999},
        {"workflow_path": ".github/workflows/foreign.yml"},
        {"workflow_revision": OTHER_REVISION},
        {"run_id": 999},
        {"run_attempt": 2},
        {"event": "push"},
        {"head_sha": OTHER_REVISION},
        {"head_ref": "refs/heads/foreign"},
    ],
)
def test_successful_but_foreign_workflow_run_is_refused(
    foreign: dict[str, object],
) -> None:
    expected = _run()
    actual = _run(**foreign)  # type: ignore[arg-type]

    with pytest.raises(SpecError, match="foreign workflow run fields"):
        actual.require_exact(expected)


def test_fork_head_cannot_impersonate_the_protected_repository() -> None:
    with pytest.raises(SpecError, match="protected repository"):
        _run(head_repository_id=909, head_repository="attacker/fork")


def test_application_history_is_bound_inside_authorization_signature(
    verifier_boundary: list[bytes],
) -> None:
    authorization = _authorization()
    signature = _signature(
        authorization,
        purpose=EvidencePurpose.AUTHORIZATION,
        key_id="authorization-2026-a",
    )
    replaced_bundle = replace(
        authorization,
        application_history=replace(
            authorization.application_history, bundle_sha256=DIGEST
        ),
    )

    with pytest.raises(SpecError, match="digest does not match"):
        verify_detached_evidence(
            replaced_bundle,
            signature,
            policy=_policy(),
            expected_purpose=EvidencePurpose.AUTHORIZATION,
        )
    assert verifier_boundary == []


def test_authorizer_and_application_history_repositories_are_independent() -> None:
    authorization = _authorization()

    assert authorization.workflow_run.repository == (
        "michaelayoade/dotmac_deployment_control"
    )
    assert authorization.application_history.repository == "michaelayoade/dotmac_sub"
    assert authorization.workflow_run.repository_id != (
        authorization.application_history.repository_id
    )


def test_trust_policy_refuses_private_key_or_unknown_fields() -> None:
    document = _policy().to_document()
    key = document["keys"][0]
    assert isinstance(key, dict)
    key["private_key_path"] = "/etc/dotmac/private.pem"

    with pytest.raises(UnknownFieldError, match="private_key_path"):
        DeploymentEvidenceTrustPolicyV1.from_document(document)


def test_signer_purposes_cannot_reuse_one_public_key() -> None:
    policy = _policy()
    release_key, authorization_key = policy.keys

    with pytest.raises(SpecError, match="require distinct public keys"):
        replace(
            policy,
            keys=(
                release_key,
                replace(
                    authorization_key,
                    # Presentation bytes may differ while the cryptographic
                    # SubjectPublicKeyInfo is the same key.
                    public_key_sha256=_sha256(
                        b"same key with a different PEM encoding"
                    ),
                    public_key_spki_sha256=release_key.public_key_spki_sha256,
                ),
            ),
        )


def test_public_key_pem_spki_and_openssl_digests_are_load_bearing(
    verifier_boundary: list[bytes],
) -> None:
    release = _release()
    signature = _signature(
        release, purpose=EvidencePurpose.RELEASE, key_id="release-2026-a"
    )
    policy = _policy()

    with pytest.raises(SpecError, match="OpenSSL binary digest"):
        verify_detached_evidence(
            release,
            signature,
            policy=replace(policy, openssl_sha256=DIGEST),
            expected_purpose=EvidencePurpose.RELEASE,
        )
    release_key = policy.keys[0]
    hostile_policy = replace(
        policy,
        keys=(replace(release_key, public_key_sha256=DIGEST), policy.keys[1]),
    )
    with pytest.raises(SpecError, match="public key digest"):
        verify_detached_evidence(
            release,
            signature,
            policy=hostile_policy,
            expected_purpose=EvidencePurpose.RELEASE,
        )
    hostile_spki_policy = replace(
        policy,
        keys=(
            replace(release_key, public_key_spki_sha256=DIGEST),
            policy.keys[1],
        ),
    )
    with pytest.raises(SpecError, match="public key SPKI digest"):
        verify_detached_evidence(
            release,
            signature,
            policy=hostile_spki_policy,
            expected_purpose=EvidencePurpose.RELEASE,
        )
    assert verifier_boundary == []


@pytest.mark.parametrize(
    ("purpose", "run"),
    [
        (EvidencePurpose.RELEASE, _run(server_origin="https://foreign.invalid")),
        (EvidencePurpose.RELEASE, _run(api_origin="https://api.foreign.invalid")),
        (EvidencePurpose.RELEASE, _run(repository_id=999)),
        (EvidencePurpose.RELEASE, _run(repository="attacker/starter")),
        (EvidencePurpose.RELEASE, _run(workflow_id=999)),
        (
            EvidencePurpose.RELEASE,
            _run(workflow_path=".github/workflows/foreign.yml"),
        ),
        (EvidencePurpose.RELEASE, _run(event="push")),
        (EvidencePurpose.RELEASE, _run(head_ref="refs/heads/dev")),
        (
            EvidencePurpose.AUTHORIZATION,
            _authorization_run(workflow_id=999),
        ),
    ],
)
def test_hostile_workflow_authority_is_refused(
    purpose: EvidencePurpose, run: GitHubWorkflowRunV1
) -> None:
    with pytest.raises(SpecError, match=f"trusted {purpose.value} authority"):
        _policy().require_workflow_authority(run=run, purpose=purpose)


@pytest.mark.parametrize(
    "history",
    [
        replace(_history(), server_origin="https://foreign.invalid"),
        replace(_history(), api_origin="https://api.foreign.invalid"),
        replace(_history(), repository_id=999),
        replace(_history(), repository="attacker/dotmac_sub"),
    ],
)
def test_hostile_application_repository_authority_is_refused(
    history: ApplicationHistorySnapshotV1,
) -> None:
    with pytest.raises(SpecError, match="untrusted repository"):
        _policy().require_application_repository(history)


def test_exact_workflow_and_application_authorities_are_accepted() -> None:
    policy = _policy()
    policy.require_workflow_authority(
        run=_release().workflow_run, purpose=EvidencePurpose.RELEASE
    )
    authorization = _authorization()
    policy.require_workflow_authority(
        run=authorization.workflow_run,
        purpose=EvidencePurpose.AUTHORIZATION,
    )
    policy.require_application_repository(authorization.application_history)


def _referenced_workflow() -> GitHubReferencedWorkflowV1:
    return GitHubReferencedWorkflowV1(
        repository="michaelayoade/dotmac_governance",
        workflow_path=".github/workflows/engineering-standards.yml",
        workflow_ref="v1",
        workflow_revision=OTHER_REVISION,
        workflow_blob_sha256=OTHER_DIGEST,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("repository", "attacker/dotmac_governance"),
        ("workflow_path", ".github/workflows/foreign.yml"),
        ("workflow_ref", "v2"),
        ("workflow_revision", REVISION),
        ("workflow_blob_sha256", DIGEST),
    ],
)
def test_reusable_workflow_code_requires_one_exact_policy_binding(
    field: str, replacement: str
) -> None:
    referenced = _referenced_workflow()
    run = _run(referenced_workflows=(referenced,))
    policy = _policy()

    with pytest.raises(SpecError, match="trusted release authority"):
        policy.require_workflow_authority(run=run, purpose=EvidencePurpose.RELEASE)

    release_authority = policy.release_authorities[0]
    exact_policy = replace(
        policy,
        release_authorities=(
            replace(release_authority, referenced_workflows=(referenced,)),
        ),
    )
    exact_policy.require_workflow_authority(run=run, purpose=EvidencePurpose.RELEASE)

    hostile = replace(referenced, **{field: replacement})
    with pytest.raises(SpecError, match="trusted release authority"):
        exact_policy.require_workflow_authority(
            run=_run(referenced_workflows=(hostile,)),
            purpose=EvidencePurpose.RELEASE,
        )


@pytest.mark.parametrize(
    "hostile_origin",
    [
        "http://api.github.com",
        "https://API.github.com",
        "https://api.github.com/api/v1",
    ],
)
def test_api_origins_must_be_canonical_https_origins(hostile_origin: str) -> None:
    with pytest.raises(SpecError, match="api_origin"):
        _run(api_origin=hostile_origin)


def test_github_enterprise_api_base_is_canonical_and_supported() -> None:
    run = _run(
        server_origin="https://github.enterprise.invalid",
        api_origin="https://github.enterprise.invalid/api/v3",
    )

    assert run.api_origin == "https://github.enterprise.invalid/api/v3"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("git_path", "relative/git"),
        ("git_sha256", "not-a-digest"),
        ("docker_path", "relative/docker"),
        ("docker_sha256", "not-a-digest"),
    ],
)
def test_hostile_tool_authority_is_unrepresentable(
    field: str, replacement: object
) -> None:
    document = _policy().to_document()
    document[field] = replacement
    with pytest.raises(SpecError):
        DeploymentEvidenceTrustPolicyV1.from_document(document)
