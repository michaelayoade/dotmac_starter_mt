"""Canaries for the authenticated application-independent controller launcher."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import run_deployment_controller as launcher

CONTROLLER: dict[str, Any] = {
    "distribution": "dotmac-deployment-foundation",
    "exact_version": "0.3.0a1",
    "artifact_sha256": "sha256:" + "a" * 64,
    "launcher_sha256": "sha256:" + "1" * 64,
    "source_revision": "b" * 40,
    "release_run_id": 60366,
    "tag": "dotmac-deployment-foundation-v0.3.0a1",
}
AUTHORIZER: dict[str, Any] = {
    "repository": "michaelayoade/dotmac_starter_mt",
    "workflow_path": ".github/workflows/deployment-release.yml",
    "workflow_revision": "c" * 40,
    "run_id": 987654321,
}
WHEEL_NAME = "dotmac_deployment_foundation-0.3.0a1-py3-none-any.whl"
RECEIPT_DIGEST = "sha256:" + "4" * 64


def _workflow_run(*, workflow_path: str, revision: str, run_id: int) -> dict[str, Any]:
    return {
        "schema": "GitHubWorkflowRunV1",
        "server_origin": "https://github.com",
        "api_origin": "https://api.github.com",
        "repository_id": 1001,
        "repository": "michaelayoade/dotmac_starter_mt",
        "head_repository_id": 1001,
        "head_repository": "michaelayoade/dotmac_starter_mt",
        "workflow_id": 2002,
        "workflow_path": workflow_path,
        "workflow_revision": revision,
        "workflow_blob_sha256": "sha256:" + "5" * 64,
        "head_ref": "refs/heads/main",
        "referenced_workflows": [],
        "run_id": run_id,
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "head_sha": revision,
        "status": "completed",
        "conclusion": "success",
    }


def _envelope_document() -> dict[str, Any]:
    identity = {
        "image_digest": "sha256:" + "d" * 64,
        "source_revision": "e" * 40,
        "configuration_digest": "sha256:" + "f" * 64,
        "manifest_digest": "sha256:" + "0" * 64,
    }
    return {
        "schema": "DeploymentExecutionEnvelope.v1",
        "execution_id": "deployment-run-60366",
        "product": "dotmac_starter",
        "target_ref": "observe:dotmac-starter",
        "plan_digest": "sha256:" + "f" * 64,
        "required_controller": dict(CONTROLLER),
        "authorizer": dict(AUTHORIZER),
        "candidate": identity,
        "expected_current": None,
        "relation_evidence": {
            "relation": "first_install",
            "from_revision": None,
            "to_revision": "e" * 40,
            "history_snapshot_digest": launcher._document_digest(_history_document()),
        },
        "override": None,
    }


def _receipt_document() -> dict[str, Any]:
    return {"schema": "DeploymentControllerReleaseReceipt.v1", **CONTROLLER}


def _release_evidence_document(
    *, receipt_size: int = 1, wheel_size: int = 1
) -> dict[str, Any]:
    artifacts = [
        {
            "schema": "DeploymentControllerReleaseArtifact.v1",
            "name": "DeploymentControllerReleaseReceipt.v1.json",
            "media_type": "application/json",
            "size": receipt_size,
            "sha256": RECEIPT_DIGEST,
        },
        {
            "schema": "DeploymentControllerReleaseArtifact.v1",
            "name": WHEEL_NAME,
            "media_type": "application/zip",
            "size": wheel_size,
            "sha256": CONTROLLER["artifact_sha256"],
        },
        {
            "schema": "DeploymentControllerReleaseArtifact.v1",
            "name": "run_deployment_controller.py",
            "media_type": "text/x-python",
            "size": Path(launcher.__file__).stat().st_size,
            "sha256": CONTROLLER["launcher_sha256"],
        },
    ]
    return {
        "schema": "DeploymentControllerReleaseEvidence.v1",
        "workflow_run": _workflow_run(
            workflow_path=".github/workflows/release-facility.yml",
            revision=str(CONTROLLER["source_revision"]),
            run_id=int(CONTROLLER["release_run_id"]),
        ),
        "distribution": CONTROLLER["distribution"],
        "exact_version": CONTROLLER["exact_version"],
        "tag": CONTROLLER["tag"],
        "source_revision": CONTROLLER["source_revision"],
        "artifacts": artifacts,
    }


def _history_document() -> dict[str, Any]:
    return {
        "schema": "ApplicationHistorySnapshot.v1",
        "server_origin": "https://github.com",
        "api_origin": "https://api.github.com",
        "repository_id": 1001,
        "repository": "michaelayoade/dotmac_starter_mt",
        "object_format": "sha1",
        "from_revision": None,
        "to_revision": "e" * 40,
        "bundle_name": "application-history.bundle",
        "bundle_size": 1024,
        "bundle_sha256": "sha256:" + "6" * 64,
    }


def _authorization_evidence_document(
    *, envelope: dict[str, Any], release: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": "DeploymentAuthorizationEvidence.v1",
        "workflow_run": _workflow_run(
            workflow_path=str(AUTHORIZER["workflow_path"]),
            revision=str(AUTHORIZER["workflow_revision"]),
            run_id=int(AUTHORIZER["run_id"]),
        ),
        "execution_envelope_digest": launcher._envelope_digest(envelope),
        "controller_release_evidence_digest": launcher._document_digest(release),
        "application_history": _history_document(),
    }


def _bootstrap_context_document(
    *,
    envelope: dict[str, Any],
    release: dict[str, Any],
    authorization: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "AuthenticatedDeploymentBootstrapContext.v1",
        "release_evidence_digest": launcher._document_digest(release),
        "authorization_evidence_digest": launcher._document_digest(authorization),
        "execution_envelope_digest": launcher._envelope_digest(envelope),
        "application_history_snapshot_digest": launcher._document_digest(
            dict(authorization["application_history"])
        ),
    }


def _arguments(
    *,
    bootstrap_context_fd: int,
    envelope: Path,
    receipt: Path,
    wheel: Path,
    release_evidence: Path,
    authorization_evidence: Path,
    authorizer_repo: Path,
    application_history_repo: Path,
    staged: Path,
) -> argparse.Namespace:
    return argparse.Namespace(
        bootstrap_context_fd=bootstrap_context_fd,
        envelope=str(envelope),
        receipt=str(receipt),
        wheel=str(wheel),
        release_evidence=str(release_evidence),
        authorization_evidence=str(authorization_evidence),
        authorizer_repo=str(authorizer_repo),
        application_history_repo=str(application_history_repo),
        staged_application_root=str(staged),
        git_bin="/usr/bin/git",
        docker_bin="/usr/bin/docker",
        descriptor="deploy/product.toml",
    )


def test_launcher_parser_exposes_only_the_authenticated_fixed_command() -> None:
    parsed = launcher.build_parser().parse_args(
        [
            "--bootstrap-context-fd",
            "19",
            "--release-evidence",
            "/authority/release.json",
            "--authorization-evidence",
            "/authority/authorization.json",
            "--envelope",
            "/authority/execution.json",
            "--receipt",
            "/authority/receipt.json",
            "--wheel",
            "/authority/controller.whl",
            "--authorizer-repo",
            "/authority/authorizer",
            "--application-history-repo",
            "/authority/application-history",
            "--staged-application-root",
            "/staged/application",
            "--git-bin",
            "/usr/bin/git",
            "--docker-bin",
            "/usr/bin/docker",
        ]
    )

    assert vars(parsed) == {
        "bootstrap_context_fd": 19,
        "release_evidence": "/authority/release.json",
        "authorization_evidence": "/authority/authorization.json",
        "envelope": "/authority/execution.json",
        "receipt": "/authority/receipt.json",
        "wheel": "/authority/controller.whl",
        "authorizer_repo": "/authority/authorizer",
        "application_history_repo": "/authority/application-history",
        "staged_application_root": "/staged/application",
        "git_bin": "/usr/bin/git",
        "docker_bin": "/usr/bin/docker",
        "descriptor": "deploy/product.toml",
    }


def test_bootstrap_context_requires_root_owned_regular_0400_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    envelope = _envelope_document()
    release = _release_evidence_document()
    authorization = _authorization_evidence_document(envelope=envelope, release=release)
    expected = _bootstrap_context_document(
        envelope=envelope, release=release, authorization=authorization
    )
    context_path = tmp_path / "bootstrap-context.json"
    context_path.write_text(json.dumps(expected), encoding="utf-8")
    context_path.chmod(0o400)
    descriptor = os.open(context_path, os.O_RDONLY)
    real_fstat = os.fstat

    def root_owned_fstat(file_descriptor: int) -> os.stat_result | SimpleNamespace:
        if file_descriptor == descriptor:
            observed = real_fstat(file_descriptor)
            return SimpleNamespace(st_mode=observed.st_mode, st_uid=0)
        return real_fstat(file_descriptor)

    monkeypatch.setattr(launcher.os, "fstat", root_owned_fstat)
    try:
        assert launcher._load_bootstrap_context(descriptor) == expected
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    ("mode", "owner"),
    [
        (stat.S_IFREG | 0o600, 0),
        (stat.S_IFREG | 0o400, 1000),
        (stat.S_IFDIR | 0o400, 0),
    ],
)
def test_bootstrap_context_rejects_wrong_owner_mode_or_file_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    owner: int,
) -> None:
    context_path = tmp_path / "bootstrap-context.json"
    context_path.write_text("{}", encoding="utf-8")
    descriptor = os.open(context_path, os.O_RDONLY)
    real_fstat = os.fstat

    def hostile_fstat(file_descriptor: int) -> os.stat_result | SimpleNamespace:
        if file_descriptor == descriptor:
            return SimpleNamespace(st_mode=mode, st_uid=owner)
        return real_fstat(file_descriptor)

    monkeypatch.setattr(launcher.os, "fstat", hostile_fstat)
    try:
        with pytest.raises(launcher.LaunchRefused, match="root-owned regular file"):
            launcher._load_bootstrap_context(descriptor)
    finally:
        os.close(descriptor)


def test_launcher_seals_all_authority_inputs_and_runs_one_controller_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = tmp_path / "authority"
    authority.mkdir()
    envelope_document = _envelope_document()
    release_document = _release_evidence_document()
    authorization_document = _authorization_evidence_document(
        envelope=envelope_document, release=release_document
    )
    context_document = _bootstrap_context_document(
        envelope=envelope_document,
        release=release_document,
        authorization=authorization_document,
    )
    documents = {
        "envelope": envelope_document,
        "receipt": _receipt_document(),
        "release": release_document,
        "authorization": authorization_document,
    }
    paths = {
        name: authority / f"{name}.json"
        for name in ("envelope", "receipt", "release", "authorization")
    }
    original_bytes: dict[str, bytes] = {}
    for name, document in documents.items():
        content = json.dumps(document).encode()
        paths[name].write_bytes(content)
        original_bytes[name] = content
    wheel = authority / WHEEL_NAME
    wheel_bytes = b"exact wheel bytes"
    wheel.write_bytes(wheel_bytes)
    context_path = authority / "bootstrap-context.json"
    context_path.write_text(json.dumps(context_document), encoding="utf-8")
    context_path.chmod(0o400)
    authorizer_repo = tmp_path / "authorizer"
    application_history_repo = tmp_path / "application-history"
    staged = tmp_path / "staged"
    for directory in (authorizer_repo, application_history_repo, staged):
        directory.mkdir()
    commands: list[tuple[list[str], dict[str, Any]]] = []
    verified: list[dict[str, Any]] = []

    def load_context(file_descriptor: int) -> dict[str, Any]:
        assert file_descriptor >= 0
        return dict(context_document)

    def assert_sealed(path: Path, *, name: str) -> None:
        assert path != paths[name]
        assert path.read_bytes() == original_bytes[name]
        assert stat.S_IMODE(path.stat().st_mode) == 0o400

    def load_envelope(path: Path) -> tuple[dict[str, Any], ...]:
        assert_sealed(path, name="envelope")
        return envelope_document, dict(CONTROLLER), dict(AUTHORIZER)

    def load_receipt(path: Path) -> dict[str, Any]:
        assert_sealed(path, name="receipt")
        return _receipt_document()

    def load_release(
        path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any], tuple[dict[str, Any], ...]]:
        assert_sealed(path, name="release")
        return (
            release_document,
            dict(release_document["workflow_run"]),
            tuple(release_document["artifacts"]),
        )

    def load_authorization(path: Path) -> tuple[dict[str, Any], ...]:
        assert_sealed(path, name="authorization")
        return (
            authorization_document,
            dict(authorization_document["workflow_run"]),
            dict(authorization_document["application_history"]),
        )

    def verify_roots(**kwargs: Any) -> None:
        verified.append(kwargs)
        sealed_wheel = kwargs["wheel"]
        assert sealed_wheel != wheel
        assert sealed_wheel.read_bytes() == wheel_bytes
        assert stat.S_IMODE(sealed_wheel.stat().st_mode) == 0o400
        assert kwargs["authorizer_repo"] == authorizer_repo.resolve()
        assert kwargs["application_history_repo"] == application_history_repo.resolve()
        for path in (*paths.values(), wheel):
            path.write_bytes(b"hostile late authority mutation")
        assert sealed_wheel.read_bytes() == wheel_bytes

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append((list(argv), dict(kwargs)))
        environment = kwargs.get("env", {})
        assert "PYTHONPATH" not in environment
        assert "HOSTILE_SECRET" not in environment
        assert "DOCKER_HOST" not in environment
        assert "/staged/hostile-bin" not in environment["PATH"]
        assert (
            environment["DOTMAC_CONTROLLER_LAUNCHER_SHA256"]
            == CONTROLLER["launcher_sha256"]
        )
        if "pass_fds" in kwargs:
            inherited = kwargs["pass_fds"]
            assert len(inherited) == 1
            metadata = os.fstat(inherited[0])
            assert stat.S_ISREG(metadata.st_mode)
            assert stat.S_IMODE(metadata.st_mode) == 0o400
            launch_context = json.loads(os.pread(inherited[0], 1024 * 1024, 0))
            assert launch_context == {
                "schema": "DeploymentControllerLaunchContext.v1",
                "controller": CONTROLLER,
                "authorizer": AUTHORIZER,
                "authorization_evidence": authorization_document,
            }
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(launcher, "_load_bootstrap_context", load_context)
    monkeypatch.setattr(launcher, "_load_envelope", load_envelope)
    monkeypatch.setattr(launcher, "_load_receipt", load_receipt)
    monkeypatch.setattr(launcher, "_load_release_evidence", load_release)
    monkeypatch.setattr(launcher, "_load_authorization_evidence", load_authorization)
    monkeypatch.setattr(launcher, "_verify_roots", verify_roots)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setenv("PYTHONPATH", "/staged/hostile-shadow")
    monkeypatch.setenv("HOSTILE_SECRET", "must-not-cross")
    monkeypatch.setenv("DOCKER_HOST", "tcp://attacker.invalid:2375")
    monkeypatch.setenv("PATH", "/staged/hostile-bin")

    context_descriptor = os.open(context_path, os.O_RDONLY)
    try:
        result = launcher.launch(
            _arguments(
                bootstrap_context_fd=context_descriptor,
                envelope=paths["envelope"],
                receipt=paths["receipt"],
                wheel=wheel,
                release_evidence=paths["release"],
                authorization_evidence=paths["authorization"],
                authorizer_repo=authorizer_repo,
                application_history_repo=application_history_repo,
                staged=staged,
            )
        )
    finally:
        os.close(context_descriptor)

    assert result == 0
    assert len(verified) == 1
    assert len(commands) == 3
    install = commands[1][0]
    assert install[1:5] == ["install", "--quiet", "--no-index", "--no-deps"]
    command = commands[2][0]
    assert command[1:5] == [
        "-I",
        "-m",
        "dotmac_deployment_foundation.cli",
        "--execution-envelope",
    ]
    assert command[5] != str(paths["envelope"])
    assert command[8] == "--launch-context-fd"
    assert int(command[9]) >= 0
    assert command[6:8] + command[10:] == [
        "--staged-application-root",
        str(staged.resolve()),
        "-f",
        "deploy/product.toml",
        "execute-authorized",
        "--authorizer-repo",
        str(authorizer_repo.resolve()),
        "--application-history-repo",
        str(application_history_repo.resolve()),
        "--git-bin",
        "/usr/bin/git",
        "--docker-bin",
        "/usr/bin/docker",
    ]


@pytest.mark.parametrize(
    "kind", ["envelope", "receipt", "wheel", "release", "authorization"]
)
def test_launcher_refuses_authority_material_inside_staged_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    staged = tmp_path / "staged"
    staged.mkdir()
    authority = tmp_path / "authority"
    authority.mkdir()
    paths = {
        "envelope": authority / "execution.json",
        "receipt": authority / "receipt.json",
        "wheel": authority / WHEEL_NAME,
        "release": authority / "release.json",
        "authorization": authority / "authorization.json",
    }
    for path in paths.values():
        path.write_text("placeholder", encoding="utf-8")
    hostile = staged / paths[kind].name
    hostile.write_text("staged authority", encoding="utf-8")
    paths[kind] = hostile
    authorizer = tmp_path / "authorizer"
    application_history = tmp_path / "application-history"
    authorizer.mkdir()
    application_history.mkdir()
    monkeypatch.setattr(launcher, "_load_bootstrap_context", lambda _fd: {})

    with pytest.raises(launcher.LaunchRefused, match="outside the staged"):
        launcher.launch(
            _arguments(
                bootstrap_context_fd=19,
                envelope=paths["envelope"],
                receipt=paths["receipt"],
                wheel=paths["wheel"],
                release_evidence=paths["release"],
                authorization_evidence=paths["authorization"],
                authorizer_repo=authorizer,
                application_history_repo=application_history,
                staged=staged,
            )
        )


@pytest.mark.parametrize(
    ("kind", "loader", "field"),
    [
        ("envelope", launcher._load_envelope, "execution_id"),
        ("receipt", launcher._load_receipt, "artifact_sha256"),
        ("release", launcher._load_release_evidence, "exact_version"),
        (
            "authorization",
            launcher._load_authorization_evidence,
            "execution_envelope_digest",
        ),
    ],
)
def test_launcher_refuses_duplicate_json_fields(
    tmp_path: Path, kind: str, loader: Any, field: str
) -> None:
    documents = {
        "envelope": _envelope_document(),
        "receipt": _receipt_document(),
        "release": _release_evidence_document(),
        "authorization": _authorization_evidence_document(
            envelope=_envelope_document(), release=_release_evidence_document()
        ),
    }
    text = json.dumps(documents[kind], separators=(",", ":"))
    text = text.replace(f'"{field}":', f'"{field}":"shadow","{field}":', 1)
    path = tmp_path / f"{kind}.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(launcher.LaunchRefused, match="duplicate JSON field"):
        loader(path)


def _verification_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tracked_workflow: bool = True,
) -> dict[str, Any]:
    envelope = _envelope_document()
    receipt = _receipt_document()
    receipt_path = tmp_path / "DeploymentControllerReleaseReceipt.v1.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    wheel = tmp_path / WHEEL_NAME
    wheel.write_bytes(b"exact wheel")
    release = _release_evidence_document(
        receipt_size=receipt_path.stat().st_size,
        wheel_size=wheel.stat().st_size,
    )
    authorization = _authorization_evidence_document(envelope=envelope, release=release)
    authorizer_repo = tmp_path / "authorizer"
    application_history_repo = tmp_path / "application-history"
    staged = tmp_path / "staged"
    for directory in (authorizer_repo, application_history_repo, staged):
        directory.mkdir()
    workflow = authorizer_repo / str(AUTHORIZER["workflow_path"])
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: authority\n", encoding="utf-8")
    git_binary = tmp_path / "git"
    docker_binary = tmp_path / "docker"
    for executable in (git_binary, docker_binary):
        executable.write_bytes(b"binary")
        executable.chmod(0o555)

    def fake_git(_git: Path, repository: Path, *arguments: str) -> str:
        if repository == authorizer_repo.resolve():
            if arguments == ("rev-parse", "HEAD"):
                return str(AUTHORIZER["workflow_revision"])
            if arguments == ("remote", "get-url", "origin"):
                return "https://github.com/michaelayoade/dotmac_starter_mt.git"
            if arguments[:1] == ("status",):
                return ""
            if arguments == ("rev-parse", "--is-shallow-repository"):
                return "false"
            if arguments[:2] == ("ls-files", "--error-unmatch"):
                return str(AUTHORIZER["workflow_path"]) if tracked_workflow else ""
        if repository == application_history_repo.resolve():
            if arguments == ("rev-parse", "--show-object-format"):
                return "sha1"
            if arguments == ("rev-parse", "--is-shallow-repository"):
                return "false"
            if arguments[:2] == ("cat-file", "-e"):
                return ""
        raise AssertionError((repository, arguments))

    def fake_sha(path: Path) -> str:
        resolved = path.resolve()
        if resolved == Path(launcher.__file__).resolve():
            return str(CONTROLLER["launcher_sha256"])
        if resolved == wheel.resolve():
            return str(CONTROLLER["artifact_sha256"])
        if resolved == receipt_path.resolve():
            return RECEIPT_DIGEST
        if resolved == workflow.resolve():
            return str(authorization["workflow_run"]["workflow_blob_sha256"])
        raise AssertionError(path)

    monkeypatch.setattr(launcher, "_run_git", fake_git)
    monkeypatch.setattr(launcher, "_sha256", fake_sha)
    return {
        "envelope": envelope,
        "controller": dict(CONTROLLER),
        "authorizer": dict(AUTHORIZER),
        "receipt": receipt,
        "release_evidence": release,
        "release_run": dict(release["workflow_run"]),
        "release_artifacts": tuple(release["artifacts"]),
        "authorization_evidence": authorization,
        "authorization_run": dict(authorization["workflow_run"]),
        "application_history": dict(authorization["application_history"]),
        "bootstrap_context": _bootstrap_context_document(
            envelope=envelope, release=release, authorization=authorization
        ),
        "wheel": wheel,
        "receipt_path": receipt_path,
        "authorizer_repo": authorizer_repo,
        "application_history_repo": application_history_repo,
        "staged_application_root": staged,
        "git_binary": git_binary,
        "docker_binary": docker_binary,
    }


@pytest.mark.parametrize(
    "field",
    [
        "release_evidence_digest",
        "authorization_evidence_digest",
        "execution_envelope_digest",
        "application_history_snapshot_digest",
    ],
)
def test_launcher_refuses_every_mismatched_authenticated_bootstrap_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    inputs = _verification_inputs(tmp_path, monkeypatch)
    inputs["bootstrap_context"][field] = "sha256:" + "9" * 64

    with pytest.raises(launcher.LaunchRefused, match=f"disagrees on {field}"):
        launcher._verify_roots(**inputs)


def test_self_consistent_forged_release_root_cannot_replace_required_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _verification_inputs(tmp_path, monkeypatch)
    forged_release = {
        **inputs["release_evidence"],
        "exact_version": "9.9.9",
        "tag": "dotmac-deployment-foundation-v9.9.9",
    }
    forged_authorization = {
        **inputs["authorization_evidence"],
        "controller_release_evidence_digest": launcher._document_digest(forged_release),
    }
    inputs["release_evidence"] = forged_release
    inputs["authorization_evidence"] = forged_authorization
    inputs["bootstrap_context"] = _bootstrap_context_document(
        envelope=inputs["envelope"],
        release=forged_release,
        authorization=forged_authorization,
    )

    with pytest.raises(launcher.LaunchRefused, match="required release"):
        launcher._verify_roots(**inputs)


def test_self_consistent_forged_authorizer_root_cannot_replace_envelope_authorizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _verification_inputs(tmp_path, monkeypatch)
    forged_run = {**inputs["authorization_run"], "run_id": 111222333}
    forged_authorization = {
        **inputs["authorization_evidence"],
        "workflow_run": forged_run,
    }
    inputs["authorization_evidence"] = forged_authorization
    inputs["authorization_run"] = forged_run
    inputs["bootstrap_context"] = _bootstrap_context_document(
        envelope=inputs["envelope"],
        release=inputs["release_evidence"],
        authorization=forged_authorization,
    )

    with pytest.raises(launcher.LaunchRefused, match="workflow run disagrees"):
        launcher._verify_roots(**inputs)


def test_self_consistent_forged_history_root_cannot_replace_candidate_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _verification_inputs(tmp_path, monkeypatch)
    forged_history = {
        **inputs["application_history"],
        "to_revision": "7" * 40,
    }
    forged_authorization = {
        **inputs["authorization_evidence"],
        "application_history": forged_history,
    }
    inputs["authorization_evidence"] = forged_authorization
    inputs["application_history"] = forged_history
    inputs["bootstrap_context"] = _bootstrap_context_document(
        envelope=inputs["envelope"],
        release=inputs["release_evidence"],
        authorization=forged_authorization,
    )

    with pytest.raises(launcher.LaunchRefused, match="does not end at the candidate"):
        launcher._verify_roots(**inputs)


@pytest.mark.parametrize("collision", ["same-as-authorizer", "inside-staged"])
def test_application_history_root_must_be_distinct_from_other_execution_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collision: str
) -> None:
    inputs = _verification_inputs(tmp_path, monkeypatch)
    if collision == "same-as-authorizer":
        inputs["application_history_repo"] = inputs["authorizer_repo"]
    else:
        nested = inputs["staged_application_root"] / "history"
        nested.mkdir()
        inputs["application_history_repo"] = nested

    with pytest.raises(launcher.LaunchRefused, match="distinct and unnested"):
        launcher._verify_roots(**inputs)


@pytest.mark.parametrize("tracked", [True, False])
def test_authorizing_workflow_must_be_tracked_at_the_exact_authorizer_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tracked: bool
) -> None:
    inputs = _verification_inputs(tmp_path, monkeypatch, tracked_workflow=tracked)

    if tracked:
        launcher._verify_roots(**inputs)
    else:
        with pytest.raises(launcher.LaunchRefused, match="exact tracked path"):
            launcher._verify_roots(**inputs)
