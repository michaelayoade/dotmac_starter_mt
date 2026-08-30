"""Release evidence must be signed, and it must be OURS.

The gate this replaces read a JSON file and checked it was **non-empty**. So
"CI accepted this revision" was satisfied by any writable file containing
`{"<revision>": {"x": "y"}}`. The step was named `verify_release_evidence` and
verified that a file existed.

## The fork case is the one to read first

All seven Dotmac repositories are `all_external_contributors`, which stops fork
code **executing**. It does nothing about evidence being **admitted**: a fork's
run is real, green, and describes a real commit. GitHub hands over the
discriminator for free — `repository_id` is where the workflow lives,
`head_repository_id` is where the branch lives, and they differ exactly when
the run came from a fork.

`test_a_fork_run_is_refused` uses a **validly signed** document, because the
interesting failure is the one where every other check passes.

## Order matters, and is asserted

Content is checked only after the signature verifies. Refusing on content first
would let someone probe which repository, ref or conclusion this facility
accepts using documents they never had to sign —
`test_content_is_not_judged_before_the_signature` pins that.
"""

from __future__ import annotations

import json

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.evidence import (
    RELEASE_EVIDENCE_SCHEMA,
    ReleaseEvidenceV1,
    TrustPolicy,
    accept_release_evidence,
)

REVISION = "a" * 40
REPO = "michaelayoade/dotmac_starter_mt"
KEY = "release-signer-1"


class FakeVerifier:
    """Accepts one exact (key_id, message) pair. Records what it was asked."""

    def __init__(self, *, good: bytes | None = None, explode: bool = False) -> None:
        self.good = good
        self.explode = explode
        self.calls: list[bytes] = []

    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool:
        self.calls.append(message)
        if self.explode:
            raise RuntimeError("verifier exploded")
        return signature == "valid" and (self.good is None or message == self.good)


def _policy(**overrides: object) -> TrustPolicy:
    fields: dict[str, object] = {
        "accepted_key_ids": frozenset({KEY}),
        "repository": REPO,
    }
    fields.update(overrides)
    return TrustPolicy(**fields)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> ReleaseEvidenceV1:
    fields: dict[str, object] = {
        "revision": REVISION,
        "repository": REPO,
        "repository_id": "1001",
        "head_repository_id": "1001",
        "ref": "refs/heads/main",
        "run_id": "33326824657",
        "workflow": "ci.yml",
        "conclusion": "success",
    }
    fields.update(overrides)
    return ReleaseEvidenceV1(**fields)  # type: ignore[arg-type]


def _envelope(evidence: ReleaseEvidenceV1, *, signature: str = "valid", key: str = KEY):  # type: ignore[no-untyped-def]
    return {
        "document": evidence.as_document(),
        "signature": signature,
        "key_id": key,
    }


def _accept(payload: object, **overrides: object) -> ReleaseEvidenceV1:
    kwargs: dict[str, object] = {
        "revision": REVISION,
        "policy": _policy(),
        "verifier": FakeVerifier(),
    }
    kwargs.update(overrides)
    return accept_release_evidence(payload, **kwargs)  # type: ignore[arg-type]


# ── the positive control ────────────────────────────────────────────────────


def test_well_formed_signed_evidence_is_accepted() -> None:
    """Without this, refusing everything would score full marks below."""
    assert _accept(_envelope(_evidence())).run_id == "33326824657"


# ── signature and signer ────────────────────────────────────────────────────


def test_no_verifier_refuses_rather_than_skipping() -> None:
    """Degrading to trust when the verifier is missing is a bypass anyone can
    trigger by deleting configuration."""
    with pytest.raises(PreconditionFailed, match="no signature verifier"):
        _accept(_envelope(_evidence()), verifier=None)


def test_unsigned_evidence_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="carries no signature"):
        _accept(_envelope(_evidence(), signature=""))


def test_a_bad_signature_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="does not verify"):
        _accept(_envelope(_evidence(), signature="forged"))


def test_a_valid_signature_from_an_unaccepted_signer_is_refused() -> None:
    """A valid signature from a stranger is still a stranger."""
    with pytest.raises(PreconditionFailed, match="not an accepted signer"):
        _accept(_envelope(_evidence(), key="somebody-else"))


def test_a_verifier_that_raises_is_a_refusal_not_a_crash() -> None:
    with pytest.raises(PreconditionFailed, match="verification failed"):
        _accept(_envelope(_evidence()), verifier=FakeVerifier(explode=True))


def test_the_signature_covers_canonical_bytes_not_raw_file_bytes() -> None:
    """Re-serialising with different whitespace must not change standing."""
    evidence = _evidence()
    verifier = FakeVerifier(good=evidence.canonical_bytes())
    envelope = json.loads(json.dumps(_envelope(evidence), indent=4))
    assert _accept(envelope, verifier=verifier).revision == REVISION
    assert verifier.calls == [evidence.canonical_bytes()]


def test_an_edited_document_no_longer_matches_its_signature() -> None:
    """The tamper case, driven through the real canonical-bytes comparison."""
    verifier = FakeVerifier(good=_evidence().canonical_bytes())
    tampered = _envelope(_evidence(conclusion="success", run_id="99"))
    with pytest.raises(PreconditionFailed, match="does not verify"):
        _accept(tampered, verifier=verifier)


# ── the fork-head check ─────────────────────────────────────────────────────


def test_a_fork_run_is_refused() -> None:
    """Validly signed, green, real — and from somebody else's branch.

    `all_external_contributors` stops fork code EXECUTING; it does nothing
    about fork evidence being ADMITTED.
    """
    with pytest.raises(PreconditionFailed, match="FORK run"):
        _accept(_envelope(_evidence(head_repository_id="2002")))


def test_the_fork_predicate_is_the_id_comparison() -> None:
    assert not _evidence().from_a_fork
    assert _evidence(head_repository_id="2002").from_a_fork


def test_evidence_from_another_repository_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="comes from"):
        _accept(_envelope(_evidence(repository="someone/else")))


# ── protected refs, conclusion, revision binding ────────────────────────────


def test_a_green_run_on_an_unprotected_branch_is_refused() -> None:
    """A branch build never had the merge gate enforced."""
    with pytest.raises(PreconditionFailed, match="not one of the protected refs"):
        _accept(_envelope(_evidence(ref="refs/heads/feature-x")))


def test_a_protected_ref_named_by_policy_is_accepted() -> None:
    """Positive control for the ref check."""
    policy = _policy(protected_refs=frozenset({"refs/heads/release"}))
    assert _accept(_envelope(_evidence(ref="refs/heads/release")), policy=policy)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", ""])
def test_a_non_success_conclusion_is_refused(conclusion: str) -> None:
    with pytest.raises(PreconditionFailed, match="conclusion is"):
        _accept(_envelope(_evidence(conclusion=conclusion)))


def test_evidence_for_another_revision_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="vouches for"):
        _accept(_envelope(_evidence()), revision="b" * 40)


# ── ordering: signature before content ──────────────────────────────────────


def test_content_is_not_judged_before_the_signature() -> None:
    """An unsigned document must not reveal which content would be accepted.

    The envelope below is wrong in BOTH ways — bad signature and a fork head.
    The signature failure must be the one reported, or an attacker can probe
    accepted values using documents they never had to sign.
    """
    with pytest.raises(PreconditionFailed, match="does not verify"):
        _accept(_envelope(_evidence(head_repository_id="2002"), signature="forged"))


# ── the policy itself must be a policy ──────────────────────────────────────


def test_an_empty_signer_set_is_refused() -> None:
    """ "No configured signers" must never be the most permissive setting."""
    with pytest.raises(SpecError, match="accepted_key_ids is empty"):
        _policy(accepted_key_ids=frozenset())


def test_an_empty_protected_ref_set_is_refused() -> None:
    with pytest.raises(SpecError, match="protected_refs is empty"):
        _policy(protected_refs=frozenset())


# ── document parsing ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field", ["revision", "repository_id", "head_repository_id", "ref"]
)
def test_a_missing_required_field_is_refused(field: str) -> None:
    """An absent field must not read as a satisfied one."""
    document = _evidence().as_document()
    del document[field]
    with pytest.raises(SpecError, match="missing required field"):
        _accept({"document": document, "signature": "valid", "key_id": KEY})


def test_a_wrong_schema_is_refused() -> None:
    document = _evidence().as_document()
    document["schema"] = "ReleaseEvidence.v2"
    with pytest.raises(SpecError, match="schema is"):
        _accept({"document": document, "signature": "valid", "key_id": KEY})


def test_a_short_revision_is_refused() -> None:
    document = _evidence().as_document()
    document["revision"] = "abc123"
    with pytest.raises(SpecError, match="40-hex"):
        _accept({"document": document, "signature": "valid", "key_id": KEY})


def test_the_schema_constant_is_in_the_document() -> None:
    assert _evidence().as_document()["schema"] == RELEASE_EVIDENCE_SCHEMA
