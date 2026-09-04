"""``IncumbentPrestateDigestV1`` — the value with no producer until now.

## The gap this closes

Control's `RecoveryGrantStatementV1` carries `incumbent_prestate_digest` as a
SIGNED term, and its `RecoverySubject` requires a caller to state one. Measured
at the peeled `0.1.0a12` tag, **Control never computes it**: no canonicalizer, no
hash, only storage and comparison, refusing with `PRESTATE_MISMATCH`.

So the value existed in the contract with **no authority computing it on either
side** — the exact asymmetry `ExecutionPlanDigestV1` was created to fix. Two
sides computing it independently would diverge for the same reason `plan_digest`
did, and the failure would be a mismatch that told nobody anything.

The ruled split: Foundation defines the bytes and the function, Platform's
INSTALLED ADAPTER computes it, Control stores/signs/compares and implements no
second canonicalizer.

## The two test clauses do distinct work

**Mutating every bound field** proves each is genuinely inside the canonical
bytes rather than merely present in the document. A field that is in
`as_document()` but not in the digest is a term Control signs that nothing
protects.

**Exchanging document and digest independently** proves the comparison is not
passing on identity. A test that only ever moves both together cannot tell a real
comparison from `x == x` — because recomputing a digest from a document and
comparing it with itself passes for every input.
"""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.execution_plan import (
    EXECUTION_PLAN_SCHEMA,
    HostPrestateV1,
)
from dotmac_deployment_foundation.recovery_plan import (
    INCUMBENT_PRESTATE_DIGEST_SCHEMA,
    INCUMBENT_PRESTATE_DISCRIMINATOR,
    KNOWN_PRESTATE_DISCRIMINATORS,
    PRESTATE_MISMATCH,
    PRESTATE_SCHEMA,
    PRESTATE_UNDISCRIMINATED,
    PRESTATE_UNKNOWN_DISCRIMINATOR,
    RECOVERY_PLAN_SCHEMA,
    FailedSystemObservationV1,
    canonical_prestate_bytes,
    incumbent_prestate_digest,
    require_incumbent_prestate_digest,
)

A = "sha256:" + "a" * 64
C = "sha256:" + "c" * 64

#: Every field the digest binds, written LONGHAND. Derived from the dataclass it
#: would agree with the type for every input, including the input where somebody
#: dropped a field out of `as_document()`.
BOUND_FIELDS = ("target", "roles", "observed_descriptor_digest")


def _observation(**over) -> FailedSystemObservationV1:
    kwargs = {
        "target": "prod-lagos-01",
        "roles": HostPrestateV1(roles=(("app", C),)),
        "observed_descriptor_digest": A,
    }
    kwargs.update(over)
    return FailedSystemObservationV1(**kwargs)


# ── every bound field is genuinely INSIDE the bytes ────────────────────────


@pytest.mark.parametrize("field", BOUND_FIELDS)
def test_mutating_any_bound_field_moves_the_digest(field: str) -> None:
    """A field present in the document but absent from the digest is a term
    Control SIGNS that nothing protects."""
    mutated = {
        "target": "prod-abuja-02",
        "roles": HostPrestateV1(roles=(("app", "sha256:" + "d" * 64),)),
        "observed_descriptor_digest": "sha256:" + "e" * 64,
    }[field]
    assert _observation(**{field: mutated}).digest() != _observation().digest()


def test_the_bound_field_list_is_the_whole_document() -> None:
    """The parametrisation above is only as good as its extent. Every key in the
    document except the schema label is a bound field, and a new one appearing
    fails here rather than going unmutated and unproven."""
    keys = set(_observation().as_document()) - {"schema"}
    assert keys == set(BOUND_FIELDS)


def test_an_empty_roles_observation_still_digests_distinctly() -> None:
    """ "No role containers are running" is a claim about a failed system, not an
    absence, so it must be a different digest rather than the same one."""
    empty = _observation(roles=HostPrestateV1.first_deploy())
    assert empty.digest() != _observation().digest()


# ── the comparison is not passing on identity ─────────────────────────────


def test_the_matching_pair_is_admitted() -> None:
    """The positive control. Without it every refusal below could come from a
    function that refuses everything."""
    one = _observation()
    assert (
        require_incumbent_prestate_digest(
            one, authorized=one.digest(), discriminator=INCUMBENT_PRESTATE_DISCRIMINATOR
        )
        == one.digest()
    )


def test_document_A_against_digest_B_refuses() -> None:
    """Half the independent exchange. A comparison that recomputed from the
    document and compared it with ITSELF would pass here for every input — which
    is exactly the `x == x` failure this clause exists to detect."""
    other = _observation(target="prod-abuja-02")
    with pytest.raises(PreconditionFailed) as exc:
        require_incumbent_prestate_digest(
            _observation(),
            authorized=other.digest(),
            discriminator=INCUMBENT_PRESTATE_DISCRIMINATOR,
        )
    assert exc.value.code == PRESTATE_MISMATCH


def test_document_B_against_digest_A_refuses() -> None:
    """The other half, driven the opposite way. One direction alone can be
    satisfied by a check that always compares against the same operand."""
    one = _observation()
    other = _observation(target="prod-abuja-02")
    with pytest.raises(PreconditionFailed) as exc:
        require_incumbent_prestate_digest(
            other,
            authorized=one.digest(),
            discriminator=INCUMBENT_PRESTATE_DISCRIMINATOR,
        )
    assert exc.value.code == PRESTATE_MISMATCH


def test_moving_BOTH_together_is_not_a_pass_against_the_original() -> None:
    """The subtler half. A test that only ever mutates document and digest
    together proves nothing: they agree with each other by construction. What
    must still refuse is that pair measured against the ORIGINAL authorization.
    """
    original = _observation().digest()
    moved = _observation(target="prod-abuja-02")
    assert require_incumbent_prestate_digest(
        moved, authorized=moved.digest(), discriminator=INCUMBENT_PRESTATE_DISCRIMINATOR
    )
    with pytest.raises(PreconditionFailed) as exc:
        require_incumbent_prestate_digest(
            moved, authorized=original, discriminator=INCUMBENT_PRESTATE_DISCRIMINATOR
        )
    assert exc.value.code == PRESTATE_MISMATCH


# ── one canonicalizer, and the document is its own kind ───────────────────


def test_the_prestate_is_a_standalone_canonical_document() -> None:
    """Control signs a digest of THIS observation, never of the plan containing
    it — so it carries its own schema and can be canonicalized alone. A fragment
    has no kind, and the shared core's guard is what stops one document kind
    being hashed as another."""
    assert _observation().as_document()["schema"] == PRESTATE_SCHEMA
    assert canonical_prestate_bytes(_observation().as_document())


@pytest.mark.parametrize("foreign", [EXECUTION_PLAN_SCHEMA, RECOVERY_PLAN_SCHEMA])
def test_another_document_kind_is_refused_by_the_prestate_canonicalizer(
    foreign: str,
) -> None:
    document = _observation().as_document()
    document["schema"] = foreign
    with pytest.raises(SpecError):
        canonical_prestate_bytes(document)


def test_the_value_schema_is_not_the_document_schema() -> None:
    """Two names for two things — whoever handles the digest needs a word for it
    that is not the word for a document they never parse. Control is exactly
    that party: it stores and signs the value and has no canonicalizer."""
    assert INCUMBENT_PRESTATE_DIGEST_SCHEMA == "IncumbentPrestateDigestV1"
    assert PRESTATE_SCHEMA == "FailedSystemObservationV1"
    assert INCUMBENT_PRESTATE_DIGEST_SCHEMA != PRESTATE_SCHEMA


def test_the_digest_is_a_pure_function_of_the_observation() -> None:
    """Platform's installed adapter computes it and Control compares it. Two
    parties reaching different values for one observation is the divergence this
    binding exists to prevent."""
    assert _observation().digest() == _observation().digest()
    assert incumbent_prestate_digest(_observation().as_document()) == (
        _observation().digest()
    )


def test_the_prestate_digest_is_not_the_recovery_plan_digest() -> None:
    """Control carries both as separate signed terms and refuses each with its
    own code. Conflating them would make one approval cover the other."""
    from tests.unit.test_deployment_foundation_execution_plan_v2 import _recovery

    plan = _recovery()
    assert plan.failed_state.digest() != plan.digest()


# ── the discriminator: a digest cannot say which encoding produced it ──────


def test_an_undiscriminated_row_is_historical_and_unexecutable() -> None:
    """Never backfilled as V1 by assumption.

    An older stored row predates the term, so nobody produced its digest under
    rules anyone can name. Assuming the current discriminator would manufacture
    provenance for a value whose provenance is exactly what is missing — the
    defect this term exists to close, re-created by a migration.
    """
    one = _observation()
    with pytest.raises(PreconditionFailed) as exc:
        require_incumbent_prestate_digest(
            one, authorized=one.digest(), discriminator=""
        )
    assert exc.value.code == PRESTATE_UNDISCRIMINATED


def test_an_unknown_discriminator_refuses() -> None:
    """The repair is a VERSION, not a re-observation: comparing under rules this
    facility does not have is not comparing."""
    one = _observation()
    with pytest.raises(PreconditionFailed) as exc:
        require_incumbent_prestate_digest(
            one,
            authorized=one.digest(),
            discriminator="dotmac.deployment_foundation.incumbent_prestate.v9",
        )
    assert exc.value.code == PRESTATE_UNKNOWN_DISCRIMINATOR


def test_the_correct_discriminator_with_a_wrong_digest_still_refuses() -> None:
    """The discriminator does not vouch for the value. Naming the right encoding
    and carrying the wrong digest is still the wrong incumbent."""
    one = _observation()
    with pytest.raises(PreconditionFailed) as exc:
        require_incumbent_prestate_digest(
            one,
            authorized="sha256:" + "9" * 64,
            discriminator=INCUMBENT_PRESTATE_DISCRIMINATOR,
        )
    assert exc.value.code == PRESTATE_MISMATCH


def test_the_three_prestate_refusals_are_distinct() -> None:
    """Undiscriminated, unknown encoding, and wrong value send an operator to
    three different places: a historical row, a version, and the host."""
    assert (
        len(
            {
                PRESTATE_UNDISCRIMINATED,
                PRESTATE_UNKNOWN_DISCRIMINATOR,
                PRESTATE_MISMATCH,
            }
        )
        == 3
    )


def test_the_discriminator_names_the_schema_and_is_closed() -> None:
    """It names the observation schema AND the rules that turned it into bytes.
    A consumer accepting any string would trust a producer it has never met to
    have used rules it cannot check."""
    assert INCUMBENT_PRESTATE_DISCRIMINATOR == (
        "dotmac.deployment_foundation.incumbent_prestate.v1"
    )
    assert KNOWN_PRESTATE_DISCRIMINATORS == {INCUMBENT_PRESTATE_DISCRIMINATOR}
