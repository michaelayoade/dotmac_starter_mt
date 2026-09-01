from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import write_kernel_release_verification_record as writer  # noqa: E402
from release_artifact_verification import canonical_json  # noqa: E402


def receipts(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    version = "0.1.0a101"
    source = "a" * 40
    facility = "b" * 40
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
    verification = {
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
            "expected_tag": f"dotmac-kernel-v{version}",
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
    verification_bytes = canonical_json(verification)
    compact = [{"name": item["name"], "size": 1, "sha256": "c" * 64} for item in files]
    decision = {
        "schema": "KernelReleaseTagDecision.v1",
        "disposition": "CREATE",
        "tag": f"dotmac-kernel-v{version}",
        "tag_object": "d" * 40,
        "source_sha": source,
        "verification": {
            "receipt_sha256": hashlib.sha256(verification_bytes).hexdigest(),
            "facility_source_sha": facility,
            "facility_run_id": 123,
            "facility_run_attempt": 1,
            "publisher_run_id": 100,
            "publisher_run_attempt": 1,
            "publisher_artifact_id": 200,
            "files": compact,
        },
    }
    verification_path = tmp_path / "verification.json"
    decision_path = tmp_path / "decision.json"
    verification_path.write_bytes(verification_bytes)
    decision_path.write_bytes(canonical_json(decision))
    return verification_path, decision_path


def rewrite(path: Path, mutation) -> None:
    import json

    document = json.loads(path.read_text())
    mutation(document)
    path.write_bytes(canonical_json(document))


def build(verification: Path, decision: Path):
    return writer.build_record(
        verification,
        decision,
        expected_version="0.1.0a101",
        expected_tag="dotmac-kernel-v0.1.0a101",
    )


def test_evidence_record_is_per_version_immutable_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verification, decision = receipts(tmp_path)
    records = tmp_path / "records"
    monkeypatch.setattr(writer, "RECORDS", records)
    monkeypatch.setattr(writer, "verify_live_tag", lambda record, repo: None)
    version, record = build(verification, decision)
    assert writer.write_record(version, record) == "CREATE"
    destination = records / "0.1.0a101.json"
    assert destination.read_bytes() == canonical_json(record)
    assert writer.write_record(version, record) == "ALREADY"
    second_verification, second_decision = receipts(tmp_path / "second")
    rewrite(
        second_verification,
        lambda value: value["facility"].__setitem__("run_id", 124),
    )
    verification_bytes = second_verification.read_bytes()
    rewrite(
        second_decision,
        lambda value: value.update(
            {
                "disposition": "ALREADY",
                "verification": {
                    **value["verification"],
                    "receipt_sha256": hashlib.sha256(verification_bytes).hexdigest(),
                    "facility_run_id": 124,
                },
            }
        ),
    )
    second_version, second_record = build(second_verification, second_decision)
    assert writer.write_record(second_version, second_record) == "ALREADY"
    assert destination.read_bytes() == canonical_json(record)
    changed = {**record, "tag_object": "e" * 40}
    with pytest.raises(writer.RecordRefused, match="immutable release facts"):
        writer.write_record(version, changed)


@pytest.mark.parametrize(
    ("target", "mutation", "message"),
    [
        (
            "verification",
            lambda value: value.__setitem__("extra", True),
            "fields differ",
        ),
        (
            "verification",
            lambda value: value.pop("verdict"),
            "fields differ",
        ),
        (
            "verification",
            lambda value: value["release"].__setitem__("version", "../../escape"),
            "outer release coordinates",
        ),
        (
            "verification",
            lambda value: value["release"]["retained_build_observation"].__setitem__(
                "repository", "outsider/fork"
            ),
            "provider observations",
        ),
        (
            "verification",
            lambda value: value["release"]["retained_build_observation"].__setitem__(
                "workflow_path", ".github/workflows/other.yml"
            ),
            "provider observations",
        ),
        (
            "verification",
            lambda value: value["release"]["registry_observation"].__setitem__(
                "index_origin", "https://packages.example"
            ),
            "provider observations",
        ),
        (
            "verification",
            lambda value: value["release"]["registry_observation"][
                "observed_identity"
            ].__setitem__("is_admin", True),
            "provider observations",
        ),
        (
            "decision",
            lambda value: value.__setitem__("source_sha", "e" * 40),
            "tag and release coordinates",
        ),
        (
            "decision",
            lambda value: value["verification"]["files"][0].__setitem__(
                "sha256", "e" * 64
            ),
            "verification chain",
        ),
    ],
)
def test_tampered_missing_or_extra_evidence_is_refused(
    tmp_path: Path, target: str, mutation, message: str
) -> None:
    verification, decision = receipts(tmp_path)
    rewrite(verification if target == "verification" else decision, mutation)
    with pytest.raises((writer.RecordRefused, ValueError), match=message):
        build(verification, decision)


def test_noncanonical_receipt_bytes_are_refused(tmp_path: Path) -> None:
    verification, decision = receipts(tmp_path)
    verification.write_bytes(verification.read_bytes().replace(b"{", b"{ ", 1))
    with pytest.raises(writer.RecordRefused, match="not canonical"):
        build(verification, decision)


def test_version_cannot_select_a_path_outside_the_record_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(writer, "RECORDS", tmp_path / "records")
    monkeypatch.setattr(writer, "verify_live_tag", lambda record, repo: None)
    with pytest.raises(ValueError, match="canonical public prerelease"):
        writer.write_record("../../escape", {})


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_durable_record_revalidates_the_live_annotated_remote_tag(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Record Test")
    _git(repo, "config", "user.email", "record@example.invalid")
    (repo / "subject").write_text("subject\n")
    _git(repo, "add", "subject")
    _git(repo, "commit", "-m", "subject")
    source = _git(repo, "rev-parse", "HEAD")
    tag = "dotmac-kernel-v0.1.0a101"
    _git(repo, "tag", "-a", tag, "-m", "verified", source)
    tag_object = _git(repo, "rev-parse", tag)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "main", tag)
    record = {"tag": tag, "tag_object": tag_object, "source_sha": source}
    writer.verify_live_tag(record, repo=repo)
    with pytest.raises(writer.RecordRefused, match="tag object differs"):
        writer.verify_live_tag({**record, "tag_object": "e" * 40}, repo=repo)
