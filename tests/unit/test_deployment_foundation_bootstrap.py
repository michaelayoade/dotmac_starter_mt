"""Adversarial canaries for the root-owned authenticated bootstrap."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts import run_authenticated_deployment as bootstrap

RELEASE_REVISION = "1" * 40
AUTHORIZER_REVISION = "2" * 40
CURRENT_REVISION = "3" * 40
CANDIDATE_REVISION = "4" * 40
IMAGE_DIGEST = "sha256:" + "5" * 64
CONFIGURATION_DIGEST = "sha256:" + "6" * 64
MANIFEST_DIGEST = "sha256:" + "7" * 64
PLAN_DIGEST = "sha256:" + "8" * 64
WORKFLOW_BYTES = b"name: authorize exact deployment\n"
SIGNATURE_BYTES = b"s" * 64
OPENSSL_BYTES = b"root owned openssl"
RELEASE_KEY_BYTES = b"release public key"
AUTHORIZATION_KEY_BYTES = b"authorization public key"
RELEASE_KEY_SPKI = b"canonical release SubjectPublicKeyInfo"
AUTHORIZATION_KEY_SPKI = b"canonical authorization SubjectPublicKeyInfo"
GIT_BYTES = b"root owned git"
DOCKER_BYTES = b"root owned docker"
PYTHON_BYTES = b"root owned python"


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _canonical(document: dict[str, object]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _typed_digest(kind: str, document: dict[str, object]) -> str:
    return _sha256(_canonical({"kind": kind, **document}))


def _workflow(
    *,
    repository_id: int,
    repository: str,
    workflow_id: int,
    workflow_path: str,
    workflow_revision: str,
    run_id: int,
    head_sha: str,
    status: str = "completed",
    conclusion: str = "success",
) -> dict[str, object]:
    return {
        "schema": bootstrap.WORKFLOW_RUN_SCHEMA,
        "server_origin": "https://github.com",
        "api_origin": "https://api.github.com",
        "repository_id": repository_id,
        "repository": repository,
        "head_repository_id": repository_id,
        "head_repository": repository,
        "workflow_id": workflow_id,
        "workflow_path": workflow_path,
        "workflow_revision": workflow_revision,
        "workflow_blob_sha256": _sha256(WORKFLOW_BYTES),
        "run_id": run_id,
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "head_sha": head_sha,
        "head_ref": "refs/heads/main",
        "referenced_workflows": [],
        "status": status,
        "conclusion": conclusion,
    }


def _signature(
    document: dict[str, object], *, purpose: str, key_id: str
) -> dict[str, object]:
    return {
        "schema": bootstrap.SIGNATURE_SCHEMA,
        "algorithm": "ed25519",
        "purpose": purpose,
        "key_id": key_id,
        "payload_schema": document["schema"],
        "payload_sha256": _sha256(_canonical(document)),
        "signature_b64": base64.b64encode(SIGNATURE_BYTES).decode("ascii"),
    }


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_bytes(_canonical(document))


def _trust_policy(bundle: Bundle) -> dict[str, object]:
    document = json.loads(bundle.paths["trust_policy"].read_text())
    assert isinstance(document, dict)
    return document


def _rewrite_trust_policy(bundle: Bundle, document: dict[str, object]) -> None:
    _write_json(bundle.paths["trust_policy"], document)


@dataclass(slots=True)
class Bundle:
    args: argparse.Namespace
    paths: dict[str, Path]
    release: dict[str, object]
    authorization: dict[str, object]
    receipt: dict[str, object]
    envelope: dict[str, object]

    def rewrite_release_signature(self) -> None:
        _write_json(
            self.paths["release_signature"],
            _signature(
                self.release,
                purpose=bootstrap.RELEASE_PURPOSE,
                key_id="release-2026-a",
            ),
        )

    def rewrite_authorization_signature(self) -> None:
        _write_json(
            self.paths["authorization_signature"],
            _signature(
                self.authorization,
                purpose=bootstrap.AUTHORIZATION_PURPOSE,
                key_id="authorization-2026-a",
            ),
        )


def _build_bundle(tmp_path: Path) -> Bundle:
    files = tmp_path / "inputs"
    files.mkdir()
    staged = tmp_path / "staged"
    staged.mkdir()
    authorizer_source = tmp_path / "authorizer-source"
    authorizer_source.mkdir()

    launcher_bytes = b"# authenticated launcher\n"
    wheel_bytes = b"authenticated wheel bytes"
    history_bytes = b"authenticated Git bundle bytes"
    wheel_name = "dotmac_deployment_foundation-0.3.0a1-py3-none-any.whl"

    controller = {
        "distribution": bootstrap.CONTROLLER_DISTRIBUTION,
        "exact_version": "0.3.0a1",
        "artifact_sha256": _sha256(wheel_bytes),
        "launcher_sha256": _sha256(launcher_bytes),
        "source_revision": RELEASE_REVISION,
        "release_run_id": 303,
        "tag": "dotmac-deployment-foundation-v0.3.0a1",
    }
    receipt = {"schema": bootstrap.RECEIPT_SCHEMA, **controller}
    receipt_bytes = _canonical(receipt)
    release_run = _workflow(
        repository_id=101,
        repository="michaelayoade/dotmac_starter_mt",
        workflow_id=202,
        workflow_path=bootstrap.RELEASE_WORKFLOW_PATH,
        workflow_revision=RELEASE_REVISION,
        run_id=303,
        head_sha=RELEASE_REVISION,
    )
    artifacts: list[dict[str, object]] = [
        {
            "schema": bootstrap.RELEASE_ARTIFACT_SCHEMA,
            "name": bootstrap.RECEIPT_NAME,
            "media_type": "application/json",
            "size": len(receipt_bytes),
            "sha256": _sha256(receipt_bytes),
        },
        {
            "schema": bootstrap.RELEASE_ARTIFACT_SCHEMA,
            "name": wheel_name,
            "media_type": "application/octet-stream",
            "size": len(wheel_bytes),
            "sha256": _sha256(wheel_bytes),
        },
        {
            "schema": bootstrap.RELEASE_ARTIFACT_SCHEMA,
            "name": bootstrap.LAUNCHER_NAME,
            "media_type": "text/x-python",
            "size": len(launcher_bytes),
            "sha256": _sha256(launcher_bytes),
        },
    ]
    release: dict[str, object] = {
        "schema": bootstrap.RELEASE_EVIDENCE_SCHEMA,
        "workflow_run": release_run,
        "distribution": bootstrap.CONTROLLER_DISTRIBUTION,
        "exact_version": "0.3.0a1",
        "tag": "dotmac-deployment-foundation-v0.3.0a1",
        "source_revision": RELEASE_REVISION,
        "artifacts": artifacts,
    }
    history: dict[str, object] = {
        "schema": bootstrap.HISTORY_SNAPSHOT_SCHEMA,
        "server_origin": "https://github.com",
        "api_origin": "https://api.github.com",
        "repository_id": 404,
        "repository": "michaelayoade/dotmac_sub",
        "object_format": "sha1",
        "from_revision": CURRENT_REVISION,
        "to_revision": CANDIDATE_REVISION,
        "bundle_name": "dotmac-sub-history.bundle",
        "bundle_size": len(history_bytes),
        "bundle_sha256": _sha256(history_bytes),
    }
    authorizer: dict[str, object] = {
        "repository": "michaelayoade/dotmac_deployment_control",
        "workflow_path": ".github/workflows/authorize-deployment.yml",
        "workflow_revision": AUTHORIZER_REVISION,
        "run_id": 606,
    }
    current: dict[str, object] = {
        "image_digest": "sha256:" + "a" * 64,
        "source_revision": CURRENT_REVISION,
        "configuration_digest": "sha256:" + "b" * 64,
        "manifest_digest": "sha256:" + "c" * 64,
    }
    candidate: dict[str, object] = {
        "image_digest": IMAGE_DIGEST,
        "source_revision": CANDIDATE_REVISION,
        "configuration_digest": CONFIGURATION_DIGEST,
        "manifest_digest": MANIFEST_DIGEST,
    }
    history_digest = _sha256(_canonical(history))
    envelope: dict[str, object] = {
        "schema": bootstrap.ENVELOPE_SCHEMA,
        "execution_id": "deployment-606",
        "product": "dotmac_sub",
        "target_ref": "observe:dotmac-sub",
        "plan_digest": PLAN_DIGEST,
        "required_controller": controller,
        "authorizer": authorizer,
        "candidate": candidate,
        "expected_current": current,
        "relation_evidence": {
            "relation": "forward",
            "from_revision": CURRENT_REVISION,
            "to_revision": CANDIDATE_REVISION,
            "history_snapshot_digest": history_digest,
        },
        "override": None,
    }
    authorization_run = _workflow(
        repository_id=505,
        repository="michaelayoade/dotmac_deployment_control",
        workflow_id=707,
        workflow_path=".github/workflows/authorize-deployment.yml",
        workflow_revision=AUTHORIZER_REVISION,
        run_id=606,
        head_sha=AUTHORIZER_REVISION,
    )
    authorization: dict[str, object] = {
        "schema": bootstrap.AUTHORIZATION_EVIDENCE_SCHEMA,
        "workflow_run": authorization_run,
        "execution_envelope_digest": _typed_digest(
            "DeploymentExecutionEnvelopeV1", envelope
        ),
        "controller_release_evidence_digest": _sha256(_canonical(release)),
        "application_history": history,
    }

    paths = {
        "trust_policy": files / "trust.json",
        "release_evidence": files / "release.json",
        "release_signature": files / "release.sig.json",
        "authorization_evidence": files / "authorization.json",
        "authorization_signature": files / "authorization.sig.json",
        "launcher": files / bootstrap.LAUNCHER_NAME,
        "wheel": files / wheel_name,
        "receipt": files / bootstrap.RECEIPT_NAME,
        "envelope": files / "execution-envelope.json",
        "history_bundle": files / "dotmac-sub-history.bundle",
        "authorizer": tmp_path / "authorizer-source",
        "staged": staged,
    }
    policy: dict[str, object] = {
        "schema": bootstrap.TRUST_POLICY_SCHEMA,
        "openssl_path": "/usr/bin/openssl",
        "openssl_sha256": _sha256(OPENSSL_BYTES),
        "git_path": "/usr/bin/git",
        "git_sha256": _sha256(GIT_BYTES),
        "docker_path": "/usr/bin/docker",
        "docker_sha256": _sha256(DOCKER_BYTES),
        "keys": [
            {
                "key_id": "release-2026-a",
                "purpose": bootstrap.RELEASE_PURPOSE,
                "public_key_path": "/etc/dotmac/release.pem",
                "public_key_sha256": _sha256(RELEASE_KEY_BYTES),
                "public_key_spki_sha256": _sha256(RELEASE_KEY_SPKI),
            },
            {
                "key_id": "authorization-2026-a",
                "purpose": bootstrap.AUTHORIZATION_PURPOSE,
                "public_key_path": "/etc/dotmac/authorization.pem",
                "public_key_sha256": _sha256(AUTHORIZATION_KEY_BYTES),
                "public_key_spki_sha256": _sha256(AUTHORIZATION_KEY_SPKI),
            },
        ],
        "release_authorities": [
            {
                "server_origin": "https://github.com",
                "api_origin": "https://api.github.com",
                "repository_id": 101,
                "repository": "michaelayoade/dotmac_starter_mt",
                "workflow_id": 202,
                "workflow_path": bootstrap.RELEASE_WORKFLOW_PATH,
                "event": "workflow_dispatch",
                "protected_ref": "refs/heads/main",
                "referenced_workflows": [],
            }
        ],
        "authorization_authorities": [
            {
                "server_origin": "https://github.com",
                "api_origin": "https://api.github.com",
                "repository_id": 505,
                "repository": "michaelayoade/dotmac_deployment_control",
                "workflow_id": 707,
                "workflow_path": ".github/workflows/authorize-deployment.yml",
                "event": "workflow_dispatch",
                "protected_ref": "refs/heads/main",
                "referenced_workflows": [],
            }
        ],
        "application_repositories": [
            {
                "server_origin": "https://github.com",
                "api_origin": "https://api.github.com",
                "repository_id": 404,
                "repository": "michaelayoade/dotmac_sub",
            }
        ],
    }
    _write_json(paths["trust_policy"], policy)
    _write_json(paths["release_evidence"], release)
    _write_json(
        paths["release_signature"],
        _signature(
            release,
            purpose=bootstrap.RELEASE_PURPOSE,
            key_id="release-2026-a",
        ),
    )
    _write_json(paths["authorization_evidence"], authorization)
    _write_json(
        paths["authorization_signature"],
        _signature(
            authorization,
            purpose=bootstrap.AUTHORIZATION_PURPOSE,
            key_id="authorization-2026-a",
        ),
    )
    paths["launcher"].write_bytes(launcher_bytes)
    paths["wheel"].write_bytes(wheel_bytes)
    paths["receipt"].write_bytes(receipt_bytes)
    _write_json(paths["envelope"], envelope)
    paths["history_bundle"].write_bytes(history_bytes)

    args = argparse.Namespace(
        trust_policy=str(paths["trust_policy"]),
        release_evidence=str(paths["release_evidence"]),
        release_signature=str(paths["release_signature"]),
        authorization_evidence=str(paths["authorization_evidence"]),
        authorization_signature=str(paths["authorization_signature"]),
        launcher=str(paths["launcher"]),
        wheel=str(paths["wheel"]),
        receipt=str(paths["receipt"]),
        execution_envelope=str(paths["envelope"]),
        history_bundle=str(paths["history_bundle"]),
        authorizer_repo=str(tmp_path / "authorizer-source"),
        staged_application_root=str(staged),
        git_bin="/usr/bin/git",
        docker_bin="/usr/bin/docker",
        descriptor="deploy/product.toml",
    )
    return Bundle(args, paths, release, authorization, receipt, envelope)


@pytest.fixture
def authenticated_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    invocations: list[dict[str, Any]] = []

    def root_bytes(path: Path, *, executable: bool) -> bytes:
        expected = {
            Path("/usr/bin/openssl"): OPENSSL_BYTES,
            Path("/etc/dotmac/release.pem"): RELEASE_KEY_BYTES,
            Path("/etc/dotmac/authorization.pem"): AUTHORIZATION_KEY_BYTES,
            Path("/usr/bin/git"): GIT_BYTES,
            Path("/usr/bin/docker"): DOCKER_BYTES,
            Path(bootstrap.sys.executable).resolve(): PYTHON_BYTES,
        }
        if path in expected:
            return expected[path]
        return path.read_bytes()

    def prepare_authorizer(**kwargs: Any) -> None:
        destination = kwargs["destination"]
        destination.mkdir()
        (destination / ".git").mkdir()

    def prepare_history(**kwargs: Any) -> None:
        destination = kwargs["destination"]
        destination.mkdir()
        (destination / "objects").mkdir()

    def invoke(**kwargs: Any) -> int:
        invocations.append(kwargs)
        assert kwargs["authorizer_repo"] != kwargs["history_repo"]
        assert not kwargs["authorizer_repo"].is_relative_to(kwargs["history_repo"])
        assert not kwargs["history_repo"].is_relative_to(kwargs["authorizer_repo"])
        for name in (
            "launcher",
            "bootstrap_context",
            "release_evidence",
            "authorization_evidence",
            "envelope",
            "receipt",
            "wheel",
        ):
            assert stat.S_IMODE(kwargs[name].stat().st_mode) == 0o400
        context = json.loads(kwargs["bootstrap_context"].read_text())
        assert context["schema"] == bootstrap.BOOTSTRAP_CONTEXT_SCHEMA
        assert context["release_evidence_digest"] == _sha256(
            kwargs["release_evidence"].read_bytes()
        )
        assert context["authorization_evidence_digest"] == _sha256(
            kwargs["authorization_evidence"].read_bytes()
        )
        return 0

    monkeypatch.setattr(bootstrap, "_root_owned_bytes", root_bytes)
    monkeypatch.setattr(
        bootstrap,
        "_public_key_spki_sha256",
        lambda *, openssl, public_key: (
            _sha256(RELEASE_KEY_SPKI)
            if public_key == Path("/etc/dotmac/release.pem")
            else _sha256(AUTHORIZATION_KEY_SPKI)
        ),
    )
    monkeypatch.setattr(bootstrap, "_openssl_verify", lambda **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_prepare_authorizer_checkout", prepare_authorizer)
    monkeypatch.setattr(bootstrap, "_prepare_history_checkout", prepare_history)
    monkeypatch.setattr(bootstrap, "_invoke_launcher", invoke)
    return invocations


def test_verified_inputs_are_sealed_before_distinct_checkouts_are_invoked(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)

    assert bootstrap.authenticate_and_launch(bundle.args) == 0
    assert len(authenticated_boundaries) == 1


@pytest.mark.parametrize(
    ("authority_name", "field", "replacement", "message"),
    [
        (
            "release_authorities",
            "workflow_id",
            999,
            "trusted release authority",
        ),
        (
            "release_authorities",
            "api_origin",
            "https://api.foreign.invalid",
            "trusted release authority",
        ),
        (
            "release_authorities",
            "protected_ref",
            "refs/heads/dev",
            "trusted release authority",
        ),
        (
            "authorization_authorities",
            "repository",
            "attacker/deployment-control",
            "trusted authorization authority",
        ),
        (
            "application_repositories",
            "repository_id",
            999,
            "untrusted repository",
        ),
        (
            "application_repositories",
            "api_origin",
            "https://api.foreign.invalid",
            "untrusted repository",
        ),
    ],
)
def test_signed_evidence_must_match_an_exact_policy_authority(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
    authority_name: str,
    field: str,
    replacement: object,
    message: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    policy = _trust_policy(bundle)
    authorities = policy[authority_name]
    assert isinstance(authorities, list) and len(authorities) == 1
    authority = authorities[0]
    assert isinstance(authority, dict)
    authority[field] = replacement
    _rewrite_trust_policy(bundle, policy)

    with pytest.raises(bootstrap.BootstrapRefused, match=message):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


def _referenced_workflow() -> dict[str, object]:
    return {
        "repository": "michaelayoade/dotmac_governance",
        "workflow_path": ".github/workflows/engineering-standards.yml",
        "workflow_ref": "v1",
        "workflow_revision": "e" * 40,
        "workflow_blob_sha256": "sha256:" + "d" * 64,
    }


def test_unbound_reusable_workflow_code_is_refused(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)
    run = bundle.release["workflow_run"]
    assert isinstance(run, dict)
    run["referenced_workflows"] = [_referenced_workflow()]
    _write_json(bundle.paths["release_evidence"], bundle.release)
    bundle.rewrite_release_signature()
    bundle.authorization["controller_release_evidence_digest"] = _sha256(
        _canonical(bundle.release)
    )
    _write_json(bundle.paths["authorization_evidence"], bundle.authorization)
    bundle.rewrite_authorization_signature()

    with pytest.raises(bootstrap.BootstrapRefused, match="trusted release authority"):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


def test_exact_reusable_workflow_code_binding_is_accepted(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)
    referenced = _referenced_workflow()
    run = bundle.release["workflow_run"]
    assert isinstance(run, dict)
    run["referenced_workflows"] = [referenced]
    _write_json(bundle.paths["release_evidence"], bundle.release)
    bundle.rewrite_release_signature()
    bundle.authorization["controller_release_evidence_digest"] = _sha256(
        _canonical(bundle.release)
    )
    _write_json(bundle.paths["authorization_evidence"], bundle.authorization)
    bundle.rewrite_authorization_signature()
    policy = _trust_policy(bundle)
    authorities = policy["release_authorities"]
    assert isinstance(authorities, list) and isinstance(authorities[0], dict)
    authorities[0]["referenced_workflows"] = [referenced]
    _rewrite_trust_policy(bundle, policy)

    assert bootstrap.authenticate_and_launch(bundle.args) == 0
    assert len(authenticated_boundaries) == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("api_origin", "https://api.foreign.invalid"),
        ("head_ref", "refs/heads/dev"),
    ],
)
def test_signed_workflow_api_and_branch_claims_are_policy_bound(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
    field: str,
    replacement: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    run = bundle.release["workflow_run"]
    assert isinstance(run, dict)
    run[field] = replacement
    _write_json(bundle.paths["release_evidence"], bundle.release)
    bundle.rewrite_release_signature()
    bundle.authorization["controller_release_evidence_digest"] = _sha256(
        _canonical(bundle.release)
    )
    _write_json(bundle.paths["authorization_evidence"], bundle.authorization)
    bundle.rewrite_authorization_signature()

    with pytest.raises(bootstrap.BootstrapRefused, match="trusted release authority"):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


@pytest.mark.parametrize(
    ("tool", "mutation", "message"),
    [
        ("git", "argument", "differs from trust policy"),
        ("git", "digest", "Git binary digest"),
        ("docker", "argument", "differs from trust policy"),
        ("docker", "digest", "Docker binary digest"),
    ],
)
def test_git_and_docker_identity_are_path_and_digest_pinned(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
    tool: str,
    mutation: str,
    message: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    if mutation == "argument":
        setattr(bundle.args, f"{tool}_bin", f"/hostile/{tool}")
    else:
        policy = _trust_policy(bundle)
        policy[f"{tool}_sha256"] = "sha256:" + "f" * 64
        _rewrite_trust_policy(bundle, policy)

    with pytest.raises(bootstrap.BootstrapRefused, match=message):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


@pytest.mark.parametrize(
    ("digest_field", "message"),
    [
        ("public_key_sha256", "public key digest"),
        ("public_key_spki_sha256", "public key SPKI digest"),
    ],
)
def test_public_key_pem_and_canonical_spki_digests_are_both_enforced(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
    digest_field: str,
    message: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    policy = _trust_policy(bundle)
    keys = policy["keys"]
    assert isinstance(keys, list)
    release_key = keys[0]
    assert isinstance(release_key, dict)
    release_key[digest_field] = "sha256:" + "f" * 64
    _rewrite_trust_policy(bundle, policy)

    with pytest.raises(bootstrap.BootstrapRefused, match=message):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


def test_different_pem_encodings_cannot_hide_one_reused_cryptographic_key(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)
    policy = _trust_policy(bundle)
    keys = policy["keys"]
    assert isinstance(keys, list) and len(keys) == 2
    release_key, authorization_key = keys
    assert isinstance(release_key, dict) and isinstance(authorization_key, dict)
    assert release_key["public_key_sha256"] != authorization_key["public_key_sha256"]
    authorization_key["public_key_spki_sha256"] = release_key["public_key_spki_sha256"]
    _rewrite_trust_policy(bundle, policy)

    with pytest.raises(
        bootstrap.BootstrapRefused, match="require distinct public keys"
    ):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


def test_launcher_argv_inherits_context_and_names_both_evidence_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = {
        name: tmp_path / name
        for name in (
            "launcher",
            "context",
            "release",
            "authorization",
            "envelope",
            "receipt",
            "wheel",
            "authorizer",
            "history",
            "staged",
        )
    }
    for directory in ("authorizer", "history", "staged"):
        paths[directory].mkdir()
    for file_name in (
        "launcher",
        "context",
        "release",
        "authorization",
        "envelope",
        "receipt",
        "wheel",
    ):
        paths[file_name].write_bytes(b"sealed")
    observed: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append(argv)
        context_index = argv.index("--bootstrap-context-fd") + 1
        context_fd = int(argv[context_index])
        assert kwargs["pass_fds"] == (context_fd,)
        assert argv[argv.index("--release-evidence") + 1] == str(paths["release"])
        assert argv[argv.index("--authorization-evidence") + 1] == str(
            paths["authorization"]
        )
        assert argv[argv.index("--authorizer-repo") + 1] == str(paths["authorizer"])
        assert argv[argv.index("--application-history-repo") + 1] == str(
            paths["history"]
        )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(bootstrap, "_root_owned_bytes", lambda *_args, **_kwargs: b"")
    monkeypatch.setattr(bootstrap.subprocess, "run", run)

    assert (
        bootstrap._invoke_launcher(
            launcher=paths["launcher"],
            bootstrap_context=paths["context"],
            release_evidence=paths["release"],
            authorization_evidence=paths["authorization"],
            envelope=paths["envelope"],
            receipt=paths["receipt"],
            wheel=paths["wheel"],
            authorizer_repo=paths["authorizer"],
            history_repo=paths["history"],
            staged_application_root=paths["staged"],
            git=Path("/usr/bin/git"),
            docker=Path("/usr/bin/docker"),
            descriptor="deploy/product.toml",
        )
        == 0
    )
    assert len(observed) == 1


def test_forged_self_consistent_receipt_cannot_replace_signed_release(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)
    malicious_wheel = b"self-consistent malicious wheel"
    malicious_digest = _sha256(malicious_wheel)
    bundle.paths["wheel"].write_bytes(malicious_wheel)
    bundle.receipt["artifact_sha256"] = malicious_digest
    bundle.envelope["required_controller"][  # type: ignore[index]
        "artifact_sha256"
    ] = malicious_digest
    bundle.authorization["execution_envelope_digest"] = _typed_digest(
        "DeploymentExecutionEnvelopeV1", bundle.envelope
    )
    bundle.paths["receipt"].write_bytes(_canonical(bundle.receipt))
    _write_json(bundle.paths["envelope"], bundle.envelope)
    _write_json(bundle.paths["authorization_evidence"], bundle.authorization)
    bundle.rewrite_authorization_signature()

    with pytest.raises(
        bootstrap.BootstrapRefused, match="controller receipt does not match"
    ):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong", "does not match envelope authorizer"),
        ("failed", "must be completed successfully"),
    ],
)
def test_wrong_or_failed_authorization_workflow_is_refused(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
    mutation: str,
    message: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    run = bundle.authorization["workflow_run"]
    assert isinstance(run, dict)
    if mutation == "wrong":
        run["workflow_path"] = ".github/workflows/foreign.yml"
    else:
        run["conclusion"] = "failure"
    _write_json(bundle.paths["authorization_evidence"], bundle.authorization)
    bundle.rewrite_authorization_signature()

    with pytest.raises(bootstrap.BootstrapRefused, match=message):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


def test_release_signature_cannot_cross_the_authorization_purpose(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)
    _write_json(
        bundle.paths["release_signature"],
        _signature(
            bundle.release,
            purpose=bootstrap.AUTHORIZATION_PURPOSE,
            key_id="authorization-2026-a",
        ),
    )

    with pytest.raises(bootstrap.BootstrapRefused, match="wrong signer purpose"):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


@pytest.mark.parametrize("foreign_binding", ["release", "history"])
def test_authorization_cannot_be_rebound_to_another_release_or_history(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
    foreign_binding: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    if foreign_binding == "release":
        bundle.authorization["controller_release_evidence_digest"] = (
            "sha256:" + "f" * 64
        )
        expected = "different controller release"
    else:
        relation = bundle.envelope["relation_evidence"]
        assert isinstance(relation, dict)
        relation["history_snapshot_digest"] = "sha256:" + "e" * 64
        bundle.authorization["execution_envelope_digest"] = _typed_digest(
            "DeploymentExecutionEnvelopeV1", bundle.envelope
        )
        _write_json(bundle.paths["envelope"], bundle.envelope)
        expected = "not bound to the signed application history"
    _write_json(bundle.paths["authorization_evidence"], bundle.authorization)
    bundle.rewrite_authorization_signature()

    with pytest.raises(bootstrap.BootstrapRefused, match=expected):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


def test_registry_replaced_wheel_is_refused_before_launcher(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)
    bundle.paths["wheel"].write_bytes(b"registry replacement")

    with pytest.raises(
        bootstrap.BootstrapRefused, match="authenticated release evidence"
    ):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


def test_swapped_history_bundle_is_refused_before_checkout(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)
    bundle.paths["history_bundle"].write_bytes(b"foreign application history")

    with pytest.raises(bootstrap.BootstrapRefused, match="signed authorization"):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []


def test_malicious_launcher_is_never_executed(
    tmp_path: Path,
    authenticated_boundaries: list[dict[str, Any]],
) -> None:
    bundle = _build_bundle(tmp_path)
    bundle.paths["launcher"].write_text(
        "raise SystemExit(0)  # malicious bypass\n", encoding="utf-8"
    )

    with pytest.raises(
        bootstrap.BootstrapRefused, match="authenticated release evidence"
    ):
        bootstrap.authenticate_and_launch(bundle.args)
    assert authenticated_boundaries == []
