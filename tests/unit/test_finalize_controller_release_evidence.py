"""Adversarial canaries for the post-completion release finalizer."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "finalize_controller_release_evidence.py"
REVISION = "a" * 40
OTHER_REVISION = "b" * 40
VERSION = "0.3.0a1"
WHEEL_NAME = f"dotmac_deployment_foundation-{VERSION}-py3-none-any.whl"
WHEEL = b"wheel-bytes"
LAUNCHER = b"#!/usr/bin/env python3\n"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "controller_evidence_finalizer", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


finalizer = _load()


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _expected(**overrides: object):
    values = {
        "server_origin": "https://github.com",
        "api_url": "https://api.github.com",
        "repository_id": 101,
        "repository": "michaelayoade/dotmac_starter_mt",
        "head_repository_id": 101,
        "head_repository": "michaelayoade/dotmac_starter_mt",
        "workflow_id": 202,
        "run_id": 303,
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "head_sha": REVISION,
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
        "head_sha": REVISION,
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "referenced_workflows": [],
        "path": ".github/workflows/release-facility.yml",
        "repository": {
            "id": 101,
            "full_name": "michaelayoade/dotmac_starter_mt",
        },
        "head_repository": {
            "id": 101,
            "full_name": "michaelayoade/dotmac_starter_mt",
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
    return finalizer.validate_workflow_run(
        run_document=_run_document(**run_overrides),
        repository_document={
            "id": 101,
            "full_name": "michaelayoade/dotmac_starter_mt",
        },
        workflow_document={
            "id": 202,
            "name": "Release facility",
            "path": ".github/workflows/release-facility.yml",
            "state": "active",
        },
        workflow_blob=b"exact release workflow",
        branch_document=branch_document
        or {
            "name": "main",
            "protected": True,
            "commit": {"sha": OTHER_REVISION},
        },
        referenced_workflows=referenced_workflows,
        expected=_expected(),
    )


def _receipt(*, wheel: bytes = WHEEL, launcher: bytes = LAUNCHER) -> bytes:
    return (
        json.dumps(
            {
                "schema": "DeploymentControllerReleaseReceipt.v1",
                "distribution": "dotmac-deployment-foundation",
                "exact_version": VERSION,
                "artifact_sha256": _digest(wheel),
                "launcher_sha256": _digest(launcher),
                "source_revision": REVISION,
                "release_run_id": 303,
                "tag": f"dotmac-deployment-foundation-v{VERSION}",
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _archive(*, receipt: bytes | None = None, extra: bool = False) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr(f"wheel/{WHEEL_NAME}", WHEEL)
        bundle.writestr("launcher/run_deployment_controller.py", LAUNCHER)
        bundle.writestr(
            "DeploymentControllerReleaseReceipt.v1.json",
            _receipt() if receipt is None else receipt,
        )
        if extra:
            bundle.writestr("unbound.txt", b"not in the release")
    return stream.getvalue()


def _bundle():
    return finalizer.read_release_bundle(_archive(), version=VERSION, run=_expected())


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("id", 999),
        ("run_attempt", 2),
        ("workflow_id", 999),
        ("event", "push"),
        ("head_sha", OTHER_REVISION),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("path", ".github/workflows/foreign.yml"),
    ],
)
def test_every_triggered_run_coordinate_is_independently_rechecked(
    field: str, replacement: object
) -> None:
    with pytest.raises(finalizer.FinalizationRefused):
        _workflow_run(**{field: replacement})  # type: ignore[arg-type]


def test_foreign_head_repository_is_refused_even_when_the_run_is_green() -> None:
    with pytest.raises(finalizer.FinalizationRefused):
        _workflow_run(head_repository={"id": 404, "full_name": "attacker/fork"})


def test_exact_workflow_blob_digest_is_carried_into_the_run_identity() -> None:
    first = _workflow_run()
    second = finalizer.validate_workflow_run(
        run_document=_run_document(),
        repository_document={
            "id": 101,
            "full_name": "michaelayoade/dotmac_starter_mt",
        },
        workflow_document={
            "id": 202,
            "name": "Release facility",
            "path": ".github/workflows/release-facility.yml",
            "state": "active",
        },
        workflow_blob=b"substituted release workflow",
        branch_document={"name": "main", "protected": True},
        referenced_workflows=(),
        expected=_expected(),
    )
    assert first.workflow_blob_sha256 != second.workflow_blob_sha256


def test_run_path_suffix_is_bound_to_the_requeried_protected_branch() -> None:
    workflow = _workflow_run(
        path=".github/workflows/release-facility.yml@refs/heads/main"
    )
    assert workflow.head_ref == "refs/heads/main"
    assert (
        _workflow_run(path=".github/workflows/release-facility.yml@main").head_ref
        == "refs/heads/main"
    )

    with pytest.raises(finalizer.FinalizationRefused):
        _workflow_run(path=".github/workflows/release-facility.yml@refs/heads/foreign")


def test_branch_protection_is_required_without_current_tip_equality() -> None:
    workflow = _workflow_run(
        branch_document={
            "name": "main",
            "protected": True,
            "commit": {"sha": OTHER_REVISION},
        }
    )
    assert workflow.head_sha == REVISION

    with pytest.raises(finalizer.FinalizationRefused):
        _workflow_run(branch_document={"name": "main", "protected": False})


def test_api_origin_is_derived_before_a_reader_can_hold_credentials() -> None:
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.HttpReader(
            server_origin="https://github.com",
            api_url="https://attacker.example/api/v3",
            github_token="not-a-real-token",
            registry_username="publisher",
            registry_token="not-a-real-token",
        )


class _ReferencedWorkflowReader:
    def github_json(self, url: str, *, name: str) -> dict[str, object]:
        del name
        assert url == (
            "https://api.github.com/repos/dotmac/shared/contents/"
            ".github/workflows/reusable.yml?ref=" + OTHER_REVISION
        )
        return {
            "type": "file",
            "path": ".github/workflows/reusable.yml",
            "encoding": "base64",
            "content": "ZXhhY3QgcmV1c2FibGUgd29ya2Zsb3c=",
        }


def test_referenced_workflow_ref_revision_and_blob_are_all_bound() -> None:
    run = _run_document(
        referenced_workflows=[
            {
                "path": "dotmac/shared/.github/workflows/reusable.yml@stable",
                "ref": "refs/heads/stable",
                "sha": OTHER_REVISION,
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
    assert observed[0].workflow_revision == OTHER_REVISION
    assert observed[0].workflow_blob_sha256 == _digest(b"exact reusable workflow")

    hostile = _run_document(
        referenced_workflows=[
            {
                "path": "dotmac/shared/.github/workflows/reusable.yml@foreign",
                "ref": "refs/heads/stable",
                "sha": OTHER_REVISION,
            }
        ]
    )
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer._referenced_workflows(
            reader=_ReferencedWorkflowReader(),
            expected=_expected(),
            run_document=hostile,
        )
    missing = _run_document()
    missing.pop("referenced_workflows")
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer._referenced_workflows(
            reader=_ReferencedWorkflowReader(),
            expected=_expected(),
            run_document=missing,
        )


def test_actions_artifact_is_exactly_the_receipt_launcher_and_wheel() -> None:
    bundle = _bundle()
    assert bundle.wheel == WHEEL
    assert bundle.launcher == LAUNCHER
    assert bundle.receipt == _receipt()


def test_a_forged_receipt_is_refused() -> None:
    document = json.loads(_receipt())
    document["launcher_sha256"] = _digest(b"attacker launcher")
    forged = (json.dumps(document) + "\n").encode()
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.read_release_bundle(
            _archive(receipt=forged), version=VERSION, run=_expected()
        )


def test_an_unbound_fourth_actions_artifact_file_is_refused() -> None:
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer.read_release_bundle(
            _archive(extra=True), version=VERSION, run=_expected()
        )


class _RegistryReader:
    def __init__(self, *, wheel: bytes = WHEEL) -> None:
        self.wheel = wheel

    def registry_bytes(self, url: str, *, name: str, limit: int) -> bytes:
        del name, limit
        if url.endswith("/run_deployment_controller.py"):
            return LAUNCHER
        if url.endswith("/DeploymentControllerReleaseReceipt.v1.json"):
            return _receipt()
        if "/simple/" in url:
            return f'<a href="/files/{WHEEL_NAME}#sha256=ignored">wheel</a>'.encode()
        if url.endswith(WHEEL_NAME):
            return self.wheel
        raise AssertionError(url)


def test_all_three_registry_files_must_equal_the_actions_artifact() -> None:
    material = finalizer._registry_material(
        reader=_RegistryReader(),
        registry_origin="https://registry.dotmac.io",
        bundle=_bundle(),
    )
    assert material == (WHEEL, LAUNCHER, _receipt())


def test_registry_wheel_replacement_is_refused() -> None:
    with pytest.raises(finalizer.FinalizationRefused):
        finalizer._registry_material(
            reader=_RegistryReader(wheel=b"replacement"),
            registry_origin="https://registry.dotmac.io",
            bundle=_bundle(),
        )


def test_release_evidence_contains_all_three_sorted_registry_artifacts() -> None:
    evidence = finalizer.build_evidence(
        workflow_run=_workflow_run(),
        bundle=_bundle(),
        registry_material=(WHEEL, LAUNCHER, _receipt()),
    )
    assert [artifact.name for artifact in evidence.artifacts] == sorted(
        [
            WHEEL_NAME,
            "run_deployment_controller.py",
            "DeploymentControllerReleaseReceipt.v1.json",
        ]
    )
    assert evidence.workflow_run.run_attempt == 1


def test_signer_receives_the_domain_separated_release_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "release.pem"
    key.write_text("not inspected by Python")
    key.chmod(0o600)
    seen: list[bytes] = []

    def fake_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        if argv[1] == "pkey":
            return subprocess.CompletedProcess(argv, 0, stdout=b"ED25519 Private-Key")
        payload = kwargs.get("input")
        assert isinstance(payload, bytes)
        seen.append(payload)
        return subprocess.CompletedProcess(argv, 0, stdout=b"s" * 64)

    monkeypatch.setattr(finalizer.subprocess, "run", fake_run)
    evidence = finalizer.build_evidence(
        workflow_run=_workflow_run(),
        bundle=_bundle(),
        registry_material=(WHEEL, LAUNCHER, _receipt()),
    )
    signature = finalizer.sign_evidence(
        evidence,
        key_id="release-2026-a",
        private_key_path=key,
        openssl_path=Path("/bin/echo"),
    )
    expected = finalizer.signing_payload_bytes(
        purpose=finalizer.EvidencePurpose.RELEASE,
        key_id="release-2026-a",
        payload_schema=finalizer.RELEASE_EVIDENCE_SCHEMA,
        document=evidence.to_document(),
    )
    assert seen == [expected]
    assert signature.purpose is finalizer.EvidencePurpose.RELEASE
