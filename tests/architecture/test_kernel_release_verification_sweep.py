from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from kernel_release_verification_sweep import (  # noqa: E402
    SweepRefused,
    git_output,
    sweep,
)
from release_artifact_verification import canonical_json  # noqa: E402


def record(*, source: str, tag_object: str) -> dict[str, object]:
    version = "0.1.0a101"
    names = sorted(
        {
            f"dotmac_kernel-{version}-py3-none-any.whl",
            f"dotmac_kernel-{version}.tar.gz",
        }
    )
    return {
        "schema": "KernelReleaseEvidence.v1",
        "version": version,
        "tag": f"dotmac-kernel-v{version}",
        "tag_object": tag_object,
        "tag_disposition": "CREATE",
        "source_sha": source,
        "authorization": {
            "schema": "KernelReleaseSourceBinding.v1",
            "state": "allocated",
            "source_sha": source,
            "authorization_commit": "a" * 40,
            "authorization": {
                "latest_tag": "dotmac-kernel-v0.1.0a100",
                "latest_tag_object": "b" * 40,
                "latest_tag_commit": "c" * 40,
                "base_sha": "d" * 40,
                "target_version": version,
                "normalized_release_input_digest": f"sha256:{'e' * 64}",
            },
        },
        "publisher": {
            "repository": "michaelayoade/dotmac_starter_mt",
            "workflow_path": ".github/workflows/release-kernel.yml",
            "head_branch": "main",
            "run_id": 100,
            "run_attempt": 1,
            "artifact_id": 200,
        },
        "verifier": {
            "repository": "michaelayoade/dotmac_starter_mt",
            "ref": "refs/heads/main",
            "source_sha": "f" * 40,
            "run_id": 300,
            "run_attempt": 1,
        },
        "verification_receipt_sha256": "1" * 64,
        "verification_receipt_artifact": "kernel-release-verification-receipt",
        "tag_decision_receipt_sha256": "2" * 64,
        "tag_decision_receipt_artifact": "kernel-release-tag-decision",
        "registry": {
            "index_origin": "https://registry.dotmac.io",
            "observed_identity": {"login": "ci-reader", "is_admin": False},
            "facility_http_methods": ["GET"],
        },
        "files": [{"name": name, "size": 1, "sha256": "3" * 64} for name in names],
    }


def fixture(tmp_path: Path, *, lightweight: bool = False) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    records = tmp_path / "records"
    git_output(tmp_path, "init", "-b", "main", str(repo))
    git_output(repo, "config", "user.name", "Sweep Test")
    git_output(repo, "config", "user.email", "sweep@example.invalid")
    (repo / "subject").write_text("subject\n")
    git_output(repo, "add", "subject")
    git_output(repo, "commit", "-m", "subject")
    source = git_output(repo, "rev-parse", "HEAD")
    tag = "dotmac-kernel-v0.1.0a101"
    tag_args = (
        ("tag", tag, source)
        if lightweight
        else ("tag", "-a", tag, "-m", "verified", source)
    )
    git_output(repo, *tag_args)
    tag_object = git_output(repo, "rev-parse", tag)
    records.mkdir()
    (records / "0.1.0a101.json").write_bytes(
        canonical_json(record(source=source, tag_object=tag_object))
    )
    return repo, records


def test_checked_in_kernel_release_evidence_matches_the_tag_oracle() -> None:
    sweep(ROOT, ROOT / "docs/inventories/kernel-release-verifications")


def test_sweep_detects_deletion_or_orphan_record(tmp_path: Path) -> None:
    repo, records = fixture(tmp_path)
    sweep(repo, records)
    (records / "0.1.0a101.json").unlink()
    with pytest.raises(SweepRefused, match="versions differ"):
        sweep(repo, records)
    git_output(repo, "tag", "-d", "dotmac-kernel-v0.1.0a101")
    (records / "0.1.0a101.json").write_bytes(
        canonical_json(record(source="a" * 40, tag_object="b" * 40))
    )
    with pytest.raises(SweepRefused, match="versions differ"):
        sweep(repo, records)


@pytest.mark.parametrize("field", ["tag_object", "source_sha"])
def test_sweep_detects_changed_tag_coordinates(tmp_path: Path, field: str) -> None:
    repo, records = fixture(tmp_path)
    path = records / "0.1.0a101.json"
    document = json.loads(path.read_text())
    document[field] = "0" * 40
    if field == "source_sha":
        document["authorization"]["source_sha"] = "0" * 40
    path.write_bytes(canonical_json(document))
    with pytest.raises(SweepRefused, match="differs"):
        sweep(repo, records)


def test_sweep_detects_changed_file_hash_and_lightweight_tag(tmp_path: Path) -> None:
    repo, records = fixture(tmp_path)
    path = records / "0.1.0a101.json"
    document = json.loads(path.read_text())
    document["files"][0]["sha256"] = "not-a-digest"
    path.write_bytes(canonical_json(document))
    with pytest.raises(ValueError, match="file evidence"):
        sweep(repo, records)

    other = tmp_path / "lightweight"
    repo, records = fixture(other, lightweight=True)
    with pytest.raises(SweepRefused, match="not annotated"):
        sweep(repo, records)
