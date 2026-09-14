"""Stateless advisory protocol: one invocation/result crossing a boundary.

This module carries no workflow node, routing decision, wait deadline or
tool loop. Session, routing/orchestration policy and workflow next-step
remain owned by the consuming product's own orchestration owner (for
example, Sub's ``ai.intake``). Canonical capability declarations — schema,
permission, approval and idempotency — remain domain-owned; this module
carries only an opaque exposure reference to one.

THREAT MODEL (read this before adding another check here). Every type in
this module validates ORDINARY construction — its own ``__init__`` and
``__post_init__``, called with plain keyword/positional arguments — against
ACCIDENT and ordinary misuse: a blank ref, a malformed digest, a naive
datetime, a caller wiring the wrong object into the wrong field. That is
what these checks buy, and it is real.

What they do NOT buy, and were never going to: protection against a
DETERMINED IN-PROCESS CALLER. This is a frozen dataclass running in the
same process as its caller. A caller motivated to smuggle a `next_step` or
a routing marker through this protocol can already do so — trivially — via
``object.__new__(SomeType)`` plus ``object.__setattr__``, which builds an
instance that never runs ``__post_init__`` at all. No amount of exact-type
checking, scalar coercion or subclass rejection closes that door, because
none of it runs unless the caller chooses to go through ``__init__`` in the
first place. A frozen dataclass is not a trust boundary; it cannot be made
into one by adding more validation. Earlier revisions of this module
described some of these checks as closing a "channel" a hostile subclass
could exploit — that framing overclaimed what was achieved: it hardened
what an ACCIDENTAL misuse looks like, not what an adversarial one can do.

Concretely: exact-type checks (``type(x) is ExpectedType``) reject an
innocently-constructed subclass instance, which is a real and common
mistake. Scalar coercion (below) reads the ACTUAL underlying data of a
``str``/``float`` where that is possible without invoking caller-controlled
code, and REFUSES the value outright where it is not — it does not and
cannot "purify" arbitrary caller objects. Neither mechanism is evaluated
against, or claims to defend against, a caller willing to bypass
``__post_init__`` entirely.

The real boundary that keeps orchestration OUT of this package is
architectural, not type-level: the consuming product's own orchestration
owner (for example, Sub's ``ai.intake``) is the one place session, routing
and workflow next-step are allowed to live, and this module simply never
defines fields for them. No caller-supplied subclass can add a field this
module reads, acts on, or persists — the fields this module's types have
are the only fields anything downstream of it will ever see. That is the
guarantee; it does not depend on the caller being honest.

Scalar coercion, concretely: every validated ``str``/``float`` field is
normalized to its exact built-in type before it is validated, and the
normalization REFUSES rather than coerces wherever coercion would require
invoking a method the caller's object could have overridden. Concretely:
a value that is already ``type(x) is str`` (or ``float``) is used as-is;
anything else is REJECTED with a ``ValueError`` naming the field, never
"coerced" via ``str.__new__(str, x)`` — that construction was tried in an
earlier revision of this module and was ITSELF broken, since
``str.__new__(str, x)`` for a non-str argument dispatches ``x.__str__()``
(and ``float.__new__(float, x)`` dispatches ``x.__float__()``), which is
exactly the caller-controlled code path this coercion was meant to avoid:
an object whose ``__str__`` returns a fabricated valid digest, or whose
``__float__`` returns ``0.5`` regardless of its real state, would have
passed validation with a value the caller never actually supplied. Refusing
the value outright is the only honest choice once "read the data without
running caller code" turns out to be impossible for an arbitrary object.
Every aware ``datetime`` is additionally canonicalized to a UTC instant
carrying the plain ``datetime.timezone.utc`` singleton, never the caller's
original ``tzinfo`` object — a ``tzinfo`` subclass can hold its own hidden
state or a mutable offset, and a zero-offset zone tagged that way would
otherwise compare equal to UTC while still carrying it.

``AIExecutionObservation``'s invariants ARE enforced on the authoritative
write path: ``dotmac_ai_operations.service.record_attempt`` takes raw
attempt inputs and constructs the canonical ``AIExecutionObservation``
itself, so a caller cannot hand it an unvalidated ``AttemptInput`` and
cannot bypass this module's invariants on the way to durable storage or
the idempotency fingerprint.

NOT AUTHORIZATION, NOT PROOF OF REVIEW. ``AIActionDecision`` records that
SOME CALLER asserted a human reviewed a proposal and reached an outcome.
That assertion is not authorization to execute anything, and it is not
proof a human actually looked at the proposal — this module has no signer,
no authenticated reviewer identity, and no mechanism to verify either
claim, and it deliberately adds none. A consuming product that needs real
authorization, or real proof of human review, must obtain it from its own
authenticated review system and enforce it there; this protocol only
carries whatever that system (or an unverified caller) chose to assert.
This includes the `edited` outcome: an `edited` decision records an
assertion that a human edited the proposed command before it was executed,
not proof of what was actually approved or what the owner command ended up
containing.

If an authoritative, authenticated review system is ever composed with
this protocol, the receipt it emits would need to be an IMMUTABLE record
bound to, at minimum: the exact reviewed `proposal` (or its canonical
identity), the `execution_policy_version_ref`/`execution_policy_digest` in
force at review time, the exact `proposed_command_ref`/
`proposed_command_digest` acted on, the `outcome` reached, an authenticated
reviewer identity (not a caller-supplied `human_actor_ref` string), the
wall-clock time of review, and a digest over the evidence set considered.
No such service exists today, and composing one is the CONSUMING PRODUCT's
decision to make, not this module's — this module does not invent a
signer, and none of `AIActionDecision`'s fields should be read as
satisfying any part of that specification until such a service actually
backs them.

Evidence references. A naked reference string carries no evidentiary
weight: it can be relocated, replaced, or reinterpreted without anyone
knowing the artifact changed. `AIActionDecision`'s
`resulting_owner_command_evidence` and `AIActionProposal`'s
`supporting_evidence_refs` items are therefore each an `AIEvidenceBinding`
— never a bare `str` — but see that type's own docstring for exactly what
it does and does not prove: a structurally-valid CLAIM the caller made,
not a verified fact. This module's other `*_ref` fields (`proposal_ref`,
`invocation_ref`, `proposed_command_ref`, and similar) are a different
kind of thing entirely: operational lookup handles for THIS protocol's
own structural identity, each already paired with its own content digest
where content identity matters (see the digest fields beside them). They
are not evidence and were never offered as evidence; only a value
explicitly typed `AIEvidenceBinding` carries evidentiary weight in this
protocol, and nothing else should be read as if it did.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from dotmac_ai_operations.contracts import AttemptInput

# Replicated (not imported) from `dotmac_integration.spi`'s `_CAPABILITY_RE` /
# `_CONTRACT_DIGEST_RE` — this package stays dependency-free of
# `dotmac-integration` (ADR-0040: narrow owners import the kernel, not each
# other), but the shapes must match exactly since `contract_digest` here is
# `CapabilityContract.contract_digest` in that owning contract, a different
# family from the kernel's `sha256:`-prefixed schema/operation digests.
_CAPABILITY_ID_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$"
)
_CONTRACT_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InvalidCapabilityIdError(ValueError):
    """A capability id does not match the required business-act syntax."""


class InvalidContractDigestError(ValueError):
    """A digest does not match the required 64-lowercase-hex shape."""


def _exact_str(value: object, name: str = "value") -> str:
    """Return ``value`` if it is already an exact ``str``; otherwise REFUSE
    it.

    An earlier revision of this module tried to "coerce" a non-exact value
    via ``str.__new__(str, value)``, on the premise that this reads the
    underlying buffer directly. That premise was wrong: for anything other
    than an exact ``str``, ``str.__new__(str, value)`` dispatches
    ``value.__str__()`` — exactly the caller-controlled method this check
    exists to avoid trusting. There is no way to "read the data" of an
    arbitrary object claiming to be a string without running its code, so
    a non-exact value is rejected rather than coerced.
    """
    if type(value) is not str:
        raise ValueError(
            f"{name} must be an exact str instance (a str subclass is "
            "refused rather than coerced, since coercing it would "
            f"dispatch its own possibly-overridden __str__), got {type(value)!r}"
        )
    return value


def _exact_optional_str(value: object, name: str = "value") -> str | None:
    return None if value is None else _exact_str(value, name)


def _exact_float(value: object, name: str = "value") -> float:
    """Return ``value`` if it is already an exact ``float``; otherwise
    REFUSE it — see ``_exact_str`` for why coercion (via
    ``float.__new__(float, value)``, dispatching ``value.__float__()``) is
    not a safe substitute. This also refuses ``bool``/``int``: a `bool` is
    a `float`-shaped no-op in arithmetic contexts but not an exact `float`,
    and silently accepting it would let `True`/`1` become a valid `1.0`
    confidence without ever exercising real float validation.
    """
    if type(value) is not float:
        raise ValueError(
            f"{name} must be an exact float instance (a float subclass, "
            "bool or int is refused rather than coerced, since coercing "
            "it would dispatch its own possibly-overridden __float__), "
            f"got {type(value)!r}"
        )
    return value


def _exact_datetime(value: datetime, name: str = "value") -> datetime:
    """Coerce ``value`` to an exact ``datetime``.

    Reconstructs a plain ``datetime`` from the field accessors rather than
    trusting any overridden comparison dunder (``__eq__``, ``__lt__``) on a
    subclass instance. This does not defend against a subclass that also
    overrides the y/m/d/... accessors themselves to lie about their own
    values — a deliberately obscure attack this module does not attempt to
    close, consistent with the module-level threat-model disclaimer.
    """
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime instance, got {type(value)!r}")
    if type(value) is datetime:
        return value
    return datetime(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        tzinfo=value.tzinfo,
        fold=value.fold,
    )


def _require_non_blank(value: str, name: str) -> str:
    exact_value = _exact_str(value, name)
    stripped = exact_value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be blank")
    return stripped


def _require_aware(value: datetime, name: str) -> datetime:
    exact_value = _exact_datetime(value, name)
    if exact_value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    # Canonicalize to a UTC instant carrying the plain `timezone.utc`
    # singleton — an exact `datetime` can still carry a `tzinfo` SUBCLASS
    # holding its own hidden state (or a mutable offset, which would make
    # equality/hash time-dependent under a frozen dataclass), and a
    # zero-offset zone tagged that way would otherwise compare equal to
    # UTC while still carrying it. Reconstructing via the field accessors
    # afterward also guarantees the exact `datetime` TYPE, since
    # `astimezone()` on a datetime subclass can return that same subclass.
    canonical = exact_value.astimezone(UTC)
    return datetime(
        canonical.year,
        canonical.month,
        canonical.day,
        canonical.hour,
        canonical.minute,
        canonical.second,
        canonical.microsecond,
        tzinfo=UTC,
    )


def _require_confidence(value: float, name: str) -> float:
    exact_value = _exact_float(value, name)
    if not math.isfinite(exact_value) or not (0.0 <= exact_value <= 1.0):
        raise ValueError(f"{name} must be a finite value between 0.0 and 1.0")
    return exact_value


def _validate_capability_id(value: str) -> str:
    exact_value = _exact_str(value, "capability_id")
    if not _CAPABILITY_ID_PATTERN.fullmatch(exact_value):
        raise InvalidCapabilityIdError(
            f"capability_id {exact_value!r} must be lowercase dotted segments "
            "(at least two) followed by a trailing '.v<digits>', where "
            "<digits> has no leading zero (rejects v0, v01)"
        )
    return exact_value


def _validate_contract_digest(value: str, name: str) -> str:
    exact_value = _exact_str(value, name)
    if not _CONTRACT_DIGEST_PATTERN.fullmatch(exact_value):
        raise InvalidContractDigestError(
            f"{name} {exact_value!r} must be exactly 64 lowercase hex "
            "characters, matching dotmac_integration.spi's "
            "canonical_digest() output (no 'sha256:' prefix)"
        )
    return exact_value


def _set_non_blank(obj: object, name: str, value: str) -> None:
    object.__setattr__(obj, name, _require_non_blank(value, name))


def _set_contract_digest(obj: object, name: str, value: str) -> None:
    object.__setattr__(obj, name, _validate_contract_digest(value, name))


def _set_confidence(obj: object, name: str, value: float) -> None:
    object.__setattr__(obj, name, _require_confidence(value, name))


def _set_aware_datetime(obj: object, name: str, value: datetime) -> None:
    object.__setattr__(obj, name, _require_aware(value, name))


def _require_exact_class(obj: object, expected: type) -> None:
    """Reject a subclass instance of ``expected`` at the top level.

    A subclass inherits the dataclass-generated ``__eq__`` (which compares
    only the base fields), so it could carry an added workflow/orchestration
    field — a `next_step`, a `wait_deadline` — that never gets examined by
    any check in this module, and still construct successfully if only
    NESTED fields were checked. This closes that at the boundary itself.
    """
    if type(obj) is not expected:
        raise ValueError(
            f"{expected.__name__} must not be subclassed — a subclass "
            "could construct successfully here while still carrying an "
            "extension field (e.g. a workflow `next_step`/`wait_deadline`) "
            f"that this protocol's validation never examines, got {type(obj)!r}"
        )


@dataclass(frozen=True, slots=True)
class AICapabilityExposureRef:
    capability_id: str
    contract_digest: str

    def __post_init__(self) -> None:
        _require_exact_class(self, AICapabilityExposureRef)
        object.__setattr__(
            self, "capability_id", _validate_capability_id(self.capability_id)
        )
        _set_contract_digest(self, "contract_digest", self.contract_digest)


@dataclass(frozen=True, slots=True)
class AIEvidenceBinding:
    """An immutable, structurally-valid CLAIM by the caller about a piece
    of evidence — never a bare locator string, but also not a verified
    fact about the world.

    What this type actually proves, and nothing more: `locator_namespace`,
    `locator_ref` and `media_type` are non-blank exact `str` values, and
    `content_digest` matches the 64-lowercase-hex SHAPE this module uses
    for every digest. That is all. Constructing an `AIEvidenceBinding`
    does not resolve the locator, does not fetch or hash the content it
    names, does not check that `content_digest` is actually the digest of
    whatever `locator_ref` addresses, and does not check that `media_type`
    matches the real shape of that content. A caller can pair one
    locator with an unrelated digest and a false media type, and this
    type will accept it — it has no way to know otherwise.

    Resolving the locator, verifying the digest against fetched content,
    and confirming the media type are the CONSUMER's responsibility; this
    type only guarantees that the three pieces of information travel
    together, in a validated shape, from construction onward. A plain
    locator string with no such binding at all is strictly worse — it
    carries no digest and no media type — but a binding is a well-formed
    claim, not a verified one. Neither form authorizes anything or proves
    review occurred.
    """

    locator_namespace: str
    locator_ref: str
    content_digest: str
    media_type: str

    def __post_init__(self) -> None:
        _require_exact_class(self, AIEvidenceBinding)
        _set_non_blank(self, "locator_namespace", self.locator_namespace)
        _set_non_blank(self, "locator_ref", self.locator_ref)
        _set_contract_digest(self, "content_digest", self.content_digest)
        _set_non_blank(self, "media_type", self.media_type)


@dataclass(frozen=True, slots=True)
class AIAcknowledgementAttribution:
    """An immutable, structurally-valid CLAIM about WHO acknowledged an
    insight and WHEN — never a bare actor string, for the same reason
    ``AIEvidenceBinding`` replaced a bare evidence locator.

    Michael's ruling (round 13/14): a row that pairs authoritative typed
    evidence with an actor/time an N-1 writer can silently overwrite makes
    a false HISTORICAL claim, not merely a stale display one — actor and
    time are part of the acknowledgement's integrity envelope, the same
    way evidence is. This type gives attribution the identical shadowing
    treatment ``AIEvidenceBinding`` already gave evidence: a namespaced,
    typed actor identity that travels together, validated, rather than a
    bare string that could be relocated or reinterpreted with nothing to
    notice.

    ``actor_namespace``/``actor_type`` say what KIND of actor this is and
    in what system it is meaningful (e.g. namespace ``"platform-identity"``,
    type ``"human"`` or ``"service-account"``) — this package does not
    define or constrain the vocabulary, exactly as ``AIEvidenceBinding``
    does not resolve its own locator. ``actor_ref`` is opaque, exactly
    like ``AIEvidenceBinding.locator_ref``: this type only guarantees it
    is a non-blank string, not that it resolves to a real, authenticated
    identity. ``acknowledged_at`` must be an aware ``datetime``, using the
    same validation as everywhere else in this module — see ``_require_aware``.

    What this type does NOT prove, stated as plainly as ``AIEvidenceBinding``
    states its own limits: it does not authenticate the actor, does not
    prove the actor's session was ever verified, and does not prove
    ``acknowledged_at`` matches when anything actually happened — it only
    guarantees these fields travel together, in a validated shape, from
    construction onward. Constructing this type is not authorization and
    not proof of review, matching ``AIActionDecision``'s equivalent
    disclaimer.
    """

    actor_namespace: str
    actor_type: str
    actor_ref: str
    acknowledged_at: datetime

    def __post_init__(self) -> None:
        _require_exact_class(self, AIAcknowledgementAttribution)
        _set_non_blank(self, "actor_namespace", self.actor_namespace)
        _set_non_blank(self, "actor_type", self.actor_type)
        _set_non_blank(self, "actor_ref", self.actor_ref)
        _set_aware_datetime(self, "acknowledged_at", self.acknowledged_at)


@dataclass(frozen=True, slots=True)
class AIAdvisoryInvocation:
    invocation_ref: str
    interaction_ref: str
    context_projection_ref: str
    context_projection_digest: str
    context_projection_as_of: datetime
    context_sensitivity: str
    capability: AICapabilityExposureRef
    execution_policy_version_ref: str
    execution_policy_digest: str

    def __post_init__(self) -> None:
        _require_exact_class(self, AIAdvisoryInvocation)
        _set_non_blank(self, "invocation_ref", self.invocation_ref)
        _set_non_blank(self, "interaction_ref", self.interaction_ref)
        _set_non_blank(self, "context_projection_ref", self.context_projection_ref)
        _set_contract_digest(
            self, "context_projection_digest", self.context_projection_digest
        )
        _set_aware_datetime(
            self, "context_projection_as_of", self.context_projection_as_of
        )
        _set_non_blank(self, "context_sensitivity", self.context_sensitivity)
        if type(self.capability) is not AICapabilityExposureRef:
            raise ValueError(
                "capability must be exactly an AICapabilityExposureRef "
                "instance (not a subclass — this contract compares "
                "capability fields by value, and a subclass that adds "
                "fields would inherit the base __eq__ and compare equal "
                f"despite differing meaning), got {type(self.capability)!r}"
            )
        _set_non_blank(
            self, "execution_policy_version_ref", self.execution_policy_version_ref
        )
        _set_contract_digest(
            self, "execution_policy_digest", self.execution_policy_digest
        )


@dataclass(frozen=True, slots=True)
class AIActionProposal:
    proposal_ref: str
    invocation: AIAdvisoryInvocation
    capability: AICapabilityExposureRef
    proposed_command_ref: str
    proposed_command_digest: str
    supporting_evidence_refs: tuple[AIEvidenceBinding, ...]
    confidence: float
    execution_policy_version_ref: str
    execution_policy_digest: str

    def __post_init__(self) -> None:
        _require_exact_class(self, AIActionProposal)
        _set_non_blank(self, "proposal_ref", self.proposal_ref)
        # The FULL invocation this proposal was generated for — not just a
        # ref/digest subset. A subset (this module's previous shape) leaves
        # `interaction_ref`, `context_projection_ref`,
        # `context_projection_as_of` and `context_sensitivity` uncompared:
        # two invocations sharing an `invocation_ref` and projection digest
        # but differing in any of those would both wrongly accept the same
        # proposal. Carrying the whole object lets `AIAdvisoryResult`
        # compare every field via ordinary dataclass equality.
        if type(self.invocation) is not AIAdvisoryInvocation:
            raise ValueError(
                "invocation must be exactly an AIAdvisoryInvocation "
                "instance (not a subclass — this contract compares "
                "invocation fields by value, and a subclass that adds "
                "fields would inherit the base __eq__ and compare equal "
                f"despite differing meaning), got {type(self.invocation)!r}"
            )
        if type(self.capability) is not AICapabilityExposureRef:
            raise ValueError(
                "capability must be exactly an AICapabilityExposureRef "
                "instance (not a subclass — this contract compares "
                "capability fields by value, and a subclass that adds "
                "fields would inherit the base __eq__ and compare equal "
                f"despite differing meaning), got {type(self.capability)!r}"
            )
        _set_non_blank(self, "proposed_command_ref", self.proposed_command_ref)
        _set_contract_digest(
            self, "proposed_command_digest", self.proposed_command_digest
        )
        if isinstance(self.supporting_evidence_refs, str | bytes):
            raise ValueError(
                "supporting_evidence_refs must be a sequence of "
                "AIEvidenceBinding instances, not a single "
                f"{type(self.supporting_evidence_refs).__name__}"
            )
        evidence_items = tuple(self.supporting_evidence_refs)
        checked_items: list[AIEvidenceBinding] = []
        for index, item in enumerate(evidence_items):
            # A naked string (or any other type) carries no evidentiary
            # weight — every item must be an exact `AIEvidenceBinding`,
            # verified at ITS OWN construction, not a locator this class
            # would have to trust or re-validate.
            if type(item) is not AIEvidenceBinding:
                raise ValueError(
                    "supporting_evidence_refs must contain only exact "
                    "AIEvidenceBinding instances — a naked locator string "
                    "is descriptive metadata only and cannot carry "
                    f"evidentiary weight; item at index {index} is {item!r}"
                )
            checked_items.append(item)
        object.__setattr__(self, "supporting_evidence_refs", tuple(checked_items))
        _set_confidence(self, "confidence", self.confidence)
        _set_non_blank(
            self, "execution_policy_version_ref", self.execution_policy_version_ref
        )
        _set_contract_digest(
            self, "execution_policy_digest", self.execution_policy_digest
        )
        # A proposal must not contradict the invocation it carries: these
        # own scalar fields are independent data (a proposal generator
        # could in principle be given the wrong capability/policy pins to
        # stamp), and without this check nothing at CONSTRUCTION time
        # stops a proposal naming capability/policy B while embedding an
        # invocation for capability/policy A. Only `AIAdvisoryResult`
        # checked this before, and only when the proposal was wrapped in
        # one — a bare, directly-constructed proposal (or one carried by
        # an `AIActionDecision`, which never wraps it in a result) reached
        # a consumer self-contradictory. This is the primary gate now;
        # `AIAdvisoryResult`'s equivalent checks remain as defence in
        # depth, not the only place this is enforced.
        if self.capability != self.invocation.capability:
            raise ValueError(
                "capability must match invocation.capability — a proposal "
                "cannot name a capability different from the invocation "
                "it was generated for"
            )
        if (
            self.execution_policy_version_ref
            != self.invocation.execution_policy_version_ref
        ):
            raise ValueError(
                "execution_policy_version_ref must match "
                "invocation.execution_policy_version_ref — a proposal "
                "cannot be pinned to a different policy version than the "
                "invocation it was generated for"
            )
        if self.execution_policy_digest != self.invocation.execution_policy_digest:
            raise ValueError(
                "execution_policy_digest must match "
                "invocation.execution_policy_digest — a proposal cannot "
                "be pinned to different policy content than the "
                "invocation it was generated for"
            )


@dataclass(frozen=True, slots=True)
class AIAdvisoryResult:
    invocation: AIAdvisoryInvocation
    advisory_value: str
    confidence: float
    action_proposal: AIActionProposal | None = None

    def __post_init__(self) -> None:
        _require_exact_class(self, AIAdvisoryResult)
        if type(self.invocation) is not AIAdvisoryInvocation:
            raise ValueError(
                "invocation must be exactly an AIAdvisoryInvocation "
                "instance (not a subclass — this is the one channel a "
                "workflow/orchestration extension such as `next_step` "
                "could enter through unexamined, and a subclass inherits "
                "the base __eq__ so its extra fields would never be "
                f"compared), got {type(self.invocation)!r}"
            )
        _set_non_blank(self, "advisory_value", self.advisory_value)
        _set_confidence(self, "confidence", self.confidence)
        if (
            self.action_proposal is not None
            and type(self.action_proposal) is not AIActionProposal
        ):
            raise ValueError(
                "action_proposal must be exactly an AIActionProposal "
                "instance or None (not a subclass — this is the one "
                "channel a workflow/orchestration extension such as "
                "`wait_deadline` could enter through unexamined, and a "
                "subclass inherits the base __eq__ so its extra fields "
                f"would never be compared), got {type(self.action_proposal)!r}"
            )
        if self.action_proposal is not None:
            # Full-object equality: this compares EVERY field of the
            # proposal's remembered invocation against this result's
            # invocation — invocation_ref, interaction_ref,
            # context_projection_ref, context_projection_digest,
            # context_projection_as_of, context_sensitivity, capability and
            # both execution-policy pins — not a hand-picked subset. Every
            # field on both sides is coerced to its exact built-in type at
            # construction, so this `!=` cannot be defeated by an
            # overridden `__eq__`/`__ne__` on a scalar.
            if self.action_proposal.invocation != self.invocation:
                raise ValueError(
                    "action_proposal.invocation must match invocation — the "
                    "proposal was not generated for this exact invocation"
                )
            # UNREACHABLE THROUGH ORDINARY CONSTRUCTION, and that is
            # stated plainly rather than left implicit: `AIActionProposal`
            # itself now requires `self.capability`/`execution_policy_
            # version_ref`/`execution_policy_digest` to match its OWN
            # `self.invocation` (see `AIActionProposal.__post_init__`).
            # Given that, and the whole-invocation equality check
            # immediately above (which already fails if `action_proposal.
            # invocation` differs from `self.invocation` in ANY field,
            # including these three), a proposal built through its normal
            # constructor can never reach this point with one of these
            # three fields disagreeing with `self.invocation`. These three
            # checks fire ONLY for a proposal that bypassed
            # `__post_init__` entirely (see `_bypass_construct` in the
            # test suite) — a tampered object, not one any ordinary caller
            # can produce. They remain as defence in depth against that
            # tampering, not because ordinary construction can reach them.
            if self.action_proposal.capability != self.invocation.capability:
                raise ValueError(
                    "action_proposal.capability must match invocation.capability"
                )
            if (
                self.action_proposal.execution_policy_version_ref
                != self.invocation.execution_policy_version_ref
            ):
                raise ValueError(
                    "action_proposal.execution_policy_version_ref must match "
                    "invocation.execution_policy_version_ref"
                )
            if (
                self.action_proposal.execution_policy_digest
                != self.invocation.execution_policy_digest
            ):
                raise ValueError(
                    "action_proposal.execution_policy_digest must match "
                    "invocation.execution_policy_digest"
                )


@dataclass(frozen=True, slots=True)
class AIExecutionObservation(AttemptInput):
    def __post_init__(self) -> None:
        _require_exact_class(self, AIExecutionObservation)
        outcome = _exact_str(self.outcome, "outcome")
        if outcome not in {"succeeded", "failed"}:
            raise ValueError("outcome must be one of 'succeeded' or 'failed'")
        object.__setattr__(self, "outcome", outcome)

        # Blank-after-strip is normalized to `None` BEFORE computing
        # presence — otherwise a whitespace-only value (e.g.
        # `output_ref="   "`) is treated as ABSENT for the pairing/
        # succeeded checks below yet the original blank string is what
        # ends up stored, contradicting the "entirely present or entirely
        # absent" invariant the pairing check exists to enforce. This
        # returns the ORIGINAL (unstripped) value when non-blank — a
        # stripped COPY must never be what gets validated or stored: the
        # owning digest oracle applies `fullmatch` to exactly what was
        # supplied, so a padded digest (`" " + 64 hex + " "`) must be
        # REJECTED, not silently trimmed into a passing one.
        def _blank_to_none(value: str | None, name: str) -> str | None:
            if value is None:
                return None
            exact_value = _exact_str(value, name)
            return None if exact_value.strip() == "" else exact_value

        output_ref = _blank_to_none(self.output_ref, "output_ref")
        output_digest = _blank_to_none(self.output_digest, "output_digest")

        output_ref_present = output_ref is not None
        output_digest_present = output_digest is not None

        # The output pair is content-identity evidence (a mutable lookup
        # reference alongside its immutable digest), so it is either
        # entirely present or entirely absent — regardless of outcome. A
        # `failed` observation carrying a partial pair (e.g. a ref with no
        # digest) would be unverifiable evidence, not a lesser form of it.
        if output_ref_present != output_digest_present:
            raise ValueError(
                "output_ref and output_digest must both be present or both " "be absent"
            )
        if output_digest is not None:
            # Validated against the ORIGINAL text (see `_blank_to_none`
            # above) — a digest is never trimmed before this check.
            output_digest = _validate_contract_digest(output_digest, "output_digest")
        if output_ref is not None:
            # No regex applies to `output_ref`; trimming it for storage is
            # consistent with every other non-blank ref field in this
            # module (see `_require_non_blank`).
            output_ref = output_ref.strip()

        object.__setattr__(self, "output_ref", output_ref)
        object.__setattr__(self, "output_digest", output_digest)

        if outcome == "succeeded" and not (
            output_ref_present and output_digest_present
        ):
            raise ValueError(
                "a succeeded observation requires output_ref and output_digest"
            )

        provider_observation = _exact_optional_str(
            self.provider_observation, "provider_observation"
        )
        model_observation = _exact_optional_str(
            self.model_observation, "model_observation"
        )
        request_observation = _exact_optional_str(
            self.request_observation, "request_observation"
        )
        error_code = _exact_optional_str(self.error_code, "error_code")
        object.__setattr__(self, "provider_observation", provider_observation)
        object.__setattr__(self, "model_observation", model_observation)
        object.__setattr__(self, "request_observation", request_observation)
        object.__setattr__(self, "error_code", error_code)

        if outcome == "failed":
            failure_evidence_fields = (
                provider_observation,
                model_observation,
                request_observation,
                error_code,
            )
            has_failure_evidence = any(
                field is not None and field.strip() != ""
                for field in failure_evidence_fields
            )
            if not has_failure_evidence:
                raise ValueError(
                    "a failed observation requires at least one non-blank "
                    "failure-evidence field (provider_observation, "
                    "model_observation, request_observation, error_code)"
                )

        object.__setattr__(
            self, "attempt_key", _require_non_blank(self.attempt_key, "attempt_key")
        )


_ACTION_DECISION_OUTCOMES = frozenset({"accepted", "edited", "rejected", "expired"})


@dataclass(frozen=True, slots=True)
class AIActionDecision:
    """A caller's ASSERTION that a human reached an outcome on a proposal.

    This is NOT AUTHORIZATION and NOT PROOF OF REVIEW — see the module
    docstring's "NOT AUTHORIZATION, NOT PROOF OF REVIEW" section for the
    full statement, including what an authoritative reviewer receipt would
    have to bind if one is ever composed with this protocol (a decision
    for the consuming product to make, not this module's). In particular,
    `outcome == "edited"` records an assertion that a human edited the
    proposed command before execution, not proof of what the resulting
    owner command actually contained.

    `resulting_owner_command_evidence` is an `AIEvidenceBinding` (locator
    namespace + immutable content digest + media type), never a bare
    string — a naked locator cannot serve as evidence of what the owner
    command actually was.
    """

    proposal_ref: str
    proposal: AIActionProposal
    outcome: Literal["accepted", "edited", "rejected", "expired"]
    human_actor_ref: str | None
    resulting_owner_command_evidence: AIEvidenceBinding | None

    def __post_init__(self) -> None:
        _require_exact_class(self, AIActionDecision)
        _set_non_blank(self, "proposal_ref", self.proposal_ref)
        # `proposal` is the exact reviewed object, not a set of loose scalar
        # copies of its pins. This proves internal self-consistency of
        # whatever `AIActionProposal` the caller supplies (its
        # `proposal_ref` matches its own `proposal.proposal_ref`) — it does
        # NOT prove the supplied proposal ever appeared in a reviewed
        # `AIAdvisoryResult`. A fresh, never-reviewed, internally-consistent
        # proposal still passes every check here; closing that gap needs a
        # decided mechanism (binding to an `AIAdvisoryResult`, or a
        # canonical proposal identity the result records) that is not yet
        # implemented. See CHANGELOG.md.
        if type(self.proposal) is not AIActionProposal:
            raise ValueError(
                "proposal must be exactly an AIActionProposal instance "
                "(not a subclass — this contract compares proposal fields "
                "by value, and a subclass that adds fields would inherit "
                "the base __eq__ and compare equal despite differing "
                f"meaning), got {type(self.proposal)!r}"
            )
        if self.proposal_ref != self.proposal.proposal_ref:
            raise ValueError(
                "proposal_ref must match proposal.proposal_ref — this is "
                "only a self-consistency check between the decision's two "
                "own fields, not evidence the proposal was ever reviewed"
            )
        outcome = _exact_str(self.outcome, "outcome")
        if outcome not in _ACTION_DECISION_OUTCOMES:
            outcomes = sorted(_ACTION_DECISION_OUTCOMES)
            raise ValueError(f"outcome must be one of {outcomes}")
        object.__setattr__(self, "outcome", outcome)

        actor = _exact_optional_str(self.human_actor_ref, "human_actor_ref")
        object.__setattr__(self, "human_actor_ref", actor)
        actor_present = actor is not None and actor.strip() != ""

        evidence = self.resulting_owner_command_evidence
        if evidence is not None and type(evidence) is not AIEvidenceBinding:
            raise ValueError(
                "resulting_owner_command_evidence must be exactly an "
                "AIEvidenceBinding instance or None — a naked locator "
                "string is descriptive metadata only and cannot carry "
                f"evidentiary weight, got {type(evidence)!r}"
            )
        evidence_present = evidence is not None

        if outcome in {"accepted", "edited"}:
            if not actor_present or not evidence_present:
                raise ValueError(
                    f"outcome {outcome!r} requires both human_actor_ref and "
                    "resulting_owner_command_evidence"
                )
        elif outcome == "rejected":
            if not actor_present:
                raise ValueError("outcome 'rejected' requires human_actor_ref")
            if evidence is not None:
                raise ValueError(
                    "outcome 'rejected' must not carry "
                    "resulting_owner_command_evidence"
                )
        else:  # "expired"
            if actor is not None:
                raise ValueError("outcome 'expired' must not carry human_actor_ref")
            if evidence is not None:
                raise ValueError(
                    "outcome 'expired' must not carry "
                    "resulting_owner_command_evidence"
                )


__all__ = [
    "AIAcknowledgementAttribution",
    "AIActionDecision",
    "AIActionProposal",
    "AIAdvisoryInvocation",
    "AIAdvisoryResult",
    "AICapabilityExposureRef",
    "AIEvidenceBinding",
    "AIExecutionObservation",
    "InvalidCapabilityIdError",
    "InvalidContractDigestError",
]
