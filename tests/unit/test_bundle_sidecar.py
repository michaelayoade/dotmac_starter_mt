"""Hosted-CI tests for the bundle sidecar consumer."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest

from scripts.bundle_sidecar import MAX_SIDECAR_BYTES, SidecarError, consume_sidecar


def _observation(*, artifact_id: int, artifact_name: str) -> dict[str, Any]:
    return {
        "repository_id": 7,
        "repository_full_name": "acme/bundle",
        "head_repository_id": 7,
        "head_repository_full_name": "acme/bundle",
        "run_id": 1,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "head_branch": "main",
        "event": "workflow_dispatch",
        "workflow_id": 9,
        "workflow_observed_id": 9,
        "workflow_observed_path": ".github/workflows/dependency-bundle.yml",
        "workflow_path": ".github/workflows/dependency-bundle.yml",
        "status": "completed",
        "conclusion": "success",
        "artifact_id": artifact_id,
        "artifact_name": artifact_name,
        "artifact_digest": "sha256:" + "d" * 64,
        "artifact_size_in_bytes": 123,
        "artifact_run_id": 1,
        "artifact_repository_id": 7,
        "artifact_head_repository_id": 7,
        "artifact_head_sha": "a" * 40,
        "artifact_head_branch": "main",
    }


def _manifest(archive: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "plan_digest": "b" * 64,
        "archive_sha256": "c" * 64,
        "members": {},
        "run": {
            "repository_full_name": archive["repository_full_name"],
            "repository_id": archive["repository_id"],
            "workflow_path": archive["workflow_observed_path"],
            "run_id": archive["run_id"],
            "run_attempt": archive["run_attempt"],
            "trusted_workflow_sha": archive["head_sha"],
            "artifact_id": archive["artifact_id"],
            "artifact_name": archive["artifact_name"],
            "artifact_run_id": archive["artifact_run_id"],
            "environment_name": "production",
        },
    }


def _write_sidecar(
    path: Path,
    contents: bytes,
    *,
    external_attr: int | None = None,
    extra: bool = False,
) -> None:
    info = zipfile.ZipInfo("bundle-manifest.json")
    info.external_attr = (
        external_attr if external_attr is not None else (stat.S_IFREG | 0o600) << 16
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, contents)
        if extra:
            archive.writestr("extra", b"x")


def _inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    return _observation(artifact_id=2, artifact_name="dependency-bundle"), _observation(
        artifact_id=3, artifact_name="dependency-bundle-manifest"
    )


def test_sidecar_accepts_archive_and_sidecar_from_same_run(tmp_path: Path) -> None:
    archive, sidecar = _inputs()
    path = tmp_path / "sidecar.zip"
    _write_sidecar(path, json.dumps(_manifest(archive)).encode())
    assert consume_sidecar(path, archive, sidecar)["run"]["artifact_id"] == 2


def test_sidecar_manifest_binds_archive_not_sidecar_artifact(tmp_path: Path) -> None:
    archive, sidecar = _inputs()
    manifest = _manifest(archive)
    manifest["run"]["artifact_id"] = sidecar["artifact_id"]
    path = tmp_path / "sidecar.zip"
    _write_sidecar(path, json.dumps(manifest).encode())
    with pytest.raises(SidecarError, match="archive observation"):
        consume_sidecar(path, archive, sidecar)


@pytest.mark.parametrize("field", ["repository_id", "head_repository_id", "head_sha"])
def test_sidecar_refuses_observations_from_different_producers(
    tmp_path: Path, field: str
) -> None:
    archive, sidecar = _inputs()
    mismatch = 8 if field.endswith("id") else "b" * 40
    sidecar[field] = mismatch
    if field == "head_repository_id":
        sidecar["artifact_head_repository_id"] = mismatch
    if field == "repository_id":
        sidecar["artifact_repository_id"] = mismatch
    if field == "head_sha":
        sidecar["artifact_head_sha"] = mismatch
    path = tmp_path / "sidecar.zip"
    _write_sidecar(path, json.dumps(_manifest(archive)).encode())
    with pytest.raises(SidecarError, match="observations disagree"):
        consume_sidecar(path, archive, sidecar)


@pytest.mark.parametrize("field", ["status", "artifact_digest"])
def test_sidecar_refuses_incomplete_sidecar_observation(
    tmp_path: Path, field: str
) -> None:
    archive, sidecar = _inputs()
    sidecar[field] = "d" * 64 if field == "artifact_digest" else "in_progress"
    path = tmp_path / "sidecar.zip"
    _write_sidecar(path, json.dumps(_manifest(archive)).encode())
    with pytest.raises(SidecarError, match="sidecar observation"):
        consume_sidecar(path, archive, sidecar)


@pytest.mark.parametrize("field", ["run_id", "artifact_id"])
def test_sidecar_refuses_zero_or_absent_observation_keys(
    tmp_path: Path, field: str
) -> None:
    archive, sidecar = _inputs()
    archive[field] = 0
    path = tmp_path / "sidecar.zip"
    _write_sidecar(
        path,
        json.dumps(
            _manifest(_observation(artifact_id=2, artifact_name="dependency-bundle"))
        ).encode(),
    )
    with pytest.raises(SidecarError, match=f"archive observation {field}"):
        consume_sidecar(path, archive, sidecar)
    archive, sidecar = _inputs()
    del archive[field]
    with pytest.raises(SidecarError, match=f"archive observation {field}"):
        consume_sidecar(path, archive, sidecar)


def test_sidecar_refuses_duplicate_json_keys(tmp_path: Path) -> None:
    archive, sidecar = _inputs()
    path = tmp_path / "sidecar.zip"
    _write_sidecar(path, b'{"schema_version":2,"schema_version":2}')
    with pytest.raises(SidecarError, match="duplicate JSON keys"):
        consume_sidecar(path, archive, sidecar)


@pytest.mark.parametrize(
    ("key", "value"), [("schema_version", 3), ("plan_digest", "not-a-digest")]
)
def test_sidecar_refuses_malformed_schema_or_digest(
    tmp_path: Path, key: str, value: object
) -> None:
    archive, sidecar = _inputs()
    manifest = _manifest(archive)
    manifest[key] = value
    path = tmp_path / "sidecar.zip"
    _write_sidecar(path, json.dumps(manifest).encode())
    with pytest.raises(SidecarError):
        consume_sidecar(path, archive, sidecar)


def test_sidecar_refuses_symlink_member(tmp_path: Path) -> None:
    archive, sidecar = _inputs()
    path = tmp_path / "sidecar.zip"
    _write_sidecar(
        path,
        json.dumps(_manifest(archive)).encode(),
        external_attr=(stat.S_IFLNK | 0o777) << 16,
    )
    with pytest.raises(SidecarError, match="regular file"):
        consume_sidecar(path, archive, sidecar)


def test_sidecar_accepts_permission_only_regular_member(tmp_path: Path) -> None:
    archive, sidecar = _inputs()
    manifest = _manifest(archive)
    path = tmp_path / "sidecar.zip"
    _write_sidecar(
        path,
        json.dumps(manifest).encode(),
        external_attr=0o600 << 16,
    )
    assert consume_sidecar(path, archive, sidecar)["run"] == manifest["run"]


def test_sidecar_refuses_oversized_member(tmp_path: Path) -> None:
    archive, sidecar = _inputs()
    path = tmp_path / "sidecar.zip"
    _write_sidecar(path, b"x" * (MAX_SIDECAR_BYTES + 1))
    with pytest.raises(SidecarError, match="size cap"):
        consume_sidecar(path, archive, sidecar)


def test_sidecar_refuses_extra_member(tmp_path: Path) -> None:
    archive, sidecar = _inputs()
    path = tmp_path / "sidecar.zip"
    _write_sidecar(path, json.dumps(_manifest(archive)).encode(), extra=True)
    with pytest.raises(SidecarError, match="exactly"):
        consume_sidecar(path, archive, sidecar)
