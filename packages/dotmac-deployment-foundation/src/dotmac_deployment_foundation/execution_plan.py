"""``FoundationExecutionPlanV1`` and ``ExecutionPlanDigestV1`` — the middle term.

A controlled deployment has three parties and one value has to survive all
three. The Foundation renders what will actually run. Platform CP asks Control
to authorize it. Control freezes and signs. Foundation then executes, and the
report has to be recognisable as the thing that was authorized.

## The bug this dissolves

Today there is no shared middle term, and the two ends *look* like they agree.
Control's ``plan_digest`` hashes the spec **wrapped in six sibling keys**; the
Foundation hashes the **descriptor alone**. Same serialization rules, different
payload — so the two values can never be equal, for any input, and nothing says
so out loud. Both sides compute "the plan digest", both are internally
consistent, and the comparison is dead on arrival while reading as correct.

Patching either end would leave the shape intact: whoever normalizes gets to
decide what was authorized, and the other party is trusting a reconstruction.
So the middle term moves to a document the **Foundation renders** and Control
merely **freezes**:

1. Foundation renders :class:`FoundationExecutionPlanV1` from the immutable
   artifact and the authorized environment inventory, and computes
   :func:`execution_plan_digest`.
2. Platform CP submits **that exact digest** and an explicit operation to
   Control.
3. Control freezes and signs it. **Control never reconstructs or normalizes
   the Foundation plan** — it has no canonicalizer for this document, by
   design, because a second canonicalizer is a second answer.
4. Foundation **recomputes the digest before execution**
   (:func:`require_execution_plan_digest`) — the descriptor, the image or the
   inventory may have moved between authorization and execution, and "nothing
   changed since we were authorized" is exactly what a long-running process
   cannot assume (`authorization.ExecutionGrant.require` draws the same line).
5. The execution report carries the same digest and the same operation.

## ``ExecutionPlanDigestV1`` is not three other digests

Named apart because each of the three is a real value that a reader could
plausibly reach for, and every one of them is wrong here:

* **not the descriptor digest** — the descriptor says what the product IS; the
  plan says what will be DONE to one target under one operation. One descriptor
  yields a different plan per target and per operation.
* **not the authorization-envelope digest** — that covers Control's signature
  wrapper, which does not exist yet at step 1 and is not what Foundation
  executes.
* **not Control's internal snapshot digest** — an implementation detail of a
  different system, which is precisely how the six-sibling-keys divergence
  happened.

## Canonicalization — byte level, because two repositories bind to it

Two other lanes compute or compare this value, so the rules are stated
exhaustively rather than left to "whatever ``json.dumps`` did":

1. **Bytes** are ``json.dumps(document, sort_keys=True, separators=(",", ":"),
   ensure_ascii=True).encode("utf-8")``.
2. **ASCII only.** Every string in the document must be ASCII, and a non-ASCII
   string is REFUSED (:func:`_refuse_non_ascii`). With ``ensure_ascii=True`` the
   output is ASCII by construction, so the check and the encoder can never
   disagree — and the whole Unicode-normalization question (``é`` versus
   ``e`` + U+0301, two byte strings for one name) never arises.
3. **Keys sorted** by code point at every depth (``sort_keys=True``).
4. **No insignificant whitespace** (``separators=(",", ":")``).
5. **Every declared key is always present, and ``null`` never appears.**
   Absence is ``""``, ``[]`` or ``false``. A missing key and an explicit null
   are two encodings of one fact and would produce two digests for it.
6. **Integers only.** No floats anywhere: float repr is platform- and
   version-sensitive, and a duration that serializes as ``1.1`` on one runtime
   and ``1.1000000000000001`` on another is a digest that disagrees with itself.
7. **``steps`` preserves plan order** — order is meaning there. Every other
   array is sorted and deduplicated, because it is a set.
8. **No prose.** A step's human ``description`` is excluded deliberately.
   Prose is not what is authorized, and including it would let an edit to a
   sentence change a digest Control has already signed.
9. **The digest covers this document ALONE**, with no wrapper, no envelope and
   no sibling keys. That is the entire lesson of the divergence above.
10. ``foundation_version`` is INSIDE the document, because the plan's meaning is
    the steps *this version* emits — the same reason `IngressPolicy.v1` carries
    it.

## What is deliberately absent

No values. The environment inventory carries material NAMES and environment
KEYS, never a resolved secret (ADR-0009), and :func:`require_no_secrets` is run
over the finished document as well.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Final

from .authorization import OPERATIONS
from .digest import Digest
from .engine.plan import DeploymentPlan
from .errors import PreconditionFailed, SpecError
from .secrets_guard import require_no_secrets
from .spec import ProductDeploymentSpec
from .version import VERSION

__all__ = [
    "EXECUTION_PLAN_DIGEST_SCHEMA",
    "EXECUTION_PLAN_SCHEMA",
    "FoundationExecutionPlanV1",
    "HostPrestateV1",
    "canonical_execution_plan_bytes",
    "execution_plan_digest",
    "render_execution_plan",
    "require_execution_plan_digest",
]

EXECUTION_PLAN_SCHEMA: Final = "FoundationExecutionPlanV1"

#: The name of the VALUE, kept separate from the name of the document. Platform
#: CP submits a digest and Control stores one; both need a word for the thing
#: they are handling that is not the word for the document they never parse.
EXECUTION_PLAN_DIGEST_SCHEMA: Final = "ExecutionPlanDigestV1"


@dataclasses.dataclass(frozen=True, slots=True)
class HostPrestateV1:
    """What the host LOOKED LIKE when this plan was rendered — inside the plan.

    A plan without an observed prestate binds to any host state at all: it says
    "apply this change" without saying "to the host as it stood when this was
    reviewed". Between authorization and execution a host can move — another
    deployment, a manual `docker compose up`, a rollback — and a plan digest
    that cannot see the base state authorizes applying a reviewed change to an
    unreviewed starting point. The prestate closes that: it is part of the
    document, therefore part of `ExecutionPlanDigestV1`, therefore part of what
    Control froze — and the executor RE-OBSERVES before mutating and refuses a
    host that no longer matches.

    ## Deliberately identity, not liveness

    `roles` carries ``(role_code, image_digest)`` pairs and nothing else.
    `running` and `restarts` are OBSERVED by `observe_roles` and deliberately
    excluded here: they are liveness facts that flap between authorization and
    execution (a restart, a crash-loop settling), and a prestate that flaps
    makes every authorized plan unexecutable in practice — after which the
    check gets removed rather than fixed. Which image each existing role
    container is on IS stable, and it is the fact a concurrent deployment
    changes. Liveness stays owned by `verify_roles` and the stabilise window,
    where it is judged at the right time: after the switch.

    An EMPTY prestate is a claim, not an absence: it says "this target has no
    role containers — a first deployment", and executing against a host that
    turns out to have containers refuses like any other mismatch.
    """

    #: Sorted ``(role_code, image_digest)`` pairs, one per existing role
    #: container. Sorted because it is a set-shaped fact (rule 3: canonicalize
    #: unordered collections); a digest must not depend on discovery order.
    roles: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        codes = [code for code, _ in self.roles]
        if codes != sorted(codes):
            raise SpecError(
                "HostPrestateV1.roles must be sorted by role code; an ordering "
                "that depends on discovery would make one host state hash two "
                "ways"
            )
        if len(set(codes)) != len(codes):
            raise SpecError(
                "HostPrestateV1.roles names a role twice; one container per "
                "role is the shape everything downstream assumes"
            )

    @classmethod
    def from_observations(cls, observations: Any) -> HostPrestateV1:
        """From `Effects.observe_roles()` output, identity facts only."""
        return cls(
            roles=tuple(
                sorted(
                    (str(item.code), str(item.image_digest)) for item in observations
                )
            )
        )

    @classmethod
    def first_deploy(cls) -> HostPrestateV1:
        """The explicit empty claim: no role containers exist on this target."""
        return cls(roles=())

    @classmethod
    def from_document(cls, document: Any) -> HostPrestateV1:
        if not isinstance(document, dict):
            raise SpecError(
                f"host prestate must be a JSON object, got {type(document).__name__}"
            )
        roles = document.get("roles")
        if not isinstance(roles, list):
            raise SpecError("host prestate must carry a 'roles' list")
        return cls(
            roles=tuple(
                (str(item["role"]), str(item["image_digest"])) for item in roles
            )
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "roles": [
                {"image_digest": digest, "role": role} for role, digest in self.roles
            ]
        }


def _refuse_non_ascii(value: Any, *, path: str) -> None:
    """Rule 2, applied to every string at every depth.

    Refusing is better than normalizing. A normalizer is a second opinion about
    what the bytes are, and this contract's whole problem was two parties each
    holding a defensible opinion — so there is exactly one rule here and it is
    checkable by anyone in one line.
    """
    if isinstance(value, str):
        if not value.isascii():
            raise SpecError(
                f"{path} contains a non-ASCII character. The execution plan "
                "digest is compared across three systems, and two byte strings "
                "for one name (NFC versus NFD) would silently be two plans. "
                "ASCII removes the question rather than answering it"
            )
        return
    if isinstance(value, bool | int):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _refuse_non_ascii(key, path=f"{path}.{key}")
            _refuse_non_ascii(item, path=f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _refuse_non_ascii(item, path=f"{path}[{index}]")
        return
    if value is None:
        raise SpecError(
            f'{path} is null. Rule 5: absence is "", [] or false, never null — '
            "a missing key and an explicit null are two encodings of one fact "
            "and would produce two digests for it"
        )
    if isinstance(value, float):
        raise SpecError(
            f"{path} is a float. Rule 6: float repr is platform- and "
            "version-sensitive, so a duration serializing as 1.1 here and "
            "1.1000000000000001 elsewhere is a digest that disagrees with itself"
        )
    raise SpecError(
        f"{path} is a {type(value).__name__}, which this document cannot carry"
    )


@dataclasses.dataclass(frozen=True, slots=True)
class FoundationExecutionPlanV1:
    """What will be done, to ONE target, under ONE operation.

    Every field is part of the binding, and dropping any one produces a plan
    that is reusable somewhere it was never meant to apply — the enumeration
    `authorization.ExecutionGrant` makes for a deploy approval, applied to the
    thing being approved rather than to the approval:

    - without ``target``, an authorization for staging authorizes production;
    - without ``operation``, a deploy approval also authorizes the rollback
      that erases it;
    - without ``image_digest`` / ``source_revision`` / ``manifest_digest``, an
      approval for a reviewed artifact authorizes a rebuilt one;
    - without ``descriptor_digest``, an approval for a reviewed configuration
      authorizes an edited one;
    - without ``environment_inventory``, the same image against a different set
      of resolved materials is the same plan, which it is not;
    - without ``application_profile_digest``, an approval for an artifact whose
      foundation bindings were verified also authorizes one whose bindings
      changed underneath it (ADR 0039 § 8);
    - without ``steps``, "deploy" is a word rather than a procedure.
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
    #: The observed base state this plan applies TO — see `HostPrestateV1`.
    #: Required with no default: a plan that does not state its starting point
    #: binds to every starting point.
    host_prestate: HostPrestateV1
    #: `ApplicationFoundationProfile.v1`'s digest, or `""` when the candidate
    #: declares no profile. ADR 0039 § 8: the digest travels with the release
    #: and is read back from the running system and COMPARED.
    #:
    #: Required with no default, and `""` is a stated value rather than an
    #: omission — rule 5 of this module's canonicalization. A default would let
    #: a caller carry "no profile" without deciding it, which is the difference
    #: between an assembly that declares none and one whose plumbing forgot to
    #: ask.
    application_profile_digest: str
    steps: tuple[tuple[str, str, tuple[str, ...], int, int], ...]

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise SpecError(
                f"unknown operation {self.operation!r}; expected one of "
                f"{list(OPERATIONS)}. An open operation is one nobody wrote a "
                "policy for, and Control's vocabulary is closed for the same "
                "reason"
            )
        if not self.target.strip():
            raise SpecError(
                "an execution plan with no target is a plan that authorizes "
                "every host. The target is stated by the caller, never derived "
                "from the descriptor"
            )

    def as_document(self) -> dict[str, Any]:
        """The document the digest covers. No wrapper, ever."""
        return {
            "schema": EXECUTION_PLAN_SCHEMA,
            "application_profile_digest": self.application_profile_digest,
            "descriptor_digest": self.descriptor_digest,
            "environment_inventory": list(self.environment_inventory),
            "foundation_version": self.foundation_version,
            "host_prestate": self.host_prestate.as_document(),
            "image_digest": self.image_digest,
            "image_reference": self.image_reference,
            "manifest_digest": self.manifest_digest,
            "operation": self.operation,
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
                # Rule 7: plan order, never sorted. A reordered procedure is a
                # different procedure, and a digest that could not tell them
                # apart would authorize migrating after switching traffic.
                for kind, target, command, timeout, retries in self.steps
            ],
            "strategy": self.strategy,
            "target": self.target,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_execution_plan_bytes(self.as_document())

    def digest(self) -> str:
        """``ExecutionPlanDigestV1`` for this plan."""
        return execution_plan_digest(self.as_document())


def canonical_execution_plan_bytes(document: Any) -> bytes:
    """The exact bytes, per the ten rules in this module's docstring.

    Public because two other repositories need to be able to produce or check
    them, and a canonicalization that only exists inside a method is one those
    repositories will reimplement — which is how there came to be two answers
    in the first place.
    """
    if not isinstance(document, dict):
        raise SpecError("an execution plan document must be a JSON object")
    if document.get("schema") != EXECUTION_PLAN_SCHEMA:
        raise SpecError(
            f"this is not a {EXECUTION_PLAN_SCHEMA} document (schema "
            f"{document.get('schema')!r}). The digest covers THIS document "
            "alone: hashing a wrapper that merely contains one is how Control's "
            "plan_digest and the Foundation's came to be permanently unequal "
            "while both looked correct"
        )
    _refuse_non_ascii(document, path="execution_plan")
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def execution_plan_digest(document: Any) -> str:
    """``ExecutionPlanDigestV1 = sha256(canonical FoundationExecutionPlanV1)``.

    Not the descriptor digest, not the authorization-envelope digest, not
    Control's internal snapshot digest. See this module's docstring for why each
    of those three is a plausible mistake.
    """
    return str(Digest.of(canonical_execution_plan_bytes(document)))


def render_execution_plan(
    spec: ProductDeploymentSpec,
    plan: DeploymentPlan,
    *,
    target: str,
    operation: str,
    descriptor_digest: str,
    prestate: HostPrestateV1,
    application_profile_digest: str,
) -> FoundationExecutionPlanV1:
    """Render the target-bound execution plan. The Foundation owns this.

    ``target`` and ``descriptor_digest`` are stated by the CALLER rather than
    derived here, and that is deliberate in both cases. A target derived from
    the descriptor would make every comparison against it compare the descriptor
    with itself and pass for every input. A descriptor digest computed here
    would hide which canonicalization produced it, at the exact seam where two
    canonicalizations diverging is the failure being repaired.

    The environment inventory is NAMES ONLY — the materials a host must resolve,
    never a resolved value (ADR-0009). Sorted and deduplicated because it is a
    set; the finished document is additionally run through
    :func:`require_no_secrets`.
    """
    inventory = sorted(
        {
            *spec.runtime_materials,
            *(name for role in spec.roles for name in role.materials),
        }
    )
    rendered = FoundationExecutionPlanV1(
        product=spec.product,
        target=target,
        operation=operation,
        foundation_version=VERSION,
        image_reference=spec.image,
        image_digest=spec.image_digest,
        source_revision=spec.source_revision,
        manifest_digest=spec.manifest_digest,
        descriptor_digest=descriptor_digest,
        host_prestate=prestate,
        application_profile_digest=application_profile_digest,
        strategy=plan.strategy.value,
        environment_inventory=tuple(inventory),
        steps=tuple(
            (
                step.kind.value,
                step.target,
                tuple(step.command),
                step.timeout_seconds,
                step.retries,
            )
            # Rule 8: `step.description` is NOT carried. Prose is not what is
            # authorized, and an edit to a sentence must not change a digest
            # Control has already signed.
            for step in plan.steps
        ),
    )
    require_no_secrets(rendered.as_document(), source="execution plan")
    return rendered


def require_execution_plan_digest(
    plan: FoundationExecutionPlanV1, *, authorized: str
) -> str:
    """Recompute before execution, or refuse. Step 4 of the flow.

    Re-derived at the point of use rather than trusted from authorization. The
    plan is rendered early — while Platform CP is asking Control — and executed
    later, and "the descriptor, the image and the inventory have not changed
    since we were authorized" is precisely what a long-running process cannot
    assume.

    Returns the digest so a caller can put it on the execution report without
    computing it a second time and risking two answers.
    """
    actual = plan.digest()
    if actual != authorized:
        raise PreconditionFailed(
            f"the authorized execution plan digest is {authorized} and the plan "
            f"in hand digests to {actual}. Something changed between "
            "authorization and execution, and executing would run what was not "
            "frozen. This is NOT a canonicalization disagreement to reconcile: "
            "Control never reconstructs or normalizes this document, so a "
            "mismatch is a changed plan"
        )
    return actual
