"""``RehearsalGrant.v1`` — Control issues it, this facility only CONSUMES it.

## The violation this closes

`rehearsal.py` is the evidence half of Lane 3 and it is correctly shaped: a
receipt records what happened, `verify_publication` refuses to publish unless
every item passed, and `require_rehearsed_artifact` refuses a receipt about
other bytes. What it does NOT do — and must not — is authorize anything.

The gap was next to it. Item 8 is `provoked_rollback`, *"Rollback, provoked
rather than simulated"*, and provoking it means deliberately breaking a live
target: seeding rules that belong to nobody into `DOCKER-USER` and `INPUT`,
arming an ip6tables rule the apply path structurally cannot clear, and letting
the transaction meet its own failure. That is a destructive act against a host,
and until this module **nothing authorized it**. There was a named act, an
executor for it, and no grant type at all.

`authorization.py` opens by naming the same defect one layer over: the distance
between printing a plan and mutating production must not be "one boolean flag
that the caller supplied to itself", because *the party being restrained is the
party answering the question*. A provocation harness that arms itself is that
sentence with the flag removed entirely.

## There is NO issuer here, and that is the whole design

This module can verify a grant, bind it to the act in hand, and refuse. It
cannot make one. There is no `build_rehearsal_grant`, no default verifier, no
inference of permission from a `RehearsalReceiptV1`, and no key material. The
grant arrives as raw material and becomes usable only by passing through an
:class:`RehearsalGrantVerifier` the ASSEMBLY injects through
`ExecutionBindings.rehearsal_grant_verifier` — never by being parsed out of a
JSON file, which is the rule `provenance.verify_authorization` already states
and this module obeys rather than restates.

`tests/architecture/test_deployment_foundation_rehearsal_grant.py` holds the
static half: no function in this facility RETURNS a
:class:`RehearsalGrantV1`. The near-miss that guard must stay silent on is
`rehearsal.build_receipt`, and the distinction is the point. Minting EVIDENCE
about a run that happened is this facility's job; minting PERMISSION is not.

## Two witnesses, mirroring the deployment path exactly

`provenance.VerifiedAuthorization` makes "execute on unattested material"
unexpressible; `authorization.ExecutionGrant` makes "execute on a flag"
unexpressible. The same two steps, for the same reason:

* :class:`VerifiedRehearsalGrant` — only :func:`verify_rehearsal_grant`
  holds its witness, so a caller who skips the verifier has nothing to
  construct one with.
* :class:`ProvocationPermit` — only :func:`permit_provocation` holds its
  witness, so a provocation harness cannot be handed anything but a permit
  that was bound, term by term, to the act it is about to perform.

:class:`RehearsalGrantV1` itself carries NO witness, deliberately, exactly as
`AuthorizationReceipt` carries none. It is a structurally complete document and
nothing more. A hand-built one is constructible and INERT: it satisfies no
signature, and :func:`permit_provocation` takes the verified type, so offering
the bare document to the provocation path is refused at the boundary.

## The step vocabulary is OWNED here, not mirrored

Control's PR #45 mirrors this facility's `StepKind` as a frozen literal set,
cut at a value because the two are released independently. That is the right
call on that side and it has already drifted: the mirror was read at
``98435a0c`` and does not contain `apply_exposure` or `restore_exposure`, which
`engine/plan.py` has carried since `ExposureTransaction` was retired — and
`apply_exposure` is precisely where item 8's verification refuses. So a grant
Control can currently issue cannot name the step the provocation happens at.

This side does not mirror anything: `provocation_at_step` is checked against
`engine.plan.StepKind`, which this facility owns. A step this executor does not
perform is refused here, and no transcription can go stale.

## What this module does NOT decide

Whether a rehearsal SHOULD be authorized. Same line `provenance.py` draws.
Control owns the decision; this owns whether the act about to happen is the act
that was authorized — an equality check over target, plan digest, refusal and
step. A grant that authorizes a different provocation is not permission; it is
evidence that two things drifted apart.

`single_use_reference` is carried and compared against a set of already-spent
references the CALLER supplies, for the reason Control states about its own
half: this facility is pure and holds no store, so it can refuse a coordinate
it is TOLD was spent and cannot itself know. The durable at-most-once record
belongs to whoever holds the store, and until one exists that region is
UNMONITORED rather than covered.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final, Protocol, runtime_checkable

from .engine.plan import StepKind
from .errors import PreconditionFailed, SpecError, UnknownFieldError
from .provenance import normalize_digest

__all__ = [
    "ACCEPTED_STATEMENT_KEYS",
    "REHEARSAL_GRANT_SCHEMA",
    "REHEARSAL_GRANT_VERSION",
    "REHEARSAL_PURPOSE",
    "ProvocableRefusal",
    "ProvocationPermit",
    "ProvokedTerminal",
    "RehearsalGrantV1",
    "RehearsalGrantVerifier",
    "VerifiedRehearsalGrant",
    "permit_provocation",
    "verify_rehearsal_grant",
]

#: Control's name for the document, spelled exactly as Control emits it. This
#: facility does not own the schema and does not get to rename it: a receiver
#: that accepts a document under a name the issuer never writes accepts nothing.
REHEARSAL_GRANT_SCHEMA: Final = "dotmac.deployment_control.rehearsal_grant"
REHEARSAL_GRANT_VERSION: Final = 1

#: The signer purpose. A key that authorizes an act which MUST FAIL is not the
#: key that authorizes production, and `purpose` is what keeps them apart on the
#: wire. A deployment authorization presented here fails on `schema` before this
#: is read; this is the second, independent refusal.
REHEARSAL_PURPOSE: Final = "deployment_rehearsal"

#: WHERE THE ACCEPT-SHAPE CAME FROM, as an immutable coordinate.
#:
#: `<repository>@<commit>:<path>`. Read rather than imported: Control is a
#: stateful module with SQLAlchemy and a migration lineage, and this facility
#: declares ZERO runtime dependencies (ADR-0070), so every cross-repository
#: coupling is cut at a VALUE — the same rule `provenance.py` states for the
#: authorization receipt.
#:
#: THE COMMIT IS ON AN UNMERGED BRANCH (`feat/rehearsal-grant`, PR #45). The
#: object is immutable and this coordinate will always resolve to these bytes;
#: what it does NOT establish is that this shape is the one Control ships. If
#: #45 lands with a different statement, this side accepts a document nobody
#: issues, which is the `recover` vocabulary divergence repeating with the
#: parties swapped. Agreeing the schema with Control is gated 2026-10-15.
ISSUER_SHAPE_SOURCE: Final = (
    "michaelayoade/dotmac_deployment_control"
    "@3a06488cd34c42caa93b9d9bac89fd203b738246"
    ":src/dotmac_deployment_control/rehearsal_grant.py"
)


class ProvokedTerminal(str, Enum):
    """How a provoked rehearsal must END for the grant to have been honoured.

    DERIVED from the refusal through :data:`_TERMINAL_OF`, never read back off
    the document. The issuer emits it so a receiver need not reach into
    Control's tables; a receiver that TRUSTED the emitted value would let one
    document carry a refusal and a terminal that disagree, which is the single
    contradiction this type exists to make unrepresentable.
    """

    #: The provoked refusal fired and the transaction restored the pre-change
    #: state. This is the rehearsal SUCCEEDING.
    ROLLED_BACK = "rolled_back"


class ProvocableRefusal(str, Enum):
    """Which refusal a rehearsal may deliberately provoke. Closed, one member.

    One member because exactly one provocation is grounded in a published Lane
    3 item (`rehearsal.REQUIRED_ITEMS` item 8) and an executor that performs it.
    `authorization.OPERATIONS` records at length what happens when a vocabulary
    member is added on any weaker basis: *"An executor existing is not the test;
    an executor for THE NAMED ACT is."* A second member here is a coordinated
    change to both systems, not an intention on one side.
    """

    #: Item 8, `provoked_rollback`. The named act is the VERIFICATION REFUSING;
    #: the rollback is what the refusal must produce, which is why the terminal
    #: is derived rather than being a second name for the same thing.
    PLAN_VERIFICATION_REFUSAL = "plan_verification_refusal"


_TERMINAL_OF: Final[Mapping[ProvocableRefusal, ProvokedTerminal]] = {
    ProvocableRefusal.PLAN_VERIFICATION_REFUSAL: ProvokedTerminal.ROLLED_BACK,
}

#: The statement this facility will accept, key for key. Strict in BOTH
#: directions: a missing key is a term nothing bound, and an unknown key may
#: carry a rule this version cannot evaluate, so the document is refused rather
#: than read partly. Identical policy to `AuthorizationReceipt.from_document`.
ACCEPTED_STATEMENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "version",
        "purpose",
        "grant_id",
        "single_use_reference",
        "product_code",
        "target_id",
        "target_ref",
        "environment",
        "candidate_repository",
        "candidate_run_id",
        "candidate_artifact_id",
        "execution_plan_digest",
        "provocation_refusal",
        "provocation_at_step",
        "expected_terminal",
        "lease_id",
        "approval_policy_code",
        "approval_policy_version",
        "approval_decision_ref",
        "approval_decision_status",
        "approved_at",
        "not_before",
        "issued_at",
        "expires_at",
        "control_version",
        "key_id",
        "algorithm",
        "public_key_fingerprint",
    }
)

#: Every step this executor actually performs, read from the type that defines
#: them rather than transcribed. A provocation at a step nothing performs is an
#: authorization for a place that does not exist.
_PERFORMED_STEPS: Final[frozenset[str]] = frozenset(kind.value for kind in StepKind)


def _instant(row: Mapping[str, Any], field: str) -> datetime:
    """Parse an ISO-8601 instant, the same way `provenance._instant` does."""
    text = str(row.get(field) or "").strip()
    if not text:
        raise SpecError(f"RehearsalGrant.v1 statement field {field!r} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpecError(
            f"RehearsalGrant.v1 statement field {field!r} is {text!r}, which is "
            "not an ISO-8601 instant"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SpecError(
            f"RehearsalGrant.v1 statement field {field!r} is missing or empty. "
            "Every field on a grant exists so a reader can go and check it; an "
            "empty one is an unverifiable claim"
        )
    return value.strip()


@dataclasses.dataclass(frozen=True, slots=True)
class RehearsalGrantV1:
    """The grant document, as Control issues it. Structurally complete, INERT.

    Deliberately witness-free, exactly as `provenance.AuthorizationReceipt` is.
    Holding one proves the JSON had the right keys and proves nothing about who
    signed it — which is why the provocation path takes
    :class:`ProvocationPermit` and never this. A hand-built one offered there is
    refused at the type boundary.

    Nothing in this facility RETURNS one. It is constructed at exactly one
    place, inside :func:`verify_rehearsal_grant`, from material an injected
    verifier has already attested.
    """

    grant_id: str
    single_use_reference: str
    product_code: str
    target_id: str
    target_ref: str
    environment: str
    candidate_repository: str
    candidate_run_id: str
    candidate_artifact_id: str
    #: Normalized to this facility's `sha256:<hex>` spelling on the way in, so
    #: a comparison can never fail merely because two owners spell one digest
    #: differently — the digest-format trap `provenance.py` names.
    execution_plan_digest: str
    refusal: ProvocableRefusal
    at_step: str
    #: The lease the rehearsal may run under. A rehearsal authorization must not
    #: outlive its window, and the window has two halves: this and `expires_at`.
    lease_id: str
    approval_policy_code: str
    approval_policy_version: int
    approval_decision_ref: str
    approval_decision_status: str
    approved_at: datetime
    not_before: datetime
    issued_at: datetime
    expires_at: datetime
    control_version: str
    key_id: str
    algorithm: str
    public_key_fingerprint: str
    signature: str

    @property
    def expected_terminal(self) -> ProvokedTerminal:
        """DERIVED. A rehearsal that ends any other way was not authorized."""
        return _TERMINAL_OF[self.refusal]

    def require_live(self, *, now: datetime) -> None:
        """Refuse a grant outside its window.

        `now` is INJECTED, never read here — the rule `AuthorizationReceipt`
        follows and for the same reason: a check that reads a clock cannot be
        re-derived from stored JSON months later, and an expiry a test cannot
        move is a field rather than a gate.
        """
        if now < self.not_before:
            raise PreconditionFailed(
                f"this rehearsal grant is not valid until {self.not_before} and "
                f"it is now {now.isoformat()}. An authorization presented early "
                "is not a weak authorization; the window it names has not opened"
            )
        if now >= self.expires_at:
            raise PreconditionFailed(
                f"this rehearsal grant expired at {self.expires_at} and it is "
                f"now {now.isoformat()}. A provocation deliberately breaks a "
                "target, and whatever was true about that target when the grant "
                "was issued has had the whole window to stop being true"
            )


class _Attestation:
    """Proof that :func:`verify_rehearsal_grant` produced this value."""

    __slots__ = ()


_ATTESTED: Final = _Attestation()


class _Permission:
    """Proof that :func:`permit_provocation` produced this value."""

    __slots__ = ()


_PERMITTED: Final = _Permission()


@dataclasses.dataclass(frozen=True, slots=True)
class VerifiedRehearsalGrant:
    """A grant an injected verifier has ATTESTED — the typed verified terms.

    The same shape as `provenance.VerifiedAuthorization`, one act over. Without
    it, raw grant material would reach the provocation harness through an
    ordinary parse, and structural parsing is not attestation: it proves the
    JSON has the right KEYS and says nothing about whether Control signed it.
    """

    #: Positional and first, with no default, so a hand-built value cannot be
    #: mistaken for an ordinary constructor call in review.
    witness: _Attestation
    grant: RehearsalGrantV1

    def __post_init__(self) -> None:
        if self.witness is not _ATTESTED:
            raise PreconditionFailed(
                "a VerifiedRehearsalGrant may only be produced by "
                "verify_rehearsal_grant(). Constructing one directly is a "
                "rehearsal authorization that attested itself — the exact "
                "failure this type exists to make impossible to write by "
                "accident"
            )


@runtime_checkable
class RehearsalGrantVerifier(Protocol):
    """Attest raw rehearsal-grant material, or raise.

    The PRODUCT supplies this. This facility declares zero runtime dependencies
    (ADR-0070), ships no signature library, and must not grow one: a weak
    in-house verifier would be worse than none because it would read as
    coverage. What the facility owns is that attestation is UNAVOIDABLE, not how
    it is performed.

    `attest_rehearsal` returns the grant document it vouches for —
    deliberately the document rather than a :class:`VerifiedRehearsalGrant`, so
    a product implementation cannot mint verified terms either.

    THE METHOD NAME IS NOT `attest`, and that is deliberate.
    `provenance.AuthorizationVerifier.attest` answers a different question with
    a different key. Sharing the name would let a deployment verifier be passed
    where a rehearsal verifier is expected and satisfy `isinstance` — one key
    answering two questions, which is the property both sides separate purposes
    to prevent.
    """

    def attest_rehearsal(self, material: Mapping[str, Any]) -> Mapping[str, Any]: ...


def verify_rehearsal_grant(
    material: Mapping[str, Any], *, verifier: RehearsalGrantVerifier
) -> VerifiedRehearsalGrant:
    """The ONLY route from raw grant material to verified terms.

    Two steps that must stay separate: the injected verifier decides whether the
    material is authentic, and this module decides whether the attested document
    is a structurally complete grant. Collapsing them would let a product's
    verifier also define what a grant IS.

    This is the sole construction site of :class:`RehearsalGrantV1` in the
    facility, and the static sweep in
    `tests/architecture/test_deployment_foundation_rehearsal_grant.py` allows it
    on an ENFORCEABLE premise it checks rather than asserts: this function takes
    a keyword-only `verifier` and returns :class:`VerifiedRehearsalGrant`. A
    function of this name that lost either is reported like any other issuer.
    """
    if verifier is None:  # pragma: no cover - defensive, typed non-optional
        raise PreconditionFailed(
            "verify_rehearsal_grant requires a RehearsalGrantVerifier. Raw "
            "rehearsal-grant material has no other way in, and this facility "
            "issues no grant of its own to fall back on"
        )
    if not isinstance(material, Mapping):
        raise SpecError(
            f"a rehearsal grant is a JSON object, got {type(material).__name__}"
        )
    attested = verifier.attest_rehearsal(material)
    if not isinstance(attested, Mapping):
        raise SpecError(
            "RehearsalGrantVerifier.attest_rehearsal must return the grant "
            f"document it vouches for, got {type(attested).__name__}"
        )
    envelope = set(attested)
    if envelope != {"statement", "signature"}:
        raise SpecError(
            f"a rehearsal grant envelope carries exactly 'statement' and "
            f"'signature', got {sorted(envelope)}"
        )
    statement = attested["statement"]
    if not isinstance(statement, Mapping):
        raise SpecError(
            f"the rehearsal grant statement is a JSON object, got "
            f"{type(statement).__name__}"
        )
    signature = attested["signature"]
    if not isinstance(signature, str) or not signature.strip():
        raise SpecError("the rehearsal grant carries no signature")

    # SCHEMA FIRST, before any field is read. This is what refuses a DEPLOYMENT
    # authorization presented as rehearsal authority. A deploy approval reaching
    # the provocation path would authorize an act that may succeed for an act
    # whose entire purpose is to fail.
    schema = statement.get("schema")
    if schema != REHEARSAL_GRANT_SCHEMA:
        raise SpecError(
            f"expected {REHEARSAL_GRANT_SCHEMA}, got {schema!r}. A rehearsal is "
            "authorized by its own grant; no deployment authorization becomes "
            "one by carrying a matching field"
        )
    if statement.get("version") != REHEARSAL_GRANT_VERSION:
        raise SpecError(
            f"unsupported rehearsal grant version "
            f"{statement.get('version')!r}; this facility reads "
            f"{REHEARSAL_GRANT_VERSION}"
        )
    keys = set(statement)
    missing = sorted(ACCEPTED_STATEMENT_KEYS - keys)
    if missing:
        raise SpecError(
            f"the rehearsal grant statement is missing {missing}. An absent "
            "term is a term nothing bound, and a grant is only as narrow as the "
            "terms it carries"
        )
    unknown = sorted(str(key) for key in keys - ACCEPTED_STATEMENT_KEYS)
    if unknown:
        raise UnknownFieldError(
            f"the rehearsal grant statement carries unknown field(s) {unknown}. "
            "It may have been issued under rules this version cannot evaluate; "
            "refusing rather than reading it partly"
        )
    purpose = _text(statement, "purpose")
    if purpose != REHEARSAL_PURPOSE:
        raise SpecError(
            f"a rehearsal grant declares purpose {REHEARSAL_PURPOSE!r}, not "
            f"{purpose!r}. The key that authorizes an act which must fail is "
            "not the key that authorizes production"
        )

    raw_refusal = _text(statement, "provocation_refusal")
    try:
        refusal = ProvocableRefusal(raw_refusal)
    except ValueError as exc:
        raise SpecError(
            f"{raw_refusal!r} is not a provocable refusal this version knows "
            f"({sorted(member.value for member in ProvocableRefusal)}). The "
            "repair is a coordinated version, not a re-issue: authorizing a "
            "provocation under rules this version does not have is not "
            "authorizing"
        ) from exc
    at_step = _text(statement, "provocation_at_step")
    if at_step not in _PERFORMED_STEPS:
        raise SpecError(
            f"{at_step!r} is not a step this executor performs "
            f"({sorted(_PERFORMED_STEPS)}). A provocation at a step nothing "
            "performs is an authorization for a place that does not exist. "
            "This set is read from `engine.plan.StepKind`, which this facility "
            "owns, so it cannot be a stale transcription"
        )
    # RE-DERIVED, never read back. The document carries `expected_terminal` so a
    # receiver need not reach into Control's tables; trusting the carried value
    # would let one grant name a refusal and a terminal that disagree.
    carried = statement.get("expected_terminal")
    if carried != _TERMINAL_OF[refusal].value:
        raise SpecError(
            f"the grant carries expected_terminal {carried!r} but "
            f"{refusal.value!r} must end in {_TERMINAL_OF[refusal].value!r}. A "
            "document that names a refusal and a terminal that disagree is "
            "wrong about the only thing it is for"
        )
    policy_version = statement.get("approval_policy_version")
    if (
        not isinstance(policy_version, int)
        or isinstance(policy_version, bool)
        or policy_version < 1
    ):
        raise SpecError(
            f"approval_policy_version must be a positive integer, got "
            f"{policy_version!r}"
        )

    not_before = _instant(statement, "not_before")
    issued_at = _instant(statement, "issued_at")
    expires_at = _instant(statement, "expires_at")
    if not (not_before <= issued_at < expires_at):
        raise SpecError(
            "the authorized window is not not_before <= issued_at < expires_at "
            f"({not_before}, {issued_at}, {expires_at})"
        )

    return VerifiedRehearsalGrant(
        _ATTESTED,
        grant=RehearsalGrantV1(
            grant_id=_text(statement, "grant_id"),
            single_use_reference=_text(statement, "single_use_reference"),
            product_code=_text(statement, "product_code"),
            target_id=_text(statement, "target_id"),
            target_ref=_text(statement, "target_ref"),
            environment=_text(statement, "environment"),
            candidate_repository=_text(statement, "candidate_repository"),
            candidate_run_id=_text(statement, "candidate_run_id"),
            candidate_artifact_id=_text(statement, "candidate_artifact_id"),
            execution_plan_digest=normalize_digest(
                _text(statement, "execution_plan_digest"),
                where="RehearsalGrant.execution_plan_digest",
            ),
            refusal=refusal,
            at_step=at_step,
            lease_id=_text(statement, "lease_id"),
            approval_policy_code=_text(statement, "approval_policy_code"),
            approval_policy_version=int(policy_version),
            approval_decision_ref=_text(statement, "approval_decision_ref"),
            approval_decision_status=_text(statement, "approval_decision_status"),
            approved_at=_instant(statement, "approved_at"),
            not_before=not_before,
            issued_at=issued_at,
            expires_at=expires_at,
            control_version=_text(statement, "control_version"),
            key_id=_text(statement, "key_id"),
            algorithm=_text(statement, "algorithm"),
            public_key_fingerprint=_text(statement, "public_key_fingerprint"),
            signature=signature.strip(),
        ),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ProvocationPermit:
    """Permission for ONE refusal at ONE step against ONE target.

    Every field is part of the binding, on the same argument
    `authorization.ExecutionGrant` makes field by field: drop `at_step` and an
    approval to break the exposure apply also permits breaking the migration;
    drop `target`, and an approval for a disposable rehearsal host permits the
    same act against production; drop `execution_plan_digest`, and an approval
    for a reviewed plan permits an edited one.
    """

    #: Positional and first, with no default, so a hand-built permit cannot be
    #: mistaken for an ordinary constructor call in review.
    witness: _Permission
    refusal: ProvocableRefusal
    at_step: str
    target: str
    execution_plan_digest: str
    lease_id: str
    single_use_reference: str
    grant: RehearsalGrantV1

    def __post_init__(self) -> None:
        if self.witness is not _PERMITTED:
            raise PreconditionFailed(
                "a ProvocationPermit may only be produced by "
                "permit_provocation(). A hand-built permit is a deliberate "
                "break of a live target that authorized itself, which is the "
                "exact failure this type exists to make impossible to write by "
                "accident"
            )

    @property
    def expected_terminal(self) -> ProvokedTerminal:
        return self.grant.expected_terminal

    def require(self, *, refusal: ProvocableRefusal, at_step: str) -> None:
        """Refuse unless this permit covers exactly this act.

        Re-checked at the point of use rather than trusted from construction —
        the same argument `ExecutionGrant.require` makes. The permit is built
        while a harness is being assembled and used later, against a host whose
        state has had the whole interval to change.

        **Target is deliberately not re-checked here.** A provocation harness
        reaches its host through an injected effects object and has no
        independent notion of which host that is, so a comparison at this point
        could only compare the permit against itself and would pass
        unconditionally. That is worse than no check: it reads in a diff exactly
        like a real one. The target binding is made once, in
        :func:`permit_provocation`, against a target the CALLER states
        independently of the grant.
        """
        if self.refusal is not refusal:
            raise PreconditionFailed(
                f"this permit authorizes {self.refusal.value!r}, not "
                f"{getattr(refusal, 'value', refusal)!r}"
            )
        if self.at_step != at_step:
            raise PreconditionFailed(
                f"this permit authorizes a provocation at {self.at_step!r}, not "
                f"at {at_step!r}. A refusal provoked somewhere else is a "
                "different act against the same host"
            )


def permit_provocation(
    *,
    verified: VerifiedRehearsalGrant,
    refusal: ProvocableRefusal,
    at_step: str,
    target: str,
    execution_plan_digest: str,
    now: datetime,
    consumed_references: Iterable[str] = (),
) -> ProvocationPermit:
    """Turn ATTESTED terms into permission to provoke, or refuse.

    Shaped on :func:`authorization.authorize` deliberately, down to the
    argument order and the refusal ordering. Takes a
    :class:`VerifiedRehearsalGrant`, never a bare :class:`RehearsalGrantV1`: a
    grant document is structurally complete, and it becomes verified terms only
    by passing through an injected verifier. Requiring the verified type here is
    what stops a caller parsing a JSON file straight into a destructive act.

    The ONLY issuer of :class:`ProvocationPermit`. Every refusal below is a
    mismatch between what Control authorized and what the caller is holding —
    never a judgement about whether the rehearsal should have been approved,
    which belongs to Control and is not re-litigated here.

    `target` must be stated by the caller INDEPENDENTLY of the grant. Deriving
    it from `grant.target_ref` would make the comparison compare the grant with
    itself and pass for every input, which is the shape of a check that has
    stopped checking.

    `consumed_references` is supplied by the caller because this facility holds
    no store. It refuses a coordinate it is TOLD was spent; it cannot itself
    know one was. See the module docstring for what that leaves unmonitored.
    """
    if not isinstance(verified, VerifiedRehearsalGrant):
        raise PreconditionFailed(
            "permit_provocation takes a VerifiedRehearsalGrant, got "
            f"{type(verified).__name__}. A RehearsalGrantV1 offered here is a "
            "document nobody attested: it proves the JSON had the right keys "
            "and says nothing about whether Control signed it. Route it through "
            "verify_rehearsal_grant() with the assembly's injected verifier"
        )
    grant = verified.grant
    # Time first, before any equality check, so an expired grant is refused for
    # being expired rather than for whichever term happens to disagree — and if
    # every term agrees, an expired grant must still refuse. `now` is supplied
    # by the caller because nothing in this facility reads a clock.
    grant.require_live(now=now)

    spent = {str(item).strip() for item in consumed_references}
    if grant.single_use_reference in spent:
        raise PreconditionFailed(
            f"the replay coordinate {grant.single_use_reference!r} has already "
            "been spent. A re-presentable grant is a second execution "
            "authority, and this act deliberately breaks a target"
        )
    if grant.refusal is not refusal:
        raise PreconditionFailed(
            f"Control authorized {grant.refusal.value!r} but "
            f"{getattr(refusal, 'value', refusal)!r} was requested. Ask Control "
            "for a grant naming this refusal"
        )
    if grant.at_step != at_step:
        raise PreconditionFailed(
            f"the grant authorizes a provocation at {grant.at_step!r} and "
            f"{at_step!r} was requested. A grant that CARRIES a provocation is "
            "not a grant FOR that provocation"
        )
    if grant.target_ref != target:
        raise PreconditionFailed(
            f"the grant authorizes target {grant.target_ref!r}, not {target!r}"
        )
    wanted = normalize_digest(
        execution_plan_digest, where="permit_provocation.execution_plan_digest"
    )
    if grant.execution_plan_digest != wanted:
        raise PreconditionFailed(
            f"the grant authorizes execution plan {grant.execution_plan_digest} "
            f"and the plan in hand is {wanted}. Something changed between "
            "authorization and provocation, and provoking would break a target "
            "under a plan that was not reviewed"
        )
    return ProvocationPermit(
        _PERMITTED,
        refusal=refusal,
        at_step=at_step,
        target=target,
        execution_plan_digest=wanted,
        lease_id=grant.lease_id,
        single_use_reference=grant.single_use_reference,
        grant=grant,
    )
