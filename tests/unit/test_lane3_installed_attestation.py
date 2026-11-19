"""S2: `scripts/lane3_installed_attestation.py` (render / assemble / verify).

The tool holds no key. The tests play the ceremony: they sign the rendered
`signed_bytes` with a synthetic key standing in for the transit mount.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import lane3_installed_attestation as ia
import pytest
from dotmac_deployment_foundation.trusted_host_source import (
    AttestationEnvelopeV2,
    AttestationTrustRootV2,
)

from tests.unit import lane3_attestation_support as support

PUBLIC = support.b64url(support.raw_public(support.INCARNATION_KEY))
SCRIPT = support.SCRIPTS / "lane3_installed_attestation.py"


def _render(**overrides: Any) -> ia.Rendered:
    candidate = support.candidate_envelope()
    arguments: dict[str, Any] = {
        "candidate": candidate,
        "candidate_root": AttestationTrustRootV2(**support.candidate_root()),
        "candidate_audience": support.CANDIDATE_AUDIENCE,
        "host_id": support.HOST_ID,
        "observation_id": support.DISPATCH_ID,
        "enrolment_uuid": support.ENROLMENT_UUID,
        "key_id": support.INSTALLED_KEY_ID,
        "issuer": support.INSTALLED_ISSUER,
        "incarnation_public_key_b64url": support.b64url(
            support.raw_public(support.INCARNATION_KEY)
        ),
        "incarnation_fingerprint": support.fingerprint(support.INCARNATION_KEY),
        "t0": support.T0,
        "authorization_expires_at": support.AUTH_EXPIRES,
        "root_not_after": support.ROOT_NOT_AFTER,
    }
    arguments.update(overrides)
    return ia.render_installed(**arguments)


def _sign(rendered: ia.Rendered, key: Any = support.INCARNATION_KEY) -> str:
    return support.std_b64(key.sign(rendered.signed_bytes))


def _assemble(rendered: ia.Rendered, **overrides: Any) -> AttestationEnvelopeV2:
    arguments: dict[str, Any] = {
        "signed_bytes": rendered.signed_bytes,
        "signature": _sign(rendered),
        "incarnation_public_key_b64url": support.b64url(
            support.raw_public(support.INCARNATION_KEY)
        ),
        "expect_signed_bytes_sha256": rendered.signed_bytes_sha256,
    }
    arguments.update(overrides)
    return ia.assemble_installed(**arguments)


def _verify(envelope: AttestationEnvelopeV2, **overrides: Any) -> dict[str, str]:
    arguments: dict[str, Any] = {
        "installed": envelope,
        "candidate": support.candidate_envelope(),
        "candidate_root": AttestationTrustRootV2(**support.candidate_root()),
        "installed_root": AttestationTrustRootV2(**support.installed_root()),
        "candidate_audience": support.CANDIDATE_AUDIENCE,
        "now": "2026-11-19T10:30:00Z",
    }
    arguments.update(overrides)
    return ia.verify_installed(**arguments)


def _refusal(call: Any, code: str) -> None:
    with pytest.raises(ia.Refused) as refusal:
        call()
    assert refusal.value.code == code


# ── round trip ──────────────────────────────────────────────────────────────


def test_render_assemble_verify_round_trip() -> None:
    rendered = _render()
    assert (
        rendered.signed_bytes_sha256
        == hashlib.sha256(rendered.signed_bytes).hexdigest()
    )
    envelope = _assemble(rendered)
    result = _verify(envelope)
    assert result["observation_id"] == support.DISPATCH_ID
    assert result["host_id"] == support.HOST_ID
    assert result["installed_fingerprint"] == support.fingerprint(
        support.INCARNATION_KEY
    )
    assert result["installed_attestation_digest"].startswith("sha256:")


def test_the_rendered_bytes_are_foundations_own_canonical_form() -> None:
    rendered = _render()
    document = json.loads(rendered.signed_bytes)
    assert "signature" not in document
    assert document["purpose"] == "dotmac.foundation.installed-host.v2"
    assert document["custody_domain"] == "host_attester"
    assert document["audience"] == support.HOST_ID
    assert document["observation_id"] == support.DISPATCH_ID
    assert document["trust_root_version"] == support.ENROLMENT_UUID
    assert document["issued_at"] == support.T0
    envelope = _assemble(rendered)
    assert envelope.signed_bytes() == rendered.signed_bytes


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "2026-11-19T12:00:00Z"),  # t0 + 2 h is the smallest
        ({"authorization_expires_at": "2026-11-19T10:45:00Z"}, "2026-11-19T10:45:00Z"),
        ({"root_not_after": "2026-11-19T11:15:00Z"}, "2026-11-19T11:15:00Z"),
    ],
)
def test_expiry_is_the_minimum_of_the_three_limits(
    overrides: dict[str, str], expected: str
) -> None:
    assert _render(**overrides).expires_at == expected


def test_the_installed_subject_is_derived_from_the_verified_candidate() -> None:
    rendered = _render()
    subject = json.loads(rendered.signed_bytes)["subject"]
    expected = support.installed_subject_for(support.candidate_envelope())
    assert subject == expected.canonical_document()


# ── render refusals ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"t0": "2026-11-19T10:00:00"}, "t0-invalid"),
        ({"t0": "2026-11-19T10:00:00.5Z"}, "t0-invalid"),
        ({"authorization_expires_at": "tomorrow"}, "authorization-expiry-invalid"),
        ({"root_not_after": "2026-12-31"}, "root-not-after-invalid"),
        ({"authorization_expires_at": support.T0}, "installed-expiry-not-after-issue"),
        (
            {"root_not_after": "2026-11-19T09:00:00Z"},
            "installed-expiry-not-after-issue",
        ),
        ({"host_id": ""}, "host-id-invalid"),
        ({"observation_id": "has space"}, "observation-id-invalid"),
        ({"key_id": ""}, "key-id-invalid"),
        ({"issuer": ""}, "issuer-invalid"),
        ({"candidate_audience": ""}, "candidate-audience-invalid"),
        ({"enrolment_uuid": "NOT-A-UUID"}, "enrolment-uuid-invalid"),
        (
            {"enrolment_uuid": support.ENROLMENT_UUID.upper()},
            "enrolment-uuid-invalid",
        ),
        (
            {"incarnation_public_key_b64url": support.b64url(b"x" * 32) + "="},
            "incarnation-public-key-invalid",
        ),
        (
            {"incarnation_fingerprint": "sha256:" + "0" * 64},
            "incarnation-fingerprint-does-not-identify-the-key",
        ),
    ],
)
def test_render_refusals(overrides: dict[str, Any], code: str) -> None:
    _refusal(lambda: _render(**overrides), code)


def test_a_candidate_signed_by_another_key_is_refused_with_foundations_code() -> None:
    forged = support.candidate_envelope(key=support.OTHER_KEY)
    _refusal(
        lambda: _render(candidate=forged),
        "trusted-host-source-key-not-trusted",
    )


def test_a_candidate_whose_signature_is_tampered_is_refused() -> None:
    document = support.candidate_envelope().canonical_document()
    document["subject"] = {**document["subject"], "version": "9.9.9"}
    tampered = AttestationEnvelopeV2.from_mapping(document)
    _refusal(
        lambda: _render(candidate=tampered), "trusted-host-source-signature-invalid"
    )


def test_a_revoked_candidate_root_is_refused() -> None:
    root = AttestationTrustRootV2(**support.candidate_root(revoked=True))
    _refusal(lambda: _render(candidate_root=root), "trusted-host-source-root-revoked")


def test_the_wrong_candidate_audience_is_refused() -> None:
    _refusal(
        lambda: _render(candidate_audience="someone-else"),
        "trusted-host-source-audience-mismatch",
    )


def test_a_candidate_outside_its_validity_is_refused() -> None:
    _refusal(
        lambda: _render(
            t0="2027-01-05T00:00:00Z",
            authorization_expires_at="2027-01-06T00:00:00Z",
            root_not_after="2027-02-01T00:00:00Z",
        ),
        "trusted-host-source-root-not-valid",
    )


def test_an_incarnation_key_equal_to_the_candidate_root_is_refused() -> None:
    _refusal(
        lambda: _render(
            incarnation_public_key_b64url=support.b64url(
                support.raw_public(support.CANDIDATE_KEY)
            ),
            incarnation_fingerprint=support.fingerprint(support.CANDIDATE_KEY),
        ),
        "trusted-host-source-trust-roots-not-distinct",
    )


def test_a_candidate_for_another_package_is_refused() -> None:
    other = support.candidate_envelope(package="some-other-package")
    _refusal(lambda: _render(candidate=other), "candidate-package-unexpected")


# ── assemble refusals and tamper cases ──────────────────────────────────────


def test_assemble_refuses_a_digest_that_is_not_the_rendered_one() -> None:
    rendered = _render()
    _refusal(
        lambda: _assemble(rendered, expect_signed_bytes_sha256="0" * 64),
        "signed-bytes-digest-mismatch",
    )
    _refusal(
        lambda: _assemble(rendered, expect_signed_bytes_sha256="ABC"),
        "signed-bytes-digest-invalid",
    )


def test_assemble_refuses_a_signature_from_another_key() -> None:
    rendered = _render()
    _refusal(
        lambda: _assemble(rendered, signature=_sign(rendered, support.OTHER_KEY)),
        "installed-signature-invalid",
    )


def test_assemble_refuses_a_signature_over_other_bytes() -> None:
    rendered = _render()
    other = _render(observation_id="another-dispatch")
    _refusal(
        lambda: _assemble(rendered, signature=_sign(other)),
        "installed-signature-invalid",
    )


def test_assemble_refuses_tampered_bytes_even_with_a_matching_digest() -> None:
    rendered = _render()
    document = json.loads(rendered.signed_bytes)
    document["audience"] = "host-b"
    forged = ia.Rendered(
        signed_bytes=json.dumps(
            document, sort_keys=True, separators=(",", ":")
        ).encode(),
        signed_bytes_sha256="",
        issued_at="",
        expires_at="",
        candidate_envelope_digest="",
    )
    digest = hashlib.sha256(forged.signed_bytes).hexdigest()
    _refusal(
        lambda: _assemble(
            rendered,
            signed_bytes=forged.signed_bytes,
            expect_signed_bytes_sha256=digest,
        ),
        "installed-signature-invalid",
    )


def test_assemble_refuses_non_canonical_bytes() -> None:
    rendered = _render()
    pretty = json.dumps(json.loads(rendered.signed_bytes), indent=2).encode()
    key = support.INCARNATION_KEY
    _refusal(
        lambda: _assemble(
            rendered,
            signed_bytes=pretty,
            signature=support.std_b64(key.sign(pretty)),
            expect_signed_bytes_sha256=hashlib.sha256(pretty).hexdigest(),
        ),
        "signed-bytes-not-canonical",
    )


def test_assemble_refuses_malformed_signed_bytes() -> None:
    rendered = _render()
    for blob in (b"not json", b"[]", json.dumps({"a": 1}).encode()):
        _refusal(
            lambda blob=blob: _assemble(
                rendered,
                signed_bytes=blob,
                expect_signed_bytes_sha256=hashlib.sha256(blob).hexdigest(),
            ),
            "signed-bytes-malformed",
        )


def test_assemble_refuses_bytes_that_are_a_candidate_attestation() -> None:
    candidate = support.candidate_envelope()
    blob = candidate.signed_bytes()
    _refusal(
        lambda: ia.assemble_installed(
            signed_bytes=blob,
            signature=support.std_b64(support.CANDIDATE_KEY.sign(blob)),
            incarnation_public_key_b64url=support.b64url(
                support.raw_public(support.CANDIDATE_KEY)
            ),
            expect_signed_bytes_sha256=hashlib.sha256(blob).hexdigest(),
        ),
        "signed-bytes-not-an-installed-attestation",
    )


def test_assemble_refuses_an_enrolled_key_that_is_not_the_signing_key() -> None:
    rendered = _render()
    _refusal(
        lambda: _assemble(
            rendered,
            incarnation_public_key_b64url=support.b64url(
                support.raw_public(support.OTHER_KEY)
            ),
        ),
        "signed-bytes-key-differs-from-enrolled-key",
    )
    _refusal(
        lambda: _assemble(rendered, incarnation_public_key_b64url="short"),
        "incarnation-public-key-invalid",
    )


def test_assemble_accepts_the_transit_prefix_and_only_key_version_one() -> None:
    rendered = _render()
    bare = _sign(rendered)
    assert _assemble(rendered, signature="vault:v1:" + bare).signature == bare
    _refusal(
        lambda: _assemble(rendered, signature="vault:v2:" + bare),
        "installed-signature-key-version-unexpected",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s[:-4],  # truncated
        lambda s: s + "A",  # not a multiple of four
        lambda s: s.replace("=", ""),  # unpadded: 64 bytes need one '='
        lambda s: " " + s,
        lambda s: "!" + s[1:],
        lambda s: "",
    ],
)
def test_assemble_refuses_malformed_signatures(mutate: Any) -> None:
    rendered = _render()
    _refusal(
        lambda: _assemble(rendered, signature=mutate(_sign(rendered))),
        "installed-signature-malformed",
    )


# ── verify tamper cases ─────────────────────────────────────────────────────


def _installed() -> AttestationEnvelopeV2:
    return _assemble(_render())


def test_verify_refuses_an_installed_envelope_changed_after_signing() -> None:
    document = _installed().canonical_document()
    document["observation_id"] = "another-dispatch"
    tampered = AttestationEnvelopeV2.from_mapping(document)
    _refusal(lambda: _verify(tampered), "trusted-host-source-signature-invalid")


def test_verify_refuses_an_unenrolled_key() -> None:
    root = AttestationTrustRootV2(
        **support.root_document(
            support.OTHER_KEY,
            purpose="dotmac.foundation.installed-host.v2",
            custody_domain="host_attester",
            issuer=support.INSTALLED_ISSUER,
            key_id=support.INSTALLED_KEY_ID,
            trust_root_version=support.ENROLMENT_UUID,
        )
    )
    _refusal(
        lambda: _verify(_installed(), installed_root=root),
        "trusted-host-source-key-not-trusted",
    )


def test_verify_refuses_a_revoked_installed_root() -> None:
    root = AttestationTrustRootV2(**support.installed_root(revoked=True))
    _refusal(
        lambda: _verify(_installed(), installed_root=root),
        "trusted-host-source-root-revoked",
    )


def test_verify_refuses_a_root_binding_that_differs() -> None:
    root = AttestationTrustRootV2(**support.installed_root(key_id="another-key-id"))
    _refusal(
        lambda: _verify(_installed(), installed_root=root),
        "trusted-host-source-root-binding-mismatch",
    )


def test_verify_refuses_stale_and_future_times() -> None:
    _refusal(
        lambda: _verify(_installed(), now="2026-11-19T12:00:00Z"),
        "trusted-host-source-stale",
    )
    _refusal(
        lambda: _verify(_installed(), now="2026-11-19T09:59:59Z"),
        "trusted-host-source-future",
    )
    _refusal(lambda: _verify(_installed(), now="soon"), "now-invalid")


def test_verify_refuses_a_candidate_it_does_not_bind() -> None:
    other = support.candidate_envelope(version="0.4.0a3")
    _refusal(
        lambda: _verify(_installed(), candidate=other),
        "trusted-host-source-subject-mismatch",
    )


def test_verify_refuses_a_swapped_candidate_audience() -> None:
    _refusal(
        lambda: _verify(_installed(), candidate_audience="someone-else"),
        "trusted-host-source-audience-mismatch",
    )


# ── the tool holds no key ───────────────────────────────────────────────────


def test_the_script_imports_and_calls_no_signing_primitive() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name:
                calls.add(name)
    assert not any("cryptography" in name for name in imported)
    assert (
        not {"Ed25519PrivateKey", "load_pem_private_key", "from_private_bytes"}
        & imported
    )
    assert "sign" not in calls
    assert "load_pem_private_key" not in calls


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_round_trip_and_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = support.write_json(
        tmp_path / "candidate.json", support.candidate_envelope().canonical_document()
    )
    candidate_root = support.write_json(
        tmp_path / "croot.json", support.candidate_root()
    )
    installed_root = support.write_json(
        tmp_path / "iroot.json", support.installed_root()
    )
    signed = tmp_path / "signed-bytes"
    render_args = [
        "render",
        "--candidate", str(candidate),
        "--candidate-root", str(candidate_root),
        "--candidate-audience", support.CANDIDATE_AUDIENCE,
        "--host-id", support.HOST_ID,
        "--observation-id", support.DISPATCH_ID,
        "--enrolment-uuid", support.ENROLMENT_UUID,
        "--key-id", support.INSTALLED_KEY_ID,
        "--issuer", support.INSTALLED_ISSUER,
        "--incarnation-public-key", PUBLIC,
        "--incarnation-fingerprint", support.fingerprint(support.INCARNATION_KEY),
        "--t0", support.T0,
        "--authorization-expires-at", support.AUTH_EXPIRES,
        "--root-not-after", support.ROOT_NOT_AFTER,
        "--out-signed-bytes", str(signed),
    ]  # fmt: skip
    assert ia.main(render_args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert (
        printed["signed_bytes_sha256"]
        == hashlib.sha256(signed.read_bytes()).hexdigest()
    )
    assert oct(signed.stat().st_mode & 0o777) == "0o600"

    # create-only: a second render onto the same path refuses
    assert ia.main(render_args) == 2
    assert "refused: output-not-creatable" in capsys.readouterr().err

    out = tmp_path / "installed.json"
    signature = support.std_b64(support.INCARNATION_KEY.sign(signed.read_bytes()))
    assert (
        ia.main(
            [
                "assemble",
                "--signed-bytes", str(signed),
                "--signature", "vault:v1:" + signature,
                "--incarnation-public-key", PUBLIC,
                "--expect-signed-bytes-sha256", printed["signed_bytes_sha256"],
                "--out", str(out),
            ]
        )
        == 0
    )  # fmt: skip
    assemble_out = json.loads(capsys.readouterr().out)
    assert (
        ia.main(
            [
                "verify",
                "--installed", str(out),
                "--candidate", str(candidate),
                "--candidate-root", str(candidate_root),
                "--installed-root", str(installed_root),
                "--candidate-audience", support.CANDIDATE_AUDIENCE,
                "--now", "2026-11-19T10:30:00Z",
            ]
        )
        == 0
    )  # fmt: skip
    verified = json.loads(capsys.readouterr().out)
    assert (
        verified["installed_attestation_digest"]
        == assemble_out["installed_attestation_digest"]
    )

    # a bad signature never becomes a file
    refused_out = tmp_path / "never.json"
    assert (
        ia.main(
            [
                "assemble",
                "--signed-bytes", str(signed),
                "--signature", support.std_b64(b"\x00" * 64),
                "--incarnation-public-key", PUBLIC,
                "--expect-signed-bytes-sha256", printed["signed_bytes_sha256"],
                "--out", str(refused_out),
            ]
        )
        == 2
    )  # fmt: skip
    assert not refused_out.exists()
