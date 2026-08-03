"""Canary tests for the WS8 applied-state ENVELOPE (ADR-0007).

Written before the implementation, per the kernel slice convention.

This is the envelope a deployment signs to prove WHO is reporting — distinct
from the licence envelope, which the vendor signs to prove what was issued.
They travel in opposite directions and are signed by different parties with
different key custody, so they are deliberately separate structures.

The kernel owns the CONTRACT and the conformance vectors; it ships no
production signer, exactly as it ships none for licences. `FakeDeploymentSigner`
in the testing kit is the only place a private key appears, and it is ephemeral.

What these canaries pin, in the order the security argument depends on:

1. The signature covers the EXACT payload bytes — never a re-serialisation.
2. It also covers a WS8-specific domain separator, so a signature made for any
   other purpose cannot be replayed as an applied-state report.
3. `key_id` is what resolves to an identity; the payload's `deployment_ref` is
   only a claim, and the envelope keeps them separable.
4. Verification is fail-closed, with a closed error vocabulary.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from dotmac_kernel.licensing import (
    APPLIED_STATE_DOMAIN,
    APPLIED_STATE_ENVELOPE_SCHEMA,
    BadSignatureError,
    MalformedAppliedStateError,
    ReceiverAppliedState,
    UnknownKeyError,
    applied_state_signing_input,
    parse_applied_state_envelope,
    seal_applied_state,
    verify_applied_state,
)
from dotmac_kernel.testing import FakeDeploymentSigner

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32


def _state(**over) -> ReceiverAppliedState:
    fields: dict[str, object] = {
        "report_id": "rep-1",
        "deployment_ref": "edge-site-1",
        "licence_id": "lic-1",
        "licence_version": 3,
        "digest": DIGEST,
        "keyring_generation": 2,
        "revocation_list_version": 5,
        "observed_at": NOW,
        "status": "applied",
    }
    fields.update(over)
    return ReceiverAppliedState(**fields)  # type: ignore[arg-type]


@pytest.fixture
def signer() -> FakeDeploymentSigner:
    return FakeDeploymentSigner(key_id="dep-edge1-2026-08")


# ── Round trip ──────────────────────────────────────────────────────────────


def test_seal_then_verify_returns_the_same_report(signer) -> None:
    envelope = seal_applied_state(_state(), signer=signer)
    verified = verify_applied_state(
        envelope, public_keys={signer.key_id: signer.public_key_b64}
    )
    assert verified.state == _state()


def test_verification_reports_the_key_that_proved_it(signer) -> None:
    """The vendor resolves `key_id` -> deployment_ref, so the verified result
    must say which key verified — not merely that one did."""
    envelope = seal_applied_state(_state(), signer=signer)
    verified = verify_applied_state(
        envelope, public_keys={signer.key_id: signer.public_key_b64}
    )
    assert verified.key_id == "dep-edge1-2026-08"


def test_envelope_names_its_schema_and_key_id(signer) -> None:
    envelope = seal_applied_state(_state(), signer=signer)
    assert envelope["schema"] == APPLIED_STATE_ENVELOPE_SCHEMA
    assert envelope["key_id"] == signer.key_id


def test_envelope_is_json_safe(signer) -> None:
    """It crosses a process boundary; a value needing a custom encoder would
    fail at the transport rather than here."""
    json.dumps(seal_applied_state(_state(), signer=signer))


# ── 1. The signature covers the EXACT bytes ─────────────────────────────────


def test_the_payload_travels_as_bytes_not_as_a_nested_object(signer) -> None:
    """A nested JSON object would be re-serialised by the verifier, and two
    encodings of the same object differ in key order and whitespace. Carrying
    the base64 of the exact signed bytes removes the ambiguity entirely."""
    envelope = seal_applied_state(_state(), signer=signer)
    assert isinstance(envelope["payload_b64"], str)
    raw = base64.urlsafe_b64decode(
        envelope["payload_b64"] + "=" * (-len(envelope["payload_b64"]) % 4)
    )
    assert json.loads(raw)["report_id"] == "rep-1"


def test_a_single_altered_payload_byte_fails_verification(signer) -> None:
    envelope = seal_applied_state(_state(), signer=signer)
    tampered = dict(envelope)
    raw = bytearray(
        base64.urlsafe_b64decode(
            envelope["payload_b64"] + "=" * (-len(envelope["payload_b64"]) % 4)
        )
    )
    raw[-2] ^= 0x01
    tampered["payload_b64"] = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
    with pytest.raises(BadSignatureError):
        verify_applied_state(
            tampered, public_keys={signer.key_id: signer.public_key_b64}
        )


def test_swapping_the_payload_for_another_valid_report_fails(signer) -> None:
    """The classic substitution: both payloads are individually well-formed,
    so only binding the signature to THESE bytes catches it."""
    envelope = seal_applied_state(_state(), signer=signer)
    other = seal_applied_state(_state(report_id="rep-2"), signer=signer)
    spliced = dict(envelope) | {"payload_b64": other["payload_b64"]}
    with pytest.raises(BadSignatureError):
        verify_applied_state(
            spliced, public_keys={signer.key_id: signer.public_key_b64}
        )


# ── 2. Domain separation ────────────────────────────────────────────────────


def test_signing_input_is_prefixed_with_the_ws8_domain_separator() -> None:
    """Explicit, because this is the whole cross-protocol defence: if the
    deployment's key ever signs caller-influenced bytes for another purpose,
    the separator is what stops that becoming a forgery oracle."""
    payload = b'{"report_id":"rep-1"}'
    signing_input = applied_state_signing_input(payload)
    assert signing_input.startswith(APPLIED_STATE_DOMAIN)
    assert signing_input.endswith(payload)


def test_a_bare_payload_signature_is_refused(signer) -> None:
    """A signature over the payload WITHOUT the domain separator must not
    verify — otherwise the separator is decorative."""
    envelope = seal_applied_state(_state(), signer=signer)
    raw = base64.urlsafe_b64decode(
        envelope["payload_b64"] + "=" * (-len(envelope["payload_b64"]) % 4)
    )
    bare = signer.sign_raw(raw)  # deliberately skips the domain prefix
    forged = dict(envelope) | {
        "signature_b64": base64.urlsafe_b64encode(bare).rstrip(b"=").decode()
    }
    with pytest.raises(BadSignatureError):
        verify_applied_state(forged, public_keys={signer.key_id: signer.public_key_b64})


def test_the_domain_separator_is_specific_to_applied_state() -> None:
    """Pinned as a VALUE: changing it is a wire-compatibility break that must
    be a deliberate, visible edit rather than an incidental refactor."""
    assert APPLIED_STATE_DOMAIN == b"dotmac-ws8-applied-state/1\x00"


# ── 3. key_id resolves identity; deployment_ref is only a claim ─────────────


def test_key_id_is_outside_the_signed_payload_but_still_bound(signer) -> None:
    """`key_id` must be readable BEFORE verification — the verifier needs it to
    find the key. Substituting it is still caught, because the wrong key simply
    fails to verify the signature."""
    envelope = seal_applied_state(_state(), signer=signer)
    other = FakeDeploymentSigner(key_id="dep-other")
    with pytest.raises(BadSignatureError):
        verify_applied_state(
            dict(envelope) | {"key_id": "dep-other"},
            public_keys={
                signer.key_id: signer.public_key_b64,
                "dep-other": other.public_key_b64,
            },
        )


def test_a_claimed_deployment_ref_is_carried_but_never_authoritative(signer) -> None:
    """The envelope preserves the claim so the vendor can compare it against
    the identity it resolved from `key_id` and quarantine a contradiction."""
    envelope = seal_applied_state(
        _state(deployment_ref="claimed-elsewhere"), signer=signer
    )
    verified = verify_applied_state(
        envelope, public_keys={signer.key_id: signer.public_key_b64}
    )
    assert verified.state.deployment_ref == "claimed-elsewhere"
    assert verified.key_id == signer.key_id


def test_an_unregistered_key_id_is_refused(signer) -> None:
    with pytest.raises(UnknownKeyError):
        verify_applied_state(
            seal_applied_state(_state(), signer=signer), public_keys={}
        )


# ── 4. Fail-closed parsing ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "breakage",
    [
        {"schema": "dotmac-applied-state-envelope/999"},
        {"schema": None},
        {"key_id": ""},
        {"key_id": None},
        {"payload_b64": ""},
        {"payload_b64": "!!!not-base64!!!"},
        {"payload_b64": None},
        {"signature_b64": ""},
        {"signature_b64": "!!!not-base64!!!"},
        {"signature_b64": None},
    ],
)
def test_malformed_envelopes_fail_closed(signer, breakage) -> None:
    envelope = dict(seal_applied_state(_state(), signer=signer)) | breakage
    with pytest.raises(MalformedAppliedStateError):
        parse_applied_state_envelope(envelope)


def test_a_non_object_envelope_is_rejected() -> None:
    for payload in ([], "applied", 7, None):
        with pytest.raises(MalformedAppliedStateError):
            parse_applied_state_envelope(payload)  # type: ignore[arg-type]


def test_a_payload_that_is_not_a_valid_report_is_rejected(signer) -> None:
    """Sealing garbage must not produce something that verifies into a
    ReceiverAppliedState — the envelope is a carrier, not an escape hatch
    around the value object's own validation."""
    envelope = signer.seal_raw(b'{"schema":"dotmac-licence-applied-state/1"}')
    with pytest.raises(MalformedAppliedStateError):
        verify_applied_state(
            envelope, public_keys={signer.key_id: signer.public_key_b64}
        )


def test_unknown_envelope_fields_are_ignored_not_trusted(signer) -> None:
    envelope = dict(seal_applied_state(_state(), signer=signer)) | {"future": "x"}
    verified = verify_applied_state(
        envelope, public_keys={signer.key_id: signer.public_key_b64}
    )
    assert verified.state == _state()


# ── Conformance vectors ─────────────────────────────────────────────────────


def test_signing_input_is_stable_for_a_fixed_payload() -> None:
    """The vendor and the receiver must compute byte-identical signing input
    from the same payload, in different processes and languages. Pinning it
    here is what makes 'both planes agree' testable rather than hoped for."""
    assert applied_state_signing_input(b"abc") == b"dotmac-ws8-applied-state/1\x00abc"
