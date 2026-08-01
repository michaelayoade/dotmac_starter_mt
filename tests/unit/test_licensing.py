"""Consumer tests for WS8 signed-licence verification (`dotmac_kernel.licensing`).

Canary-first per the WS8 design brief
(docs/superpowers/reviews/2026-08-01-ws8-signed-licence-design.md): these pin
the public contract — DSSE-style bytes-are-truth envelope, Ed25519-only
keyring with active/retired/revoked rotation states, fail-closed offline
verification with an injected clock (explicit valid/in_grace states), optional
deployment binding, monotonic-version replay/rollback protection, signed
monotonic revocation lists, and the version/digest acknowledgement type.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_kernel.licensing import (
    AppliedLicence,
    BadSignatureError,
    DeploymentMismatchError,
    DuplicateKeyError,
    KeyStatus,
    LicenceAcknowledgement,
    LicenceConflictError,
    LicenceError,
    LicenceExpiredError,
    LicenceKeyRing,
    LicenceNotYetValidError,
    MalformedLicenceError,
    RevokedKeyError,
    RevokedLicenceError,
    StaleLicenceError,
    StaleRevocationListError,
    UnknownKeyError,
    VerificationUnavailableError,
    payload_digest,
    verify_licence,
    verify_revocation_list,
)
from dotmac_kernel.testing.licensing import FakeLicenceSigner

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def signer() -> FakeLicenceSigner:
    return FakeLicenceSigner(key_id="test-key-1")


def _verify(envelope, signer, **kwargs):
    kwargs.setdefault("keyring", signer.keyring())
    kwargs.setdefault("now", NOW)
    return verify_licence(envelope, **kwargs)


# ── 1. Round-trip ───────────────────────────────────────────────────────────


def test_round_trip_verifies_and_parses(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(
        licence_id="lic-1",
        licence_version=1,
        product="dotmac-sub",
        edition="standard",
        capabilities=[{"code": "inventory.use", "limits": {"seats": 25}}],
    )
    verified = _verify(envelope, signer)
    doc = verified.document
    assert doc.licence_id == "lic-1"
    assert doc.licence_version == 1
    assert doc.product == "dotmac-sub"
    assert doc.edition == "standard"
    assert doc.subject.customer == "test-customer"
    assert doc.capabilities[0].code == "inventory.use"
    assert doc.capabilities[0].limits == {"seats": 25}
    assert verified.validity == "valid"
    assert verified.reapplied is False


def test_digest_is_sha256_of_payload_bytes(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope()
    payload = base64.urlsafe_b64decode(
        envelope["payload_b64"] + "=" * (-len(envelope["payload_b64"]) % 4)
    )
    expected = "sha256:" + hashlib.sha256(payload).hexdigest()
    assert payload_digest(payload) == expected
    assert _verify(envelope, signer).digest == expected


# ── 2. Tamper ───────────────────────────────────────────────────────────────


def test_tampered_payload_fails_signature(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(capabilities=[{"code": "basic.use"}])
    payload = base64.urlsafe_b64decode(
        envelope["payload_b64"] + "=" * (-len(envelope["payload_b64"]) % 4)
    )
    doc = json.loads(payload)
    doc["capabilities"] = [{"code": "everything.use"}]
    forged = json.dumps(doc).encode()
    envelope["payload_b64"] = base64.urlsafe_b64encode(forged).rstrip(b"=").decode()
    with pytest.raises(BadSignatureError):
        _verify(envelope, signer)


def test_byte_identical_payloads_share_digest(signer: FakeLicenceSigner) -> None:
    other = FakeLicenceSigner(key_id="other-key")
    payload = json.dumps(signer.licence_payload()).encode()
    # Different signers, same payload bytes — the digest identity is the
    # payload, never the signature set.
    d1 = _verify(signer.sign(payload), signer).digest
    d2 = verify_licence(other.sign(payload), keyring=other.keyring(), now=NOW).digest
    assert d1 == d2


# ── 3. Keyring ──────────────────────────────────────────────────────────────


def test_unknown_key_fails_closed(signer: FakeLicenceSigner) -> None:
    stranger = FakeLicenceSigner(key_id="stranger")
    envelope = stranger.envelope()
    with pytest.raises(UnknownKeyError):
        _verify(envelope, signer)  # signer's ring does not know "stranger"


def test_retired_key_still_verifies(signer: FakeLicenceSigner) -> None:
    ring = LicenceKeyRing([signer.key(status=KeyStatus.RETIRED)])
    verified = verify_licence(signer.envelope(), keyring=ring, now=NOW)
    assert verified.validity == "valid"


def test_revoked_key_never_verifies(signer: FakeLicenceSigner) -> None:
    ring = LicenceKeyRing([signer.key(status=KeyStatus.REVOKED)])
    with pytest.raises(RevokedKeyError):
        verify_licence(signer.envelope(), keyring=ring, now=NOW)


def test_rotation_double_signature_verifies_with_either_key(
    signer: FakeLicenceSigner,
) -> None:
    new_signer = FakeLicenceSigner(key_id="test-key-2")
    envelope = new_signer.add_signature(signer.envelope())
    assert len(envelope["signatures"]) == 2
    # Old ring (knows only the old key) and new ring (knows only the new key)
    # both verify the double-signed envelope.
    verify_licence(envelope, keyring=signer.keyring(), now=NOW)
    verify_licence(envelope, keyring=new_signer.keyring(), now=NOW)


def test_duplicate_key_id_fails_ring_construction(signer: FakeLicenceSigner) -> None:
    with pytest.raises(DuplicateKeyError):
        LicenceKeyRing([signer.key(), signer.key(status=KeyStatus.RETIRED)])


# ── 4. Validity clock ───────────────────────────────────────────────────────


def test_not_yet_valid(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(not_before="2026-09-01T00:00:00+00:00")
    with pytest.raises(LicenceNotYetValidError):
        _verify(envelope, signer)


def test_valid_within_window(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(
        not_before="2026-07-01T00:00:00+00:00",
        expires_at="2026-09-01T00:00:00+00:00",
    )
    assert _verify(envelope, signer).validity == "valid"


def test_grace_window_is_explicit_and_bounded(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(expires_at="2026-07-25T00:00:00+00:00", grace_days=14)
    # NOW (2026-08-01) is past expiry but inside the 14-day grace window.
    assert _verify(envelope, signer).validity == "in_grace"
    # Exactly at the grace boundary — still in grace (inclusive).
    boundary = datetime(2026, 8, 8, 0, 0, 0, tzinfo=UTC)
    assert _verify(envelope, signer, now=boundary).validity == "in_grace"
    with pytest.raises(LicenceExpiredError):
        _verify(envelope, signer, now=boundary + timedelta(seconds=1))


def test_expired_without_grace(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(expires_at="2026-07-25T00:00:00+00:00")
    with pytest.raises(LicenceExpiredError):
        _verify(envelope, signer)


def test_perpetual_licence_never_expires(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(expires_at=None)
    far_future = datetime(2099, 1, 1, tzinfo=UTC)
    assert _verify(envelope, signer, now=far_future).validity == "valid"


def test_naive_now_is_rejected(signer: FakeLicenceSigner) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _verify(signer.envelope(), signer, now=datetime(2026, 8, 1, 12, 0, 0))


def test_naive_document_timestamp_is_malformed(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(expires_at="2026-09-01T00:00:00")
    with pytest.raises(MalformedLicenceError):
        _verify(envelope, signer)


def test_validity_window_inversion_is_malformed(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(
        not_before="2026-09-01T00:00:00+00:00",
        expires_at="2026-08-01T00:00:00+00:00",
    )
    with pytest.raises(MalformedLicenceError):
        _verify(envelope, signer)


# ── 5. Deployment binding ───────────────────────────────────────────────────


def test_bound_licence_requires_matching_deployment(
    signer: FakeLicenceSigner,
) -> None:
    envelope = signer.envelope(subject={"customer": "acme", "deployment_id": "dep-a"})
    verified = _verify(envelope, signer, expected_deployment_id="dep-a")
    assert verified.document.subject.deployment_id == "dep-a"
    with pytest.raises(DeploymentMismatchError):
        _verify(envelope, signer, expected_deployment_id="dep-b")
    # A bound licence with no expected id to check against fails closed too.
    with pytest.raises(DeploymentMismatchError):
        _verify(envelope, signer)


def test_unbound_licence_is_portable_unless_binding_required(
    signer: FakeLicenceSigner,
) -> None:
    envelope = signer.envelope(subject={"customer": "acme"})
    _verify(envelope, signer, expected_deployment_id="dep-a")  # portable — ok
    with pytest.raises(DeploymentMismatchError):
        _verify(envelope, signer, expected_deployment_id="dep-a", require_binding=True)


# ── 6. Replay / rollback ────────────────────────────────────────────────────


def test_lower_version_is_stale(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(licence_id="lic-1", licence_version=2)
    applied = AppliedLicence(
        licence_id="lic-1", licence_version=3, digest="sha256:aaaa"
    )
    with pytest.raises(StaleLicenceError):
        _verify(envelope, signer, applied=applied)


def test_same_version_same_digest_is_idempotent_reapply(
    signer: FakeLicenceSigner,
) -> None:
    envelope = signer.envelope(licence_id="lic-1", licence_version=3)
    digest = _verify(envelope, signer).digest
    applied = AppliedLicence(licence_id="lic-1", licence_version=3, digest=digest)
    verified = _verify(envelope, signer, applied=applied)
    assert verified.reapplied is True


def test_same_version_different_digest_is_a_conflict(
    signer: FakeLicenceSigner,
) -> None:
    envelope = signer.envelope(licence_id="lic-1", licence_version=3)
    applied = AppliedLicence(
        licence_id="lic-1", licence_version=3, digest="sha256:bbbb"
    )
    with pytest.raises(LicenceConflictError):
        _verify(envelope, signer, applied=applied)


def test_higher_version_supersedes_applied(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(licence_id="lic-1", licence_version=4)
    applied = AppliedLicence(
        licence_id="lic-1", licence_version=3, digest="sha256:aaaa"
    )
    verified = _verify(envelope, signer, applied=applied)
    assert verified.reapplied is False


def test_different_lineage_is_not_compared(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(licence_id="lic-2", licence_version=1)
    applied = AppliedLicence(
        licence_id="lic-1", licence_version=9, digest="sha256:aaaa"
    )
    _verify(envelope, signer, applied=applied)  # different licence_id — no guard


# ── 7. Revocation ───────────────────────────────────────────────────────────


def test_revoked_licence_id_fails(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope(licence_id="lic-1")
    with pytest.raises(RevokedLicenceError):
        _verify(envelope, signer, revoked_licence_ids=frozenset({"lic-1"}))


def test_revocation_list_round_trip_and_monotonicity(
    signer: FakeLicenceSigner,
) -> None:
    envelope = signer.sign_revocation_list(
        list_version=2, revoked_licence_ids=["lic-1", "lic-9"]
    )
    rl = verify_revocation_list(envelope, keyring=signer.keyring())
    assert rl.list_version == 2
    assert rl.revoked_licence_ids == frozenset({"lic-1", "lic-9"})
    # Idempotent re-import of the same version is allowed…
    verify_revocation_list(envelope, keyring=signer.keyring(), applied_list_version=2)
    # …but a stale list cannot un-revoke.
    with pytest.raises(StaleRevocationListError):
        verify_revocation_list(
            envelope, keyring=signer.keyring(), applied_list_version=3
        )


def test_revocation_list_is_signature_checked(signer: FakeLicenceSigner) -> None:
    stranger = FakeLicenceSigner(key_id="stranger")
    envelope = stranger.sign_revocation_list(
        list_version=1, revoked_licence_ids=["lic-1"]
    )
    with pytest.raises(UnknownKeyError):
        verify_revocation_list(envelope, keyring=signer.keyring())


# ── 8. Fail-closed order / malformed input ──────────────────────────────────


def test_signature_is_checked_before_payload_parse(
    signer: FakeLicenceSigner,
) -> None:
    # An EXPIRED document with a broken signature must report the signature
    # failure — a tampered document reveals nothing about its contents.
    envelope = signer.envelope(expires_at="2020-01-01T00:00:00+00:00")
    envelope["signatures"][0]["signature_b64"] = (
        envelope["signatures"][0]["signature_b64"][:-4] + "AAAA"
    )
    with pytest.raises(BadSignatureError):
        _verify(envelope, signer)


def test_unknown_envelope_schema_fails_closed(signer: FakeLicenceSigner) -> None:
    envelope = signer.envelope()
    envelope["schema"] = "dotmac-licence-envelope/999"
    with pytest.raises(MalformedLicenceError):
        _verify(envelope, signer)


def test_unknown_signature_algorithm_fails_closed(
    signer: FakeLicenceSigner,
) -> None:
    envelope = signer.envelope()
    envelope["signatures"][0]["algorithm"] = "rsa-pkcs1"
    with pytest.raises(MalformedLicenceError):
        _verify(envelope, signer)


def test_unknown_payload_schema_fails_closed(signer: FakeLicenceSigner) -> None:
    envelope = signer.sign(
        json.dumps(signer.licence_payload(schema="dotmac-licence/999")).encode()
    )
    with pytest.raises(MalformedLicenceError):
        _verify(envelope, signer)


@pytest.mark.parametrize(
    "breakage",
    [
        {"licence_id": ""},
        {"licence_id": None},
        {"licence_version": 0},
        {"licence_version": -1},
        {"licence_version": "3"},
        {"licence_version": True},
        {"issuer": ""},
        {"product": None},
        {"subject": {}},
        {"subject": {"customer": ""}},
        {"subject": "acme"},
        {"capabilities": [{"limits": {}}]},
        {"capabilities": [{"code": ""}]},
        {"capabilities": "inventory.use"},
        {"issued_at": None},
        {"issued_at": "not-a-date"},
        {"grace_days": -1},
        {"grace_days": "14"},
        {"constraints": []},
    ],
)
def test_malformed_payload_fields_fail_closed(
    signer: FakeLicenceSigner, breakage: dict[str, object]
) -> None:
    envelope = signer.sign(json.dumps(signer.licence_payload(**breakage)).encode())
    with pytest.raises(MalformedLicenceError):
        _verify(envelope, signer)


@pytest.mark.parametrize(
    "mangle",
    [
        lambda e: e.pop("payload_b64"),
        lambda e: e.pop("signatures"),
        lambda e: e.update(signatures=[]),
        lambda e: e.update(payload_b64="!!not-base64!!"),
        lambda e: e.update(signatures=[{"key_id": "test-key-1"}]),
    ],
)
def test_malformed_envelope_fails_closed(signer: FakeLicenceSigner, mangle) -> None:
    envelope = signer.envelope()
    mangle(envelope)
    with pytest.raises(MalformedLicenceError):
        _verify(envelope, signer)


def test_non_json_payload_fails_after_signature(signer: FakeLicenceSigner) -> None:
    envelope = signer.sign(b"this is not json")
    with pytest.raises(MalformedLicenceError):
        _verify(envelope, signer)


# ── 9. Offline / dependency posture ─────────────────────────────────────────

_LICENSING_SRC = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-kernel/src/dotmac_kernel/licensing.py"
)


def test_module_has_no_top_level_cryptography_import() -> None:
    """`import dotmac_kernel.licensing` must work WITHOUT the `licensing`
    extra — cryptography is a lazy, function-local import (fail-closed at
    verification time via VerificationUnavailableError)."""
    tree = ast.parse(_LICENSING_SRC.read_text())
    for node in tree.body:  # top level only — function bodies may lazy-import
        assert not (
            isinstance(node, ast.Import)
            and any(a.name.startswith("cryptography") for a in node.names)
        ), "top-level `import cryptography` breaks the optional-extra contract"
        assert not (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("cryptography")
        ), "top-level `from cryptography …` breaks the optional-extra contract"


def test_module_never_reads_the_wall_clock_or_network() -> None:
    """Verification is deterministic and offline: `now` is always injected —
    no datetime.now/utcnow, and no network/DB imports anywhere in the module."""
    src = _LICENSING_SRC.read_text()
    for forbidden in ("datetime.now(", "utcnow(", "socket", "httpx", "requests"):
        assert forbidden not in src
    tree = ast.parse(src)
    top_level_imports = {
        name.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for name in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "sqlalchemy" not in top_level_imports  # no storage in this slice


def test_verification_unavailable_without_cryptography(
    signer: FakeLicenceSigner, monkeypatch: pytest.MonkeyPatch
) -> None:
    envelope = signer.envelope()
    # Poison the import machinery the way a missing extra would present.
    for mod in [m for m in sys.modules if m.startswith("cryptography")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setitem(sys.modules, "cryptography", None)
    with pytest.raises(VerificationUnavailableError, match="licensing"):
        _verify(envelope, signer)


# ── Acknowledgement type ────────────────────────────────────────────────────


def test_acknowledgement_carries_version_and_digest() -> None:
    ack = LicenceAcknowledgement(
        licence_id="lic-1",
        licence_version=3,
        digest="sha256:abcd",
        status="applied",
        deployment_id="dep-a",
    )
    assert ack.status == "applied"
    assert ack.reason is None
    rejected = LicenceAcknowledgement(
        licence_id="lic-1",
        licence_version=4,
        digest="sha256:ef01",
        status="rejected",
        reason="LicenceExpiredError",
    )
    assert rejected.reason == "LicenceExpiredError"
    with pytest.raises(ValueError, match="status"):
        LicenceAcknowledgement(
            licence_id="lic-1",
            licence_version=3,
            digest="sha256:abcd",
            status="maybe",
        )


def test_all_errors_share_the_licence_error_base() -> None:
    for err in (
        MalformedLicenceError,
        UnknownKeyError,
        RevokedKeyError,
        BadSignatureError,
        RevokedLicenceError,
        DeploymentMismatchError,
        LicenceNotYetValidError,
        LicenceExpiredError,
        StaleLicenceError,
        StaleRevocationListError,
        LicenceConflictError,
        VerificationUnavailableError,
        DuplicateKeyError,
    ):
        assert issubclass(err, LicenceError)
