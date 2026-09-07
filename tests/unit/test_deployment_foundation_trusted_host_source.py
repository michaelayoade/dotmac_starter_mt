"""The fail-closed two-attestation verifier — step 3 of the host-source chain.

## Why every test in this file failed before `trusted_host_source.py` existed

At the commit this file is added, `grep -r verify_trusted_host_source` over the
whole repository returns nothing outside this file and the module itself, and
`packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation/`
contains no `trusted_host_source.py`. Every test below fails at COLLECTION
with `ModuleNotFoundError` — there was no code to pass it.

## What "genuine" means here, honestly

No real production signer for either half exists anywhere reachable in this
repository (see the package `CHANGELOG.md`'s "Trusted provenance was
investigated" and "A caller who cannot produce two independent signatures..."
entries). So the `SignatureVerifier` used below is a REAL keyed-MAC scheme
(`hmac.new(secret, message, sha256)`, checked with `hmac.compare_digest`) over
TWO DISTINCT secret keys — not a magic string like the codebase's own
`"probe-valid"` fixtures. A party without the correct secret genuinely cannot
produce a signature `HMACVerifier.verify` accepts; this is as real an
admission and refusal proof as is possible without a production key management
system, and the module under test never sees the secrets at all — only
`key_id`, `message` and `signature`, exactly as `evidence.SignatureVerifier`
already requires of a real caller.

## The one-caller negative control (test 3 below)

Both prior admission attempts on this chain failed because a single caller
could supply both halves of the comparison from data it authored itself.
`test_one_key_signing_both_attestations_is_refused_though_each_verifies`
is the direct analogue here: ONE key signs BOTH documents, EACH signature
verifies genuinely (the HMAC is correct), and the pair is still refused —
`SAME_KEY_SIGNED_BOTH`, checked before either envelope's key is even matched
against a trust root, so the refusal does not depend on `DistinctTrustRoots`
having been configured correctly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATIONS_DISAGREE,
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    KEY_NOT_TRUSTED,
    OBSERVATION_ABSENT,
    OBSERVATION_MALFORMED,
    ROOT_PURPOSE_MISMATCH,
    SAME_KEY_SIGNED_BOTH,
    TRUST_ROOTS_NOT_DISTINCT,
    AttestationTrustRoot,
    DistinctTrustRoots,
    SignedAttestationEnvelope,
    verify_trusted_host_source,
)
from dotmac_deployment_foundation.trusted_host_source import (
    SIGNATURE_INVALID as VERIFY_SIGNATURE_INVALID,
)

CANDIDATE_KEY = "release-ci-2026"
INSTALLED_KEY = "deploy-agent-2026"
ROGUE_KEY = "rogue-holder"

SECRETS: dict[str, bytes] = {
    CANDIDATE_KEY: b"candidate-signing-secret",
    INSTALLED_KEY: b"installed-observation-signing-secret",
    ROGUE_KEY: b"a-key-no-trust-root-accepts",
}


class HMACVerifier:
    """A REAL keyed-MAC `SignatureVerifier` — genuinely unforgeable without
    the matching secret, unlike the codebase's own `"probe-valid"` doubles.
    """

    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool:
        secret = SECRETS.get(key_id)
        if secret is None:
            return False
        expected = hmac.new(secret, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def _sign(key_id: str, document: dict[str, Any]) -> SignedAttestationEnvelope:
    secret = SECRETS[key_id]
    message = json.dumps(document, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return SignedAttestationEnvelope(
        document=document, signature=signature, key_id=key_id
    )


CANDIDATE_DOCUMENT: dict[str, Any] = {
    "schema": "CandidateArtifact.v1",
    "facility": "dotmac-deployment-foundation",
    "version": "0.4.0a2",
    "sha256": "b" * 64,
    "source_sha": "c" * 40,
    "repository": "michaelayoade/dotmac_starter_mt",
    "run_id": "111",
    "artifact_id": "222",
}

INSTALLED_DOCUMENT: dict[str, Any] = {
    "schema": "InstalledHostObservation.v1",
    "distribution": "dotmac-deployment-foundation",
    "version": "0.4.0a2",
    "sha256": "b" * 64,
    "observed_at": "2026-09-08T00:00:00Z",
}


def _roots() -> DistinctTrustRoots:
    return DistinctTrustRoots(
        candidate=AttestationTrustRoot(
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
            accepted_key_ids=frozenset({CANDIDATE_KEY}),
        ),
        installed=AttestationTrustRoot(
            purpose=INSTALLED_OBSERVATION_PURPOSE,
            accepted_key_ids=frozenset({INSTALLED_KEY}),
        ),
    )


def _genuine_pair() -> tuple[SignedAttestationEnvelope, SignedAttestationEnvelope]:
    return _sign(CANDIDATE_KEY, dict(CANDIDATE_DOCUMENT)), _sign(
        INSTALLED_KEY, dict(INSTALLED_DOCUMENT)
    )


# ── admit control ────────────────────────────────────────────────────────────


def test_a_genuine_two_authority_pair_is_admitted() -> None:
    """Two DIFFERENT keys, each accepted only by its OWN trust root, each
    producing a real HMAC over its own canonical document: this is the first
    thing this chain has ever been able to prove real rather than
    fixture-shaped, within the honest limit stated in this file's docstring —
    the secrets are real, the roots are disjoint, and nothing here is a
    caller-constructible "already verified" dataclass.
    """
    candidate, installed = _genuine_pair()
    verifier = HMACVerifier()

    binding = verify_trusted_host_source(
        candidate=candidate,
        candidate_verifier=verifier,
        installed=installed,
        installed_verifier=verifier,
        trust_roots=_roots(),
    )

    assert binding.candidate_key_id == CANDIDATE_KEY
    assert binding.installed_key_id == INSTALLED_KEY
    assert binding.candidate_receipt.facility == "dotmac-deployment-foundation"
    assert binding.installed_observation.distribution == "dotmac-deployment-foundation"
    assert str(binding.candidate_receipt.artifact_digest) == str(
        binding.installed_observation.artifact_digest
    )


# ── the one-caller negative control — the single most important test here ──


def test_one_key_signing_both_attestations_is_refused_though_each_verifies() -> None:
    """ONE key, held by one party, signs BOTH documents. Each individual
    signature is genuinely valid HMAC output — `HMACVerifier.verify` would
    accept either one alone. The pair is still refused, because a candidate
    attestation and an installed-host observation signed by the same key are
    one party's word twice, not two independent readings. This is the exact
    shape both prior admission attempts on this chain had (a caller
    constructing both halves of the comparison itself) in different clothing,
    and it is refused BEFORE either key is checked against a trust root, so
    the refusal does not depend on `DistinctTrustRoots` having been
    configured correctly elsewhere.
    """
    candidate = _sign(CANDIDATE_KEY, dict(CANDIDATE_DOCUMENT))
    installed = _sign(CANDIDATE_KEY, dict(INSTALLED_DOCUMENT))  # same key, wrong slot
    verifier = HMACVerifier()

    with pytest.raises(PreconditionFailed) as excinfo:
        verify_trusted_host_source(
            candidate=candidate,
            candidate_verifier=verifier,
            installed=installed,
            installed_verifier=verifier,
            trust_roots=_roots(),
        )
    assert excinfo.value.code == SAME_KEY_SIGNED_BOTH


def test_the_near_miss_of_two_distinct_keys_stays_silent() -> None:
    """The sensitivity proof's other half: the SAME two documents, signed by
    the two DIFFERENT keys the trust roots actually name, must NOT trip
    `SAME_KEY_SIGNED_BOTH` — proving the check discriminates on same-vs-
    distinct authorship rather than refusing every pair unconditionally.
    """
    candidate, installed = _genuine_pair()
    verifier = HMACVerifier()

    binding = verify_trusted_host_source(
        candidate=candidate,
        candidate_verifier=verifier,
        installed=installed,
        installed_verifier=verifier,
        trust_roots=_roots(),
    )
    assert binding.candidate_key_id != binding.installed_key_id


# ── distinct trust roots — construction-time, not merely at call time ──────


def test_two_trust_roots_that_share_an_accepted_key_refuse_at_construction() -> None:
    """The PLANT: a candidate root and an installed root that both accept
    `"shared-key"`. Two policies that could both be satisfied by one signer
    are one trust root wearing two names, and this is refused before any
    envelope is even looked at — `verify_trusted_host_source` never runs.
    """
    with pytest.raises(SpecError) as excinfo:
        DistinctTrustRoots(
            candidate=AttestationTrustRoot(
                purpose=CANDIDATE_ATTESTATION_PURPOSE,
                accepted_key_ids=frozenset({"shared-key"}),
            ),
            installed=AttestationTrustRoot(
                purpose=INSTALLED_OBSERVATION_PURPOSE,
                accepted_key_ids=frozenset({"shared-key"}),
            ),
        )
    assert excinfo.value.code == TRUST_ROOTS_NOT_DISTINCT


def test_two_trust_roots_with_disjoint_keys_construct_cleanly() -> None:
    """The NEAR-MISS for the check above: disjoint accepted-key sets
    construct without incident. Proven, not assumed — `_roots()` is called by
    every admitting test in this file, so this is exercised, not merely
    plausible.
    """
    roots = _roots()
    assert roots.candidate.accepted_key_ids.isdisjoint(roots.installed.accepted_key_ids)


def test_a_root_declaring_the_wrong_purpose_in_the_candidate_slot_is_refused() -> None:
    """A root minted for the installed-observation slot must not be usable in
    the candidate slot, however its accepted keys are configured.
    """
    with pytest.raises(SpecError) as excinfo:
        DistinctTrustRoots(
            candidate=AttestationTrustRoot(
                purpose=INSTALLED_OBSERVATION_PURPOSE,  # wrong slot, deliberately
                accepted_key_ids=frozenset({CANDIDATE_KEY}),
            ),
            installed=AttestationTrustRoot(
                purpose=INSTALLED_OBSERVATION_PURPOSE,
                accepted_key_ids=frozenset({INSTALLED_KEY}),
            ),
        )
    assert excinfo.value.code == ROOT_PURPOSE_MISMATCH


# ── absence — the "gate before the first effect" arm ────────────────────────


def test_absent_candidate_attestation_is_refused() -> None:
    _, installed = _genuine_pair()
    verifier = HMACVerifier()
    with pytest.raises(PreconditionFailed) as excinfo:
        verify_trusted_host_source(
            candidate=None,
            candidate_verifier=verifier,
            installed=installed,
            installed_verifier=verifier,
            trust_roots=_roots(),
        )
    assert excinfo.value.code == OBSERVATION_ABSENT


def test_absent_installed_observation_is_refused() -> None:
    candidate, _ = _genuine_pair()
    verifier = HMACVerifier()
    with pytest.raises(PreconditionFailed) as excinfo:
        verify_trusted_host_source(
            candidate=candidate,
            candidate_verifier=verifier,
            installed=None,
            installed_verifier=verifier,
            trust_roots=_roots(),
        )
    assert excinfo.value.code == OBSERVATION_ABSENT


# ── forged evidence — a valid-shaped document, no valid signature ──────────


def test_a_document_edited_after_signing_fails_signature_verification() -> None:
    """The PLANT: the candidate document is tampered with AFTER signing (the
    digest field is changed), so the stale signature no longer covers it —
    this is what "forged evidence" looks like against a real MAC: not a
    missing signature, a signature over different bytes than are presented.
    """
    candidate, installed = _genuine_pair()
    tampered_document = dict(candidate.document)
    tampered_document["sha256"] = "f" * 64
    forged = SignedAttestationEnvelope(
        document=tampered_document,
        signature=candidate.signature,
        key_id=candidate.key_id,
    )
    verifier = HMACVerifier()

    with pytest.raises(PreconditionFailed) as excinfo:
        verify_trusted_host_source(
            candidate=forged,
            candidate_verifier=verifier,
            installed=installed,
            installed_verifier=verifier,
            trust_roots=_roots(),
        )
    assert excinfo.value.code == VERIFY_SIGNATURE_INVALID


def test_a_valid_signature_from_an_untrusted_key_is_refused() -> None:
    """A key nobody's trust root names can still produce a genuinely valid
    HMAC over the canonical bytes — `HMACVerifier.verify` returns `True` for
    it. Refused anyway: a valid signature from a stranger is still a
    stranger.
    """
    _, installed = _genuine_pair()
    rogue = _sign(ROGUE_KEY, dict(CANDIDATE_DOCUMENT))
    verifier = HMACVerifier()

    with pytest.raises(PreconditionFailed) as excinfo:
        verify_trusted_host_source(
            candidate=rogue,
            candidate_verifier=verifier,
            installed=installed,
            installed_verifier=verifier,
            trust_roots=_roots(),
        )
    assert excinfo.value.code == KEY_NOT_TRUSTED


# ── disagreement — two authentic, independently signed, conflicting claims ──


def test_two_authentically_signed_attestations_about_different_bytes_disagree() -> None:
    """Both signatures verify, both keys are trusted, both keys differ — and
    the two authorities describe DIFFERENT artifact digests. Neither prior
    attempt on this chain ever reached this arm, because neither ever
    achieved two independently authenticated readings to compare.
    """
    candidate = _sign(CANDIDATE_KEY, dict(CANDIDATE_DOCUMENT))
    disagreeing_document = dict(INSTALLED_DOCUMENT)
    disagreeing_document["sha256"] = "e" * 64
    installed = _sign(INSTALLED_KEY, disagreeing_document)
    verifier = HMACVerifier()

    with pytest.raises(PreconditionFailed) as excinfo:
        verify_trusted_host_source(
            candidate=candidate,
            candidate_verifier=verifier,
            installed=installed,
            installed_verifier=verifier,
            trust_roots=_roots(),
        )
    assert excinfo.value.code == ATTESTATIONS_DISAGREE


# ── envelope parsing refuses a document that is not a mapping ──────────────


def test_a_stringified_document_is_refused_rather_than_silently_restringified() -> None:
    """The exact corruption `evidence.SignedEvidenceEnvelope`'s own docstring
    recounts (a document flattened to its Python `repr` between disk and
    verifier, so a signature ends up checked over a restatement) is refused
    at construction here for the identical reason.
    """
    with pytest.raises(SpecError) as excinfo:
        SignedAttestationEnvelope(
            document="{'schema': 'CandidateArtifact.v1'}",
            signature="deadbeef",
            key_id=CANDIDATE_KEY,
        )
    assert excinfo.value.code == OBSERVATION_MALFORMED
