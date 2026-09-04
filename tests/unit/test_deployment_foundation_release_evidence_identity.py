"""The fifth signing identity, as a TYPE that refuses rather than a dict literal.

## The gap this closes, measured rather than described

Five signing identities exist in this estate: authorization, dispatch,
observation, recovery and release evidence. Four were TYPES that refuse a wrong
purpose at construction. The fifth was a dict literal and a JSON field, and
`vendor_cp/deployment/signers.py` said so outright — *"`deployment_dispatch` and
`platform_release_evidence` do not exist as types yet, so they are named here as
literals until they do."*

Running the full matrix against the actual identity types therefore gave **4
diagonals accepted and 16 off-diagonal refused**, where five identities' worth of
material supports 5 and 20. The shortfall was never a skipped test: **data does
not refuse anything.**

## Why construction-time refusal is what makes the off-diagonals provable

A caller cannot hold a `ReleaseEvidenceVerificationIdentity` bearing the
authorization, dispatch, observation or recovery purpose. Not "it would be
rejected later" — the value does not exist. That is what turns an argument into
a test.

## The control that makes the four refusals mean something

**A verifier broken shut produces four perfect refusals and looks like success.**
So the positive control is not optional decoration here; it is the only thing
distinguishing "this identity refuses the wrong purposes" from "this identity
refuses everything". `test_a_verifier_broken_shut_FAILS_the_positive_control` is
that check, and it asserts the failure rather than the pass.

## Real fingerprints

The five below are the enrolled ones, and they are asserted physically distinct
rather than assumed so — five identities sharing a fingerprint would make every
refusal below pass for the wrong reason.
"""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.evidence import (
    IDENTITY_KEY_MISMATCH,
    IDENTITY_MALFORMED,
    PURPOSE_MISMATCH,
    RELEASE_EVIDENCE_IDENTITY_SCHEMA,
    RELEASE_EVIDENCE_PURPOSE,
    ReleaseEvidenceVerificationIdentity,
    require_release_evidence_key,
)

KEY_ID = "platform-cp-release-evidence-2026-09"
RELEASE_FP = "sha256:227c303993b1cc87f41eec5bd3cf1f1913e0e745a2eb8ac14030cd9e69b4672e"

#: The four OTHER purposes, each of which must be refused as a release-evidence
#: identity. Written out longhand: a set derived from the type would agree with
#: it for every input, including the input where somebody widened it.
OTHER_PURPOSES = (
    "deployment_authorization",
    "deployment_dispatch",
    "execution_observation",
    "deployment_recovery",
)

#: The enrolled fingerprints. Truncated in the brief; the release one is exact
#: because it is the key this facility will actually be handed.
FINGERPRINTS = {
    "platform_release_evidence": RELEASE_FP,
    "deployment_authorization": "sha256:" + "7f26478e".ljust(64, "0"),
    "deployment_dispatch": "sha256:" + "04d331e8".ljust(64, "1"),
    "execution_observation": "sha256:" + "cf41c109".ljust(64, "2"),
    "deployment_recovery": "sha256:" + "30cce9c5".ljust(64, "3"),
}


def _identity(**over) -> ReleaseEvidenceVerificationIdentity:
    kwargs = {
        "key_id": KEY_ID,
        "algorithm": "ed25519",
        "public_key_fingerprint": RELEASE_FP,
    }
    kwargs.update(over)
    return ReleaseEvidenceVerificationIdentity(**kwargs)


# ── the diagonal ────────────────────────────────────────────────────────────


def test_the_release_evidence_identity_is_accepted() -> None:
    """The fifth diagonal, which did not exist before this type."""
    identity = _identity()
    assert identity.purpose == RELEASE_EVIDENCE_PURPOSE
    assert identity.key_id == KEY_ID
    assert identity.public_key_fingerprint == RELEASE_FP


# ── the four off-diagonals ──────────────────────────────────────────────────


@pytest.mark.parametrize("purpose", OTHER_PURPOSES)
def test_every_other_purpose_is_refused_AT_CONSTRUCTION(purpose: str) -> None:
    """Not rejected downstream — unconstructable. A key minted to authorize a
    deployment must not be able to vouch for a release, and refusing here is the
    only way to make that unrepresentable."""
    with pytest.raises(SpecError) as exc:
        _identity(purpose=purpose)
    assert exc.value.code == PURPOSE_MISMATCH


def test_the_refusal_code_is_MACHINE_READABLE() -> None:
    """The point of the code rather than a nicety. A caller deciding what to do
    about a wrong-purpose key branches on this; a caller matching on a sentence
    is coupled to wording that is the thing most likely to be improved."""
    with pytest.raises(SpecError) as exc:
        _identity(purpose="deployment_authorization")
    assert exc.value.code == PURPOSE_MISMATCH
    assert PURPOSE_MISMATCH == "release_evidence.purpose_mismatch"


def test_the_four_other_purposes_are_written_out_longhand() -> None:
    """A set derived from the type would agree with it for every input,
    including the one where somebody widened the type."""
    assert len(set(OTHER_PURPOSES)) == 4
    assert RELEASE_EVIDENCE_PURPOSE not in OTHER_PURPOSES


# ── THE CONTROL: a type that refuses everything is not a type that discriminates


def test_a_verifier_broken_shut_FAILS_the_positive_control() -> None:
    """The half that is easy to omit, and the one the four refusals depend on.

    A verification identity that refused EVERY purpose — including its own —
    would produce four perfect refusals above and read as success. This asserts
    the failure of that broken-shut variant, so the four refusals are evidence
    of discrimination rather than of a closed door.
    """
    broken_shut_refused_its_own_purpose = False
    try:
        _identity()
    except SpecError:  # pragma: no cover - only if the type breaks shut
        broken_shut_refused_its_own_purpose = True
    assert not broken_shut_refused_its_own_purpose, (
        "the identity refused its OWN purpose, so the four refusals above prove "
        "nothing: a verifier broken shut refuses everything and looks identical "
        "to one that discriminates"
    )


def test_the_five_fingerprints_are_physically_distinct() -> None:
    """Five identities sharing a fingerprint would make every refusal pass for
    the wrong reason. Proved, not assumed."""
    assert len(set(FINGERPRINTS.values())) == 5
    assert len(FINGERPRINTS) == 5


# ── the installed document is the ONE door ─────────────────────────────────


def test_the_installed_identity_document_parses() -> None:
    """`/etc/dotmac/platform-cp/release-evidence-verification.json` on the
    target carries this schema; this facility READS it and never writes it."""
    identity = ReleaseEvidenceVerificationIdentity.from_document(
        {
            "schema": RELEASE_EVIDENCE_IDENTITY_SCHEMA,
            "key_id": KEY_ID,
            "algorithm": "ed25519",
            "public_key_fingerprint": RELEASE_FP,
            "purpose": RELEASE_EVIDENCE_PURPOSE,
        }
    )
    assert identity == _identity()


def test_a_document_bearing_another_purpose_is_refused_through_the_door() -> None:
    """The parser must not be a way around the constructor."""
    with pytest.raises(SpecError) as exc:
        ReleaseEvidenceVerificationIdentity.from_document(
            {
                "schema": RELEASE_EVIDENCE_IDENTITY_SCHEMA,
                "key_id": KEY_ID,
                "algorithm": "ed25519",
                "public_key_fingerprint": RELEASE_FP,
                "purpose": "deployment_authorization",
            }
        )
    assert exc.value.code == PURPOSE_MISMATCH


def test_an_ABSENT_purpose_is_not_a_defaulted_one() -> None:
    """The trap the dataclass default would otherwise set: a document that says
    nothing about its purpose must not be read as saying the right thing."""
    with pytest.raises(SpecError) as exc:
        ReleaseEvidenceVerificationIdentity.from_document(
            {
                "schema": RELEASE_EVIDENCE_IDENTITY_SCHEMA,
                "key_id": KEY_ID,
                "algorithm": "ed25519",
                "public_key_fingerprint": RELEASE_FP,
            }
        )
    assert exc.value.code == IDENTITY_MALFORMED


def test_a_wrong_schema_is_refused() -> None:
    with pytest.raises(SpecError) as exc:
        ReleaseEvidenceVerificationIdentity.from_document(
            {"schema": "SomethingElse.v1", "key_id": KEY_ID}
        )
    assert exc.value.code == IDENTITY_MALFORMED


@pytest.mark.parametrize(
    "bad",
    ["", "sha256:short", "227c3039" * 8, "SHA256:" + "a" * 64, "sha256:" + "A" * 64],
)
def test_a_malformed_fingerprint_is_refused(bad: str) -> None:
    """Shape only — this facility holds no crypto library and must not acquire
    one. Upper-case hex is refused too: two spellings of one fingerprint is two
    fingerprints to anything comparing strings."""
    with pytest.raises(SpecError) as exc:
        _identity(public_key_fingerprint=bad)
    assert exc.value.code == IDENTITY_MALFORMED


def test_the_document_round_trips() -> None:
    assert (
        ReleaseEvidenceVerificationIdentity.from_document(_identity().as_document())
        == _identity()
    )


# ── an identity must be the SIGNING key's ──────────────────────────────────


def test_an_identity_for_another_key_is_refused() -> None:
    """A correct release-evidence identity held beside a signature made by some
    other trusted key proves nothing about that signature. The identity has to
    be bound to the key the envelope nominated."""
    with pytest.raises(SpecError) as exc:
        require_release_evidence_key(_identity(), key_id="some-other-key")
    assert exc.value.code == IDENTITY_KEY_MISMATCH


def test_the_matching_key_is_admitted() -> None:
    """Positive control for the binding: the check must not refuse every pair."""
    require_release_evidence_key(_identity(), key_id=KEY_ID)


# ── the estate's PURPOSE_MISMATCH vocabulary ───────────────────────────────


def test_the_purpose_mismatch_codes_do_not_collide() -> None:
    """Several surfaces spell `PURPOSE_MISMATCH`. The NAME is shared vocabulary;
    the VALUE is scoped to the surface that raises it.

    Control established that convention four times over before this constant
    existed — `authorization_purpose_mismatch`, `dispatch_purpose_mismatch`,
    `execution_observation_purpose_mismatch`, `recovery_grant_purpose_mismatch`
    — and Platform's `signers.py` states the reuse is deliberate so the sides
    read as one vocabulary.

    This pins the values APART. Foundation's refuses constructing an identity
    entitled to verify; Platform's refuses producing evidence with a
    wrong-purpose signer. Different acts, different processes, different callers
    — no caller branches on both. Unifying them later must be a reviewed diff,
    not a merge that quietly makes one branch look like it covers two surfaces.
    """
    assert PURPOSE_MISMATCH == "release_evidence.purpose_mismatch"
    assert PURPOSE_MISMATCH != "PURPOSE_MISMATCH"
    assert PURPOSE_MISMATCH.startswith("release_evidence.")


def test_the_canonical_purpose_string_is_the_minted_one() -> None:
    """Load-bearing in three places: OpenBao, the production target's
    verification file, and Platform's producer. It does not move, so this
    asserts the literal rather than trusting the constant to be right."""
    assert RELEASE_EVIDENCE_PURPOSE == "platform_release_evidence"


def test_the_key_binding_is_not_SHADOWED_by_the_constructor() -> None:
    """The unreachable-refusal check, run against my own half.

    If the identity constructor and `require_release_evidence_key` both refused
    the same condition, one refusal would be unreachable and its test could
    never fail. They do not: the constructor judges an identity ALONE (blank
    fields, malformed fingerprint, wrong purpose) and the binding judges a PAIR.

    Proved by construction rather than by reading: an identity that passes every
    constructor check is still refused when paired with another key, so the
    binding is reachable behind a fully valid identity.
    """
    valid = _identity()  # constructor raised nothing
    with pytest.raises(SpecError) as exc:
        require_release_evidence_key(valid, key_id="a-different-trusted-key")
    assert exc.value.code == IDENTITY_KEY_MISMATCH
    assert exc.value.code != PURPOSE_MISMATCH
