"""``RecoveryExecutionPlanV1`` — what will be done to bring back ONE failed system.

A deployment-shaped plan is not a recovery plan, and this module exists because
that sentence has been true in this package's documentation for three releases
while nothing expressed it as a type.

## What a recovery plan binds that a deployment plan cannot

`FoundationExecutionPlanV1` says *"apply this reviewed change to this target as
it stood when reviewed"*. A recovery says something structurally different:
*"this system is broken; put it back to a state derived from a capture taken
earlier"*. Three facts have to be in the document for that to be reviewable, and
none of them exists in a deployment plan:

* :class:`CapturedPrestateV1` — **what the source system WAS when the bundle was
  taken.** Without it a recovery is authorized against any capture at all, and
  restoring last quarter's bundle satisfies exactly the same approval as
  restoring last night's.
* :class:`FailedSystemObservationV1` — **the failed system's own observed state
  at recovery start.** This is the recovery analogue of `HostPrestateV1` and it
  carries the same argument: between authorization and execution a host moves,
  and an approval that cannot see the starting point authorizes acting on an
  unreviewed one. It is sharper here, because the starting point is by
  definition abnormal — that is why anybody is recovering — and "abnormal" is
  not a synonym for "any state whatsoever".
* :class:`DesiredPoststateV1` — **what the recovered system must present to
  count as recovered.** Without it, "recovered" is whatever the executor
  happened to produce, and a database that restores and cannot run the
  application passes.

## Digests and NAMES, never parsed catalogue facts

The poststate is a descriptor digest, a bundle-manifest digest, and the
verification NAMES that must report passed. It is deliberately not a catalogue
assertion, and the reason is the same one that refused a fourteenth
`BundleComponent`: `recovery.py` runs with no database and no credential
precisely because the manifest carries digests and counts rather than facts, and
putting parsed catalogue facts inside a document on that path would take that
property away. A plan says WHICH proofs must pass. `verify_recovery` owns what
each proof compares, and it is the only thing that reads facts.

The verification names are checked against `spec.BackupDataset.VERIFICATIONS` —
the vocabulary a descriptor can actually declare — rather than against a list
written here. A poststate demanding a proof nothing can declare or perform is
unfalsifiable, which is a worse failure than demanding too few: it never passes,
so it gets removed rather than met. Deriving the vocabulary also means this
module widens on its own when the six currently-undeclarable comparisons in
`recovery.UNDECLARED_COMPARISONS` are retired into it.

## NO ``operation`` FIELD, and the schema name is why

`FoundationExecutionPlanV1` carries `operation` because ONE descriptor yields two
otherwise-identical documents — a deploy and the rollback that erases it — and
the field is the only thing telling them apart. A recovery plan has no sibling.
The field would carry no information, and a field carrying no information is one
a later author will find something to put in.

That has a consequence which must not go unowned. On the deployment plan the
`operation` check is doing DOUBLE duty: it constrains the vocabulary AND it makes
the document self-identifying. Drop the field and the second job needs an owner,
or a recovery plan arriving where a deployment plan is expected has nothing in it
that says otherwise — and type confusion becomes the way an unauthorized act gets
executed under a digest somebody recognises.

The owner is the SCHEMA, enforced at every acceptance point on both sides:

* :func:`canonical_recovery_plan_bytes` refuses a document whose ``schema`` is
  not this one, and `execution_plan.canonical_execution_plan_bytes` refuses this
  one, both through the single guard in `canonical_plan`;
* :func:`require_recovery_plan_digest` refuses anything that is not a
  :class:`RecoveryExecutionPlanV1`, and
  `execution_plan.require_execution_plan_digest` refuses anything that is not a
  `FoundationExecutionPlanV1` — the second of which was a real hole, because
  every plan kind has a ``digest()`` and a swap there used to report a digest
  MISMATCH, which reads as a changed descriptor;
* `engine.run.Executor` refuses a non-deployment plan at CONSTRUCTION rather
  than at the first attribute access.

Each refusal carries its own stable code, and
`test_deployment_foundation_recovery_plan.py` drives a real document of each kind
into the other's acceptance points and requires a distinct code every time —
both directions. A type annotation is not a check; the swap is.

## THIS MODULE IS NOT REACHABLE, deliberately

Nothing in `cli.py`, `engine/`, or any path that touches a host constructs a
`RecoveryExecutionPlanV1`. There is no ``recover`` subcommand, no grant that
covers the act, and ``recover`` is NOT in `authorization.OPERATIONS` — see that
constant, which records both the withdrawal and the measured vocabulary
divergence with `dotmac-deployment-control`.

That staging is the same one `ApplicationFoundationProfile.v1` used: the type
refuses first, reachability comes later, so a half-built authorization chain
cannot read as done. A grant, a replay coordinate and a signed result wrapped
around a plan nobody can execute would be a chain whose every link is correct and
whose SUBJECT does not exist — and it would review as finished.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Final

from .canonical_plan import RECOVERY_PLAN_WRONG_TYPE, canonical_plan_bytes
from .digest import Digest
from .errors import PreconditionFailed, SpecError
from .execution_plan import HostPrestateV1
from .provenance import normalize_digest
from .secrets_guard import require_no_secrets
from .spec import BackupDataset
from .version import VERSION

__all__ = [
    "PRESTATE_UNKNOWN_DISCRIMINATOR",
    "PRESTATE_UNDISCRIMINATED",
    "KNOWN_PRESTATE_DISCRIMINATORS",
    "INCUMBENT_PRESTATE_DISCRIMINATOR",
    "require_incumbent_prestate_digest",
    "incumbent_prestate_digest",
    "canonical_prestate_bytes",
    "PRESTATE_MISMATCH",
    "INCUMBENT_PRESTATE_DIGEST_SCHEMA",
    "PRESTATE_SCHEMA",
    "RECOVERY_PLAN_DIGEST_MISMATCH",
    "RECOVERY_PLAN_DIGEST_SCHEMA",
    "RECOVERY_PLAN_EMPTY_FIELD",
    "RECOVERY_PLAN_NO_VERIFICATION",
    "RECOVERY_PLAN_SCHEMA",
    "RECOVERY_PLAN_UNKNOWN_VERIFICATION",
    "RECOVERY_PLAN_WRONG_TYPE",
    "CapturedPrestateV1",
    "DesiredPoststateV1",
    "FailedSystemObservationV1",
    "RecoveryExecutionPlanV1",
    "canonical_recovery_plan_bytes",
    "declarable_verifications",
    "recovery_plan_digest",
    "render_recovery_plan",
    "require_recovery_plan_digest",
]

RECOVERY_PLAN_SCHEMA: Final = "RecoveryExecutionPlanV1"

#: The name of the VALUE, kept apart from the name of the document — the same
#: split `ExecutionPlanDigestV1` draws, for the same reason: whoever handles the
#: digest needs a word for it that is not the word for a document they never
#: parse.
RECOVERY_PLAN_DIGEST_SCHEMA: Final = "RecoveryExecutionPlanDigestV1"

#: The DOCUMENT schema of the incumbent prestate, so it can be canonicalized and
#: digested on its own rather than only as a member of a recovery plan.
PRESTATE_SCHEMA: Final = "FailedSystemObservationV1"

#: The VALUE schema — the name of the digest, kept apart from the name of the
#: document, the same split `ExecutionPlanDigestV1` draws.
#:
#: **This is a third cross-repository binding and it has an owner.** Control's
#: `RecoveryGrantStatementV1` carries `incumbent_prestate_digest` as a signed
#: term and its `RecoverySubject` requires a caller to state one — and Control
#: NEVER COMPUTES IT. Measured at the peeled `0.1.0a12` tag: no canonicalizer, no
#: hash, only storage and comparison, refusing with `PRESTATE_MISMATCH`.
#:
#: So the value existed in the contract with no authority computing it on either
#: side. That is the exact asymmetry `ExecutionPlanDigestV1` was created to fix —
#: *"Control freezes and signs it without reconstructing it, because a second
#: canonicalizer is a second answer."* If both sides computed a prestate digest
#: independently they would diverge for the same reason `plan_digest` did, and
#: the failure would be a `PRESTATE_MISMATCH` that told nobody anything.
#:
#: The ruled split: Foundation defines these bytes and this function; Platform's
#: INSTALLED ADAPTER computes it, so the producer is the artifact rather than a
#: source tree; Control stores, signs and compares, and implements no second
#: canonicalizer.
INCUMBENT_PRESTATE_DIGEST_SCHEMA: Final = "IncumbentPrestateDigestV1"

#: Refused: the prestate in hand is not the one that was authorized.
PRESTATE_MISMATCH: Final = "recovery_plan.prestate_mismatch"

#: THE FOUNDATION-OWNED DISCRIMINATOR Control stores BESIDE the digest.
#:
#: A digest alone is 64 hex characters. It cannot say which encoding produced it,
#: so a stored value is unfalsifiable the moment more than one encoding could
#: have: `incumbent_prestate_digest NOT NULL` proves only that a string exists.
#:
#: The discriminator names the observation schema AND the rules that turned it
#: into bytes. Control stores it and REQUIRES it; Control does not own it, and
#: neither its migration nor a `RecoveryGrantV1` version may redefine the
#: encoding — that would be the second canonicalizer this whole binding exists
#: to prevent, arriving as a schema change rather than as code.
INCUMBENT_PRESTATE_DISCRIMINATOR: Final = (
    "dotmac.deployment_foundation.incumbent_prestate.v1"
)

#: Every discriminator THIS version can honour. Closed, and it is what makes an
#: unknown one refusable: a consumer that accepted any string would be trusting
#: a producer it has never met to have used rules it cannot check.
KNOWN_PRESTATE_DISCRIMINATORS: Final[frozenset[str]] = frozenset(
    {INCUMBENT_PRESTATE_DISCRIMINATOR}
)

#: Refused: the stored row carries no discriminator.
#:
#: **An undiscriminated row is HISTORICAL AND UNEXECUTABLE, and is never
#: backfilled as V1 by assumption.** It predates the term, so nobody produced its
#: digest under rules anyone can name — and assuming V1 would manufacture
#: provenance for a value whose provenance is exactly what is missing. That is
#: the defect this term exists to close, re-created by a migration.
PRESTATE_UNDISCRIMINATED: Final = "recovery_plan.prestate_undiscriminated"

#: Refused: the discriminator names an encoding this version cannot produce.
#: Distinct from a mismatch, because the repair is a version rather than a
#: re-observation — comparing under rules you do not have is not comparing.
PRESTATE_UNKNOWN_DISCRIMINATOR: Final = "recovery_plan.prestate_unknown_discriminator"

#: Stable identifiers for this module's refusals. Assert these; read the prose.
#: A module with more than one refusal has to be testable on WHICH one fired,
#: and `match=` on a sentence makes the sentence the contract — after which the
#: message cannot be improved without breaking a test, and a test that only ever
#: saw one wording cannot tell two refusals apart.
# `RECOVERY_PLAN_WRONG_TYPE` is imported from `canonical_plan`, which owns
# both object-level codes; see there for why. Re-exported above.
RECOVERY_PLAN_EMPTY_FIELD: Final = "recovery_plan.empty_field"
RECOVERY_PLAN_NO_VERIFICATION: Final = "recovery_plan.no_verification"
RECOVERY_PLAN_UNKNOWN_VERIFICATION: Final = "recovery_plan.unknown_verification"
RECOVERY_PLAN_DIGEST_MISMATCH: Final = "recovery_plan.digest_mismatch"


def declarable_verifications() -> tuple[str, ...]:
    """The verification names a descriptor can declare, READ not respelled.

    A local tuple here would be a second authority over one vocabulary, and the
    two would diverge silently the moment `recovery.UNDECLARED_COMPARISONS`
    retires a member into the declarable set — this module would keep refusing a
    name descriptors had started accepting, and the refusal would look correct.
    """
    return tuple(BackupDataset.VERIFICATIONS)


def _required(value: object, *, field: str, why: str) -> str:
    text = str(value).strip()
    if not text:
        raise SpecError(
            f"RecoveryExecutionPlanV1{field} is empty. {why}",
            code=RECOVERY_PLAN_EMPTY_FIELD,
        )
    return text


@dataclasses.dataclass(frozen=True, slots=True)
class CapturedPrestateV1:
    """What the SOURCE system was when the bundle was captured.

    Identity only, and expressed as digests: the descriptor the source ran
    under, and the manifest of the bundle taken from it. Between them they say
    "this capture, from that configuration" — which is what an approver is
    actually approving when they approve a recovery.

    ``source_target`` is carried because a bundle taken from staging and one
    taken from production are different facts about the same product, and a
    recovery that silently crosses that line is the worst available outcome of
    an approved procedure.
    """

    source_target: str
    descriptor_digest: str
    bundle_manifest_digest: str

    def __post_init__(self) -> None:
        _required(
            self.source_target,
            field=".captured_prestate.source_target",
            why=(
                "a capture with no source is a bundle whose provenance is "
                "unstated, and a recovery from staging into production would "
                "satisfy the same approval as one from production"
            ),
        )
        object.__setattr__(
            self,
            "descriptor_digest",
            normalize_digest(
                self.descriptor_digest,
                where="CapturedPrestateV1.descriptor_digest",
            ),
        )
        object.__setattr__(
            self,
            "bundle_manifest_digest",
            normalize_digest(
                self.bundle_manifest_digest,
                where="CapturedPrestateV1.bundle_manifest_digest",
            ),
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "bundle_manifest_digest": self.bundle_manifest_digest,
            "descriptor_digest": self.descriptor_digest,
            "source_target": self.source_target,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class FailedSystemObservationV1:
    """The failed system's own observed state, at the moment recovery starts.

    The recovery analogue of `HostPrestateV1`, and it REUSES that type for the
    role half rather than restating it — the observation "which image is each
    role container on" is one fact with one shape, and a second spelling of it
    would drift. Reuse of a value type is not the type confusion this module
    guards against; that guard is at the PLAN level, where two documents could
    stand for two different acts.

    ``roles`` being EMPTY is a claim, not an absence: it says the target has no
    role containers at all, which for a failed system is a completely ordinary
    and highly relevant observation. `HostPrestateV1.first_deploy()` spells the
    same claim on the deployment side.

    ``observed_descriptor_digest`` is the one addition, and it is the binding
    that stops a recovery being a migration in disguise: restoring a bundle
    captured under one descriptor onto a system running a different one changes
    the system's configuration under cover of an approval to restore it.

    Deliberately NO timestamp. A clock reading inside the document would make the
    digest unreproducible from the same observation, and this facility reads no
    clock anyway — the same rule `HostPrestateV1` follows by carrying identity
    and not liveness.
    """

    target: str
    roles: HostPrestateV1
    observed_descriptor_digest: str

    def __post_init__(self) -> None:
        _required(
            self.target,
            field=".failed_state.target",
            why=(
                "an observation with no target does not say WHICH system was "
                "found broken, and a recovery plan that cannot name its subject "
                "binds to every host"
            ),
        )
        if not isinstance(self.roles, HostPrestateV1):
            raise SpecError(
                "FailedSystemObservationV1.roles must be a HostPrestateV1, got "
                f"{type(self.roles).__name__}. The typed observation is the "
                "contract; a look-alike is what the type exists to refuse",
                code=RECOVERY_PLAN_WRONG_TYPE,
            )
        object.__setattr__(
            self,
            "observed_descriptor_digest",
            normalize_digest(
                self.observed_descriptor_digest,
                where="FailedSystemObservationV1.observed_descriptor_digest",
            ),
        )

    def as_document(self) -> dict[str, Any]:
        """The document the prestate digest covers. Carries its own schema.

        A nested member of a recovery plan AND a standalone canonical document,
        because Control signs a digest of THIS observation and never of the plan
        that contains it. A sub-document without its own schema cannot be
        canonicalized on its own — the shared core's guard is what stops one
        document kind being hashed as another, and a fragment has no kind.
        """
        return {
            "schema": PRESTATE_SCHEMA,
            "observed_descriptor_digest": self.observed_descriptor_digest,
            "roles": self.roles.as_document(),
            "target": self.target,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_prestate_bytes(self.as_document())

    def digest(self) -> str:
        """``IncumbentPrestateDigestV1`` for this observation.

        The one authority. Control stores, signs and compares this value and
        computes nothing; Platform's installed adapter calls this function.
        """
        return incumbent_prestate_digest(self.as_document())


@dataclasses.dataclass(frozen=True, slots=True)
class DesiredPoststateV1:
    """What the recovered system must PRESENT to count as recovered.

    Digests and names. The descriptor the recovered system must be running, the
    bundle manifest its data must correspond to, and the verification names that
    must report passed.

    ``verifications`` is non-empty by construction. A poststate demanding
    nothing is satisfied by a target that was created and never restored — the
    exact shape of a check that passes because it looked at nothing, which
    `RecoverySession` already refuses on the source-evidence side for the same
    reason.
    """

    descriptor_digest: str
    bundle_manifest_digest: str
    verifications: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "descriptor_digest",
            normalize_digest(
                self.descriptor_digest, where="DesiredPoststateV1.descriptor_digest"
            ),
        )
        object.__setattr__(
            self,
            "bundle_manifest_digest",
            normalize_digest(
                self.bundle_manifest_digest,
                where="DesiredPoststateV1.bundle_manifest_digest",
            ),
        )
        # Sorted and deduplicated: it is a set, and rule 7 keeps order only
        # where order is meaning. Which proofs must pass has no order.
        names = tuple(sorted({str(name).strip() for name in self.verifications}))
        if not names or names == ("",):
            raise SpecError(
                "DesiredPoststateV1.verifications is empty. A poststate that "
                "demands no proof is satisfied by a target that was created and "
                "never restored, which is a check passing because it looked at "
                "nothing",
                code=RECOVERY_PLAN_NO_VERIFICATION,
            )
        declarable = declarable_verifications()
        unknown = sorted(set(names) - set(declarable))
        if unknown:
            raise SpecError(
                f"DesiredPoststateV1.verifications names {unknown}, which no "
                f"descriptor can declare; the vocabulary is {list(declarable)}. "
                "A poststate demanding a proof nothing can declare or perform is "
                "unfalsifiable, and an unfalsifiable requirement is removed "
                "rather than met",
                code=RECOVERY_PLAN_UNKNOWN_VERIFICATION,
            )
        object.__setattr__(self, "verifications", names)

    def as_document(self) -> dict[str, Any]:
        return {
            "bundle_manifest_digest": self.bundle_manifest_digest,
            "descriptor_digest": self.descriptor_digest,
            "verifications": list(self.verifications),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class RecoveryExecutionPlanV1:
    """What will be done to bring back ONE failed system, from ONE capture.

    Every field is part of the binding, and dropping any one produces a plan
    reusable somewhere it was never meant to apply:

    - without ``target``, an approval to recover staging recovers production;
    - without ``captured_prestate``, an approval to restore last night's bundle
      also authorizes last quarter's;
    - without ``failed_state``, a reviewed recovery applies to an unreviewed
      starting point — and the starting point is abnormal by definition, which
      is not the same as unconstrained;
    - without ``desired_poststate``, "recovered" is whatever the executor
      produced;
    - without ``image_digest``, the system comes back on an unreviewed image and
      step 9 proves only that SOMETHING started;
    - without ``environment_inventory``, the same image against a different set
      of resolved materials is the same plan, which it is not.

    NOT carried: ``operation`` (this module's docstring says why), and ``steps``.
    The restore procedure is `recovery.RESTORE_PROCEDURE` — a constant of this
    facility version, which ``foundation_version`` already binds — and
    `RecoveryExecutor.run` asserts the plan it walks IS that contract rather than
    a copy. Listing ten fixed steps in the document would carry no information
    and would invite a later author to make them caller-chosen.
    """

    product: str
    target: str
    foundation_version: str
    image_reference: str
    image_digest: str
    captured_prestate: CapturedPrestateV1
    failed_state: FailedSystemObservationV1
    desired_poststate: DesiredPoststateV1
    environment_inventory: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(
            self.product,
            field=".product",
            why="a plan that cannot name its product cannot be reviewed",
        )
        _required(
            self.target,
            field=".target",
            why=(
                "a recovery plan with no target authorizes every host. The "
                "target is stated by the caller, never derived from a "
                "descriptor, because a derived one would make every comparison "
                "against it compare the descriptor with itself"
            ),
        )
        for field_name, kind in (
            ("captured_prestate", CapturedPrestateV1),
            ("failed_state", FailedSystemObservationV1),
            ("desired_poststate", DesiredPoststateV1),
        ):
            value = getattr(self, field_name)
            if not isinstance(value, kind):
                raise SpecError(
                    f"RecoveryExecutionPlanV1.{field_name} must be a "
                    f"{kind.__name__}, got {type(value).__name__}. The three "
                    "bindings are what make this a recovery plan rather than a "
                    "restore request, and a look-alike is what the types exist "
                    "to refuse",
                    code=RECOVERY_PLAN_WRONG_TYPE,
                )
        object.__setattr__(
            self,
            "image_digest",
            normalize_digest(
                self.image_digest, where="RecoveryExecutionPlanV1.image_digest"
            ),
        )
        object.__setattr__(
            self,
            "environment_inventory",
            tuple(sorted({str(name) for name in self.environment_inventory})),
        )

    def as_document(self) -> dict[str, Any]:
        """The document the digest covers. No wrapper, ever."""
        return {
            "schema": RECOVERY_PLAN_SCHEMA,
            "captured_prestate": self.captured_prestate.as_document(),
            "desired_poststate": self.desired_poststate.as_document(),
            "environment_inventory": list(self.environment_inventory),
            "failed_state": self.failed_state.as_document(),
            "foundation_version": self.foundation_version,
            "image_digest": self.image_digest,
            "image_reference": self.image_reference,
            "product": self.product,
            "target": self.target,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_recovery_plan_bytes(self.as_document())

    def digest(self) -> str:
        """``RecoveryExecutionPlanDigestV1`` for this plan."""
        return recovery_plan_digest(self.as_document())


def canonical_prestate_bytes(document: Any) -> bytes:
    """The exact bytes of an incumbent prestate, per the ten shared rules.

    Through the same core as every other document in this package. A local
    canonicalizer here would be the second answer this whole binding exists to
    prevent — and it would be a second answer to a question ANOTHER REPOSITORY
    is signing.
    """
    return canonical_plan_bytes(document, schema=PRESTATE_SCHEMA, path="prestate")


def incumbent_prestate_digest(document: Any) -> str:
    """``IncumbentPrestateDigestV1 = sha256(canonical FailedSystemObservationV1)``.

    Not the recovery plan digest, not the bundle manifest digest, not the
    descriptor digest. Control carries all four as separate signed terms and
    refuses each with its own code.
    """
    return str(Digest.of(canonical_prestate_bytes(document)))


def require_incumbent_prestate_digest(
    observation: FailedSystemObservationV1,
    *,
    authorized: str,
    discriminator: str,
) -> str:
    """Recompute before acting, or refuse.

    The comparison is against the AUTHORIZED value passed in, never against a
    digest re-derived from the same document and compared with itself. That
    distinction is why the tests exchange document and digest INDEPENDENTLY: a
    check that only ever sees both moved together cannot tell a real comparison
    from ``x == x``.
    """
    if not isinstance(observation, FailedSystemObservationV1):
        raise PreconditionFailed(
            f"this is a {type(observation).__name__}, not a "
            f"{FailedSystemObservationV1.__name__}",
            code=RECOVERY_PLAN_WRONG_TYPE,
        )
    stated = str(discriminator).strip()
    if not stated:
        raise PreconditionFailed(
            "this authorization carries no Foundation prestate discriminator, so "
            "the stored digest cannot say which encoding produced it. An "
            "undiscriminated row is HISTORICAL AND UNEXECUTABLE and is never "
            "backfilled as "
            f"{INCUMBENT_PRESTATE_DISCRIMINATOR!r} by assumption: that would "
            "manufacture provenance for a value whose provenance is exactly what "
            "is missing",
            code=PRESTATE_UNDISCRIMINATED,
        )
    if stated not in KNOWN_PRESTATE_DISCRIMINATORS:
        raise PreconditionFailed(
            f"the authorization names prestate encoding {stated!r}, which this "
            f"facility cannot produce; it knows "
            f"{sorted(KNOWN_PRESTATE_DISCRIMINATORS)}. Comparing under rules "
            "this version does not have is not comparing — the repair is a "
            "version, not a re-observation",
            code=PRESTATE_UNKNOWN_DISCRIMINATOR,
        )
    actual = observation.digest()
    if actual != authorized:
        raise PreconditionFailed(
            f"the authorized incumbent prestate digest is {authorized} and the "
            f"observation in hand digests to {actual}. The failed system is not "
            "the one the recovery was authorized against — recovering a "
            "different incumbent is not the act that was approved",
            code=PRESTATE_MISMATCH,
        )
    return actual


def canonical_recovery_plan_bytes(document: Any) -> bytes:
    """The exact bytes, per the ten rules `canonical_plan` owns.

    Refuses a `FoundationExecutionPlanV1` document by the same guard that
    refuses a wrapper, and that is not incidental: with no ``operation`` field
    on this document the schema IS the thing that tells the two plan kinds
    apart.
    """
    return canonical_plan_bytes(
        document, schema=RECOVERY_PLAN_SCHEMA, path="recovery_plan"
    )


def recovery_plan_digest(document: Any) -> str:
    """``RecoveryExecutionPlanDigestV1 = sha256(canonical RecoveryExecutionPlanV1)``.

    Not the descriptor digest, not the bundle manifest digest, and NOT
    `ExecutionPlanDigestV1` — that last one is the mistake this package is most
    likely to make, because both are "the plan digest" in conversation and the
    two documents describe different acts.
    """
    return str(Digest.of(canonical_recovery_plan_bytes(document)))


def render_recovery_plan(
    *,
    product: str,
    target: str,
    image_reference: str,
    image_digest: str,
    captured_prestate: CapturedPrestateV1,
    failed_state: FailedSystemObservationV1,
    desired_poststate: DesiredPoststateV1,
    environment_inventory: tuple[str, ...] = (),
) -> RecoveryExecutionPlanV1:
    """Render the target-bound recovery plan. The Foundation owns this.

    ``target`` is stated by the CALLER and never derived, for the reason
    `render_execution_plan` gives: a target derived from the descriptor would
    make every comparison against it compare the descriptor with itself and pass
    for every input.

    The finished document is run through :func:`require_no_secrets`, like every
    other document this facility emits — the inventory is material NAMES, never
    a resolved value (ADR-0009).
    """
    rendered = RecoveryExecutionPlanV1(
        product=product,
        target=target,
        foundation_version=VERSION,
        image_reference=image_reference,
        image_digest=image_digest,
        captured_prestate=captured_prestate,
        failed_state=failed_state,
        desired_poststate=desired_poststate,
        environment_inventory=tuple(environment_inventory),
    )
    require_no_secrets(rendered.as_document(), source="recovery plan")
    return rendered


def require_recovery_plan_digest(
    plan: RecoveryExecutionPlanV1, *, authorized: str
) -> str:
    """Recompute before executing, or refuse.

    The recovery counterpart of `execution_plan.require_execution_plan_digest`,
    and the TYPE is checked before the digest for the same reason it is there:
    every plan kind in this package has a ``digest()``, so a swapped argument
    would otherwise produce a digest mismatch — a refusal that sends the reader
    to look for a changed plan when what happened is that two different acts were
    confused.
    """
    if not isinstance(plan, RecoveryExecutionPlanV1):
        raise PreconditionFailed(
            f"this is a {type(plan).__name__}, not a "
            f"{RecoveryExecutionPlanV1.__name__}. A deployment plan and a "
            "recovery plan are not interchangeable at any acceptance point: "
            "they describe different acts, and both can produce a digest",
            code=RECOVERY_PLAN_WRONG_TYPE,
        )
    actual = plan.digest()
    if actual != authorized:
        raise PreconditionFailed(
            f"the authorized recovery plan digest is {authorized} and the plan "
            f"in hand digests to {actual}. Something changed between "
            "authorization and execution, and executing would perform a "
            "recovery nobody reviewed",
            code=RECOVERY_PLAN_DIGEST_MISMATCH,
        )
    return actual
