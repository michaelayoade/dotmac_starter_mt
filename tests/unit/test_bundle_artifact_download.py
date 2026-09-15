"""Hosted-CI tests for verified artifact acquisition."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name in ("github_actions_transport", "bundle_github_observation"):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location(
    "bundle_artifact_download", ROOT / "scripts" / "bundle_artifact_download.py"
)
assert spec and spec.loader
download = importlib.util.module_from_spec(spec)
sys.modules["bundle_artifact_download"] = download
spec.loader.exec_module(download)

POLICY = download.TrustedProducerPolicy(
    repository_id=7,
    repository="acme/bundle",
    workflow_path=".github/workflows/dependency-bundle.yml",
    artifact_name="dependency-bundle",
)


class Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


def test_positive_observe_before_stream(monkeypatch, tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    body = b"zip bytes"
    observed = {
        "artifact_size_in_bytes": len(body),
        "artifact_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
    }
    events: list[str] = []
    requests: list[urllib.request.Request] = []
    monkeypatch.setattr(
        download.bundle_github_observation,
        "observe",
        lambda *a: events.append("observe") or observed,
    )

    def open_request(request, *, timeout):
        events.append("open")
        requests.append(request)
        assert timeout == 60
        return Response(body)

    monkeypatch.setattr(download.github_actions_transport.OPENER, "open", open_request)
    result, path = download.acquire_observed_artifact(
        POLICY, 1, 2, "placeholder", tmp_path / "artifact.zip"
    )
    assert result is observed and path.read_bytes() == body
    assert events == ["observe", "open"]
    assert requests[0].full_url == (
        "https://api.github.com/repos/acme/bundle/actions/artifacts/2/zip"
    )
    assert requests[0].get_header("Authorization") == "Bearer placeholder"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".artifact-*")) == []


@pytest.mark.parametrize("digest", ["sha256:" + "0" * 64, "bad"])
def test_digest_mismatch_leaves_no_destination(
    monkeypatch, tmp_path: Path, digest: str
) -> None:
    tmp_path.chmod(0o700)
    body = b"zip bytes"
    observed = {"artifact_size_in_bytes": len(body), "artifact_digest": digest}
    monkeypatch.setattr(
        download.bundle_github_observation, "observe", lambda *a: observed
    )
    monkeypatch.setattr(
        download.github_actions_transport.OPENER, "open", lambda *a: Response(body)
    )
    with pytest.raises(ValueError):
        download.acquire_observed_artifact(
            POLICY, 1, 2, "placeholder", tmp_path / "artifact.zip"
        )
    assert not (tmp_path / "artifact.zip").exists()
    assert list(tmp_path.glob(".artifact-*")) == []


@pytest.mark.parametrize("size_delta", [-1, 1])
def test_size_mismatch_refuses_without_publishing(
    monkeypatch, tmp_path: Path, size_delta: int
) -> None:
    tmp_path.chmod(0o700)
    body = b"zip bytes"
    observed = {
        "artifact_size_in_bytes": len(body) + size_delta,
        "artifact_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
    }
    monkeypatch.setattr(
        download.bundle_github_observation, "observe", lambda *a: observed
    )
    monkeypatch.setattr(
        download.github_actions_transport.OPENER,
        "open",
        lambda *a, **kw: Response(body),
    )
    with pytest.raises(ValueError, match="size cap|size differs"):
        download.acquire_observed_artifact(
            POLICY, 1, 2, "placeholder", tmp_path / "artifact.zip"
        )
    assert not (tmp_path / "artifact.zip").exists()
    assert list(tmp_path.glob(".artifact-*")) == []


def test_observed_oversize_refuses_before_open(monkeypatch, tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    observed = {
        "artifact_size_in_bytes": download.MAX_ARTIFACT_BYTES + 1,
        "artifact_digest": "sha256:" + "a" * 64,
    }
    events: list[str] = []
    monkeypatch.setattr(
        download.bundle_github_observation,
        "observe",
        lambda *a: events.append("observe") or observed,
    )
    monkeypatch.setattr(
        download.github_actions_transport.OPENER,
        "open",
        lambda *a, **kw: events.append("open"),
    )
    with pytest.raises(ValueError, match="size cap"):
        download.acquire_observed_artifact(
            POLICY, 1, 2, "placeholder", tmp_path / "artifact.zip"
        )
    assert events == ["observe"]
    assert not (tmp_path / "artifact.zip").exists()
    assert list(tmp_path.glob(".artifact-*")) == []


def test_existing_destination_is_not_overwritten(monkeypatch, tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    destination = tmp_path / "artifact.zip"
    destination.write_bytes(b"existing")
    body = b"zip bytes"
    observed = {
        "artifact_size_in_bytes": len(body),
        "artifact_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
    }
    events: list[str] = []
    monkeypatch.setattr(
        download.bundle_github_observation,
        "observe",
        lambda *a: events.append("observe") or observed,
    )
    monkeypatch.setattr(
        download.github_actions_transport.OPENER,
        "open",
        lambda *a, **kw: events.append("open"),
    )
    with pytest.raises(FileExistsError):
        download.acquire_observed_artifact(POLICY, 1, 2, "placeholder", destination)
    assert destination.read_bytes() == b"existing"
    assert events == ["observe"]
    assert list(tmp_path.glob(".artifact-*")) == []


def test_destination_created_during_download_is_not_overwritten(
    monkeypatch, tmp_path: Path
) -> None:
    tmp_path.chmod(0o700)
    destination = tmp_path / "artifact.zip"
    body = b"zip bytes"
    observed = {
        "artifact_size_in_bytes": len(body),
        "artifact_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
    }
    monkeypatch.setattr(
        download.bundle_github_observation, "observe", lambda *a: observed
    )

    def open_request(_request, *, timeout):
        assert timeout == 60
        destination.write_bytes(b"created during download")
        return Response(body)

    monkeypatch.setattr(download.github_actions_transport.OPENER, "open", open_request)
    with pytest.raises(FileExistsError):
        download.acquire_observed_artifact(POLICY, 1, 2, "placeholder", destination)
    assert destination.read_bytes() == b"created during download"
    assert list(tmp_path.glob(".artifact-*")) == []


def test_shared_or_symlinked_parent_is_refused_before_open(
    monkeypatch, tmp_path: Path
) -> None:
    body = b"zip bytes"
    observed = {
        "artifact_size_in_bytes": len(body),
        "artifact_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
    }
    events: list[str] = []
    monkeypatch.setattr(
        download.bundle_github_observation,
        "observe",
        lambda *a: events.append("observe") or observed,
    )
    monkeypatch.setattr(
        download.github_actions_transport.OPENER,
        "open",
        lambda *a, **kw: events.append("open"),
    )
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="owned private directory"):
        download.acquire_observed_artifact(
            POLICY, 1, 2, "placeholder", tmp_path / "artifact.zip"
        )
    tmp_path.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="owned private directory"):
        download.acquire_observed_artifact(
            POLICY, 1, 2, "placeholder", alias / "artifact.zip"
        )
    assert events == ["observe", "observe"]
    assert not (tmp_path / "artifact.zip").exists()


def test_source_temp_swap_cannot_return_forged_bytes(
    monkeypatch, tmp_path: Path
) -> None:
    tmp_path.chmod(0o700)
    body = b"zip bytes"
    observed = {
        "artifact_size_in_bytes": len(body),
        "artifact_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
    }
    monkeypatch.setattr(
        download.bundle_github_observation, "observe", lambda *a: observed
    )
    monkeypatch.setattr(
        download.github_actions_transport.OPENER,
        "open",
        lambda *a, **kw: Response(body),
    )
    original_link = os.link

    def replace_source_then_link(source, destination, *, follow_symlinks):
        forged = tmp_path / "forged"
        forged.write_bytes(b"forged bytes")
        os.unlink(source)
        os.symlink(forged, source)
        return original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(download.os, "link", replace_source_then_link)
    destination = tmp_path / "artifact.zip"
    with pytest.raises(ValueError, match="discard the private directory"):
        download.acquire_observed_artifact(POLICY, 1, 2, "placeholder", destination)
    assert destination.is_symlink()
    assert list(tmp_path.glob(".artifact-*")) == []


def test_file_and_parent_are_fsynced_before_success(
    monkeypatch, tmp_path: Path
) -> None:
    tmp_path.chmod(0o700)
    body = b"zip bytes"
    observed = {
        "artifact_size_in_bytes": len(body),
        "artifact_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
    }
    monkeypatch.setattr(
        download.bundle_github_observation, "observe", lambda *a: observed
    )
    monkeypatch.setattr(
        download.github_actions_transport.OPENER,
        "open",
        lambda *a, **kw: Response(body),
    )
    original_fsync = os.fsync
    synced_modes: list[str] = []

    def record_fsync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        synced_modes.append("file" if stat.S_ISREG(mode) else "directory")
        original_fsync(fd)

    monkeypatch.setattr(download.os, "fsync", record_fsync)
    download.acquire_observed_artifact(
        POLICY, 1, 2, "placeholder", tmp_path / "artifact.zip"
    )
    assert synced_modes == ["file", "directory"]
