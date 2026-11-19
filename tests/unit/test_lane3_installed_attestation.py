"""S2: `scripts/lane3_installed_attestation.py` (render / assemble / verify).

The tool holds no key. The tests play the ceremony: they sign the rendered
`signed_bytes` with a synthetic key standing in for the transit mount.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import lane3_e10_receipt as e10
import lane3_host_source_admission as admission
import lane3_installed_attestation as ia
import pytest
from dotmac_deployment_foundation.trusted_host_source import (
    AttestationEnvelopeV2,
    AttestationTrustRootV2,
)

from tests.unit import lane3_attestation_support as support

SCRIPT = support.SCRIPTS / "lane3_installed_attestation.py"
PUBLIC = support.b64url(support.raw_public(support.INCARNATION_KEY))


def _files(tmp_path: Path, **documents: Any) -> dict[str, Path]:
    """Write each document under its role name; defaults are the valid inputs."""
    defaults: dict[str, Any] = {
        "receipt": support.receipt_document(),
        "dispatch": support.dispatch_document(),
        "installed_root": support.installed_root(),
        "candidate_root": support.candidate_root(),
    }
    defaults.update(documents)
    return {
        name: support.write_json(tmp_path / f"{name}.json", document)
        for name, document in defaults.items()
    }


def _render(tmp_path: Path, **overrides: Any) -> ia.Rendered:
    paths = _files(
        tmp_path,
        **{
            key: overrides.pop(key)
            for key in ("receipt", "dispatch", "installed_root", "candidate_root")
            if key in overrides
        },
    )
    arguments: dict[str, Any] = {
        "candidate": support.candidate_envelope(),
        "candidate_root": ia.load_root(paths["candidate_root"], role="candidate"),
        "candidate_audience": support.CANDIDATE_AUDIENCE,
        "installed_root": ia.load_root(paths["installed_root"], role="installed"),
        "receipt": ia.load_receipt(paths["receipt"]),
        "dispatch_materials": ia.load_dispatch_materials(paths["dispatch"]),
        "t0": support.T0,
    }
    arguments.update(overrides)
    return ia.render_installed(**arguments)


def _installed_root(**overrides: Any) -> AttestationTrustRootV2:
    return AttestationTrustRootV2(**support.installed_root(**overrides))


def _sign(rendered: ia.Rendered, key: Any = support.INCARNATION_KEY) -> str:
    return support.std_b64(key.sign(rendered.signed_bytes))


def _assemble(rendered: ia.Rendered, **overrides: Any) -> AttestationEnvelopeV2:
    arguments: dict[str, Any] = {
        "signed_bytes": rendered.signed_bytes,
        "signature": _sign(rendered),
        "installed_root": _installed_root(),
        "expect_signed_bytes_sha256": rendered.signed_bytes_sha256,
    }
    arguments.update(overrides)
    return ia.assemble_installed(**arguments)


def _verify(envelope: AttestationEnvelopeV2, **overrides: Any) -> dict[str, str]:
    arguments: dict[str, Any] = {
        "installed": envelope,
        "candidate": support.candidate_envelope(),
        "candidate_root": AttestationTrustRootV2(**support.candidate_root()),
        "installed_root": _installed_root(),
        "candidate_audience": support.CANDIDATE_AUDIENCE,
        "host_id": support.HOST_ID,
        "dispatch_id": support.DISPATCH_ID,
        "now": "2026-11-19T10:30:00Z",
    }
    arguments.update(overrides)
    return ia.verify_installed(**arguments)


def _refusal(call: Any, code: str) -> None:
    with pytest.raises(ia.Refused) as refusal:
        call()
    assert refusal.value.code == code


# ── round trip ──────────────────────────────────────────────────────────────


def test_render_assemble_verify_round_trip(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert (
        rendered.signed_bytes_sha256
        == hashlib.sha256(rendered.signed_bytes).hexdigest()
    )
    result = _verify(_assemble(rendered))
    assert result["observation_id"] == support.DISPATCH_ID
    assert result["host_id"] == support.HOST_ID
    assert result["installed_fingerprint"] == support.fingerprint(
        support.INCARNATION_KEY
    )
    assert result["installed_attestation_digest"].startswith("sha256:")


def test_the_rendered_bytes_come_from_the_typed_inputs(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    document = json.loads(rendered.signed_bytes)
    root = support.installed_root()
    assert "signature" not in document
    assert document["purpose"] == "dotmac.foundation.installed-host.v2"
    assert document["custody_domain"] == "host_attester"
    assert document["audience"] == support.HOST_ID  # the receipt's host_id
    assert document["observation_id"] == support.DISPATCH_ID  # attempt_id
    assert document["trust_root_version"] == support.ENROLMENT_UUID
    assert document["issuer"] == root["issuer"]
    assert document["key_id"] == root["key_id"]
    assert document["public_key_fingerprint"] == root["public_key_fingerprint"]
    assert document["issued_at"] == support.T0
    assert _assemble(rendered).signed_bytes() == rendered.signed_bytes


def test_the_installed_subject_is_derived_from_the_verified_candidate(
    tmp_path: Path,
) -> None:
    subject = json.loads(_render(tmp_path).signed_bytes)["subject"]
    expected = support.installed_subject_for(support.candidate_envelope())
    assert subject == expected.canonical_document()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "2026-11-19T12:00:00Z"),  # t0 + 2 h is the smallest
        (
            {"dispatch": support.dispatch_document(expires_at="2026-11-19T10:45:00Z")},
            "2026-11-19T10:45:00Z",
        ),
        (  # a fractional authorization expiry is floored, never rounded up
            {
                "dispatch": support.dispatch_document(
                    expires_at="2026-11-19T10:45:00.900000Z"
                )
            },
            "2026-11-19T10:45:00Z",
        ),
        (
            {
                "installed_root": support.installed_root(
                    not_after="2026-11-19T11:15:00Z"
                )
            },
            "2026-11-19T11:15:00Z",
        ),
    ],
)
def test_expiry_is_the_minimum_of_the_three_limits(
    tmp_path: Path, overrides: dict[str, Any], expected: str
) -> None:
    assert _render(tmp_path, **overrides).expires_at == expected


# ── typed inputs: receipt ───────────────────────────────────────────────────


def _without(document: dict[str, Any], name: str) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != name}


@pytest.mark.parametrize(
    ("receipt", "code"),
    [
        ({**support.receipt_document(), "extra": "x"}, "receipt-malformed"),
        (_without(support.receipt_document(), "vm_slot"), "receipt-malformed"),
        (
            {**support.receipt_document(), "incarnation_key_destroyed_at": "x"},
            "receipt-malformed",
        ),
        (support.receipt_document(machine_id="f" * 31), "receipt-malformed"),
        (support.receipt_document(vm_alias="Bad Alias"), "receipt-malformed"),
        (support.receipt_document(vm_slot="node1/0"), "receipt-malformed"),
        (
            support.receipt_document(ssh_host_key_fingerprint="SHA256:x"),
            "receipt-malformed",
        ),
        (support.receipt_document(host_id=""), "receipt-malformed"),
        (support.receipt_document(host_id="has space"), "receipt-malformed"),
        (support.receipt_document(receipt_ref=""), "receipt-malformed"),
        (
            support.receipt_document(enrolment_uuid=support.ENROLMENT_UUID.upper()),
            "receipt-malformed",
        ),
        (
            support.receipt_document(incarnation_public_key_b64url=PUBLIC + "="),
            "incarnation-public-key-invalid",
        ),
        (
            support.receipt_document(incarnation_fingerprint="sha256:" + "0" * 64),
            "incarnation-fingerprint-does-not-identify-the-key",
        ),
    ],
)
def test_the_receipt_is_a_closed_typed_input(
    tmp_path: Path, receipt: dict[str, Any], code: str
) -> None:
    _refusal(lambda: _render(tmp_path, receipt=receipt), code)


def test_a_vm_slot_of_none_is_allowed(tmp_path: Path) -> None:
    assert _render(tmp_path, receipt=support.receipt_document(vm_slot=None))


# ── typed inputs: roots ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("root", "code"),
    [
        (_without(support.installed_root(), "revoked"), "installed-root-malformed"),
        (support.installed_root(revoked="no"), "installed-root-malformed"),
        ({**support.installed_root(), "extra": 1}, "installed-root-malformed"),
        (
            support.installed_root(custody_domain="candidate_release_signer"),
            "installed-root-role-mismatch",
        ),
        (
            support.installed_root(purpose="dotmac.foundation.candidate-artifact.v2"),
            "installed-root-role-mismatch",
        ),
        (support.installed_root(algorithm="rsa"), "installed-root-role-mismatch"),
        (support.installed_root(revoked=True), "installed-root-revoked"),
        (
            support.installed_root(not_before="2026-11-19T10:00:01Z"),
            "installed-root-not-valid-at-t0",
        ),
        (
            support.installed_root(not_after=support.T0),
            "installed-root-not-valid-at-t0",
        ),
        (
            support.installed_root(trust_root_version=support.CANDIDATE_ENROLMENT),
            "receipt-root-enrolment-mismatch",
        ),
    ],
)
def test_the_installed_root_is_a_closed_pinned_input(
    tmp_path: Path, root: dict[str, Any], code: str
) -> None:
    _refusal(lambda: _render(tmp_path, installed_root=root), code)


def test_a_root_for_another_key_than_the_receipts_is_refused(tmp_path: Path) -> None:
    other = support.root_document(
        support.OTHER_KEY,
        purpose="dotmac.foundation.installed-host.v2",
        custody_domain="host_attester",
        issuer=support.INSTALLED_ISSUER,
        key_id=support.INSTALLED_KEY_ID,
        trust_root_version=support.ENROLMENT_UUID,
    )
    _refusal(
        lambda: _render(tmp_path, installed_root=other), "receipt-root-key-mismatch"
    )


@pytest.mark.parametrize(
    ("root", "code"),
    [
        (_without(support.candidate_root(), "revoked"), "candidate-root-malformed"),
        (
            support.candidate_root(custody_domain="host_attester"),
            "candidate-root-role-mismatch",
        ),
    ],
)
def test_the_candidate_root_is_pinned_too(
    tmp_path: Path, root: dict[str, Any], code: str
) -> None:
    _refusal(lambda: _render(tmp_path, candidate_root=root), code)


# ── typed inputs: dispatch materials ────────────────────────────────────────


@pytest.mark.parametrize(
    ("dispatch", "code"),
    [
        (
            _without(support.dispatch_document(), "dispatch_envelope"),
            "dispatch-materials-malformed",
        ),
        ({**support.dispatch_document(), "extra": 1}, "dispatch-materials-malformed"),
        (support.dispatch_document(attempt_id="not-a-uuid"), "attempt-id-invalid"),
        (
            support.dispatch_document(attempt_id=support.DISPATCH_ID.upper()),
            "attempt-id-invalid",
        ),
        (
            support.dispatch_document(dispatch_id="another-dispatch"),
            "dispatch-envelope-names-another-dispatch",
        ),
        (
            support.dispatch_document(expires_at="tomorrow"),
            "authorization-expiry-invalid",
        ),
        (
            support.dispatch_document(expires_at="2026-11-19T10:00:00"),
            "authorization-expiry-invalid",
        ),
        (
            support.dispatch_document(expires_at=support.T0),
            "installed-expiry-not-after-issue",
        ),
        (
            {
                **support.dispatch_document(),
                "authorization_envelope": {"signature": "x"},
            },
            "dispatch-materials-malformed",
        ),
    ],
)
def test_the_dispatch_materials_are_a_closed_typed_input(
    tmp_path: Path, dispatch: dict[str, Any], code: str
) -> None:
    _refusal(lambda: _render(tmp_path, dispatch=dispatch), code)


def test_t0_must_be_a_whole_second_instant(tmp_path: Path) -> None:
    for bad in ("2026-11-19T10:00:00", "2026-11-19T10:00:00.5Z", "now"):
        _refusal(lambda bad=bad: _render(tmp_path, t0=bad), "t0-invalid")


# ── candidate verification at render ────────────────────────────────────────


def test_a_candidate_signed_by_another_key_is_refused_with_foundations_code(
    tmp_path: Path,
) -> None:
    forged = support.candidate_envelope(key=support.OTHER_KEY)
    _refusal(
        lambda: _render(tmp_path, candidate=forged),
        "trusted-host-source-key-not-trusted",
    )


def test_a_candidate_whose_signature_is_tampered_is_refused(tmp_path: Path) -> None:
    document = support.candidate_envelope().canonical_document()
    document["subject"] = {**document["subject"], "version": "9.9.9"}
    tampered = AttestationEnvelopeV2.from_mapping(document)
    _refusal(
        lambda: _render(tmp_path, candidate=tampered),
        "trusted-host-source-signature-invalid",
    )


def test_a_revoked_candidate_root_is_refused(tmp_path: Path) -> None:
    _refusal(
        lambda: _render(tmp_path, candidate_root=support.candidate_root(revoked=True)),
        "trusted-host-source-root-revoked",
    )


def test_the_wrong_candidate_audience_is_refused(tmp_path: Path) -> None:
    _refusal(
        lambda: _render(tmp_path, candidate_audience="someone-else"),
        "trusted-host-source-audience-mismatch",
    )
    _refusal(
        lambda: _render(tmp_path, candidate_audience=""), "candidate-audience-invalid"
    )


def test_a_candidate_outside_its_validity_is_refused(tmp_path: Path) -> None:
    _refusal(
        lambda: _render(
            tmp_path,
            t0="2027-01-05T00:00:00Z",
            dispatch=support.dispatch_document(expires_at="2027-01-06T00:00:00Z"),
            installed_root=support.installed_root(not_after="2027-02-01T00:00:00Z"),
        ),
        "trusted-host-source-root-not-valid",
    )


def test_an_incarnation_key_equal_to_the_candidate_root_is_refused(
    tmp_path: Path,
) -> None:
    same = support.root_document(
        support.CANDIDATE_KEY,
        purpose="dotmac.foundation.installed-host.v2",
        custody_domain="host_attester",
        issuer=support.INSTALLED_ISSUER,
        key_id=support.INSTALLED_KEY_ID,
        trust_root_version=support.ENROLMENT_UUID,
    )
    receipt = support.receipt_document(
        incarnation_public_key_b64url=support.b64url(
            support.raw_public(support.CANDIDATE_KEY)
        ),
        incarnation_fingerprint=support.fingerprint(support.CANDIDATE_KEY),
    )
    _refusal(
        lambda: _render(tmp_path, installed_root=same, receipt=receipt),
        "trusted-host-source-trust-roots-not-distinct",
    )


def test_a_candidate_for_another_package_is_refused(tmp_path: Path) -> None:
    other = support.candidate_envelope(package="some-other-package")
    _refusal(lambda: _render(tmp_path, candidate=other), "candidate-package-unexpected")


# ── assemble refusals and tamper cases ──────────────────────────────────────


def test_assemble_refuses_a_digest_that_is_not_the_rendered_one(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)
    _refusal(
        lambda: _assemble(rendered, expect_signed_bytes_sha256="0" * 64),
        "signed-bytes-digest-mismatch",
    )
    _refusal(
        lambda: _assemble(rendered, expect_signed_bytes_sha256="ABC"),
        "signed-bytes-digest-invalid",
    )


def test_assemble_refuses_a_signature_from_another_key(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    _refusal(
        lambda: _assemble(rendered, signature=_sign(rendered, support.OTHER_KEY)),
        "installed-signature-invalid",
    )


def test_assemble_refuses_a_signature_over_other_bytes(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    other = _render(
        tmp_path, dispatch=support.dispatch_document(attempt_id=str(uuid.uuid4()))
    )
    _refusal(
        lambda: _assemble(rendered, signature=_sign(other)),
        "installed-signature-invalid",
    )


def test_assemble_refuses_tampered_bytes_even_with_a_matching_digest(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)
    document = json.loads(rendered.signed_bytes)
    document["audience"] = "host-b"
    forged = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    _refusal(
        lambda: _assemble(
            rendered,
            signed_bytes=forged,
            expect_signed_bytes_sha256=hashlib.sha256(forged).hexdigest(),
        ),
        "installed-signature-invalid",
    )


def test_assemble_refuses_non_canonical_bytes(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    pretty = json.dumps(json.loads(rendered.signed_bytes), indent=2).encode()
    _refusal(
        lambda: _assemble(
            rendered,
            signed_bytes=pretty,
            signature=support.std_b64(support.INCARNATION_KEY.sign(pretty)),
            expect_signed_bytes_sha256=hashlib.sha256(pretty).hexdigest(),
        ),
        "signed-bytes-not-canonical",
    )


def test_assemble_refuses_malformed_signed_bytes(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
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
    blob = support.candidate_envelope().signed_bytes()
    root = AttestationTrustRootV2(**support.candidate_root())
    _refusal(
        lambda: ia.assemble_installed(
            signed_bytes=blob,
            signature=support.std_b64(support.CANDIDATE_KEY.sign(blob)),
            installed_root=root,
            expect_signed_bytes_sha256=hashlib.sha256(blob).hexdigest(),
        ),
        "signed-bytes-not-an-installed-attestation",
    )


def test_assemble_refuses_an_enrolled_root_that_is_not_the_signing_one(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)
    other = AttestationTrustRootV2(
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
        lambda: _assemble(rendered, installed_root=other),
        "signed-bytes-key-differs-from-enrolled-key",
    )
    for field in ("issuer", "key_id", "trust_root_version"):
        value = support.CANDIDATE_ENROLMENT if field == "trust_root_version" else "x"
        _refusal(
            lambda field=field, value=value: _assemble(
                rendered, installed_root=_installed_root(**{field: value})
            ),
            "signed-bytes-differ-from-installed-root",
        )


def test_assemble_accepts_the_transit_prefix_and_only_key_version_one(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)
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
        lambda s: s.replace("=", ""),  # unpadded: 64 bytes need padding
        lambda s: " " + s,
        lambda s: "!" + s[1:],
        lambda s: "",
    ],
)
def test_assemble_refuses_malformed_signatures(tmp_path: Path, mutate: Any) -> None:
    rendered = _render(tmp_path)
    _refusal(
        lambda: _assemble(rendered, signature=mutate(_sign(rendered))),
        "installed-signature-malformed",
    )


# ── verify tamper cases ─────────────────────────────────────────────────────


def _installed(tmp_path: Path) -> AttestationEnvelopeV2:
    return _assemble(_render(tmp_path))


def test_verify_refuses_an_installed_envelope_changed_after_signing(
    tmp_path: Path,
) -> None:
    document = _installed(tmp_path).canonical_document()
    document["key_id"] = "another-key-id"
    tampered = AttestationEnvelopeV2.from_mapping(document)
    _refusal(lambda: _verify(tampered), "trusted-host-source-root-binding-mismatch")
    document = _installed(tmp_path).canonical_document()
    document["subject"] = {**document["subject"], "version": "9.9.9"}
    _refusal(
        lambda: _verify(AttestationEnvelopeV2.from_mapping(document)),
        "trusted-host-source-signature-invalid",
    )


def test_verify_compares_the_expected_host_and_dispatch(tmp_path: Path) -> None:
    envelope = _installed(tmp_path)
    _refusal(lambda: _verify(envelope, host_id="host-b"), "host-id-mismatch")
    _refusal(
        lambda: _verify(envelope, dispatch_id="another-dispatch"),
        "dispatch-id-mismatch",
    )
    _refusal(lambda: _verify(envelope, host_id=""), "host-id-invalid")
    _refusal(lambda: _verify(envelope, dispatch_id=""), "dispatch-id-invalid")


def test_verify_refuses_an_unenrolled_key(tmp_path: Path) -> None:
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
        lambda: _verify(_installed(tmp_path), installed_root=root),
        "trusted-host-source-key-not-trusted",
    )


def test_verify_refuses_a_revoked_installed_root(tmp_path: Path) -> None:
    _refusal(
        lambda: _verify(
            _installed(tmp_path), installed_root=_installed_root(revoked=True)
        ),
        "trusted-host-source-root-revoked",
    )


def test_verify_refuses_stale_and_future_times(tmp_path: Path) -> None:
    envelope = _installed(tmp_path)
    _refusal(
        lambda: _verify(envelope, now="2026-11-19T12:00:00Z"),
        "trusted-host-source-stale",
    )
    _refusal(
        lambda: _verify(envelope, now="2026-11-19T09:59:59Z"),
        "trusted-host-source-future",
    )
    _refusal(lambda: _verify(envelope, now="soon"), "now-invalid")


def test_verify_refuses_a_candidate_it_does_not_bind(tmp_path: Path) -> None:
    other = support.candidate_envelope(version="0.4.0a3")
    _refusal(
        lambda: _verify(_installed(tmp_path), candidate=other),
        "trusted-host-source-subject-mismatch",
    )


def test_verify_refuses_a_swapped_candidate_audience(tmp_path: Path) -> None:
    _refusal(
        lambda: _verify(_installed(tmp_path), candidate_audience="someone-else"),
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
    banned = {"Ed25519PrivateKey", "load_pem_private_key", "from_private_bytes"}
    assert not banned & imported
    assert not {"sign", "private_bytes", "generate"} & calls


# ── self-contained: runs under `python -I -P` ───────────────────────────────


def test_the_script_imports_no_neighbouring_script() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    roots = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {name for name in roots if name.startswith("lane3_")}


def _isolated(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Design 2.1: no script directory on sys.path, PYTHON* variables ignored."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-P", *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
        check=False,
    )


def test_the_script_runs_under_isolated_python(tmp_path: Path) -> None:
    result = _isolated(str(SCRIPT), "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "render" in result.stdout and "verify" in result.stdout


def test_render_runs_end_to_end_under_isolated_python(tmp_path: Path) -> None:
    paths = _files(tmp_path)
    candidate = support.write_json(
        tmp_path / "candidate.json", support.candidate_envelope().canonical_document()
    )
    signed = tmp_path / "signed-bytes"
    result = _isolated(
        str(SCRIPT),
        "render",
        "--candidate", str(candidate),
        "--candidate-root", str(paths["candidate_root"]),
        "--candidate-audience", support.CANDIDATE_AUDIENCE,
        "--installed-root", str(paths["installed_root"]),
        "--receipt", str(paths["receipt"]),
        "--dispatch-materials", str(paths["dispatch"]),
        "--t0", support.T0,
        "--out-signed-bytes", str(signed),
        cwd=tmp_path,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    assert (
        printed["signed_bytes_sha256"]
        == hashlib.sha256(signed.read_bytes()).hexdigest()
    )


# ── the copied rules agree with their originals ─────────────────────────────

_KEY_SAMPLES = [
    PUBLIC,
    PUBLIC + "=",
    support.b64url(b"x" * 31),
    support.b64url(b"x" * 33),
    base64.b64encode(support.raw_public(support.INCARNATION_KEY)).decode(),
    "",
    "short",
    PUBLIC[:-1] + ("B" if PUBLIC[-1] != "B" else "C"),  # non-canonical trailing bits
    None,
    42,
]


@pytest.mark.parametrize("sample", _KEY_SAMPLES)
def test_the_copied_key_rule_agrees_with_the_e10_receipt_rule(sample: Any) -> None:
    try:
        original: bytes | None = e10._raw_public_key(sample)
    except e10.ReceiptRefused:
        original = None
    try:
        copied: bytes | None = ia.raw_public_key(sample)
    except ia.Refused:
        copied = None
    assert copied == original


def test_the_copied_field_patterns_equal_the_e10_receipt_patterns() -> None:
    for name in ("_ALIAS", "_VM_SLOT", "_MACHINE_ID", "_OPENSSH_FINGERPRINT", "_TOKEN"):
        assert getattr(ia, name).pattern == getattr(e10, name).pattern, name


def test_the_expected_package_equals_the_admission_modules() -> None:
    assert ia.EXPECTED_PACKAGE == admission.EXPECTED_PACKAGE


def test_the_copied_verifier_agrees_with_the_admission_verifier() -> None:
    message = b"message"
    good = support.std_b64(support.INCARNATION_KEY.sign(message))
    public = support.raw_public(support.INCARNATION_KEY)
    cases = [
        (public, "ed25519", message, good),
        (public, "ed25519", b"other", good),
        (public, "ed25519", message, support.std_b64(b"\x00" * 64)),
        (public, "rsa", message, good),
        (public[:-1], "ed25519", message, good),
        (public, "ed25519", message, "!!!"),
        (public, "ed25519", message, support.std_b64(b"\x00" * 63)),
        (support.raw_public(support.OTHER_KEY), "ed25519", message, good),
    ]
    for public_key, algorithm, text, signature in cases:
        arguments = {
            "public_key": public_key,
            "algorithm": algorithm,
            "message": text,
            "signature": signature,
        }
        assert ia.Ed25519Verifier().verify(
            **arguments
        ) == admission.Ed25519AttestationVerifier().verify(**arguments)


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_round_trip_and_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _files(tmp_path)
    candidate = support.write_json(
        tmp_path / "candidate.json", support.candidate_envelope().canonical_document()
    )
    signed = tmp_path / "signed-bytes"
    render_args = [
        "render",
        "--candidate", str(candidate),
        "--candidate-root", str(paths["candidate_root"]),
        "--candidate-audience", support.CANDIDATE_AUDIENCE,
        "--installed-root", str(paths["installed_root"]),
        "--receipt", str(paths["receipt"]),
        "--dispatch-materials", str(paths["dispatch"]),
        "--t0", support.T0,
        "--out-signed-bytes", str(signed),
    ]  # fmt: skip
    assert ia.main(render_args) == 0
    printed = json.loads(capsys.readouterr().out)
    digest = hashlib.sha256(signed.read_bytes()).hexdigest()
    assert printed["signed_bytes_sha256"] == digest
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
                "--installed-root", str(paths["installed_root"]),
                "--expect-signed-bytes-sha256", digest,
                "--out", str(out),
            ]
        )
        == 0
    )  # fmt: skip
    assembled = json.loads(capsys.readouterr().out)
    verify_args = [
        "verify",
        "--installed", str(out),
        "--candidate", str(candidate),
        "--candidate-root", str(paths["candidate_root"]),
        "--installed-root", str(paths["installed_root"]),
        "--candidate-audience", support.CANDIDATE_AUDIENCE,
        "--host-id", support.HOST_ID,
        "--dispatch-id", support.DISPATCH_ID,
        "--now", "2026-11-19T10:30:00Z",
    ]  # fmt: skip
    assert ia.main(verify_args) == 0
    verified = json.loads(capsys.readouterr().out)
    assert (
        verified["installed_attestation_digest"]
        == assembled["installed_attestation_digest"]
    )

    # a wrong dispatch id is a refusal, exit 2
    wrong = [
        *verify_args[:-4],
        "--dispatch-id",
        "other",
        "--now",
        "2026-11-19T10:30:00Z",
    ]
    assert ia.main(wrong) == 2
    assert "refused: dispatch-id-mismatch" in capsys.readouterr().err

    # a bad signature never becomes a file
    refused_out = tmp_path / "never.json"
    assert (
        ia.main(
            [
                "assemble",
                "--signed-bytes", str(signed),
                "--signature", support.std_b64(b"\x00" * 64),
                "--installed-root", str(paths["installed_root"]),
                "--expect-signed-bytes-sha256", digest,
                "--out", str(refused_out),
            ]
        )
        == 2
    )  # fmt: skip
    assert not refused_out.exists()


def test_verify_requires_the_host_and_dispatch_flags() -> None:
    with pytest.raises(SystemExit) as exit_info:
        ia.main(
            [
                "verify",
                "--installed", "a",
                "--candidate", "b",
                "--candidate-root", "c",
                "--installed-root", "d",
                "--candidate-audience", "e",
                "--now", "2026-11-19T10:30:00Z",
            ]
        )  # fmt: skip
    assert exit_info.value.code == 2
