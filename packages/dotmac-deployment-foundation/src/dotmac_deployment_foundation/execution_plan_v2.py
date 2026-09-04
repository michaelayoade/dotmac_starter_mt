"""``FoundationExecutionPlanV2`` — the plan that can express a principal bootstrap.

## Why a second document and not a field on the first

`FoundationExecutionPlanV1` is frozen. Two other repositories compute or compare
its digest, and a version that grows a field is a version whose digest changes
for documents nobody edited. So V2 is a new document, V1 is untouched, and the
two are told apart by ``schema`` at every acceptance point.

## Why NOT ``Step.command``

The capability being added is *"bootstrap this database principal's credential
from this named secret path"*. The cheapest way to express it is a step whose
``command`` is an ``ALTER ROLE`` string, and that is the one shape this module
exists to refuse.

A command string puts **SQL inside a reviewed, signed plan document**, and from
there the only thing standing between a plan and a credential is
`secrets_guard.require_no_secrets` — a SHAPE detector, which cannot tell an
``ALTER ROLE ... PASSWORD '…'`` from any other sentence. **A typed member cannot
carry a command by construction**, which is a structure rather than a guard, and
it is the same argument `deployment_evidence` makes about a persisted record.

## The plan carries a REFERENCE, never material

:class:`PostgresPrincipalCredentialBootstrapV1` holds a logical service identity,
a declared principal, an OpenBao path and field, an expected version, and the
transition being requested. There is no field for a password, a DSN, a
connection string, SQL, or an executable command — not "those are rejected", but
*there is nowhere to put them*.

A pointer is public by construction: knowing where a secret lives grants nothing
without the authority to read it. `secrets_guard`'s own docstring already draws
that line for the descriptor, and the same line is drawn here.

## ``expected_version`` 1 is CAS-zero, and that is what makes it a TRANSITION

The declared transition is ``absent_to_present``: this principal has never had a
credential. Binding ``expected_version = 1`` means the write creates version 1 —
a compare-and-set against "no record exists" — so a second run against a store
that already holds the record REFUSES rather than rotating a credential other
systems now hold.

That is why the field is required and why the value is checked against the
transition rather than left free. A bootstrap that would silently overwrite is
not a bootstrap; it is a rotation nobody asked for, and the party that finds out
is whoever was still using the old value.

**This module does not perform the CAS and cannot.** The Foundation plans,
invokes and judges; the effect belongs to the assembly. What the plan does is
make the intent unambiguous and unable to mean anything else.

## Foundation only PLANS this — and half of that is not here yet

There is deliberately no ``StepKind`` for the act and no ``Effects`` method to
invoke it. Adding one widens a protocol whose implementers include
``_PROBE_BINDINGS_SOURCE`` in `scripts/release_facility.py` — the probe wheel the
publication gate installs — so a widening makes the gate's own fixture
non-conforming until updated. That is held pending a ruling, and this module is
the half that does not depend on it.

Same staging as `RecoveryExecutionPlanV1` and `ApplicationFoundationProfile.v1`:
the type refuses first, reachability comes later, so a half-built chain cannot
read as done.

## The digest keeps the V1 NAME, and that is not an oversight

:func:`execution_plan_v2_digest` produces an ``ExecutionPlanDigestV1``.

**The value schema and the document schema are deliberately separate names.**
Control freezes the value and never parses the document — it has no
canonicalizer for it, by design, because a second canonicalizer is a second
answer. So a second DOCUMENT version does not make a second VALUE type, Control
needs no change, and the two repositories binding to that value name keep
binding to it.

A reader meeting a ``V2`` document that produces a ``V1``-named digest will
assume it is a mistake and "fix" it. That edit would break both cross-repository
bindings at once, which is why this paragraph is here rather than in a changelog.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Final

from .authorization import OPERATIONS
from .canonical_plan import canonical_plan_bytes
from .digest import Digest
from .errors import PreconditionFailed, SpecError
from .execution_plan import EXECUTION_PLAN_DIGEST_SCHEMA, HostPrestateV1
from .secrets_guard import require_no_secrets
from .version import VERSION

__all__ = [
    "BOOTSTRAP_BAD_PRINCIPAL",
    "BOOTSTRAP_BAD_REFERENCE",
    "BOOTSTRAP_BAD_VERSION",
    "BOOTSTRAP_TRANSITIONS",
    "EXECUTION_PLAN_V2_SCHEMA",
    "EXECUTION_PLAN_V2_WRONG_TYPE",
    "FoundationExecutionPlanV2",
    "PostgresPrincipalCredentialBootstrapV1",
    "canonical_execution_plan_v2_bytes",
    "execution_plan_v2_digest",
    "render_execution_plan_v2",
    "require_execution_plan_v2_digest",
]

EXECUTION_PLAN_V2_SCHEMA: Final = "FoundationExecutionPlanV2"

#: Stable identifiers. Assert these; read the prose.
EXECUTION_PLAN_V2_WRONG_TYPE: Final = "execution_plan_v2.wrong_type"
BOOTSTRAP_BAD_PRINCIPAL: Final = "principal_bootstrap.bad_principal"
BOOTSTRAP_BAD_REFERENCE: Final = "principal_bootstrap.bad_reference"
BOOTSTRAP_BAD_VERSION: Final = "principal_bootstrap.bad_version"

#: The transitions a bootstrap may declare. ONE member today, and a closed
#: vocabulary rather than a bool because the next one — a deliberate, approved
#: rotation — is a different act with a different CAS expectation, and it must
#: arrive as a reviewed member rather than as `expected_version=2` slipping
#: through a boolean.
BOOTSTRAP_TRANSITIONS: Final[tuple[str, ...]] = ("absent_to_present",)

#: A PostgreSQL role name as this facility will accept it: lower-case, machine
#: shaped. Deliberately narrower than PostgreSQL's own rules, which permit a
#: quoted identifier containing almost anything — including a quote, which is
#: how an identifier becomes a statement.
_PRINCIPAL: Final = re.compile(r"^[a-z][a-z0-9_]{2,62}$")

#: An OpenBao pointer with an explicit path and no mutable ref. `secrets_guard`
#: already ALLOWS this shape in a descriptor and explains why a pointer is
#: public by construction; this is the same shape, required rather than merely
#: permitted.
_BAO_REFERENCE: Final = re.compile(r"^bao://[A-Za-z0-9][A-Za-z0-9._/-]{2,190}$")

#: A field name inside the stored record.
_FIELD: Final = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


@dataclasses.dataclass(frozen=True, slots=True)
class PostgresPrincipalCredentialBootstrapV1:
    """One principal, one secret reference, one transition. No material.

    The closed field set IS the refusal. There is no key a password, a DSN, a
    connection string, a fragment of SQL or an executable command could be
    written under — so a reviewer does not have to notice one, and a shape
    detector does not have to catch one.
    """

    #: The logical database or service this principal belongs to. A NAME, so
    #: two deployments of the same product against different hosts produce the
    #: same plan for the same act.
    service: str
    #: The PostgreSQL role that will receive a credential.
    principal: str
    #: Where the credential lives. A pointer, never a value.
    secret_path: str
    #: Which field of the stored record holds it.
    secret_field: str
    #: The store version this write expects. See the module docstring: `1` with
    #: ``absent_to_present`` is a compare-and-set against "no record exists".
    expected_version: int
    transition: str = BOOTSTRAP_TRANSITIONS[0]

    def __post_init__(self) -> None:
        if not _PRINCIPAL.match(str(self.principal)):
            raise SpecError(
                f"principal {self.principal!r} is not an acceptable role name. "
                "This facility takes lower-case machine identifiers only — "
                "narrower than PostgreSQL, which permits a quoted identifier "
                "containing almost anything, including a quote, which is how an "
                "identifier becomes a statement",
                code=BOOTSTRAP_BAD_PRINCIPAL,
            )
        if not _PRINCIPAL.match(str(self.service)):
            raise SpecError(
                f"service {self.service!r} is not a machine identifier. The "
                "service is a logical NAME, so the same act against two hosts "
                "renders the same plan",
                code=BOOTSTRAP_BAD_PRINCIPAL,
            )
        if not _BAO_REFERENCE.match(str(self.secret_path)):
            raise SpecError(
                f"secret_path {self.secret_path!r} is not an OpenBao pointer "
                "(bao://path/to/record). The plan carries a REFERENCE the "
                "executor resolves on the target; a value here would put "
                "material in a reviewed, signed document",
                code=BOOTSTRAP_BAD_REFERENCE,
            )
        if not _FIELD.match(str(self.secret_field)):
            raise SpecError(
                f"secret_field {self.secret_field!r} is not a field name",
                code=BOOTSTRAP_BAD_REFERENCE,
            )
        if str(self.transition) not in BOOTSTRAP_TRANSITIONS:
            raise SpecError(
                f"unknown transition {self.transition!r}; expected one of "
                f"{list(BOOTSTRAP_TRANSITIONS)}. An open transition is one "
                "nobody wrote a compare-and-set expectation for",
                code=BOOTSTRAP_BAD_VERSION,
            )
        version = self.expected_version
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise SpecError(
                f"expected_version must be a positive integer, got {version!r}. "
                "`bool` is an `int` in Python and `True` would sail through as "
                "the version 1, so it is refused explicitly",
                code=BOOTSTRAP_BAD_VERSION,
            )
        if self.transition == "absent_to_present" and version != 1:
            raise SpecError(
                f"an absent_to_present bootstrap must expect version 1, not "
                f"{version}. Version 1 is a compare-and-set against 'no record "
                "exists'; any other expectation would let a second run overwrite "
                "a credential other systems already hold, which is a rotation "
                "nobody asked for wearing a bootstrap's name",
                code=BOOTSTRAP_BAD_VERSION,
            )

    def as_document(self) -> dict[str, Any]:
        return {
            "expected_version": int(self.expected_version),
            "principal": str(self.principal),
            "secret_field": str(self.secret_field),
            "secret_path": str(self.secret_path),
            "service": str(self.service),
            "transition": str(self.transition),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class FoundationExecutionPlanV2:
    """V1's terms, plus the bootstraps this deployment is authorized to perform.

    Every V1 field is carried unchanged and means what it meant. The one addition
    is ``principal_bootstraps``, and it is part of the digest — an approval for a
    deployment that installs no credential does not authorize one that does.
    """

    product: str
    target: str
    operation: str
    foundation_version: str
    image_reference: str
    image_digest: str
    source_revision: str
    manifest_digest: str
    descriptor_digest: str
    strategy: str
    environment_inventory: tuple[str, ...]
    host_prestate: HostPrestateV1
    application_profile_digest: str
    steps: tuple[tuple[str, str, tuple[str, ...], int, int], ...]
    #: Sorted and deduplicated by (service, principal): it is a SET of acts, and
    #: a digest must not depend on the order a caller happened to list them.
    principal_bootstraps: tuple[PostgresPrincipalCredentialBootstrapV1, ...] = ()

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise SpecError(
                f"unknown operation {self.operation!r}; expected one of "
                f"{list(OPERATIONS)}"
            )
        if not self.target.strip():
            raise SpecError(
                "an execution plan with no target is a plan that authorizes "
                "every host"
            )
        if not isinstance(self.host_prestate, HostPrestateV1):
            raise SpecError(
                "host_prestate must be a HostPrestateV1, got "
                f"{type(self.host_prestate).__name__}",
                code=EXECUTION_PLAN_V2_WRONG_TYPE,
            )
        seen: set[tuple[str, str]] = set()
        for bootstrap in self.principal_bootstraps:
            if not isinstance(bootstrap, PostgresPrincipalCredentialBootstrapV1):
                raise SpecError(
                    "principal_bootstraps carries a "
                    f"{type(bootstrap).__name__}, not a "
                    "PostgresPrincipalCredentialBootstrapV1. A mapping would let "
                    "any key through, including one holding a password",
                    code=EXECUTION_PLAN_V2_WRONG_TYPE,
                )
            key = (bootstrap.service, bootstrap.principal)
            if key in seen:
                raise SpecError(
                    f"principal_bootstraps names {key} twice. Two records for "
                    "one principal is how a second, unreviewed expectation "
                    "reaches the same role",
                    code=EXECUTION_PLAN_V2_WRONG_TYPE,
                )
            seen.add(key)
        object.__setattr__(
            self,
            "principal_bootstraps",
            tuple(
                sorted(
                    self.principal_bootstraps,
                    key=lambda item: (item.service, item.principal),
                )
            ),
        )

    def as_document(self) -> dict[str, Any]:
        """The document the digest covers. No wrapper, ever."""
        return {
            "schema": EXECUTION_PLAN_V2_SCHEMA,
            "application_profile_digest": self.application_profile_digest,
            "descriptor_digest": self.descriptor_digest,
            "environment_inventory": list(self.environment_inventory),
            "foundation_version": self.foundation_version,
            "host_prestate": self.host_prestate.as_document(),
            "image_digest": self.image_digest,
            "image_reference": self.image_reference,
            "manifest_digest": self.manifest_digest,
            "operation": self.operation,
            "principal_bootstraps": [
                bootstrap.as_document() for bootstrap in self.principal_bootstraps
            ],
            "product": self.product,
            "source_revision": self.source_revision,
            "steps": [
                {
                    "command": list(command),
                    "kind": kind,
                    "retries": retries,
                    "target": target,
                    "timeout_seconds": timeout,
                }
                for kind, target, command, timeout, retries in self.steps
            ],
            "strategy": self.strategy,
            "target": self.target,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_execution_plan_v2_bytes(self.as_document())

    def digest(self) -> str:
        """An ``ExecutionPlanDigestV1``. See the module docstring — the value
        schema and the document schema are separate names on purpose, and this
        is NOT a mistake to correct."""
        return execution_plan_v2_digest(self.as_document())


def canonical_execution_plan_v2_bytes(document: Any) -> bytes:
    """The exact bytes, per the ten rules `canonical_plan` owns.

    Refuses a V1 document, a `RecoveryExecutionPlanV1` document and a wrapper
    through the same guard, which is what keeps three plan kinds from being
    hashed as one another.
    """
    return canonical_plan_bytes(
        document, schema=EXECUTION_PLAN_V2_SCHEMA, path="execution_plan_v2"
    )


def execution_plan_v2_digest(document: Any) -> str:
    """``ExecutionPlanDigestV1`` over a V2 document.

    The VALUE name tracks the value schema, not the document schema. Control
    freezes this value and never parses the document, so a second document
    version does not make a second value type and Control needs no change.
    """
    return str(Digest.of(canonical_execution_plan_v2_bytes(document)))


def render_execution_plan_v2(
    plan_v1: Any,
    *,
    principal_bootstraps: tuple[PostgresPrincipalCredentialBootstrapV1, ...] = (),
) -> FoundationExecutionPlanV2:
    """Render V2 from an already-rendered V1 plan plus the bootstraps.

    Taking the V1 plan rather than re-deriving every term is deliberate: the
    twelve shared fields have one renderer, so V1 and V2 cannot come to disagree
    about what a deployment IS while disagreeing only about what it additionally
    does. The alternative — a second full renderer — is the copy this package has
    paid for three times.
    """
    rendered = FoundationExecutionPlanV2(
        product=plan_v1.product,
        target=plan_v1.target,
        operation=plan_v1.operation,
        foundation_version=VERSION,
        image_reference=plan_v1.image_reference,
        image_digest=plan_v1.image_digest,
        source_revision=plan_v1.source_revision,
        manifest_digest=plan_v1.manifest_digest,
        descriptor_digest=plan_v1.descriptor_digest,
        strategy=plan_v1.strategy,
        environment_inventory=tuple(plan_v1.environment_inventory),
        host_prestate=plan_v1.host_prestate,
        application_profile_digest=plan_v1.application_profile_digest,
        steps=tuple(plan_v1.steps),
        principal_bootstraps=tuple(principal_bootstraps),
    )
    require_no_secrets(rendered.as_document(), source="execution plan v2")
    return rendered


def require_execution_plan_v2_digest(
    plan: FoundationExecutionPlanV2, *, authorized: str
) -> str:
    """Recompute before execution, or refuse. Type first, for the reason V1 gives.

    Every plan kind in this package can produce a digest, so a swapped argument
    would otherwise report a digest MISMATCH — a refusal that sends the reader to
    look for a changed plan when two different documents were confused.
    """
    if not isinstance(plan, FoundationExecutionPlanV2):
        raise PreconditionFailed(
            f"this is a {type(plan).__name__}, not a "
            f"{FoundationExecutionPlanV2.__name__}. The plan kinds are not "
            "interchangeable at any acceptance point",
            code=EXECUTION_PLAN_V2_WRONG_TYPE,
        )
    actual = plan.digest()
    if actual != authorized:
        raise PreconditionFailed(
            f"the authorized execution plan digest is {authorized} and the plan "
            f"in hand digests to {actual}. Something changed between "
            f"authorization and execution ({EXECUTION_PLAN_DIGEST_SCHEMA} is "
            "recomputed, never accepted from a caller)"
        )
    return actual
