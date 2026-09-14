"""Stateless advisory protocol: seven types, no workflow/routing authority."""

from __future__ import annotations

import math
from dataclasses import fields
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

import pytest
from dotmac_ai_operations import (
    AIAcknowledgementAttribution,
    AIActionDecision,
    AIActionProposal,
    AIAdvisoryInvocation,
    AIAdvisoryResult,
    AICapabilityExposureRef,
    AIEvidenceBinding,
    AIExecutionObservation,
    AttemptInput,
    InvalidCapabilityIdError,
    InvalidContractDigestError,
)

_VALID_DIGEST_A = "a" * 64
_VALID_DIGEST_B = "b" * 64
_VALID_DIGEST_C = "c" * 64
_VALID_DIGEST_D = "d" * 64

# A FIXED timestamp, not `datetime.now(UTC)`: two independently-built
# default invocations must be able to compare EQUAL (same field values) for
# the `AIAdvisoryResult` full-invocation-equality checks to be testable at
# all — a wall-clock default would make every such comparison fail on
# `context_projection_as_of` alone, for reasons having nothing to do with
# the property under test.
_FIXED_AS_OF = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


class _NoOffsetTzinfo(tzinfo):
    """A non-None tzinfo whose ``utcoffset`` still reports None."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "no-offset"


def _bypass_construct(cls: type, **fields_: Any) -> Any:
    """Build an instance of ``cls`` WITHOUT running ``__post_init__``.

    This module's own docstring documents that its exact-type/validation
    checks are a validation aid, not a tamper-proof boundary: a crafted
    ``pickle``, or ``object.__new__`` paired with ``object.__setattr__``,
    bypasses ``__post_init__`` entirely. This helper simulates exactly that,
    for one purpose only: since a subclass can no longer construct via the
    normal API (its own inherited ``__post_init__`` now rejects it at the
    self-type check), the only way left to exercise a PARENT type's nested
    defense-in-depth check (e.g. ``AIAdvisoryResult`` checking
    ``type(self.invocation)``) is to hand it an already-built crafted value.
    """
    obj = object.__new__(cls)
    for name, value in fields_.items():
        object.__setattr__(obj, name, value)
    return obj


def _field_names(cls: type) -> set[str]:
    return {f.name for f in fields(cls)}


def _capability(
    capability_id: str = "support.customer.identify.v1",
    contract_digest: str = _VALID_DIGEST_A,
) -> AICapabilityExposureRef:
    return AICapabilityExposureRef(
        capability_id=capability_id,
        contract_digest=contract_digest,
    )


class _CapabilitySubclass(AICapabilityExposureRef):
    """A plain subclass, used only to prove exact-type (not isinstance)
    checking: it inherits the base dataclass ``__eq__``, which compares
    only the base fields, so any extra semantic attribute it might carry
    would be invisible to a value comparison. It can no longer be
    constructed via the normal dataclass API (its own self-type check
    rejects it) — nested-check tests use ``_bypass_construct`` instead.
    """


def _evidence_binding(
    *,
    locator_namespace: str = "ticketing",
    locator_ref: str = "ticket:42",
    content_digest: str = _VALID_DIGEST_B,
    media_type: str = "text/plain",
) -> AIEvidenceBinding:
    return AIEvidenceBinding(
        locator_namespace=locator_namespace,
        locator_ref=locator_ref,
        content_digest=content_digest,
        media_type=media_type,
    )


class _EvidenceBindingSubclass(AIEvidenceBinding):
    """A plain subclass, used only to prove exact-type (not isinstance)
    checking on `AIEvidenceBinding` — it can no longer be constructed via
    the normal dataclass API; nested-check tests use `_bypass_construct`.
    """


def _acknowledgement_attribution(
    *,
    actor_namespace: str = "platform-identity",
    actor_type: str = "human",
    actor_ref: str = "user:1",
    acknowledged_at: datetime = _FIXED_AS_OF,
) -> AIAcknowledgementAttribution:
    return AIAcknowledgementAttribution(
        actor_namespace=actor_namespace,
        actor_type=actor_type,
        actor_ref=actor_ref,
        acknowledged_at=acknowledged_at,
    )


class _AcknowledgementAttributionSubclass(AIAcknowledgementAttribution):
    """A plain subclass, used only to prove exact-type (not isinstance)
    checking on `AIAcknowledgementAttribution` — mirrors
    `_EvidenceBindingSubclass`.
    """


def _invocation_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "invocation_ref": "inv-1",
        "interaction_ref": "interaction-1",
        "context_projection_ref": "proj-1",
        "context_projection_digest": _VALID_DIGEST_A,
        "context_projection_as_of": _FIXED_AS_OF,
        "context_sensitivity": "internal",
        "capability": _capability(),
        "execution_policy_version_ref": "policy-v1",
        "execution_policy_digest": _VALID_DIGEST_C,
    }
    kwargs.update(overrides)
    return kwargs


def _invocation(**overrides: Any) -> AIAdvisoryInvocation:
    return AIAdvisoryInvocation(**_invocation_kwargs(**overrides))


def _proposal_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "proposal_ref": "proposal-1",
        "invocation": _invocation(),
        "capability": _capability(),
        "proposed_command_ref": "command-1",
        "proposed_command_digest": _VALID_DIGEST_B,
        "supporting_evidence_refs": (),
        "confidence": 0.5,
        "execution_policy_version_ref": "policy-v1",
        "execution_policy_digest": _VALID_DIGEST_C,
    }
    kwargs.update(overrides)
    return kwargs


def _proposal(**overrides: Any) -> AIActionProposal:
    return AIActionProposal(**_proposal_kwargs(**overrides))


# --- AICapabilityExposureRef -------------------------------------------------


def test_capability_exposure_ref_constructs_and_is_frozen() -> None:
    ref = _capability()
    assert ref.capability_id == "support.customer.identify.v1"
    with pytest.raises(AttributeError):
        ref.capability_id = "other.thing.v1"  # type: ignore[misc]


@pytest.mark.parametrize(
    "capability_id",
    [
        "support.customer.identify.v1",
        "communications.reply.polish.v1",
        "billing.invoice_delivery.v1",
        "support.customer.identify.v2",
        "support.customer.identify.v10",
    ],
)
def test_capability_id_accepts_valid_shapes(capability_id: str) -> None:
    ref = AICapabilityExposureRef(
        capability_id=capability_id, contract_digest=_VALID_DIGEST_A
    )
    assert ref.capability_id == capability_id


@pytest.mark.parametrize(
    "capability_id",
    [
        "support.customer.identify",  # no version
        "identify.v1",  # single segment before version
        "Support.Customer.Identify.v1",  # uppercase
        "support.customer.identify.v1\n",  # trailing newline
        "3.b.v1",  # digit-leading segment
        "support.customer.identify.v0",  # v0 is not a valid version
        "support.customer.identify.v01",  # leading zero
    ],
)
def test_capability_id_rejects_malformed_shapes(capability_id: str) -> None:
    with pytest.raises(InvalidCapabilityIdError):
        AICapabilityExposureRef(
            capability_id=capability_id, contract_digest=_VALID_DIGEST_A
        )


def test_capability_exposure_ref_rejects_blank_digest() -> None:
    with pytest.raises(InvalidContractDigestError, match="contract_digest"):
        AICapabilityExposureRef(
            capability_id="support.customer.identify.v1", contract_digest="  "
        )


def test_capability_exposure_ref_rejects_being_subclassed() -> None:
    """The exact-type self-check must fire for a bare, otherwise-valid
    subclass instance constructed DIRECTLY — not just when it's later
    embedded as a nested value inside another type."""
    with pytest.raises(ValueError, match="AICapabilityExposureRef"):
        _CapabilitySubclass(
            capability_id="support.customer.identify.v1",
            contract_digest=_VALID_DIGEST_A,
        )


def test_capability_exposure_ref_has_exactly_the_permitted_field_set() -> None:
    """Exact permitted-field-set guards exist on every public type — an
    added `next_step` or `session_ref` on any type could pass unnoticed
    otherwise."""
    assert _field_names(AICapabilityExposureRef) == {
        "capability_id",
        "contract_digest",
    }


def test_capability_exposure_ref_rejects_capability_id_str_subclass() -> None:
    """A ``str`` subclass instance passed as ``capability_id`` is REFUSED,
    not coerced: "coercing" a non-exact `str` would dispatch its own
    possibly-overridden `__str__`, which is exactly the thing this check
    exists to avoid trusting."""

    class _StrSubclass(str):
        pass

    tagged = _StrSubclass("support.customer.identify.v1")
    with pytest.raises(ValueError, match="capability_id"):
        AICapabilityExposureRef(capability_id=tagged, contract_digest=_VALID_DIGEST_A)


# --- Digest shape: shared across contract_digest / context_projection_digest /
# --- proposed_command_digest --------------------------------------------------


@pytest.mark.parametrize(
    "digest",
    [
        "a" * 63,  # too short
        "a" * 65,  # too long
        "A" * 64,  # uppercase
        "sha256:" + "a" * 64,  # prefixed
        "",  # blank
        " " * 64,  # blank after strip
        "g" * 64,  # non-hex character
        ("a" * 63) + "g",  # one non-hex character
    ],
)
def test_contract_digest_rejects_malformed_shapes(digest: str) -> None:
    with pytest.raises(InvalidContractDigestError):
        AICapabilityExposureRef(
            capability_id="support.customer.identify.v1", contract_digest=digest
        )


def test_contract_digest_accepts_valid_shape() -> None:
    ref = AICapabilityExposureRef(
        capability_id="support.customer.identify.v1", contract_digest=_VALID_DIGEST_A
    )
    assert ref.contract_digest == _VALID_DIGEST_A


def test_contract_digest_rejects_str_subclass_even_with_overridden_eq() -> None:
    """A `str` subclass overriding `__eq__`/`__ne__` to always compare
    equal is REFUSED outright, not coerced-then-compared — coercing via
    `str.__new__(str, value)` dispatches the subclass's own `__str__`,
    which could fabricate a value the caller never actually supplied (e.g.
    a valid-looking digest). Refusing the value entirely, before any regex
    or comparison ever sees it, is the only honest fix.
    """

    class _AlwaysEqualStr(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        def __hash__(self) -> int:
            return 0

    tricky = _AlwaysEqualStr(_VALID_DIGEST_B)
    with pytest.raises(ValueError, match="contract_digest"):
        AICapabilityExposureRef(
            capability_id="support.customer.identify.v1", contract_digest=tricky
        )


@pytest.mark.parametrize(
    "digest",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "sha256:" + "a" * 64,
        "",
        "g" * 64,
    ],
)
def test_context_projection_digest_rejects_malformed_shapes(digest: str) -> None:
    with pytest.raises(InvalidContractDigestError):
        AIAdvisoryInvocation(**_invocation_kwargs(context_projection_digest=digest))


def test_context_projection_digest_accepts_valid_shape() -> None:
    inv = _invocation()
    assert inv.context_projection_digest == _VALID_DIGEST_A


@pytest.mark.parametrize(
    "digest",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "sha256:" + "a" * 64,
        "",
        "g" * 64,
    ],
)
def test_proposed_command_digest_rejects_malformed_shapes(digest: str) -> None:
    with pytest.raises(InvalidContractDigestError):
        AIActionProposal(**_proposal_kwargs(proposed_command_digest=digest))


def test_proposed_command_digest_accepts_valid_shape() -> None:
    proposal = _proposal()
    assert proposal.proposed_command_digest == _VALID_DIGEST_B


# --- AIEvidenceBinding ---------------------------------------------------------


def test_evidence_binding_constructs_with_valid_data() -> None:
    binding = _evidence_binding()
    assert binding.locator_namespace == "ticketing"
    assert binding.locator_ref == "ticket:42"
    assert binding.content_digest == _VALID_DIGEST_B
    assert binding.media_type == "text/plain"


@pytest.mark.parametrize(
    "field_name",
    ["locator_namespace", "locator_ref", "media_type"],
)
def test_evidence_binding_rejects_blank_string_fields(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        _evidence_binding(**{field_name: "  "})


@pytest.mark.parametrize(
    "digest",
    ["a" * 63, "a" * 65, "A" * 64, "sha256:" + "a" * 64, "", "g" * 64],
)
def test_evidence_binding_rejects_malformed_content_digest(digest: str) -> None:
    """The content digest is verified BEFORE the binding can be attached
    anywhere — construction is the only place this module ever validates
    it, and it happens before any decision or proposal can reference the
    binding at all."""
    with pytest.raises(InvalidContractDigestError, match="content_digest"):
        _evidence_binding(content_digest=digest)


def test_evidence_binding_rejects_being_subclassed() -> None:
    with pytest.raises(ValueError, match="AIEvidenceBinding"):
        _EvidenceBindingSubclass(
            locator_namespace="ticketing",
            locator_ref="ticket:42",
            content_digest=_VALID_DIGEST_B,
            media_type="text/plain",
        )


def test_evidence_binding_rejects_locator_namespace_str_subclass() -> None:
    class _StrSubclass(str):
        pass

    with pytest.raises(ValueError, match="locator_namespace"):
        _evidence_binding(locator_namespace=_StrSubclass("ticketing"))


def test_evidence_binding_has_exactly_the_permitted_field_set() -> None:
    assert _field_names(AIEvidenceBinding) == {
        "locator_namespace",
        "locator_ref",
        "content_digest",
        "media_type",
    }


# --- AIAcknowledgementAttribution ----------------------------------------------
#
# The newest type on this branch and, before this section existed, the
# least proven — the contract test file never imported it at all.
# Mirrors `AIEvidenceBinding`'s section above field-for-field: removing
# `_require_exact_class`, a required actor field, blank-string validation,
# or aware-time validation from `AIAcknowledgementAttribution.__post_init__`
# would leave every OTHER test in this suite green, because none of them
# constructed one.


def test_acknowledgement_attribution_constructs_with_valid_data() -> None:
    attribution = _acknowledgement_attribution()
    assert attribution.actor_namespace == "platform-identity"
    assert attribution.actor_type == "human"
    assert attribution.actor_ref == "user:1"
    assert attribution.acknowledged_at == _FIXED_AS_OF


@pytest.mark.parametrize(
    "field_name",
    ["actor_namespace", "actor_type", "actor_ref"],
)
def test_acknowledgement_attribution_rejects_blank_string_fields(
    field_name: str,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        _acknowledgement_attribution(**{field_name: "  "})


def test_acknowledgement_attribution_rejects_being_subclassed() -> None:
    with pytest.raises(ValueError, match="AIAcknowledgementAttribution"):
        _AcknowledgementAttributionSubclass(
            actor_namespace="platform-identity",
            actor_type="human",
            actor_ref="user:1",
            acknowledged_at=_FIXED_AS_OF,
        )


def test_acknowledgement_attribution_rejects_actor_ref_str_subclass() -> None:
    class _StrSubclass(str):
        pass

    with pytest.raises(ValueError, match="actor_ref"):
        _acknowledgement_attribution(actor_ref=_StrSubclass("user:1"))


def test_acknowledgement_attribution_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="acknowledged_at"):
        _acknowledgement_attribution(
            acknowledged_at=datetime(2026, 9, 13)  # naive on purpose
        )


def test_acknowledgement_attribution_rejects_tzinfo_with_no_offset() -> None:
    with pytest.raises(ValueError, match="acknowledged_at"):
        _acknowledgement_attribution(
            acknowledged_at=datetime(2026, 9, 13, tzinfo=_NoOffsetTzinfo())
        )


def test_acknowledgement_attribution_has_exactly_the_permitted_field_set() -> None:
    assert _field_names(AIAcknowledgementAttribution) == {
        "actor_namespace",
        "actor_type",
        "actor_ref",
        "acknowledged_at",
    }


# --- AIAdvisoryInvocation -----------------------------------------------------


def test_advisory_invocation_constructs_with_valid_data() -> None:
    inv = _invocation()
    assert inv.invocation_ref == "inv-1"
    assert inv.execution_policy_version_ref == "policy-v1"


def test_advisory_invocation_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="context_projection_as_of"):
        AIAdvisoryInvocation(
            **_invocation_kwargs(
                context_projection_as_of=datetime(2026, 9, 13)  # naive on purpose
            )
        )


def test_advisory_invocation_rejects_tzinfo_with_no_offset() -> None:
    with pytest.raises(ValueError, match="context_projection_as_of"):
        AIAdvisoryInvocation(
            **_invocation_kwargs(
                context_projection_as_of=datetime(2026, 9, 13, tzinfo=_NoOffsetTzinfo())
            )
        )


def test_advisory_invocation_rejects_non_capability_ref() -> None:
    with pytest.raises(ValueError, match="capability"):
        AIAdvisoryInvocation(
            **_invocation_kwargs(capability="support.customer.identify.v1")  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field_name", "override"),
    [
        ("invocation_ref", {"invocation_ref": "  "}),
        ("interaction_ref", {"interaction_ref": "  "}),
        ("context_projection_ref", {"context_projection_ref": "  "}),
        ("context_sensitivity", {"context_sensitivity": "  "}),
    ],
)
def test_advisory_invocation_rejects_blank_refs(
    field_name: str, override: dict[str, Any]
) -> None:
    """Each of the four independently-blankable string fields is exercised
    on its own — omitting one, deleting any OTHER field's blank check
    would not be caught."""
    with pytest.raises(ValueError, match=field_name):
        AIAdvisoryInvocation(**_invocation_kwargs(**override))


def test_advisory_invocation_rejects_blank_execution_policy_version_ref() -> None:
    with pytest.raises(ValueError, match="execution_policy_version_ref"):
        AIAdvisoryInvocation(**_invocation_kwargs(execution_policy_version_ref="  "))


@pytest.mark.parametrize(
    "digest",
    ["a" * 63, "a" * 65, "A" * 64, "sha256:" + "a" * 64, "", "g" * 64],
)
def test_advisory_invocation_rejects_malformed_execution_policy_digest(
    digest: str,
) -> None:
    with pytest.raises(InvalidContractDigestError, match="execution_policy_digest"):
        AIAdvisoryInvocation(**_invocation_kwargs(execution_policy_digest=digest))


def test_advisory_invocation_rejects_capability_subclass() -> None:
    """A crafted subclass instance nested as `capability` must be rejected
    even though isinstance() would accept it — proves the check is
    exact-type, not isinstance. (The subclass can no longer be built via
    ordinary construction at all — see `_bypass_construct`.)"""
    subclass_capability = _bypass_construct(
        _CapabilitySubclass,
        capability_id="support.customer.identify.v1",
        contract_digest=_VALID_DIGEST_A,
    )
    with pytest.raises(ValueError, match="capability"):
        AIAdvisoryInvocation(**_invocation_kwargs(capability=subclass_capability))


class _InvocationSubclassWithNextStep(AIAdvisoryInvocation):
    """A subclass carrying an added ``next_step`` attribute — exactly the
    workflow/orchestration extension channel the exact-type self-check
    exists to exclude. It inherits the base dataclass ``__eq__``, so
    ``next_step`` would be invisible to any value comparison. It can no
    longer be constructed via the normal dataclass API.
    """


def test_advisory_invocation_rejects_being_subclassed() -> None:
    """The exact-type self-check must fire for a bare, otherwise-valid
    subclass instance constructed DIRECTLY as the invocation itself — not
    just when nested inside an `AIAdvisoryResult`."""
    with pytest.raises(ValueError, match="AIAdvisoryInvocation"):
        _InvocationSubclassWithNextStep(**_invocation_kwargs())


def test_advisory_invocation_coerces_context_projection_as_of_to_exact_datetime() -> (
    None
):
    """A ``datetime`` subclass instance must not survive storage —
    `datetime` coercion (unlike `str`/`float`) reconstructs the value from
    its field accessors rather than calling an overridable method, so this
    remains a genuine coercion, not a refusal."""

    class _DatetimeSubclass(datetime):
        pass

    tagged = _DatetimeSubclass(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    inv = _invocation(context_projection_as_of=tagged)
    assert type(inv.context_projection_as_of) is datetime


def test_advisory_invocation_canonicalizes_context_projection_as_of_tzinfo_to_utc() -> (
    None
):
    """An accepted aware datetime is canonicalized to a UTC instant
    carrying the plain `datetime.UTC` singleton, never the caller's
    original `tzinfo` object — a `tzinfo` subclass could hold its own
    hidden state or a mutable offset, and a zero-offset zone tagged that
    way would otherwise compare equal to UTC while still carrying it.
    """

    class _TaggedUTC(tzinfo):
        def utcoffset(self, dt: datetime | None) -> timedelta:
            return timedelta(0)

        def dst(self, dt: datetime | None) -> timedelta | None:
            return None

        def tzname(self, dt: datetime | None) -> str | None:
            return "tagged-utc"

    tagged_zero_offset = datetime(2026, 9, 13, 12, 0, 0, tzinfo=_TaggedUTC())
    inv = _invocation(context_projection_as_of=tagged_zero_offset)
    assert inv.context_projection_as_of.tzinfo is UTC


def test_advisory_invocation_rejects_execution_policy_digest_str_subclass() -> None:
    """A ``str`` subclass instance passed as `execution_policy_digest` is
    REFUSED, not coerced."""

    class _StrSubclass(str):
        pass

    tagged = _StrSubclass(_VALID_DIGEST_C)
    with pytest.raises(ValueError, match="execution_policy_digest"):
        _invocation(execution_policy_digest=tagged)


def test_advisory_invocation_has_exactly_the_permitted_field_set() -> None:
    assert _field_names(AIAdvisoryInvocation) == {
        "invocation_ref",
        "interaction_ref",
        "context_projection_ref",
        "context_projection_digest",
        "context_projection_as_of",
        "context_sensitivity",
        "capability",
        "execution_policy_version_ref",
        "execution_policy_digest",
    }


# --- AIActionProposal ---------------------------------------------------------


def test_action_proposal_constructs_and_defensively_copies_evidence_refs() -> None:
    evidence = [
        _evidence_binding(locator_ref="ticket:1"),
        _evidence_binding(locator_ref="ticket:2"),
    ]
    passed_tuple = tuple(evidence)
    proposal = AIActionProposal(
        **_proposal_kwargs(supporting_evidence_refs=passed_tuple)
    )
    evidence.append(_evidence_binding(locator_ref="ticket:3"))
    assert proposal.supporting_evidence_refs == tuple(evidence[:2])
    # `tuple(x)` for an ALREADY-exact tuple `x` returns `x` itself (no
    # copy) in CPython, so without deliberate reconstruction this would be
    # `is` the very same object passed in. Proving non-identity is what
    # actually distinguishes "defensively copies" from "stores the input
    # tuple verbatim" — the equality assertion above alone is satisfied
    # either way.
    assert proposal.supporting_evidence_refs is not passed_tuple


def test_action_proposal_defensively_copies_a_list_passed_directly() -> None:
    binding = _evidence_binding(locator_ref="ticket:1")
    mutable_list = [binding]
    proposal = AIActionProposal(
        **_proposal_kwargs(supporting_evidence_refs=mutable_list)  # type: ignore[arg-type]
    )
    mutable_list.append(_evidence_binding(locator_ref="ticket:2"))
    assert proposal.supporting_evidence_refs == (binding,)
    assert isinstance(proposal.supporting_evidence_refs, tuple)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, math.inf, math.nan, -math.inf])
def test_action_proposal_rejects_bad_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        AIActionProposal(**_proposal_kwargs(confidence=confidence))


def test_action_proposal_rejects_confidence_float_subclass() -> None:
    """A ``float`` subclass instance is REFUSED, not coerced: coercing via
    `float.__new__(float, value)` would dispatch the subclass's own
    possibly-overridden `__float__` — e.g. a NaN-backed subclass whose
    `__float__` reports `0.5`."""

    class _FloatSubclass(float):
        pass

    tagged = _FloatSubclass(0.5)
    with pytest.raises(ValueError, match="confidence"):
        _proposal(confidence=tagged)


def test_action_proposal_rejects_bool_and_int_confidence() -> None:
    """`True`/`1` must not silently become a valid `1.0` confidence — this
    is exactly the failure mode float coercion (rather than refusal) had.
    """
    with pytest.raises(ValueError, match="confidence"):
        _proposal(confidence=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="confidence"):
        _proposal(confidence=1)  # type: ignore[arg-type]


def test_action_proposal_rejects_non_capability_ref() -> None:
    with pytest.raises(ValueError, match="capability"):
        AIActionProposal(
            **_proposal_kwargs(capability="support.customer.identify.v1")  # type: ignore[arg-type]
        )


def test_action_proposal_rejects_blank_proposal_ref() -> None:
    with pytest.raises(ValueError, match="proposal_ref"):
        AIActionProposal(**_proposal_kwargs(proposal_ref="  "))


def test_action_proposal_rejects_blank_proposed_command_ref() -> None:
    with pytest.raises(ValueError, match="proposed_command_ref"):
        AIActionProposal(**_proposal_kwargs(proposed_command_ref="  "))


def test_action_proposal_rejects_non_invocation_value() -> None:
    with pytest.raises(ValueError, match="invocation"):
        AIActionProposal(**_proposal_kwargs(invocation="inv-1"))  # type: ignore[arg-type]


def test_action_proposal_rejects_invocation_subclass() -> None:
    """A crafted subclass instance nested as `invocation` must be rejected
    even though isinstance() would accept it — proves the check is
    exact-type, not isinstance."""
    subclass_invocation = _bypass_construct(
        _InvocationSubclassWithNextStep, **_invocation_kwargs()
    )
    with pytest.raises(ValueError, match="invocation"):
        AIActionProposal(**_proposal_kwargs(invocation=subclass_invocation))


def test_action_proposal_rejects_bare_string_evidence_item() -> None:
    """No silent coercion: a bare locator string where an `AIEvidenceBinding`
    is required is an ERROR, never wrapped, defaulted or promoted into one.
    """
    with pytest.raises(ValueError, match="supporting_evidence_refs"):
        AIActionProposal(
            **_proposal_kwargs(supporting_evidence_refs=("ticket:42",))  # type: ignore[arg-type]
        )


def test_action_proposal_rejects_non_binding_evidence_item() -> None:
    with pytest.raises(ValueError, match="supporting_evidence_refs"):
        AIActionProposal(
            **_proposal_kwargs(
                supporting_evidence_refs=(_evidence_binding(), object())  # type: ignore[arg-type]
            )
        )


def test_action_proposal_rejects_a_bare_string_as_evidence_refs() -> None:
    """``tuple("evidence-1")`` silently iterates characters, not one ref."""
    with pytest.raises(ValueError, match="supporting_evidence_refs"):
        AIActionProposal(
            **_proposal_kwargs(supporting_evidence_refs="evidence-1")  # type: ignore[arg-type]
        )


def test_action_proposal_rejects_bytes_as_evidence_refs() -> None:
    """Uses EMPTY bytes deliberately. Iterating ``b""`` yields no items at
    all, so only the explicit `isinstance(..., bytes)` short-circuit branch
    — not the per-item validator — can catch this. A non-empty bytes value
    would still be rejected by the per-item check even with the explicit
    bytes branch deleted, hiding exactly that regression.
    """
    with pytest.raises(ValueError, match="supporting_evidence_refs"):
        AIActionProposal(**_proposal_kwargs(supporting_evidence_refs=b""))  # type: ignore[arg-type]


def test_action_proposal_rejects_evidence_binding_subclass_item() -> None:
    """A crafted subclass instance nested as an evidence item must be
    rejected even though isinstance() would accept it — proves the check
    is exact-type, not isinstance."""
    subclass_binding = _bypass_construct(
        _EvidenceBindingSubclass,
        locator_namespace="ticketing",
        locator_ref="ticket:42",
        content_digest=_VALID_DIGEST_B,
        media_type="text/plain",
    )
    with pytest.raises(ValueError, match="supporting_evidence_refs"):
        AIActionProposal(
            **_proposal_kwargs(supporting_evidence_refs=(subclass_binding,))
        )


def test_action_proposal_has_exactly_the_permitted_field_set() -> None:
    """A NAMED-forbidden-field test (e.g. 'no approver_ref') survives a
    rename to any other spelling of the same concept. Asserting the field
    set EXACTLY closes that — any addition, removal or rename fails this
    test.
    """
    assert _field_names(AIActionProposal) == {
        "proposal_ref",
        "invocation",
        "capability",
        "proposed_command_ref",
        "proposed_command_digest",
        "supporting_evidence_refs",
        "confidence",
        "execution_policy_version_ref",
        "execution_policy_digest",
    }


def test_action_proposal_rejects_blank_execution_policy_version_ref() -> None:
    with pytest.raises(ValueError, match="execution_policy_version_ref"):
        AIActionProposal(**_proposal_kwargs(execution_policy_version_ref="  "))


@pytest.mark.parametrize(
    "digest",
    ["a" * 63, "a" * 65, "A" * 64, "sha256:" + "a" * 64, "", "g" * 64],
)
def test_action_proposal_rejects_malformed_execution_policy_digest(
    digest: str,
) -> None:
    with pytest.raises(InvalidContractDigestError, match="execution_policy_digest"):
        AIActionProposal(**_proposal_kwargs(execution_policy_digest=digest))


def test_action_proposal_rejects_capability_subclass() -> None:
    """A crafted subclass instance nested as `capability` must be rejected
    even though isinstance() would accept it — proves the check is
    exact-type, not isinstance."""
    subclass_capability = _bypass_construct(
        _CapabilitySubclass,
        capability_id="support.customer.identify.v1",
        contract_digest=_VALID_DIGEST_A,
    )
    with pytest.raises(ValueError, match="capability"):
        AIActionProposal(**_proposal_kwargs(capability=subclass_capability))


def test_action_proposal_rejects_being_subclassed() -> None:
    """The exact-type self-check must fire for a bare, otherwise-valid
    subclass instance constructed DIRECTLY as the proposal itself — not
    just when nested inside an `AIActionDecision`."""

    class _Subclass(AIActionProposal):
        pass

    with pytest.raises(ValueError, match="AIActionProposal"):
        _Subclass(**_proposal_kwargs())


# --- AIAdvisoryResult ----------------------------------------------------------


def test_advisory_result_constructs_without_action_proposal() -> None:
    result = AIAdvisoryResult(
        invocation=_invocation(),
        advisory_value="redacted-summary",
        confidence=0.9,
    )
    assert result.action_proposal is None
    assert result.invocation.invocation_ref == "inv-1"


def test_advisory_result_constructs_with_matching_action_proposal() -> None:
    # Two SEPARATE (non-identical) but equal-valued capability instances.
    # This alone does not prove a capability comparison exists at all — the
    # companion mismatch tests below own that claim, since deleting the
    # comparison outright would still pass here. What this test proves is
    # narrower: IF the cross-check exists, an identity-based comparison
    # (`is` instead of `==`) would wrongly reject this valid case, since the
    # two objects are equal-valued but not the same object.
    invocation_capability = _capability()
    proposal_capability = _capability()
    assert invocation_capability is not proposal_capability
    assert invocation_capability == proposal_capability
    invocation = _invocation(
        capability=invocation_capability, execution_policy_version_ref="policy-v1"
    )
    proposal = _proposal(
        capability=proposal_capability, execution_policy_version_ref="policy-v1"
    )
    result = AIAdvisoryResult(
        invocation=invocation,
        advisory_value="redacted-summary",
        confidence=0.9,
        action_proposal=proposal,
    )
    assert result.action_proposal is proposal
    assert result.invocation is invocation


def test_action_proposal_rejects_capability_mismatch_with_own_invocation() -> None:
    """Round 11 fix 1: `AIActionProposal` itself must refuse a capability
    that contradicts the invocation it carries — this is the PRIMARY gate
    now, checked at proposal construction, not only when the proposal is
    later wrapped in an `AIAdvisoryResult`. Before this fix, nothing
    stopped a proposal naming a different capability than its own
    embedded invocation; a decision could then carry that
    self-contradictory proposal with nothing refusing it.
    """
    mismatched_capability = _capability(capability_id="support.customer.identify.v2")
    with pytest.raises(ValueError, match="capability"):
        _proposal(capability=mismatched_capability)


def test_action_proposal_rejects_capability_digest_mismatch_with_own_invocation() -> (
    None
):
    """Round 12 finding 5: the test above varies only `capability_id`, so
    a comparator that ignored `contract_digest` would still pass it. Same
    capability_id, different `contract_digest`, at the PRIMARY
    (proposal-construction) gate."""
    mismatched_capability = _capability(contract_digest=_VALID_DIGEST_B)
    with pytest.raises(ValueError, match="capability"):
        _proposal(capability=mismatched_capability)


def test_advisory_result_rejects_capability_mismatch() -> None:
    """UNREACHABLE THROUGH ORDINARY CONSTRUCTION (round 12 finding 1):
    once `AIActionProposal` validates its own capability against its own
    invocation, and `AIAdvisoryResult` validates the WHOLE invocation
    object, a normally-built proposal can never disagree with the exact
    SAME invocation object on capability alone — the whole-invocation
    check would already have caught any such disagreement first. This
    test uses `_bypass_construct` to build a proposal that never ran
    `__post_init__`, carrying the SAME invocation object as the result
    (so the whole-invocation-equality check trivially passes) alongside a
    contradictory capability — the only way to reach this specific check,
    proving it as a safety net against a tampered object, not something
    ordinary construction can trigger.
    """
    invocation = _invocation(
        capability=_capability(), execution_policy_version_ref="policy-v1"
    )
    mismatched_capability = _capability(capability_id="support.customer.identify.v2")
    proposal = _bypass_construct(
        AIActionProposal,
        proposal_ref="proposal-1",
        invocation=invocation,
        capability=mismatched_capability,
        proposed_command_ref="command-1",
        proposed_command_digest=_VALID_DIGEST_B,
        supporting_evidence_refs=(),
        confidence=0.5,
        execution_policy_version_ref="policy-v1",
        execution_policy_digest=_VALID_DIGEST_C,
    )
    with pytest.raises(ValueError, match="capability"):
        AIAdvisoryResult(
            invocation=invocation,
            advisory_value="redacted-summary",
            confidence=0.9,
            action_proposal=proposal,
        )


def test_advisory_result_rejects_capability_mismatch_by_contract_digest_only() -> None:
    """Same capability_id, different contract_digest — the case the
    id-only mismatch test above cannot catch. UNREACHABLE THROUGH
    ORDINARY CONSTRUCTION for the same reason as the test above; uses
    `_bypass_construct` for the same reason.
    """
    invocation = _invocation(
        capability=_capability(contract_digest=_VALID_DIGEST_A),
        execution_policy_version_ref="policy-v1",
    )
    mismatched_capability = _capability(contract_digest=_VALID_DIGEST_B)
    proposal = _bypass_construct(
        AIActionProposal,
        proposal_ref="proposal-1",
        invocation=invocation,
        capability=mismatched_capability,
        proposed_command_ref="command-1",
        proposed_command_digest=_VALID_DIGEST_B,
        supporting_evidence_refs=(),
        confidence=0.5,
        execution_policy_version_ref="policy-v1",
        execution_policy_digest=_VALID_DIGEST_C,
    )
    with pytest.raises(ValueError, match="capability"):
        AIAdvisoryResult(
            invocation=invocation,
            advisory_value="redacted-summary",
            confidence=0.9,
            action_proposal=proposal,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"invocation_ref": "inv-2"},
        {"interaction_ref": "interaction-2"},
        {"context_projection_ref": "proj-2"},
        {"context_projection_digest": _VALID_DIGEST_B},
        {"context_projection_as_of": datetime(2020, 1, 1, tzinfo=UTC)},
        {"context_sensitivity": "restricted"},
        {"capability": _capability(capability_id="support.customer.identify.v2")},
        {"execution_policy_version_ref": "policy-v2"},
        {"execution_policy_digest": _VALID_DIGEST_D},
    ],
    ids=[
        "invocation_ref",
        "interaction_ref",
        "context_projection_ref",
        "context_projection_digest",
        "context_projection_as_of",
        "context_sensitivity",
        "invocation.capability",
        "invocation.execution_policy_version_ref",
        "invocation.execution_policy_digest",
    ],
)
def test_advisory_result_rejects_proposal_generated_for_a_different_invocation(
    override: dict[str, Any],
) -> None:
    """All nine of `AIAdvisoryInvocation`'s fields are exercised
    independently — the previous set never varied the embedded
    invocation's `capability`, `execution_policy_version_ref` or
    `execution_policy_digest`, so a comparator that omitted those three
    fields would have passed every case. For the three policy/capability
    overrides, `AIActionProposal`'s OWN scalar fields are set to match so
    the proposal stays internally valid under round 11 fix 1 (otherwise
    proposal construction itself would refuse it, before this test ever
    reaches `AIAdvisoryResult`) — this isolates the RESULT's
    full-invocation-equality check specifically; deleting the comparison
    for any ONE of the nine fields (while `AIActionProposal` still carries
    the FULL invocation object) would let its case pass silently.
    """
    invocation = _invocation()
    differing_invocation = _invocation(**override)
    proposal_scalar_keys = {
        "capability",
        "execution_policy_version_ref",
        "execution_policy_digest",
    }
    proposal_overrides = {
        k: v for k, v in override.items() if k in proposal_scalar_keys
    }
    proposal = _proposal(invocation=differing_invocation, **proposal_overrides)
    with pytest.raises(ValueError, match="invocation"):
        AIAdvisoryResult(
            invocation=invocation,
            advisory_value="redacted-summary",
            confidence=0.9,
            action_proposal=proposal,
        )


def test_advisory_result_invocation_mismatch_survives_overridden_datetime_eq() -> None:
    """A ``datetime`` subclass overriding ``__eq__`` to always compare
    equal must not defeat the full invocation-equality binding — proves
    ``context_projection_as_of`` is coerced to an exact ``datetime``
    (using the real ``__eq__``) before comparison, not compared via
    whatever the caller's original object defines.
    """

    class _AlwaysEqualDatetime(datetime):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        def __hash__(self) -> int:
            return 0

    real_as_of = _FIXED_AS_OF
    different_as_of = datetime(2020, 1, 1, tzinfo=UTC)
    tricky_as_of = _AlwaysEqualDatetime(
        different_as_of.year,
        different_as_of.month,
        different_as_of.day,
        tzinfo=different_as_of.tzinfo,
    )
    invocation = _invocation(context_projection_as_of=real_as_of)
    differing_invocation = _invocation(context_projection_as_of=tricky_as_of)
    proposal = _proposal(invocation=differing_invocation)
    with pytest.raises(ValueError, match="invocation"):
        AIAdvisoryResult(
            invocation=invocation,
            advisory_value="redacted-summary",
            confidence=0.9,
            action_proposal=proposal,
        )


def test_action_proposal_rejects_own_policy_version_ref_mismatch() -> None:
    """Round 11 fix 1: the PRIMARY gate — `AIActionProposal` refuses a
    policy version ref that contradicts its own embedded invocation, at
    construction time."""
    with pytest.raises(ValueError, match="execution_policy_version_ref"):
        _proposal(
            invocation=_invocation(execution_policy_version_ref="policy-v1"),
            execution_policy_version_ref="policy-v2",
        )


def test_advisory_result_rejects_execution_policy_version_ref_mismatch() -> None:
    """UNREACHABLE THROUGH ORDINARY CONSTRUCTION (round 12 finding 1): a
    normally-built proposal can never disagree with the exact SAME
    invocation object on `execution_policy_version_ref` alone, since the
    whole-invocation-equality check above would already have caught any
    such disagreement first. `_bypass_construct` builds a proposal that
    never ran `__post_init__`, carrying the SAME invocation object as the
    result (so the whole-invocation check trivially passes) alongside a
    contradictory `execution_policy_version_ref` — the only way to reach
    this specific check.
    """
    capability = _capability()
    invocation = _invocation(
        capability=capability, execution_policy_version_ref="policy-v1"
    )
    proposal = _bypass_construct(
        AIActionProposal,
        proposal_ref="proposal-1",
        invocation=invocation,
        capability=capability,
        proposed_command_ref="command-1",
        proposed_command_digest=_VALID_DIGEST_B,
        supporting_evidence_refs=(),
        confidence=0.5,
        execution_policy_version_ref="policy-v2",
        execution_policy_digest=_VALID_DIGEST_C,
    )
    with pytest.raises(ValueError, match="execution_policy_version_ref"):
        AIAdvisoryResult(
            invocation=invocation,
            advisory_value="redacted-summary",
            confidence=0.9,
            action_proposal=proposal,
        )


def test_action_proposal_rejects_own_policy_digest_mismatch() -> None:
    """Round 11 fix 1: the PRIMARY gate — `AIActionProposal` refuses a
    policy content digest that contradicts its own embedded invocation,
    at construction time."""
    with pytest.raises(ValueError, match="execution_policy_digest"):
        _proposal(
            invocation=_invocation(execution_policy_digest=_VALID_DIGEST_C),
            execution_policy_digest=_VALID_DIGEST_D,
        )


def test_advisory_result_rejects_execution_policy_digest_mismatch() -> None:
    """Refs match but the pinned policy content digest differs.

    Proves the cross-check compares digest content, not just the mutable
    version ref — a ref that resolves differently at two moments must
    still be caught. UNREACHABLE THROUGH ORDINARY CONSTRUCTION for the
    same reason as the version-ref test above; uses `_bypass_construct`
    for the same reason — the SAME invocation object on both sides makes
    the whole-invocation check trivially pass, isolating this specific
    digest check.
    """
    capability = _capability()
    invocation = _invocation(
        capability=capability,
        execution_policy_version_ref="policy-v1",
        execution_policy_digest=_VALID_DIGEST_C,
    )
    proposal = _bypass_construct(
        AIActionProposal,
        proposal_ref="proposal-1",
        invocation=invocation,
        capability=capability,
        proposed_command_ref="command-1",
        proposed_command_digest=_VALID_DIGEST_B,
        supporting_evidence_refs=(),
        confidence=0.5,
        execution_policy_version_ref="policy-v1",
        execution_policy_digest=_VALID_DIGEST_D,
    )
    with pytest.raises(ValueError, match="execution_policy_digest"):
        AIAdvisoryResult(
            invocation=invocation,
            advisory_value="redacted-summary",
            confidence=0.9,
            action_proposal=proposal,
        )


def test_action_proposal_rejects_policy_digest_str_subclass_overriding_eq() -> None:
    """A ``str`` subclass overriding ``__eq__``/``__ne__`` to always
    compare equal is REFUSED at `AIActionProposal` construction itself —
    before it could ever reach an `AIAdvisoryResult` cross-check and
    defeat it.
    """

    class _AlwaysEqualStr(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        def __hash__(self) -> int:
            return 0

    tricky_digest = _AlwaysEqualStr(_VALID_DIGEST_D)
    with pytest.raises(ValueError, match="execution_policy_digest"):
        _proposal(execution_policy_digest=tricky_digest)


def test_advisory_result_with_no_proposal_is_unaffected_by_cross_checks() -> None:
    invocation = _invocation(
        capability=_capability(), execution_policy_version_ref="policy-v1"
    )
    result = AIAdvisoryResult(
        invocation=invocation,
        advisory_value="redacted-summary",
        confidence=0.9,
        action_proposal=None,
    )
    assert result.action_proposal is None


def test_advisory_result_rejects_non_invocation_instance() -> None:
    with pytest.raises(ValueError, match="invocation"):
        AIAdvisoryResult(
            invocation="inv-1",  # type: ignore[arg-type]
            advisory_value="redacted-summary",
            confidence=0.9,
        )


def test_advisory_result_rejects_blank_advisory_value() -> None:
    with pytest.raises(ValueError, match="advisory_value"):
        AIAdvisoryResult(
            invocation=_invocation(),
            advisory_value="  ",
            confidence=0.9,
        )


def test_advisory_result_rejects_non_proposal_action_proposal() -> None:
    with pytest.raises(ValueError, match="action_proposal"):
        AIAdvisoryResult(
            invocation=_invocation(),
            advisory_value="redacted-summary",
            confidence=0.9,
            action_proposal=object(),  # type: ignore[arg-type]
        )


class _ActionProposalSubclassWithWaitDeadline(AIActionProposal):
    """A subclass carrying an added ``wait_deadline`` attribute — the
    analogous extension channel on ``AIAdvisoryResult.action_proposal``. It
    can no longer be constructed via the normal dataclass API.
    """


def test_advisory_result_rejects_invocation_subclass_carrying_next_step() -> None:
    """A crafted subclass instance nested as `invocation` must be rejected
    even though isinstance() would accept it — proves the check is
    exact-type, not isinstance, and closes the one channel a workflow
    `next_step` could enter through unexamined.
    """
    subclass_invocation = _bypass_construct(
        _InvocationSubclassWithNextStep, **_invocation_kwargs()
    )
    object.__setattr__(subclass_invocation, "next_step", "escalate-to-human")
    with pytest.raises(ValueError, match="invocation"):
        AIAdvisoryResult(
            invocation=subclass_invocation,
            advisory_value="redacted-summary",
            confidence=0.9,
        )


def test_advisory_result_rejects_action_proposal_subclass_carrying_wait_deadline() -> (
    None
):
    """A crafted subclass instance nested as `action_proposal` must be
    rejected even though isinstance() would accept it — proves the check
    is exact-type, not isinstance, and closes the one channel a workflow
    `wait_deadline` could enter through unexamined.
    """
    subclass_proposal = _bypass_construct(
        _ActionProposalSubclassWithWaitDeadline, **_proposal_kwargs()
    )
    object.__setattr__(subclass_proposal, "wait_deadline", "2026-09-20T00:00:00Z")
    with pytest.raises(ValueError, match="action_proposal"):
        AIAdvisoryResult(
            invocation=_invocation(),
            advisory_value="redacted-summary",
            confidence=0.9,
            action_proposal=subclass_proposal,
        )


def test_advisory_result_rejects_being_subclassed() -> None:
    """The exact-type self-check must fire for a bare, otherwise-valid
    subclass instance of `AIAdvisoryResult` itself."""

    class _Subclass(AIAdvisoryResult):
        pass

    with pytest.raises(ValueError, match="AIAdvisoryResult"):
        _Subclass(
            invocation=_invocation(),
            advisory_value="redacted-summary",
            confidence=0.9,
        )


@pytest.mark.parametrize("confidence", [-1.0, 2.0, math.inf, math.nan])
def test_advisory_result_rejects_bad_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        AIAdvisoryResult(
            invocation=_invocation(),
            advisory_value="redacted-summary",
            confidence=confidence,
        )


def test_advisory_result_has_exactly_the_permitted_field_set() -> None:
    """A forbidden-NAME test (`next_step`, `routing`, `route_to`) only
    forbids the names it happens to list — `workflow_node`,
    `wait_deadline`, `session_ref` or any other spelling would pass it
    silently. Asserting the field set EXACTLY closes that.
    """
    assert _field_names(AIAdvisoryResult) == {
        "invocation",
        "advisory_value",
        "confidence",
        "action_proposal",
    }


# --- AIExecutionObservation ----------------------------------------------------


def test_execution_observation_is_an_attempt_input() -> None:
    obs = AIExecutionObservation(
        attempt_key="attempt-1",
        outcome="succeeded",
        output_ref="ref-1",
        output_digest=_VALID_DIGEST_A,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
    )
    assert isinstance(obs, AttemptInput)


def test_execution_observation_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError, match="outcome"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome="pending",
            output_ref=None,
            output_digest=None,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
        )


def test_execution_observation_rejects_succeeded_without_output() -> None:
    """Neither output field present on a succeeded observation.

    Deleting the outcome-specific 'succeeded requires output' check (while
    leaving the generic pairing check alone) would let this construct
    silently, since 'both absent' satisfies pairing on its own.
    """
    with pytest.raises(ValueError, match="succeeded"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome="succeeded",
            output_ref=None,
            output_digest=None,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
        )


def test_execution_observation_rejects_output_ref_without_digest_on_failed() -> None:
    """The pre-fix test only ever omitted ``output_ref``, so a version of
    the pairing check that dropped the ``output_digest`` requirement would
    still pass it. This is the omit-``output_digest`` case, using a
    `failed` outcome to also prove the pairing requirement applies
    regardless of outcome.
    """
    with pytest.raises(ValueError, match="output_digest"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome="failed",
            output_ref="partial-output",
            output_digest=None,
            provider_observation=None,
            model_observation="model-error",
            request_observation=None,
            error_code="E_PROVIDER",
        )


def test_execution_observation_rejects_output_digest_without_ref_on_failed() -> None:
    """The symmetric omit-``output_ref`` case, on a `failed` outcome — proves
    the pairing requirement is not limited to `succeeded`."""
    with pytest.raises(ValueError, match="output_ref"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome="failed",
            output_ref=None,
            output_digest=_VALID_DIGEST_A,
            provider_observation="provider-x",
            model_observation=None,
            request_observation=None,
            error_code=None,
        )


def test_execution_observation_rejects_output_digest_with_loose_shape() -> None:
    """``output_digest`` must be a real 64-lowercase-hex digest, not just a
    non-blank string."""
    with pytest.raises(InvalidContractDigestError, match="output_digest"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome="succeeded",
            output_ref="ref-1",
            output_digest="digest-1",
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
        )


def test_execution_observation_rejects_whitespace_padded_output_digest() -> None:
    """`output_digest` must be validated against exactly what was
    supplied, not a stripped copy — the owning digest oracle applies
    `fullmatch` to the original value, so `" " + 64 hex + " "` must fail,
    not be silently trimmed into a passing digest.
    """
    with pytest.raises(InvalidContractDigestError, match="output_digest"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome="succeeded",
            output_ref="ref-1",
            output_digest=f" {_VALID_DIGEST_A} ",
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
        )


def test_execution_observation_normalizes_blank_output_pair_to_none() -> None:
    """Whitespace-only `output_ref`/`output_digest` must not be retained
    as-is — the "entirely present or entirely absent" invariant means a
    blank pair is normalized to `None`, not kept blank.
    """
    obs = AIExecutionObservation(
        attempt_key="attempt-1",
        outcome="failed",
        output_ref="   ",
        output_digest="   ",
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code="E_PROVIDER",
    )
    assert obs.output_ref is None
    assert obs.output_digest is None


def test_execution_observation_allows_failed_without_output() -> None:
    obs = AIExecutionObservation(
        attempt_key="attempt-1",
        outcome="failed",
        output_ref=None,
        output_digest=None,
        provider_observation=None,
        model_observation="model-error",
        request_observation=None,
        error_code="E_PROVIDER",
    )
    assert obs.outcome == "failed"


def test_execution_observation_rejects_failed_with_no_failure_evidence() -> None:
    """A terminal failure with every provider/model/request/error field
    ``None`` (and no output) carries no evidence of what went wrong."""
    with pytest.raises(ValueError, match="failed"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome="failed",
            output_ref=None,
            output_digest=None,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
        )


def test_execution_observation_rejects_failed_with_only_whitespace_evidence() -> None:
    """All-``None`` alone would still pass if 'non-blank' were weakened to
    'non-None' — this whitespace-only field is what actually proves the
    blank check, not just a None check."""
    with pytest.raises(ValueError, match="failed"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome="failed",
            output_ref=None,
            output_digest=None,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code="   ",
        )


def test_execution_observation_rejects_blank_attempt_key() -> None:
    with pytest.raises(ValueError, match="attempt_key"):
        AIExecutionObservation(
            attempt_key="  ",
            outcome="failed",
            output_ref=None,
            output_digest=None,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code="E_PROVIDER",
        )


def test_execution_observation_rejects_being_subclassed() -> None:
    """The exact-type self-check must fire for a bare, otherwise-valid
    subclass instance of `AIExecutionObservation` itself."""

    class _Subclass(AIExecutionObservation):
        pass

    with pytest.raises(ValueError, match="AIExecutionObservation"):
        _Subclass(
            attempt_key="attempt-1",
            outcome="failed",
            output_ref=None,
            output_digest=None,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code="E_PROVIDER",
        )


def test_execution_observation_rejects_outcome_str_subclass() -> None:
    """A ``str`` subclass instance passed as `outcome` is REFUSED outright
    — before the exact-type fix, this would have been "coerced" via
    `str.__new__`, which dispatches the subclass's own `__str__`; now, ANY
    non-exact-`str` outcome is rejected regardless of what its overridden
    `__eq__`/`__hash__` claim about set membership, since the type check
    runs first and refuses unconditionally.
    """

    class _AlwaysMemberStr(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __hash__(self) -> int:
            return hash("succeeded")

    tricky_outcome = _AlwaysMemberStr("succeeded")  # even a "valid" value is refused
    with pytest.raises(ValueError, match="outcome"):
        AIExecutionObservation(
            attempt_key="attempt-1",
            outcome=tricky_outcome,
            output_ref=None,
            output_digest=None,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code="E_PROVIDER",
        )


def test_execution_observation_has_exactly_the_permitted_field_set() -> None:
    assert _field_names(AIExecutionObservation) == {
        "attempt_key",
        "outcome",
        "output_ref",
        "output_digest",
        "provider_observation",
        "model_observation",
        "request_observation",
        "error_code",
    }


# --- AIActionDecision -----------------------------------------------------------


class _ProposalSubclass(AIActionProposal):
    """A plain subclass, used only to prove exact-type (not isinstance)
    checking on ``AIActionDecision.proposal`` — it inherits the base
    dataclass ``__eq__``, which compares only the base fields, so an added
    field such as a workflow ``wait_deadline`` would be invisible to a
    value comparison. It can no longer be constructed via the normal
    dataclass API.
    """


def _decision(
    *,
    outcome: str,
    human_actor_ref: str | None,
    resulting_owner_command_evidence: AIEvidenceBinding | None,
    proposal: AIActionProposal | None = None,
    proposal_ref: str | None = None,
) -> AIActionDecision:
    actual_proposal = proposal if proposal is not None else _proposal()
    return AIActionDecision(
        proposal_ref=(
            proposal_ref if proposal_ref is not None else actual_proposal.proposal_ref
        ),
        proposal=actual_proposal,
        outcome=outcome,  # type: ignore[arg-type]
        human_actor_ref=human_actor_ref,
        resulting_owner_command_evidence=resulting_owner_command_evidence,
    )


def test_action_decision_accepted_valid() -> None:
    decision = _decision(
        outcome="accepted",
        human_actor_ref="actor-1",
        resulting_owner_command_evidence=_evidence_binding(),
    )
    assert decision.outcome == "accepted"


@pytest.mark.parametrize(
    ("human_actor_ref", "resulting_owner_command_evidence"),
    [
        ("actor-1", None),  # omits only evidence
        (None, _evidence_binding()),  # omits only actor
        ("   ", _evidence_binding()),  # whitespace-only actor
    ],
    ids=["omits-evidence", "omits-actor", "whitespace-actor"],
)
def test_action_decision_accepted_requires_actor_and_evidence(
    human_actor_ref: str | None,
    resulting_owner_command_evidence: AIEvidenceBinding | None,
) -> None:
    """Every omit-one-field AND whitespace-only-actor case is exercised:
    omitting only evidence catches deletion of the evidence requirement,
    omitting only actor (with evidence supplied) independently catches
    deletion of the actor requirement, and the whitespace-only actor case
    proves 'present' means non-blank, not merely non-None. (A
    whitespace-only EVIDENCE case no longer applies now that evidence is a
    typed `AIEvidenceBinding` rather than a string — see
    `test_action_decision_rejects_bare_string_evidence` for the analogous
    "no silent coercion" case on that field.)
    """
    with pytest.raises(ValueError, match="accepted"):
        _decision(
            outcome="accepted",
            human_actor_ref=human_actor_ref,
            resulting_owner_command_evidence=resulting_owner_command_evidence,
        )


def test_action_decision_rejects_bare_string_evidence() -> None:
    """No silent coercion: a bare locator string where
    `resulting_owner_command_evidence` demands an `AIEvidenceBinding` is
    an ERROR, never wrapped, defaulted or promoted into one."""
    with pytest.raises(ValueError, match="resulting_owner_command_evidence"):
        _decision(
            outcome="accepted",
            human_actor_ref="actor-1",
            resulting_owner_command_evidence="command-evidence-1",  # type: ignore[arg-type]
        )


def test_action_decision_rejects_evidence_binding_subclass() -> None:
    """A crafted subclass instance nested as `resulting_owner_command_
    evidence` must be rejected even though isinstance() would accept it —
    proves the check is exact-type, not isinstance."""
    subclass_binding = _bypass_construct(
        _EvidenceBindingSubclass,
        locator_namespace="ticketing",
        locator_ref="ticket:42",
        content_digest=_VALID_DIGEST_B,
        media_type="text/plain",
    )
    with pytest.raises(ValueError, match="resulting_owner_command_evidence"):
        _decision(
            outcome="accepted",
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=subclass_binding,
        )


def test_action_decision_edited_valid() -> None:
    decision = _decision(
        outcome="edited",
        human_actor_ref="actor-1",
        resulting_owner_command_evidence=_evidence_binding(),
    )
    assert decision.outcome == "edited"


@pytest.mark.parametrize(
    ("human_actor_ref", "resulting_owner_command_evidence"),
    [
        ("actor-1", None),  # omits only evidence
        (None, _evidence_binding()),  # omits only actor
        ("   ", _evidence_binding()),  # whitespace-only actor
    ],
    ids=["omits-evidence", "omits-actor", "whitespace-actor"],
)
def test_action_decision_edited_requires_actor_and_evidence(
    human_actor_ref: str | None,
    resulting_owner_command_evidence: AIEvidenceBinding | None,
) -> None:
    with pytest.raises(ValueError, match="edited"):
        _decision(
            outcome="edited",
            human_actor_ref=human_actor_ref,
            resulting_owner_command_evidence=resulting_owner_command_evidence,
        )


def test_action_decision_rejected_valid() -> None:
    decision = _decision(
        outcome="rejected",
        human_actor_ref="actor-1",
        resulting_owner_command_evidence=None,
    )
    assert decision.outcome == "rejected"


def test_action_decision_rejected_must_not_carry_evidence() -> None:
    with pytest.raises(ValueError, match="rejected"):
        _decision(
            outcome="rejected",
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=_evidence_binding(),
        )


def test_action_decision_rejected_requires_actor() -> None:
    with pytest.raises(ValueError, match="rejected"):
        _decision(
            outcome="rejected",
            human_actor_ref=None,
            resulting_owner_command_evidence=None,
        )


def test_action_decision_expired_valid() -> None:
    decision = _decision(
        outcome="expired",
        human_actor_ref=None,
        resulting_owner_command_evidence=None,
    )
    assert decision.outcome == "expired"


def test_action_decision_expired_must_not_carry_actor() -> None:
    with pytest.raises(ValueError, match="expired"):
        _decision(
            outcome="expired",
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=None,
        )


def test_action_decision_expired_must_not_carry_evidence() -> None:
    with pytest.raises(ValueError, match="expired"):
        _decision(
            outcome="expired",
            human_actor_ref=None,
            resulting_owner_command_evidence=_evidence_binding(),
        )


def test_action_decision_rejects_invalid_outcome() -> None:
    with pytest.raises(ValueError, match="outcome"):
        _decision(
            outcome="approved",  # not one of the four permitted outcomes
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=_evidence_binding(),
        )


def test_action_decision_is_frozen() -> None:
    decision = _decision(
        outcome="expired",
        human_actor_ref=None,
        resulting_owner_command_evidence=None,
    )
    with pytest.raises(AttributeError):
        decision.outcome = "accepted"  # type: ignore[misc]


def test_action_decision_carries_the_exact_reviewed_proposal() -> None:
    """The decision carries the WHOLE reviewed ``AIActionProposal`` object,
    not loose scalar copies of its pins.

    NOTE: this test proves only ordinary dataclass field storage plus the
    `proposal_ref == proposal.proposal_ref` self-consistency check — it
    does NOT prove the carried proposal ever appeared in a reviewed
    `AIAdvisoryResult`, and `AIActionDecision` is not authorization or
    proof of human review regardless — see the module docstring and this
    class's own docstring. A fresh, never-reviewed, internally-consistent
    proposal still passes every check here.
    """
    reviewed_proposal = AIActionProposal(
        **_proposal_kwargs(
            supporting_evidence_refs=(_evidence_binding(),),
            confidence=0.95,
        )
    )
    decision = _decision(
        outcome="accepted",
        human_actor_ref="actor-1",
        resulting_owner_command_evidence=_evidence_binding(),
        proposal=reviewed_proposal,
    )
    assert decision.proposal is reviewed_proposal
    assert decision.proposal.confidence == 0.95
    assert len(decision.proposal.supporting_evidence_refs) == 1


def test_action_decision_rejects_proposal_ref_not_matching_proposal() -> None:
    """A caller could otherwise attach a lookup ``proposal_ref`` naming one
    proposal while carrying a DIFFERENT actual ``proposal`` object.
    """
    reviewed_proposal = _proposal()  # proposal_ref == "proposal-1"
    with pytest.raises(ValueError, match="proposal_ref"):
        AIActionDecision(
            proposal_ref="a-different-proposal-ref",
            proposal=reviewed_proposal,
            outcome="accepted",
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=_evidence_binding(),
        )


def test_action_decision_rejects_non_proposal_value() -> None:
    with pytest.raises(ValueError, match="proposal"):
        AIActionDecision(
            proposal_ref="proposal-1",
            proposal="proposal-1",  # type: ignore[arg-type]
            outcome="accepted",
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=_evidence_binding(),
        )


def test_action_decision_rejects_proposal_subclass() -> None:
    """A crafted subclass instance nested as `proposal` must be rejected
    even though isinstance() would accept it — proves the check is
    exact-type, not isinstance."""
    subclass_proposal = _bypass_construct(_ProposalSubclass, **_proposal_kwargs())
    with pytest.raises(ValueError, match="proposal"):
        _decision(
            outcome="accepted",
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=_evidence_binding(),
            proposal=subclass_proposal,
        )


def test_action_decision_rejects_being_subclassed() -> None:
    """The exact-type self-check must fire for a bare, otherwise-valid
    subclass instance of `AIActionDecision` itself."""

    class _Subclass(AIActionDecision):
        pass

    with pytest.raises(ValueError, match="AIActionDecision"):
        _Subclass(
            proposal_ref="proposal-1",
            proposal=_proposal(),
            outcome="accepted",
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=_evidence_binding(),
        )


def test_action_decision_rejects_outcome_str_subclass() -> None:
    """A ``str`` subclass instance is REFUSED outright as `outcome`
    regardless of what its overridden `__eq__`/`__hash__` claim about
    permitted-outcome-set membership — the exact-type check runs first and
    refuses unconditionally, even for a genuinely valid string value.
    """

    class _AlwaysMemberStr(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __hash__(self) -> int:
            return hash("accepted")

    tricky_outcome = _AlwaysMemberStr("accepted")  # even a "valid" value is refused
    with pytest.raises(ValueError, match="outcome"):
        _decision(
            outcome=tricky_outcome,
            human_actor_ref="actor-1",
            resulting_owner_command_evidence=_evidence_binding(),
        )


def test_action_decision_has_exactly_the_permitted_field_set() -> None:
    assert _field_names(AIActionDecision) == {
        "proposal_ref",
        "proposal",
        "outcome",
        "human_actor_ref",
        "resulting_owner_command_evidence",
    }


def test_action_decision_docstring_states_non_authorizing_status() -> None:
    """The class docstring must say plainly that a decision is not
    authorization and not proof of review — this is a documentation
    guard: it fails if the statement is ever removed or watered down past
    the point of containing these exact markers."""
    doc = AIActionDecision.__doc__ or ""
    assert "NOT AUTHORIZATION" in doc
    assert "NOT PROOF OF REVIEW" in doc
    assert "edited" in doc.lower()


def test_module_docstring_states_non_authorizing_status() -> None:
    """The module docstring must carry the same statement at module level,
    not only on `AIActionDecision` — a reader importing the module without
    reading the class docstring must still see it."""
    import dotmac_ai_operations.advisory as advisory_module

    doc = advisory_module.__doc__ or ""
    assert "NOT AUTHORIZATION" in doc
    assert "NOT PROOF OF REVIEW" in doc
    assert "no signer" in doc.lower()


# --- Forbidden surface ----------------------------------------------------------


def test_workflow_and_registry_surface_is_not_importable() -> None:
    """Named-forbidden-attribute check only.

    Proves five specific names (`AIWorkflowOutcome`, `AIWorkflowDecision`,
    `register_capabilities`, `get_capability`, `AICapabilityDeclaration`) are
    not attributes of the package or the ``advisory`` module. It does NOT
    prove no differently-named workflow/routing/registry authority exists —
    a renamed equivalent would pass this check silently. That broader,
    honest guarantee is not attempted here; see
    ``test_public_export_surface_is_exactly_the_expected_set`` for the
    narrower thing that IS actually proven (the public export surface, not
    the absence of unexported implementation), and the accepted debt for a
    full AST-based structural guard.
    """
    import dotmac_ai_operations

    forbidden_names = (
        "AIWorkflowOutcome",
        "AIWorkflowDecision",
        "register_capabilities",
        "get_capability",
        "AICapabilityDeclaration",
    )
    for name in forbidden_names:
        assert not hasattr(dotmac_ai_operations, name), name

    import dotmac_ai_operations.advisory as advisory_module

    for name in forbidden_names:
        assert not hasattr(advisory_module, name), name


def test_public_export_surface_is_exactly_the_expected_set() -> None:
    """Exhaustive allowlist of the PUBLIC export surface only.

    This proves ``dotmac_ai_operations.__all__`` is exactly this set, so ANY
    addition or rename to the public export surface fails the build even if
    it doesn't match one of the five forbidden names checked above. It does
    NOT prove that no private, non-exported workflow/routing/registry
    implementation exists inside the package — a module could define such a
    thing without exporting it from ``__init__.py``, and this test would
    still pass. Closing that gap needs a structural (e.g. AST-based) scan of
    the package tree, tracked as separate debt, not attempted here.
    """
    import dotmac_ai_operations

    expected = {
        "__version__",
        "AIAcknowledgementAttribution",
        "AIActionDecision",
        "AIActionProposal",
        "AIAdvisoryInvocation",
        "AIAdvisoryResult",
        "AICapabilityExposureRef",
        "AIEvidenceBinding",
        "AIExecutionObservation",
        "AIOperationIntent",
        "AIOperationRefused",
        "AttemptInput",
        "AuthoritativeAcknowledgement",
        "InsightInput",
        "InvalidCapabilityIdError",
        "InvalidContractDigestError",
        "acknowledge_insight",
        "activate_policy_version",
        "authoritative_acknowledgement",
        "create_insight",
        "create_policy",
        "module",
        "publish_policy_version",
        "record_attempt",
        "start_operation",
        "versions_dir",
    }
    assert set(dotmac_ai_operations.__all__) == expected


# --- Package-root export -----------------------------------------------------


def test_new_types_are_exported_from_package_root() -> None:
    import dotmac_ai_operations

    for name in (
        "AICapabilityExposureRef",
        "AIAdvisoryInvocation",
        "AIActionProposal",
        "AIAdvisoryResult",
        "AIEvidenceBinding",
        "AIExecutionObservation",
        "AIActionDecision",
        "InvalidCapabilityIdError",
        "InvalidContractDigestError",
    ):
        assert hasattr(dotmac_ai_operations, name), name
        assert name in dotmac_ai_operations.__all__
