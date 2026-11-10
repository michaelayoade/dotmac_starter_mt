"""Hosted-CI-only synthetic OpenSSH authentication tests; no production keys."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import lane3_offline_signature as adapter
from lane3_offline_result import (
    NAMESPACE,
    PRINCIPAL,
    OfflineResultCoordinates,
    OfflineResultRefused,
    OfflineResultUnavailable,
    parse_policy,
)


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def command(*args, input=None):
    completed = subprocess.run(  # noqa: S603 -- fixed CI-only synthetic helper
        ["/usr/bin/ssh-keygen", *args],
        input=input,
        capture_output=True,
        check=False,
        timeout=10,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"},
    )
    assert completed.returncode == 0, "synthetic CI OpenSSH operation failed"
    return completed.stdout


@pytest.fixture
def signed(tmp_path):
    assert pathlib.Path("/usr/bin/ssh-keygen").is_file(), "CI requires OpenSSH"
    key = tmp_path / "synthetic"
    command("-q", "-t", "ed25519", "-N", "", "-f", str(key))
    public = key.with_suffix(".pub").read_bytes().split()
    public_key = b" ".join(public[:2])
    now = datetime.now(UTC).replace(microsecond=0)
    coordinates = OfflineResultCoordinates(
        1, 2, 1, digest(b"r"), digest(b"c"), digest(b"a"), "a" * 40
    )

    def build(
        *,
        principal=PRINCIPAL,
        namespace=NAMESPACE,
        revoked=b"",
        validity=None,
        tool_pin=None,
    ):
        after, before = validity or (now - timedelta(days=1), now + timedelta(days=1))
        options = (
            f'namespaces="{namespace}",valid-after="{after:%Y%m%d%H%M%SZ}",'
            f'valid-before="{before:%Y%m%d%H%M%SZ}"'
        )
        signers = f"{principal} {options} ".encode() + public_key + b"\n"
        policy_document = {
            "schema": "lane3.offline-verifier-policy.v1",
            "state": "enrolled",
            "namespace": NAMESPACE,
            "principal": PRINCIPAL,
            "audience": "dotmac.foundation.publication",
            "ssh_keygen_sha256": tool_pin
            or digest(pathlib.Path("/usr/bin/ssh-keygen").read_bytes()),
            "verifier_source_revision": "b" * 40,
            "verifier_profile_digest": digest(b"profile"),
            "allowed_signers_digest": digest(signers),
            "revoked_keys_digest": digest(revoked),
            "max_result_age_seconds": 86400,
            "future_clock_tolerance_seconds": 300,
        }
        policy = parse_policy(canonical(policy_document), signers, revoked)
        document = {
            "schema": "lane3.offline-verification-result.v1",
            "purpose": NAMESPACE,
            "audience": "dotmac.foundation.publication",
            "coordinates": {
                name: getattr(coordinates, name)
                for name in coordinates.__dataclass_fields__
            },
            "verifier_source_revision": "b" * 40,
            "verifier_profile_digest": digest(b"profile"),
            "policy_digest": policy.sha256_digest(),
            "verified_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "conclusion": "verified",
        }
        payload = canonical(document)
        signature = command(
            "-Y", "sign", "-f", str(key), "-n", NAMESPACE, input=payload
        )
        return payload, signature, policy

    return build, coordinates, now, key, public_key


def authenticate(signed, **changes):
    build, expected, now, _, _ = signed
    payload, signature, policy = build()
    values = {
        "payload": payload,
        "signature": signature,
        "expected": expected,
        "policy": policy,
        "now": now,
    }
    values.update(changes)
    return adapter.require_authenticated_result(**values)


def test_actual_signature_and_tuple(signed):
    assert authenticate(signed).coordinates == signed[1]


def test_tampered_signature_and_payload(signed):
    build, _, now, _, _ = signed
    payload, signature, _ = build()
    with pytest.raises(OfflineResultRefused):
        authenticate(signed, signature=signature.replace(b"A", b"B", 1))
    with pytest.raises(OfflineResultRefused):
        authenticate(signed, payload=payload.replace(b'"run_id":2', b'"run_id":3'))
    # Still canonical and within all binding/time constraints: only actual
    # signature verification can detect these modified claim bytes.
    tampered = payload.replace(
        now.strftime("%Y-%m-%dT%H:%M:%SZ").encode(),
        (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ").encode(),
    )
    with pytest.raises(OfflineResultRefused, match="^offline-signature-invalid$"):
        authenticate(signed, payload=tampered)


def test_wrong_signature_namespace_and_key(signed, tmp_path):
    build, _, _, key, _ = signed
    payload, _, _ = build()
    wrong_namespace = command(
        "-Y", "sign", "-f", str(key), "-n", "wrong", input=payload
    )
    other_key = tmp_path / "other"
    command("-q", "-t", "ed25519", "-N", "", "-f", str(other_key))
    wrong_key = command(
        "-Y", "sign", "-f", str(other_key), "-n", NAMESPACE, input=payload
    )
    for signature in (wrong_namespace, wrong_key):
        with pytest.raises(OfflineResultRefused):
            authenticate(signed, signature=signature)


def test_revocation_and_signer_validity(signed):
    build, expected, now, _, public_key = signed
    for options in (
        {"revoked": public_key + b"\n"},
        {"validity": (now - timedelta(days=2), now - timedelta(days=1))},
        {"validity": (now + timedelta(days=1), now + timedelta(days=2))},
    ):
        payload, signature, policy = build(**options)
        with pytest.raises(OfflineResultRefused):
            adapter.require_authenticated_result(
                payload, signature, expected=expected, policy=policy, now=now
            )


def test_tool_pin_and_bounded_inputs(signed):
    build, expected, now, _, _ = signed
    payload, signature, policy = build(tool_pin=digest(b"wrong"))
    with pytest.raises(OfflineResultRefused, match="offline-tool-pin-mismatch"):
        adapter.require_authenticated_result(
            payload, signature, expected=expected, policy=policy, now=now
        )
    for changes in ({"payload": b"x" * 16385}, {"signature": b"x" * 8193}):
        with pytest.raises(OfflineResultRefused):
            authenticate(signed, **changes)


def test_timeout_is_unavailable_and_diagnostics_private(signed, monkeypatch):
    build, expected, now, _, _ = signed
    payload, signature, policy = build()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("PRIVATE", 10, stderr=b"PRIVATE")

    monkeypatch.setattr(adapter.subprocess, "run", timeout)
    with pytest.raises(OfflineResultUnavailable, match="^offline-tool-timeout$"):
        adapter.require_authenticated_result(
            payload, signature, expected=expected, policy=policy, now=now
        )


def test_policy_protection_and_missing_revocations(signed):
    build, expected, now, _, _ = signed
    payload, signature, policy = build()
    for changes in (
        {"allowed_signers_bytes": b"x" * 8193},
        {"revoked_keys_bytes": b"x" * 65537},
        {"canonical_bytes": b"x" * 16385},
        {"revoked_keys_bytes": None},
        {"principal": "wrong"},
        {"namespace": "wrong"},
    ):
        forged = dataclasses.replace(policy, **changes)
        with pytest.raises(OfflineResultRefused):
            adapter.require_authenticated_result(
                payload, signature, expected=expected, policy=forged, now=now
            )


def test_schema_refuses_wrong_signer_principal_and_namespace(signed):
    build = signed[0]
    for options in ({"principal": "wrong"}, {"namespace": "wrong"}):
        with pytest.raises(OfflineResultRefused):
            build(**options)


def test_current_time_and_tuple_bindings(signed):
    with pytest.raises(OfflineResultRefused):
        authenticate(signed, expected=dataclasses.replace(signed[1], run_attempt=2))
    with pytest.raises(OfflineResultRefused):
        authenticate(signed, now=signed[2] + timedelta(hours=2))


def test_missing_tool_is_unavailable(signed, monkeypatch):
    build, expected, now, _, _ = signed
    payload, signature, policy = build()

    def missing(path):
        raise FileNotFoundError("PRIVATE")

    monkeypatch.setattr(adapter.os, "lstat", missing)
    with pytest.raises(OfflineResultUnavailable, match="^offline-tool-unavailable$"):
        adapter.require_authenticated_result(
            payload, signature, expected=expected, policy=policy, now=now
        )


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o755, stat.S_IFREG | 0o777])
def test_unsafe_tool_refused_before_execution(signed, monkeypatch, mode):
    build, expected, now, _, _ = signed
    payload, signature, policy = build()
    metadata = list(os.lstat("/usr/bin/ssh-keygen"))
    metadata[0] = mode
    monkeypatch.setattr(adapter.os, "lstat", lambda path: os.stat_result(metadata))
    with pytest.raises(OfflineResultRefused, match="^offline-tool-unsafe$"):
        adapter.require_authenticated_result(
            payload, signature, expected=expected, policy=policy, now=now
        )


def test_unenrolled_policy_never_executes_tool(signed, monkeypatch):
    root = pathlib.Path(__file__).resolve().parents[2] / "docs" / "inventories"
    policy = parse_policy(
        (root / "lane3-offline-verifier-policy.json").read_bytes(),
        (root / "lane3-offline-verifier.allowed_signers").read_bytes(),
        (root / "lane3-offline-verifier.revoked_keys").read_bytes(),
    )
    assert policy.state == "unenrolled"

    def forbidden(*args, **kwargs):
        pytest.fail("unenrolled policy must not invoke a tool")

    monkeypatch.setattr(adapter.subprocess, "run", forbidden)
    with pytest.raises(OfflineResultUnavailable, match="^offline-policy-unenrolled$"):
        adapter.require_authenticated_result(
            b"PRIVATE", b"PRIVATE", expected=signed[1], policy=policy, now=signed[2]
        )


def test_fixed_subprocess_boundary_and_cleanup(signed, monkeypatch):
    build, expected, now, _, _ = signed
    payload, signature, policy = build()
    real_run = subprocess.run
    paths = []

    def inspect(argv, **kwargs):
        assert argv[:3] == ["/usr/bin/ssh-keygen", "-Y", "verify"]
        assert kwargs["input"] == payload
        assert kwargs["shell"] is False and kwargs["timeout"] == 10
        assert kwargs["env"] == {"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"}
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        for flag in ("-f", "-s", "-r"):
            path = pathlib.Path(argv[argv.index(flag) + 1])
            assert path.is_file() and not path.is_symlink()
            assert os.stat(path).st_mode & 0o077 == 0
            paths.append(path)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(adapter.subprocess, "run", inspect)
    adapter.require_authenticated_result(
        payload, signature, expected=expected, policy=policy, now=now
    )
    assert paths and all(not path.exists() for path in paths)
