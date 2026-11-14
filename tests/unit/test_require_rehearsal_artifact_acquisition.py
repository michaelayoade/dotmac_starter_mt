"""Selected-run carrier acquisition and ciphertext joins; hosted CI only."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import urllib.error
import zipfile
from types import SimpleNamespace

import pytest

from tests.unit.test_require_rehearsal_organization_oracle import (
    CIPHERTEXT,
    RUN_ID,
    TOPOLOGY,
    _archive,
    _build,
    _main,
    _network,
    subject,  # noqa: F401
)
from tests.unit.test_require_rehearsal_organization_oracle import (
    require_rehearsal as oracle,
)


def _metadata(raw, **changes):
    result = {
        "id": 9,
        "name": "lane3-rehearsal-receipt",
        "expired": False,
        "size_in_bytes": len(raw),
        "expires_at": "2099-01-01T00:00:00Z",
        "workflow_run": {"id": RUN_ID},
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }
    result.update(changes)
    return result


def _acquire(monkeypatch, raw, metadata=None):
    monkeypatch.setattr(oracle, "_list", lambda *a: [metadata or _metadata(raw)])
    monkeypatch.setattr(oracle, "_download", lambda *a: raw)
    return oracle._receipt_bytes(
        "example/repository", RUN_ID, "lane3-rehearsal-receipt", ""
    )


def test_acquires_all_three_members_and_measured_metadata(monkeypatch):
    raw = _archive(b"receipt")
    selected = _acquire(monkeypatch, raw)
    assert selected.receipt_bytes == b"receipt"
    assert selected.ciphertext_bytes == CIPHERTEXT
    assert selected.status_bytes == b"synthetic status\n"
    assert selected.archive_size == len(raw)
    assert selected.archive_digest == _metadata(raw)["digest"]
    # API-reported size is a distinct observation, not an undocumented equality.
    assert (
        _acquire(monkeypatch, raw, _metadata(raw, size_in_bytes=1)).reported_size == 1
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"id": 0},
        {"name": "other"},
        {"expired": True},
        {"expired": 0},
        {"size_in_bytes": True},
        {"size_in_bytes": 0},
        {"size_in_bytes": 44 * 1024 * 1024 + 1},
        {"expires_at": "2000-01-01T00:00:00Z"},
        {"expires_at": "private-marker"},
        {"workflow_run": {"id": RUN_ID + 1}},
        {"workflow_run": {"id": True}},
        {"workflow_run": None},
        {"digest": "sha256:" + "0" * 64},
    ],
)
def test_invalid_metadata_refuses_before_acceptance(monkeypatch, changes):
    raw = _archive(b"receipt")
    with pytest.raises(oracle.RehearsalMissing, match="^rehearsal_artifact_invalid$"):
        _acquire(monkeypatch, raw, _metadata(raw, **changes))


@pytest.mark.parametrize(
    "names",
    [
        ["receipt.json", "evidence-bundle.age"],
        ["receipt.json", "receipt.json", "evidence-bundle.age"],
        ["receipt.json", "evidence-bundle.age", "../private-marker"],
        [*oracle._MEMBER_LIMITS, "extra"],
    ],
)
def test_closed_zip_members(monkeypatch, names):
    storage = io.BytesIO()
    with zipfile.ZipFile(storage, "w") as bundle:
        for name in names:
            bundle.writestr(name, b"synthetic")
    with pytest.raises(oracle.RehearsalMissing, match="^rehearsal_artifact_invalid$"):
        _acquire(monkeypatch, storage.getvalue())


def test_zip_symlink_and_member_bomb_refused(monkeypatch):
    storage = io.BytesIO()
    with zipfile.ZipFile(storage, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in oracle._MEMBER_LIMITS:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, b"private-marker")
    with pytest.raises(oracle.RehearsalMissing, match="^rehearsal_artifact_invalid$"):
        _acquire(monkeypatch, storage.getvalue())
    raw = _archive(b"receipt")
    monkeypatch.setattr(
        oracle, "_MEMBER_LIMITS", {name: 1 for name in oracle._MEMBER_LIMITS}
    )
    with pytest.raises(oracle.RehearsalMissing, match="^rehearsal_artifact_invalid$"):
        _acquire(monkeypatch, raw)


@pytest.mark.parametrize("raw", [b"private-marker", b"PK\x03\x04", b""])
def test_malformed_zip_is_sanitized(monkeypatch, raw, capsys):
    with pytest.raises(oracle.RehearsalMissing, match="^rehearsal_artifact_invalid$"):
        _acquire(monkeypatch, raw, _metadata(b"nonempty"))
    assert capsys.readouterr() == ("", "")


class Response(io.BytesIO):
    def read(self, size=-1):
        assert size > 0, "unbounded artifact read"
        return super().read(size)


@pytest.mark.parametrize("redirect", [False, True])
def test_both_download_paths_bound_reads_and_strip_token(monkeypatch, redirect):
    monkeypatch.setattr(oracle, "_MAX_ARTIFACT_BYTES", 4)

    def first(request, **kwargs):
        assert request.get_header("Authorization") == "Bearer synthetic-token"
        if redirect:
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "redirect",
                {"Location": "https://storage.example.invalid/signed"},
                None,
            )
        return Response(b"12345")

    def second(location, **kwargs):
        assert isinstance(location, str)  # No authenticated Request crosses redirect.
        return Response(b"12345")

    monkeypatch.setattr(
        oracle.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=first)
    )
    monkeypatch.setattr(oracle.urllib.request, "urlopen", second)
    with pytest.raises(
        oracle.RehearsalMissing, match="^rehearsal_artifact_download_invalid$"
    ):
        oracle._download("https://api.github.com/synthetic", "synthetic-token")


def test_ciphertext_mismatch_refuses_before_authentication(
    subject,  # noqa: F811
    tmp_path,
    monkeypatch,
    capsys,
):
    receipt = _build(subject)
    _network(monkeypatch, receipt.canonical_bytes())
    monkeypatch.setattr(
        oracle, "_download", lambda *a: _archive(receipt.canonical_bytes(), b"wrong")
    )
    monkeypatch.setattr(
        oracle,
        "_require_authenticated_offline_result",
        lambda *a: pytest.fail("mismatch reached authentication"),
    )
    assert _main(tmp_path, TOPOLOGY) == oracle.EXIT_REFUSED
    assert "rehearsal_ciphertext_binding_invalid" in capsys.readouterr().err
    assert not (tmp_path / "source-artifact.json").exists()


def test_retention_joins_actual_source_members(subject, tmp_path, monkeypatch):  # noqa: F811
    receipt = _build(subject)
    _network(monkeypatch, receipt.canonical_bytes())
    monkeypatch.setattr(
        oracle, "_require_authenticated_offline_result", lambda *a: None
    )
    assert _main(tmp_path, TOPOLOGY) == oracle.EXIT_OK
    assert (tmp_path / "evidence-bundle.age").read_bytes() == CIPHERTEXT
    assert (tmp_path / "source-status.md").read_bytes() == b"synthetic status\n"
    document = json.loads((tmp_path / "source-artifact.json").read_bytes())
    assert document["run_id"] == RUN_ID
    assert document["run_attempt"] == 1
    assert document["artifact"]["id"] == 9
    assert document["artifact"]["expires_at"] == "2099-01-01T00:00:00Z"
    assert (
        document["members"]["evidence-bundle.age"]["digest"]
        == receipt.content["evidence_bundle_digest"]
    )


@pytest.mark.parametrize("many_entries", [False, True])
def test_directory_is_bounded_before_zipinfo_allocation(monkeypatch, many_entries):
    storage = io.BytesIO()
    with zipfile.ZipFile(storage, "w") as bundle:
        names = (
            [*oracle._MEMBER_LIMITS, "extra"] if many_entries else oracle._MEMBER_LIMITS
        )
        for name in names:
            info = zipfile.ZipInfo(name)
            if not many_entries:
                info.comment = b"x" * 6000
            bundle.writestr(info, b"synthetic")
    raw = storage.getvalue()
    monkeypatch.setattr(
        oracle.zipfile,
        "ZipFile",
        lambda *a, **k: pytest.fail("unbounded directory reached ZipFile"),
    )
    with pytest.raises(oracle.RehearsalMissing, match="^rehearsal_artifact_invalid$"):
        _acquire(monkeypatch, raw)


def test_zip64_locator_cannot_override_bounded_normal_directory(monkeypatch):
    raw = _archive(b"receipt")
    # Normal EOCD size/count stay bounded; the standard reader independently
    # consults this preceding locator and can replace those EOCD values.
    altered = raw[:-42] + b"PK\x06\x07" + raw[-38:]
    monkeypatch.setattr(
        oracle.zipfile,
        "ZipFile",
        lambda *a, **k: pytest.fail("ZIP64 locator reached ZipFile"),
    )
    with pytest.raises(oracle.RehearsalMissing, match="^rehearsal_artifact_invalid$"):
        _acquire(monkeypatch, altered)
