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
2. It also covers `key_id`, so an identity cannot be swapped under a valid
   signature, and a WS8 domain separator, so a signature made for any other
   purpose cannot be replayed as an applied-state report.
3. `key_id` resolves to the PROVEN identity; the payload's `deployment_ref` is
   only a claim, kept separable so a contradiction can be quarantined.
4. Verification is fail-closed and the contract is fully typed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel import licensing
from dotmac_kernel.licensing import (
    APPLIED_STATE_DOMAIN,
    APPLIED_STATE_ENVELOPE_SCHEMA,
    DEPLOYMENT_CHALLENGE_DOMAIN,
    DEPLOYMENT_CHALLENGE_SCHEMA,
    DEPLOYMENT_RESPONSE_SCHEMA,
    AppliedStateEnvelope,
    BadSignatureError,
    DeploymentMismatchError,
    DeploymentPossessionChallenge,
    DeploymentPossessionResponse,
    DeploymentVerificationKey,
    LicenceExpiredError,
    MalformedAppliedStateError,
    ReceiverAppliedState,
    UnknownKeyError,
    VerifiedDeploymentPossession,
    answer_possession_challenge,
    applied_state_signing_input,
    seal_applied_state,
    verify_applied_state,
    verify_possession,
)
from dotmac_kernel.testing import FakeDeploymentSigner

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32
KEY_ID = "dep-edge1-2026-08"
DEP = "edge-site-1"


def _state(**over) -> ReceiverAppliedState:
    fields: dict[str, object] = {
        "report_id": "rep-1",
        "deployment_ref": DEP,
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
    return FakeDeploymentSigner(key_id=KEY_ID, deployment_ref=DEP)


class ExplodingSigner:
    """A signer whose private key MUST never be applied.

    The guards run before `sign`, and that ordering is the whole point: a
    refusal that happened after signing would be too late, because the
    signature would already exist and be portable evidence. Only a signer that
    detonates on use can prove the key was never touched — a mock that merely
    records the call would let a "signed then discarded" implementation pass.
    """

    def __init__(self, key_id: str = KEY_ID, deployment_ref: str = DEP) -> None:
        self.key_id = key_id
        self.deployment_ref = deployment_ref

    def sign(self, data: bytes) -> bytes:
        raise AssertionError("the signer was invoked for a statement it cannot attest")


@pytest.fixture
def key(signer: FakeDeploymentSigner) -> DeploymentVerificationKey:
    return DeploymentVerificationKey(
        key_id=signer.key_id, public_key_b64=signer.public_key_b64, deployment_ref=DEP
    )


# ── Round trip ──────────────────────────────────────────────────────────────


def test_seal_then_verify_returns_the_same_report(signer, key) -> None:
    verified = verify_applied_state(
        seal_applied_state(_state(), signer=signer), keys=[key]
    )
    assert verified.state == _state()


def test_verification_resolves_the_proven_deployment_identity(signer, key) -> None:
    """Resolving `key_id` -> deployment IS the identity decision, so a verified
    report must name it. Returning a bare "signature ok" and leaving the caller
    to look the ref up separately is what lets the two disagree."""
    verified = verify_applied_state(
        seal_applied_state(_state(), signer=signer), keys=[key]
    )
    assert verified.deployment_ref == DEP
    assert verified.key_id == KEY_ID


def test_wire_form_is_json_safe_and_round_trips(signer) -> None:
    envelope = seal_applied_state(_state(), signer=signer)
    wire = envelope.to_wire()
    json.dumps(wire)
    assert wire["schema"] == APPLIED_STATE_ENVELOPE_SCHEMA
    assert AppliedStateEnvelope.from_wire(wire) == envelope


def test_the_envelope_is_immutable(signer) -> None:
    envelope = seal_applied_state(_state(), signer=signer)
    with pytest.raises((AttributeError, TypeError)):
        envelope.key_id = "other"  # type: ignore[misc]


# ── 1. The signature covers the EXACT bytes ─────────────────────────────────


def test_the_payload_travels_as_bytes_not_as_a_parsed_object(signer) -> None:
    """A parsed object would be re-serialised by the verifier, and two JSON
    encodings of one object differ in key order and whitespace. Carrying the
    exact signed bytes removes the ambiguity entirely."""
    envelope = seal_applied_state(_state(), signer=signer)
    assert isinstance(envelope.payload, bytes)
    assert json.loads(envelope.payload)["report_id"] == "rep-1"


def test_a_single_altered_payload_byte_fails_verification(signer, key) -> None:
    envelope = seal_applied_state(_state(), signer=signer)
    raw = bytearray(envelope.payload)
    raw[-2] ^= 0x01
    tampered = AppliedStateEnvelope(
        key_id=envelope.key_id, payload=bytes(raw), signature=envelope.signature
    )
    with pytest.raises(BadSignatureError):
        verify_applied_state(tampered, keys=[key])


def test_swapping_the_payload_for_another_valid_report_fails(signer, key) -> None:
    """The classic substitution: both payloads are individually well-formed,
    so only binding the signature to THESE bytes catches it."""
    envelope = seal_applied_state(_state(), signer=signer)
    other = seal_applied_state(_state(report_id="rep-2"), signer=signer)
    spliced = AppliedStateEnvelope(
        key_id=envelope.key_id, payload=other.payload, signature=envelope.signature
    )
    with pytest.raises(BadSignatureError):
        verify_applied_state(spliced, keys=[key])


# ── 2. key_id is SIGNED, not merely carried ─────────────────────────────────


def test_the_same_public_key_registered_under_a_second_key_id_cannot_steal_identity(
    signer,
) -> None:
    """THE exploit this binding exists to stop.

    Register the SAME public key under a second `key_id` that maps to a
    different deployment, then replay a captured report with `key_id` swapped.
    While `key_id` travelled unsigned the signature still verified — the key
    material was identical — and the report was attributed to the attacker's
    chosen deployment. Signing `key_id` makes the substitution fail.

    Note this is NOT caught by substituting a DIFFERENT key: that fails for the
    trivial reason that the other key's signature does not verify. The attack
    requires identical material under two ids, which is exactly what an
    unsigned `key_id` permits.
    """
    envelope = seal_applied_state(_state(), signer=signer)
    victim = DeploymentVerificationKey(
        key_id=KEY_ID, public_key_b64=signer.public_key_b64, deployment_ref=DEP
    )
    attacker = DeploymentVerificationKey(
        key_id="key-b",
        public_key_b64=signer.public_key_b64,  # SAME material, different id
        deployment_ref="attacker-deployment",
    )
    swapped = AppliedStateEnvelope(
        key_id="key-b", payload=envelope.payload, signature=envelope.signature
    )
    with pytest.raises(BadSignatureError):
        verify_applied_state(swapped, keys=[victim, attacker])


def test_signing_input_binds_the_key_id(signer) -> None:
    assert applied_state_signing_input("key-a", b"x") != applied_state_signing_input(
        "key-b", b"x"
    )


def test_signing_input_is_length_delimited_against_splicing() -> None:
    """Plain concatenation is ambiguous: ("a", b"bc") and ("ab", b"c") would
    produce identical bytes, letting one signature serve two different
    (key_id, payload) pairs."""
    assert applied_state_signing_input("a", b"bc") != applied_state_signing_input(
        "ab", b"c"
    )


# ── 2b. Domain separation ───────────────────────────────────────────────────


def test_signing_input_is_prefixed_with_the_ws8_domain_separator() -> None:
    signing_input = applied_state_signing_input("k", b'{"report_id":"rep-1"}')
    assert signing_input.startswith(APPLIED_STATE_DOMAIN)


def test_a_bare_payload_signature_is_refused(signer, key) -> None:
    """A signature over the payload WITHOUT the domain and key binding must not
    verify — otherwise those bindings are decorative."""
    envelope = seal_applied_state(_state(), signer=signer)
    forged = AppliedStateEnvelope(
        key_id=envelope.key_id,
        payload=envelope.payload,
        signature=signer.sign_raw(envelope.payload),
    )
    with pytest.raises(BadSignatureError):
        verify_applied_state(forged, keys=[key])


def test_the_domain_separators_are_distinct_and_pinned() -> None:
    """Pinned as VALUES: changing either is a wire-compatibility break that
    must be a deliberate, visible edit rather than an incidental refactor."""
    assert APPLIED_STATE_DOMAIN == b"dotmac-ws8-applied-state/1\x00"
    assert DEPLOYMENT_CHALLENGE_DOMAIN == b"dotmac-ws8-deployment-challenge/1\x00"
    assert APPLIED_STATE_DOMAIN != DEPLOYMENT_CHALLENGE_DOMAIN


def test_a_challenge_response_cannot_be_replayed_as_a_report(signer, key) -> None:
    """The challenge is signed by the SAME key over a vendor-chosen nonce — a
    forgery oracle under a shared domain. The vendor hands back the exact bytes
    of a real report as a "nonce"; the response must not verify as that
    report."""
    envelope = seal_applied_state(_state(), signer=signer)
    challenge = DeploymentPossessionChallenge(
        challenge_id="c-1",
        key_id=KEY_ID,
        deployment_ref=DEP,
        nonce=envelope.payload,
        expires_at=NOW + timedelta(minutes=10),
    )
    forged = AppliedStateEnvelope(
        key_id=KEY_ID,
        payload=envelope.payload,
        signature=signer.sign(challenge.signing_input()),
    )
    with pytest.raises(BadSignatureError):
        verify_applied_state(forged, keys=[key])


# ── 3. key_id resolves identity; deployment_ref is only a claim ─────────────


def test_a_claimed_deployment_ref_is_carried_but_never_authoritative(signer) -> None:
    """The report preserves the claim so the vendor can compare it against the
    identity resolved from `key_id` and quarantine a contradiction.

    Sealed by a signer that BELIEVES it speaks for `claimed-elsewhere` while
    the vendor's registration maps its `key_id` to `edge-site-1` — a
    misconfigured or hostile receiver. An honest one cannot build this envelope
    at all (`seal_applied_state` refuses a foreign claim), but the verifier
    must still handle it, because refusing to produce a thing is not the same
    as being safe when someone else produces it.
    """
    liar = FakeDeploymentSigner(key_id=KEY_ID, deployment_ref="claimed-elsewhere")
    registered = DeploymentVerificationKey(
        key_id=KEY_ID, public_key_b64=liar.public_key_b64, deployment_ref=DEP
    )
    envelope = seal_applied_state(
        _state(deployment_ref="claimed-elsewhere"), signer=liar
    )
    verified = verify_applied_state(envelope, keys=[registered])
    assert verified.state.deployment_ref == "claimed-elsewhere"
    assert verified.deployment_ref == DEP
    assert verified.claim_matches_proof is False


def test_claim_matches_proof_is_true_when_they_agree(signer, key) -> None:
    verified = verify_applied_state(
        seal_applied_state(_state(), signer=signer), keys=[key]
    )
    assert verified.claim_matches_proof is True


def test_an_unregistered_key_id_is_refused(signer) -> None:
    with pytest.raises(UnknownKeyError):
        verify_applied_state(seal_applied_state(_state(), signer=signer), keys=[])


# ── 4. Fail-closed parsing, typed contract ──────────────────────────────────


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
def test_malformed_wire_envelopes_fail_closed(signer, breakage) -> None:
    wire = seal_applied_state(_state(), signer=signer).to_wire() | breakage
    with pytest.raises(MalformedAppliedStateError):
        AppliedStateEnvelope.from_wire(wire)


def test_a_non_object_envelope_is_rejected() -> None:
    for wire in ([], "applied", 7, None):
        with pytest.raises(MalformedAppliedStateError):
            AppliedStateEnvelope.from_wire(wire)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"key_id": ""},
        {"payload": b""},
        {"signature": b""},
        {"payload": "not-bytes"},
        {"signature": "not-bytes"},
    ],
)
def test_direct_envelope_construction_validates_identically(kwargs) -> None:
    fields = {"key_id": "k", "payload": b"p", "signature": b"s"} | kwargs
    with pytest.raises(MalformedAppliedStateError):
        AppliedStateEnvelope(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [{"key_id": ""}, {"public_key_b64": ""}, {"deployment_ref": ""}],
)
def test_a_verification_key_needs_every_field(kwargs) -> None:
    fields = {"key_id": "k", "public_key_b64": "p", "deployment_ref": "d"} | kwargs
    with pytest.raises(MalformedAppliedStateError):
        DeploymentVerificationKey(**fields)  # type: ignore[arg-type]


def test_a_payload_that_is_not_a_valid_report_is_rejected(signer, key) -> None:
    """The envelope is a carrier, not an escape hatch around the value
    object's own validation."""
    payload = b'{"schema":"dotmac-licence-applied-state/1"}'
    envelope = AppliedStateEnvelope(
        key_id=KEY_ID,
        payload=payload,
        signature=signer.sign(applied_state_signing_input(KEY_ID, payload)),
    )
    with pytest.raises(MalformedAppliedStateError):
        verify_applied_state(envelope, keys=[key])


def test_unknown_wire_fields_are_ignored_not_trusted(signer, key) -> None:
    wire = seal_applied_state(_state(), signer=signer).to_wire() | {"future": "x"}
    assert verify_applied_state(wire, keys=[key]).state == _state()


# ── Possession challenge ────────────────────────────────────────────────────


def _challenge(**over) -> DeploymentPossessionChallenge:
    fields: dict[str, object] = {
        "challenge_id": "c-1",
        "key_id": KEY_ID,
        "deployment_ref": DEP,
        "nonce": b"0123456789abcdef",
        "expires_at": NOW + timedelta(minutes=10),
    }
    fields.update(over)
    return DeploymentPossessionChallenge(**fields)  # type: ignore[arg-type]


def test_a_valid_possession_response_is_accepted(signer, key) -> None:
    challenge = _challenge()
    proof = verify_possession(
        challenge,
        answer_possession_challenge(challenge, signer=signer),
        key=key,
        now=NOW,
    )
    assert isinstance(proof, VerifiedDeploymentPossession)


def test_the_result_carries_what_the_vendor_transaction_needs(signer, key) -> None:
    """Success returns a VALUE, not just "it did not raise": consuming the
    challenge and activating the key both need the proven identity, and reading
    it back off the inputs would re-open which input was authoritative."""
    challenge = _challenge()
    proof = verify_possession(
        challenge,
        answer_possession_challenge(challenge, signer=signer),
        key=key,
        now=NOW,
    )
    assert proof.challenge_id == "c-1"  # the handle to consume, atomically
    assert proof.key_id == KEY_ID  # the pending key to activate
    assert proof.deployment_ref == DEP  # the PROVEN identity
    assert proof.verified_at == NOW


def test_an_expired_challenge_is_refused_even_with_a_valid_signature(
    signer, key
) -> None:
    challenge = _challenge()
    with pytest.raises(LicenceExpiredError):
        verify_possession(
            challenge,
            answer_possession_challenge(challenge, signer=signer),
            key=key,
            now=NOW + timedelta(hours=1),
        )


def test_a_response_cannot_be_carried_to_another_registration(signer) -> None:
    """A response is evidence for the key and deployment named IN the
    challenge; accepting it elsewhere would let one valid response activate an
    unrelated key."""
    challenge = _challenge()
    other_key = DeploymentVerificationKey(
        key_id="other-key", public_key_b64=signer.public_key_b64, deployment_ref=DEP
    )
    with pytest.raises(DeploymentMismatchError):
        verify_possession(
            challenge,
            answer_possession_challenge(challenge, signer=signer),
            key=other_key,
            now=NOW,
        )


# ── The typed possession RESPONSE ───────────────────────────────────────────


def test_a_response_wire_form_round_trips(signer) -> None:
    response = answer_possession_challenge(_challenge(), signer=signer)
    wire = response.to_wire()
    json.dumps(wire)  # JSON-safe: every value is a string
    assert wire["schema"] == DEPLOYMENT_RESPONSE_SCHEMA
    assert DeploymentPossessionResponse.from_wire(wire) == response


def test_a_wire_response_verifies_exactly_like_a_typed_one(signer, key) -> None:
    """The verifier accepts the transport form, so a vendor endpoint need not
    hand-parse before it can check anything."""
    challenge = _challenge()
    wire = answer_possession_challenge(challenge, signer=signer).to_wire()
    assert verify_possession(challenge, wire, key=key, now=NOW).deployment_ref == DEP
    assert (
        verify_possession(challenge, json.dumps(wire), key=key, now=NOW).key_id
        == KEY_ID
    )


def test_the_response_is_immutable(signer) -> None:
    response = answer_possession_challenge(_challenge(), signer=signer)
    with pytest.raises(AttributeError):
        response.key_id = "other"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        response.signature = b"x"  # type: ignore[misc]


def test_the_verified_result_is_immutable(signer, key) -> None:
    challenge = _challenge()
    proof = verify_possession(
        challenge,
        answer_possession_challenge(challenge, signer=signer),
        key=key,
        now=NOW,
    )
    with pytest.raises(AttributeError):
        proof.key = None  # type: ignore[misc]


@pytest.mark.parametrize(
    "echoed",
    [
        {"nonce_b64": "AAAA"},
        {"nonce": "0123456789abcdef"},
        {"deployment_ref": "attacker-deployment"},
        {"expires_at": "2099-01-01T00:00:00+00:00"},
    ],
)
def test_a_response_may_not_echo_the_challenges_own_bindings(signer, echoed) -> None:
    """The STORED challenge is authoritative for the nonce, deployment and
    expiry. A response restating them is refused rather than ignored: a field
    accepted and ignored today is a field something reads tomorrow — which is
    exactly how a claim gets substituted for a proof."""
    wire = answer_possession_challenge(_challenge(), signer=signer).to_wire() | echoed
    with pytest.raises(MalformedAppliedStateError):
        DeploymentPossessionResponse.from_wire(wire)


def test_a_response_naming_another_challenge_is_a_mismatch_not_a_bad_signature(
    signer, key
) -> None:
    """Routing failure and forgery are different operator problems. The
    identifiers are routing — the signature commits to them via the challenge's
    signing input — so a mismatch must say so."""
    challenge = _challenge()
    other = _challenge(challenge_id="c-2")
    with pytest.raises(DeploymentMismatchError):
        verify_possession(
            challenge,
            answer_possession_challenge(other, signer=signer),
            key=key,
            now=NOW,
        )


def test_a_response_naming_another_key_is_a_mismatch(signer, key) -> None:
    challenge = _challenge()
    forged = DeploymentPossessionResponse(
        challenge_id=challenge.challenge_id,
        key_id="other-key",
        signature=signer.sign(challenge.signing_input()),
    )
    with pytest.raises(DeploymentMismatchError):
        verify_possession(challenge, forged, key=key, now=NOW)


def test_a_response_signed_for_a_different_challenge_fails_verification(
    signer, key
) -> None:
    """Identifiers can be made to line up; the signature cannot. A response
    that names the right challenge but signed a different one is a forgery."""
    forged = DeploymentPossessionResponse(
        challenge_id="c-1",
        key_id=KEY_ID,
        signature=signer.sign(_challenge(nonce=b"fedcba9876543210").signing_input()),
    )
    with pytest.raises(BadSignatureError):
        verify_possession(_challenge(), forged, key=key, now=NOW)


def test_signing_a_challenge_for_another_key_is_refused_at_the_receiver(signer) -> None:
    """Caught on the way out, not diagnosed from the other side of the wire."""
    with pytest.raises(DeploymentMismatchError):
        answer_possession_challenge(_challenge(key_id="not-this-signer"), signer=signer)


def test_signing_a_challenge_for_a_FOREIGN_DEPLOYMENT_is_refused(signer) -> None:
    """THE gap this guard exists to close.

    Checking `key_id` alone was not enough: a challenge naming this signer's
    own key but a foreign `deployment_ref` was signed happily, minting a
    portable signed statement that this key proved possession for someone
    else's deployment. The verifier refuses to ACTIVATE on it — the key's
    registration names a different deployment — but ADR-0007 §1 sells these
    signatures as evidence any third party can check, so the artifact must
    never be produced in the first place.
    """
    with pytest.raises(DeploymentMismatchError):
        answer_possession_challenge(
            _challenge(deployment_ref="foreign-deployment"), signer=signer
        )


def test_the_signer_is_never_invoked_for_a_foreign_deployment() -> None:
    """Refusal happens BEFORE `sign`, not after. A signature discarded after
    the fact has still been computed; this proves the key is never applied."""
    with pytest.raises(DeploymentMismatchError):
        answer_possession_challenge(
            _challenge(deployment_ref="foreign-deployment"), signer=ExplodingSigner()
        )
    with pytest.raises(DeploymentMismatchError):
        answer_possession_challenge(
            _challenge(key_id="not-this-signer"), signer=ExplodingSigner()
        )


def test_sealing_a_report_claiming_another_deployment_is_refused() -> None:
    """Same invariant on the report path: a receiver reports its OWN state.
    The verifier would quarantine a foreign claim via `claim_matches_proof`,
    but the signed artifact should never exist."""
    with pytest.raises(DeploymentMismatchError):
        seal_applied_state(
            _state(deployment_ref="foreign-deployment"), signer=ExplodingSigner()
        )


def test_a_signer_owns_both_halves_of_its_identity(signer) -> None:
    """The protocol carries `deployment_ref` alongside `key_id`, because a
    signer that knows only its key id cannot refuse a foreign deployment."""
    assert signer.key_id == KEY_ID
    assert signer.deployment_ref == DEP


@pytest.mark.parametrize(
    "kwargs",
    [
        {"challenge_id": ""},
        {"key_id": ""},
        {"signature": b""},
        {"signature": "not-bytes"},
    ],
)
def test_malformed_responses_fail_closed(kwargs) -> None:
    fields: dict[str, object] = {
        "challenge_id": "c-1",
        "key_id": KEY_ID,
        "signature": b"sig",
    }
    fields.update(kwargs)
    with pytest.raises(MalformedAppliedStateError):
        DeploymentPossessionResponse(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "breakage",
    [
        {"schema": "dotmac-deployment-possession-response/2"},
        {"schema": DEPLOYMENT_CHALLENGE_SCHEMA},  # the wrong document entirely
        {"challenge_id": ""},
        {"signature_b64": "not base64!!"},
    ],
)
def test_malformed_response_wire_forms_fail_closed(signer, breakage) -> None:
    wire = answer_possession_challenge(_challenge(), signer=signer).to_wire() | breakage
    with pytest.raises(MalformedAppliedStateError):
        DeploymentPossessionResponse.from_wire(wire)


def test_a_non_object_response_is_rejected() -> None:
    for wire in ("[]", "null", b"not json"):
        with pytest.raises(MalformedAppliedStateError):
            DeploymentPossessionResponse.from_wire(wire)


def test_each_challenge_has_distinct_signing_bytes() -> None:
    base = _challenge().signing_input()
    for field, value in [
        ("challenge_id", "c-2"),
        ("key_id", "other"),
        ("deployment_ref", "other-dep"),
        ("nonce", b"fedcba98765432100"),
        ("expires_at", NOW + timedelta(minutes=11)),
    ]:
        assert _challenge(**{field: value}).signing_input() != base, field


def test_a_challenge_wire_form_round_trips() -> None:
    challenge = _challenge()
    wire = challenge.to_wire()
    json.dumps(wire)
    assert wire["schema"] == DEPLOYMENT_CHALLENGE_SCHEMA
    assert DeploymentPossessionChallenge.from_wire(wire) == challenge


@pytest.mark.parametrize(
    "kwargs",
    [
        {"challenge_id": ""},
        {"key_id": ""},
        {"deployment_ref": ""},
        {"nonce": b"short"},  # predictable -> precomputable response
        {"nonce": "not-bytes"},
        {"expires_at": "not-a-datetime"},
        {"expires_at": datetime(2026, 8, 3, 12, 0, 0)},  # naive
    ],
)
def test_malformed_challenges_fail_closed(kwargs) -> None:
    with pytest.raises(MalformedAppliedStateError):
        _challenge(**kwargs)


# ── Conformance vectors ─────────────────────────────────────────────────────


def test_applied_state_signing_input_is_stable() -> None:
    """The vendor and the receiver must compute byte-identical signing input in
    different processes and languages. Pinning it is what makes 'both planes
    agree' testable rather than hoped for."""
    assert applied_state_signing_input("k", b"abc") == (
        b"dotmac-ws8-applied-state/1\x00" b"\x00\x00\x00\x01k" b"\x00\x00\x00\x03abc"
    )


def test_the_wire_schemas_are_pinned_and_distinct() -> None:
    """Pinned as VALUES, like the domains: a challenge and its answer are
    different documents and a reader holding only one must be able to tell."""
    assert DEPLOYMENT_CHALLENGE_SCHEMA == "dotmac-deployment-challenge/1"
    assert DEPLOYMENT_RESPONSE_SCHEMA == "dotmac-deployment-possession-response/1"
    assert DEPLOYMENT_CHALLENGE_SCHEMA != DEPLOYMENT_RESPONSE_SCHEMA


def test_the_possession_contract_is_reachable_from_the_public_surface() -> None:
    """The vendor control plane may import ONLY the kernel's public surface
    (its D5 deny-case), so a name missing from `__all__` is a name V6 cannot
    legally consume — an export gap that would surface as a vendor-side lint
    failure long after this release."""
    for name in (
        "DEPLOYMENT_RESPONSE_SCHEMA",
        "DeploymentPossessionChallenge",
        "DeploymentPossessionResponse",
        "VerifiedDeploymentPossession",
        "answer_possession_challenge",
        "verify_possession",
    ):
        assert name in licensing.__all__, f"{name} is not exported"
        assert hasattr(licensing, name)


def test_challenge_signing_input_is_stable() -> None:
    assert _challenge(
        expires_at=datetime(2026, 8, 3, 12, 10, tzinfo=UTC)
    ).signing_input() == (
        b"dotmac-ws8-deployment-challenge/1\x00"
        b"\x00\x00\x00\x1ddotmac-deployment-challenge/1"
        b"\x00\x00\x00\x03c-1"
        b"\x00\x00\x00\x11dep-edge1-2026-08"
        b"\x00\x00\x00\x0bedge-site-1"
        b"\x00\x00\x00\x100123456789abcdef"
        b"\x00\x00\x00\x192026-08-03T12:10:00+00:00"
    )
