"""Hosted-only contracts for bounded late evidence encryption.

The fake age process checks transport and cleanup, never cryptographic proof.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_evidence_bundle as bundle  # noqa: E402
import lane3_evidence_finalization as final  # noqa: E402


def _recipient():
    # Synthetic public encoding only; this does not generate key material.
    alphabet = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    data = [0] * 52
    values = [3, 3, 3, 0, 1, 7, 5] + data + [0] * 6
    checksum = final._polymod(values) ^ 1
    return (
        "age1"
        + "".join(alphabet[x] for x in data)
        + "".join(alphabet[(checksum >> (5 * (5 - i))) & 31] for i in range(6))
    )


def _policy(**changes):
    document = {
        "age_sha256": "sha256:" + "a" * 64,
        "recipient": _recipient(),
        "schema": "lane3.evidence-encryption-policy.v1",
        "state": "enrolled",
    }
    document.update(changes)
    return (json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n").encode()


@pytest.fixture
def enrolled(monkeypatch, tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_bytes(_policy())
    monkeypatch.setattr(final, "_POLICY", policy)
    monkeypatch.setattr(final, "_require_tool", lambda digest: None)
    return final.prepare_encryption()


def _finish(tmp_path, context):
    return final.finalize_evidence(
        probe_evidence=b'{"probe":"private-marker"}\n',
        exposure_calls=b'{"calls":[]}\n',
        context=context,
        output=tmp_path / "evidence.age",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "unknown"},
        {"state": "unenrolled"},
        {"age_sha256": None},
        {"age_sha256": "sha256:" + "A" * 64},
        {"recipient": None},
        {"recipient": "age1" + "q" * 58},
        {"recipient": "ssh-ed25519 AAAA"},
        {"extra": True},
    ],
)
def test_policy_refuses_invalid_state_pins_and_recipients(changes):
    with pytest.raises(final.FinalizationRefused):
        final._parse_policy(_policy(**changes))


def test_unenrolled_before_tool_or_subprocess(monkeypatch, tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_bytes(_policy(state="unenrolled", recipient=None, age_sha256=None))
    monkeypatch.setattr(final, "_POLICY", policy)
    monkeypatch.setattr(final, "_require_tool", lambda _: pytest.fail("tool contacted"))
    monkeypatch.setattr(final.subprocess, "run", lambda *a, **k: pytest.fail("process"))
    with pytest.raises(final.FinalizationUnavailable, match="unenrolled"):
        final.prepare_encryption()


def test_policy_canonical_and_closed():
    raw = _policy()
    for altered in (
        raw + b" ",
        raw.replace(b'"state":', b'"state":"enrolled","state":'),
    ):
        with pytest.raises(final.FinalizationRefused):
            final._parse_policy(altered)


def test_archive_exact_joined_deterministic_members():
    probe, calls = b'{"probe":true}', b'{"calls":[]}'
    first = final._archive(probe, calls)
    assert first == final._archive(probe, calls)
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
        members = archive.getmembers()
        assert [m.name for m in members] == [
            "manifest.json",
            "probe-evidence.json",
            "exposure-calls.json",
        ]
        assert all(m.isfile() and m.mode == 0o600 and m.mtime == 0 for m in members)
        payloads = {m.name: archive.extractfile(m).read() for m in members}
    manifest = payloads.pop("manifest.json")
    bundle.require_plaintext_bundle(manifest, payloads)
    assert payloads == {"probe-evidence.json": probe, "exposure-calls.json": calls}


def _intake_archive(
    *, names=None, overrides=None, contents=None, format=tarfile.USTAR_FORMAT
):
    """Independent tar writer for malformed-input fixtures, never extraction."""
    members = {
        "probe-evidence.json": b'{"probe":true}',
        "exposure-calls.json": b'{"calls":[]}',
    }
    payloads = {"manifest.json": bundle.build_manifest(members), **members}
    payloads.update(contents or {})
    if contents and "manifest.json" not in contents:
        payloads["manifest.json"] = bundle.build_manifest(
            {name: payloads[name] for name in members}
        )
    storage = io.BytesIO()
    with tarfile.open(fileobj=storage, mode="w", format=format) as archive:
        for name in names or payloads:
            raw = payloads.get(name, b"{}")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(raw), 0o600
            for key, value in (overrides or {}).items():
                setattr(info, key, value)
            archive.addfile(info, io.BytesIO(raw))
    return storage.getvalue()


def test_archive_intake_exact_payloads_and_recomputed_manifest():
    bracketed = b'{"probe":"' + b"{" * 200 + b'\\"[' + b"}" * 200 + b'"}'
    for probe in (b'{"probe":true}', b'{"probe":"changed"}', bracketed):
        calls = b'{"calls":[]}'
        manifest, members = final.read_plaintext_evidence_archive(
            final._archive(probe, calls)
        )
        assert members == {"probe-evidence.json": probe, "exposure-calls.json": calls}
        assert manifest == bundle.build_manifest(members)
    independently_written = _intake_archive()
    manifest, members = final.read_plaintext_evidence_archive(independently_written)
    assert manifest == bundle.build_manifest(members)
    # This is consistency, not authenticity: a freshly joined changed bundle is valid.


@pytest.mark.parametrize(
    "names",
    [
        ["manifest.json", "probe-evidence.json", "probe-evidence.json"],
        ["manifest.json", "probe-evidence.json", "exposure-calls.json", "extra"],
        ["probe-evidence.json", "manifest.json", "exposure-calls.json"],
        ["manifest.json", "/probe-evidence.json", "exposure-calls.json"],
        ["manifest.json", "../probe-evidence.json", "exposure-calls.json"],
    ],
)
def test_archive_intake_refuses_wrong_names_order_or_count(names):
    with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
        final.read_plaintext_evidence_archive(_intake_archive(names=names))


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": tarfile.SYMTYPE, "linkname": "private-marker"},
        {"type": tarfile.LNKTYPE, "linkname": "private-marker"},
        {"type": tarfile.GNUTYPE_SPARSE},
        {"type": tarfile.XHDTYPE},
        {"type": tarfile.XGLTYPE},
        {"type": tarfile.GNUTYPE_LONGNAME},
        {"mode": 0o644},
        {"uid": 1},
        {"gid": 1},
        {"mtime": 1},
        {"uname": "private-marker"},
        {"gname": "private-marker"},
        {"linkname": "private-marker"},
    ],
)
def test_archive_intake_refuses_extension_link_and_metadata(overrides):
    with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
        final.read_plaintext_evidence_archive(_intake_archive(overrides=overrides))


@pytest.mark.parametrize("format", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_archive_intake_refuses_extended_formats(format):
    raw = _intake_archive(
        format=format, overrides={"pax_headers": {"comment": "private-marker"}}
    )
    with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
        final.read_plaintext_evidence_archive(raw)


def test_archive_intake_refuses_checksum_truncation_padding_and_trailers():
    raw = _intake_archive()
    bad_checksum = bytes([raw[0] ^ 1]) + raw[1:]
    for altered in (
        bad_checksum,
        raw[:511],
        raw[:700],
        raw[:3072],
        raw[:-512],
        raw + b"private-marker",
        raw + bytes(512),
        raw[:-1] + b"x",
        bytes(512) + raw[512:],
        gzip.compress(raw),
        raw[:1000] + b"x" + raw[1001:],
    ):
        with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
            final.read_plaintext_evidence_archive(altered)


@pytest.mark.parametrize(
    "contents",
    [
        {"manifest.json": b'{"private-marker":true}'},
        {"probe-evidence.json": b"[]"},
        {"probe-evidence.json": b"{"},
        {"probe-evidence.json": b'{"deep":' * 1100 + b"0" + b"}" * 1100},
    ],
)
def test_archive_intake_refuses_invalid_json_and_manifest_joins(contents, capsys):
    raw = _intake_archive(contents=contents)
    with pytest.raises(final.FinalizationRefused) as error:
        final.read_plaintext_evidence_archive(raw)
    assert str(error.value) == "archive-input-invalid"
    assert "private-marker" not in "".join(traceback.format_exception(error.value))
    assert capsys.readouterr() == ("", "")


def test_archive_intake_refuses_digest_mismatch():
    raw = _intake_archive()
    changed = raw.replace(b'{"probe":true}', b'{"probe":null}', 1)
    assert changed != raw
    with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
        final.read_plaintext_evidence_archive(changed)


@pytest.mark.parametrize("declared_size", [16 * 1024 * 1024 + 1, 40 * 1024 * 1024])
def test_archive_intake_refuses_oversized_declared_member(declared_size):
    raw = _intake_archive()
    info = tarfile.TarInfo("probe-evidence.json")
    info.size, info.mode = declared_size, 0o600
    # Find the second header independently, without parsing untrusted data.
    manifest_size = len(
        bundle.build_manifest(
            {
                "probe-evidence.json": b'{"probe":true}',
                "exposure-calls.json": b'{"calls":[]}',
            }
        )
    )
    second_header = 512 + ((manifest_size + 511) // 512) * 512
    changed = (
        raw[:second_header]
        + info.tobuf(format=tarfile.USTAR_FORMAT)
        + raw[second_header + 512 :]
    )
    with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
        final.read_plaintext_evidence_archive(changed)


def test_archive_intake_bounds_and_type_before_header_parse(monkeypatch):
    raw = _intake_archive()

    def forbidden(*args, **kwargs):
        pytest.fail("header parsed before input bound")

    monkeypatch.setattr(final.tarfile.TarInfo, "frombuf", forbidden)
    monkeypatch.setattr(final, "MAX_ARCHIVE_BYTES", len(raw) - 1)
    for bad in (raw, b"", bytearray(raw), True):
        with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
            final.read_plaintext_evidence_archive(bad)


def test_archive_intake_member_bounds_before_payload_join(monkeypatch):
    raw = _intake_archive()
    monkeypatch.setattr(final, "MAX_MEMBER_BYTES", 3)
    with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
        final.read_plaintext_evidence_archive(raw)
    large_manifest = _intake_archive(contents={"manifest.json": b" " * 4097})
    with pytest.raises(final.FinalizationRefused, match="^archive-input-invalid$"):
        final.read_plaintext_evidence_archive(large_manifest)


@pytest.mark.parametrize("bad", [b"", b"private-marker", b"[]", b"{", True])
def test_member_refusals_are_safe(bad):
    with pytest.raises(final.FinalizationRefused) as error:
        final._archive(bad, b"{}")
    assert "private-marker" not in str(error.value)


def test_member_and_archive_bounds(monkeypatch):
    monkeypatch.setattr(final, "MAX_MEMBER_BYTES", 3)
    with pytest.raises(final.FinalizationRefused):
        final._archive(b"{}  ", b"{}")
    monkeypatch.setattr(final, "MAX_MEMBER_BYTES", 20)
    monkeypatch.setattr(final, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(final.FinalizationRefused):
        final._archive(b"{}", b"{}")


def test_encrypt_exact_output_and_safe_transport(enrolled, monkeypatch, tmp_path):
    ciphertext = final._AGE_HEADER + b"synthetic-transport-only"
    monkeypatch.setenv("PRIVATE_CREDENTIAL", "must-not-inherit")

    def run(args, **kwargs):
        assert args == ["/usr/bin/age", "--encrypt", "--recipient", _recipient()]
        assert kwargs["env"] == {"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"}
        assert kwargs["shell"] is False and kwargs["timeout"] == 30
        assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["check"] is False
        assert os.fstat(kwargs["stdout"].fileno()).st_mode & 0o777 == 0o600
        assert b"private-marker" in kwargs["input"]
        kwargs["stdout"].write(ciphertext)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(final.subprocess, "run", run)
    assert _finish(tmp_path, enrolled) == ciphertext
    assert (tmp_path / "evidence.age").read_bytes() == ciphertext


@pytest.mark.parametrize(
    "failure", ["timeout", "nonzero", "plaintext", "oversize", "oserror"]
)
def test_process_failures_remove_only_partial(enrolled, monkeypatch, tmp_path, failure):
    def run(args, **kwargs):
        kwargs["stdout"].write(b"private-marker")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30, output=b"private-marker")
        if failure == "oserror":
            raise OSError("private-marker")
        if failure == "oversize":
            kwargs["stdout"].write(b"x" * 100)
        return subprocess.CompletedProcess(args, 1 if failure == "nonzero" else 0)

    monkeypatch.setattr(final, "MAX_CIPHERTEXT_BYTES", 50)
    monkeypatch.setattr(final.subprocess, "run", run)
    with pytest.raises(
        (final.FinalizationRefused, final.FinalizationUnavailable)
    ) as error:
        _finish(tmp_path, enrolled)
    assert "private-marker" not in str(error.value)
    assert not (tmp_path / "evidence.age").exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_existing_output_never_overwritten(enrolled, monkeypatch, tmp_path, symlink):
    target = tmp_path / "evidence.age"
    existing = tmp_path / "existing"
    existing.write_bytes(b"keep")
    if symlink:
        target.symlink_to(existing)
    else:
        target.write_bytes(b"keep")
    monkeypatch.setattr(final.subprocess, "run", lambda *a, **k: pytest.fail("process"))
    with pytest.raises(final.FinalizationRefused):
        _finish(tmp_path, enrolled)
    assert target.read_bytes() == existing.read_bytes() == b"keep"


def test_context_rechecked_before_output(enrolled, monkeypatch, tmp_path):
    final._POLICY.write_bytes(_policy(recipient="age1" + "q" * 58))
    monkeypatch.setattr(final.subprocess, "run", lambda *a, **k: pytest.fail("process"))
    with pytest.raises(final.FinalizationRefused):
        _finish(tmp_path, enrolled)
    assert not (tmp_path / "evidence.age").exists()


def test_replaced_output_never_unlinked(enrolled, monkeypatch, tmp_path):
    def run(args, **kwargs):
        target = tmp_path / "evidence.age"
        target.unlink()
        target.write_bytes(b"replacement")
        kwargs["stdout"].write(final._AGE_HEADER + b"transport")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(final.subprocess, "run", run)
    with pytest.raises(final.FinalizationRefused):
        _finish(tmp_path, enrolled)
    assert (tmp_path / "evidence.age").read_bytes() == b"replacement"


def test_readback_failure_removes_partial(enrolled, monkeypatch, tmp_path):
    def run(args, **kwargs):
        kwargs["stdout"].write(final._AGE_HEADER + b"transport")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(final.subprocess, "run", run)

    def broken_readback(*args):
        raise OSError("private-marker")

    monkeypatch.setattr(final, "_same_output", broken_readback)
    with pytest.raises(final.FinalizationUnavailable) as error:
        _finish(tmp_path, enrolled)
    assert "private-marker" not in str(error.value)
    assert not (tmp_path / "evidence.age").exists()


def test_output_parent_symlink_refused(enrolled, monkeypatch, tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(final.subprocess, "run", lambda *a, **k: pytest.fail("process"))
    with pytest.raises(final.FinalizationUnavailable):
        final.finalize_evidence(
            probe_evidence=b"{}",
            exposure_calls=b"{}",
            context=enrolled,
            output=link / "evidence.age",
        )
    assert not (target / "evidence.age").exists()


def test_tool_root_ownership_modes_symlink_and_digest(monkeypatch, tmp_path):
    tool = tmp_path / "age"
    tool.write_bytes(b"synthetic executable")
    tool.chmod(0o755)
    monkeypatch.setattr(final, "_AGE", str(tool))
    # A workstation-owned executable never qualifies, even with a correct hash.
    digest = "sha256:" + hashlib.sha256(tool.read_bytes()).hexdigest()
    if tool.stat().st_uid != 0:
        with pytest.raises(final.FinalizationRefused):
            final._require_tool(digest)
    tool.chmod(0o777)
    with pytest.raises(final.FinalizationRefused):
        final._require_tool(digest)
    link = tmp_path / "link"
    link.symlink_to(tool)
    monkeypatch.setattr(final, "_AGE", str(link))
    with pytest.raises(final.FinalizationRefused):
        final._require_tool(digest)


def test_tool_hash_pin_and_size_with_synthetic_root_metadata(monkeypatch, tmp_path):
    tool = tmp_path / "age"
    tool.write_bytes(b"synthetic executable")
    tool.chmod(0o755)
    monkeypatch.setattr(final, "_AGE", str(tool))
    original_lstat, original_fstat = os.lstat, os.fstat

    def as_root(metadata):
        fields = {
            name: getattr(metadata, name)
            for name in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_gid",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        return SimpleNamespace(**fields, st_uid=0)

    monkeypatch.setattr(final.os, "lstat", lambda path: as_root(original_lstat(path)))
    monkeypatch.setattr(final.os, "fstat", lambda fd: as_root(original_fstat(fd)))
    final._require_tool("sha256:" + hashlib.sha256(b"synthetic executable").hexdigest())
    with pytest.raises(final.FinalizationRefused, match="pin-mismatch"):
        final._require_tool("sha256:" + "0" * 64)
    monkeypatch.setattr(final, "_MAX_TOOL_BYTES", 2)
    with pytest.raises(final.FinalizationRefused, match="unsafe"):
        final._require_tool("sha256:" + "0" * 64)
