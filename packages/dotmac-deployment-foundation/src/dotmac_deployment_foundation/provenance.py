"""``DeploymentProvenance.v1`` — what ran, built from what, authorized by what.

The descriptor document (`document.py`) answers "what was authorized". This
module answers the question immediately after it: **is the thing about to
execute the thing that was authorized, and can a reader months later say what
it was built from?**

Six bindings, and each exists because its absence has a named failure:

============================  =================================================
`descriptor_digest`           the canonical descriptor document's digest
`rendered_digests`            one sha256 per rendered asset (the Compose hashes)
`image_digests`               role → ``…@sha256:…``, never a tag
`source_revision`             the full 40-hex commit the descriptor came from
`service_roster`              exactly the roles the descriptor composes
`authorization`               the receipt Control issued for this descriptor
============================  =================================================

## Foundation does not decide authorization, and this module is where that
## line is easiest to cross

`dotmac-deployment-control` owns plans, approvals, attempts and receipts.
This facility owns rendering and execution. So the receipt arrives here as a
typed INPUT — the same shape as `ProbeResult` and `ProbeVantage`, and for the
same reason — and this module **never** imports Control, queries Control, or
evaluates whether an approval *should* have been granted.

What it does instead is a pure equality check: the digest the receipt cites
must equal the digest of the descriptor in hand. That is not an authorization
decision. It is refusing to execute something OTHER than what was authorized,
which is this facility's own business and nobody else's. A receipt that says
"approved" for a different descriptor is not permission; it is evidence that
two things have drifted apart.

The import direction is also load-bearing: `dotmac-deployment-control` is a
stateful module with SQLAlchemy and a migration lineage, and this facility
declares ZERO runtime dependencies (ADR-0070, `AGENTS.md` rule 41). Binding the
receipt by VALUE rather than by import is what keeps both true at once. A
future refactor that "simplifies" this by importing Control breaks the
classification guard, and it should.

## The digest-format trap

Control's ``plan_digest`` is **bare hex**; ``ApprovalEvidence.content_digest``
is compared to it with a raw ``!=``. This facility's
`DeploymentDescriptorDocumentV1.sha256_digest` emits the **prefixed**
``sha256:<hex>`` form, and its docstring already says the producer is the side
that should normalize. :func:`normalize_digest` is that normalization, applied
to both sides before they are compared — so a receipt carrying either form
binds correctly, and a comparison can never fail merely because two owners
spell the same digest differently. Getting this wrong fails in the worst
direction: it looks like a security refusal and is actually a formatting bug,
which is precisely the kind of alarm that gets suppressed.

## Why a tag is refused outright

An image tag is a mutable pointer. ``acme:1.4.2`` is a name someone can move to
different bytes after the approval and before the deploy, and the provenance
record would still read as true. Identity is the digest or there is no
identity, so :func:`build_provenance` refuses a reference that is not
digest-pinned rather than recording one and hoping.

The same argument applies to an abbreviated commit: a 12-hex prefix is not a
revision, it is a lookup that can become ambiguous in a repository that keeps
growing. Only the full 40 hex is accepted, and a branch name is refused.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final, Protocol, runtime_checkable

from .document import build_canonical_document
from .errors import PreconditionFailed, SpecError, UnknownFieldError
from .spec import ProductDeploymentSpec
from .version import VERSION

__all__ = [
    "PROVENANCE_SCHEMA",
    "AuthorizationReceipt",
    "AuthorizationVerifier",
    "DeploymentProvenanceV1",
    "VerifiedAuthorization",
    "build_provenance",
    "normalize_digest",
    "verify_authorization",
]

PROVENANCE_SCHEMA: Final = "DeploymentProvenance.v1"


def _instant(value: str, *, field: str) -> datetime:
    """Parse an ISO-8601 instant, the same way `HostLease` does."""
    text = str(value).strip()
    if not text:
        raise SpecError(f"AuthorizationReceipt.{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpecError(
            f"AuthorizationReceipt.{field} {value!r} is not an ISO-8601 instant"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
#: A digest-pinned reference: anything, then `@sha256:<64 hex>`. A tag may also
#: be present (`acme:1.4.2@sha256:…` is legal and useful for humans) — what is
#: refused is a reference with NO digest at all.
_DIGEST_PINNED = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")


def normalize_digest(value: str, *, where: str) -> str:
    """Return the ``sha256:<64 hex>`` form, accepting either spelling.

    Control stores bare hex, this facility emits the prefixed form, and both
    are the same digest. Normalizing at every boundary means a mismatch that
    this module reports is always a real mismatch.
    """
    text = value.strip().lower()
    if not text:
        raise SpecError(f"{where}: a digest is required, and this one is empty")
    body = text[len("sha256:") :] if text.startswith("sha256:") else text
    if not _SHA256_HEX.match(body):
        raise SpecError(
            f"{where}: {value!r} is not a sha256 digest. Expected 64 hex "
            "characters, optionally prefixed `sha256:`"
        )
    return f"sha256:{body}"


@dataclasses.dataclass(frozen=True, slots=True)
class AuthorizationReceipt:
    """Control's authorization, as a value this facility can check against.

    Deliberately NOT `dotmac_deployment_control.ApprovalEvidence`, though it
    carries the same facts. Importing that type would give a zero-dependency
    build runner a SQLAlchemy dependency and would let this facility reach into
    another owner's state. The caller — the assembly, which legitimately
    depends on both — reads Control and constructs this.

    ## THREE DIGESTS, THREE NAMES — and they are not interchangeable

    This type used to carry ONE digest field, `descriptor_digest`, whose own
    docstring said it held *"Control's `plan_digest` /
    `ApprovalEvidence.content_digest`"* — and `build_provenance` then refused
    unless that value equalled the digest of the descriptor in hand. That is
    two different measurements asserted equal: a digest over Control's frozen
    plan snapshot, and a digest over this facility's canonical descriptor
    document. They agree only while both sides canonicalize identically, and
    `execution_plan.py` records that they once did not — *"Control's
    plan_digest and the Foundation's came to be permanently unequal while both
    looked correct"*. That module was written to fix exactly this and its
    correct design never reached the receipt.

    So each term is named for what it measures, and NOTHING converts between
    them. Two digest shapes that disagree are a finding, not something to
    bridge locally:

    * :attr:`descriptor_digest` — **this facility's** digest of the canonical
      descriptor document, the value Platform CP submitted alongside the plan.
      Re-checked against the descriptor actually in hand.
    * :attr:`execution_plan_digest` — `ExecutionPlanDigestV1`, **the binding**.
      The Foundation renders `FoundationExecutionPlanV1`, Platform CP submits
      that digest, and Control freezes and signs it **without reconstructing
      it** (`execution_plan.py` step 3). It is the one term both sides can
      agree on precisely because only one side ever computes it.
    * :attr:`control_plan_digest` — Control's own internal snapshot digest.
      Recorded for traceability into Control's records and **never compared to
      anything this facility computes**, because it is an implementation detail
      of a different system. Comparing it is how the divergence above happened.
    """

    #: Control's `DeploymentPlan.id`, so the receipt is traceable to one plan.
    plan_id: str
    #: Control's `DeploymentTarget.target_ref` — which target was authorized.
    target_ref: str
    #: This facility's digest of the canonical descriptor document, in either
    #: spelling. NOT Control's plan digest — see the class docstring.
    descriptor_digest: str
    #: `ExecutionPlanDigestV1` — the middle term Control froze. THE binding.
    execution_plan_digest: str
    #: Control's own snapshot digest. Recorded, never compared.
    control_plan_digest: str
    #: `ApprovalEvidence.policy_code` and `.policy_version`.
    policy_code: str
    policy_version: int
    #: `ApprovalEvidence.decision_ref` — the approvals decision this rests on.
    decision_ref: str
    #: When the approval stops being usable, ISO-8601 UTC. REQUIRED and with no
    #: default, because a default is a policy and this facility does not own
    #: authorization policy — Control decides how long its own approval is good
    #: for. An approval with no end is a standing permission, and a standing
    #: permission to mutate production is what an approval exists to avoid: the
    #: gap between "this was reviewed" and "this may run" is the whole control.
    #: `HostLease` already draws exactly this line for the host itself.
    expires_at: str
    #: `DeploymentPlan.approved_at`, ISO-8601 UTC. A string, because a digest
    #: must be re-derivable from stored JSON months later (document.py rule 1).
    approved_at: str
    #: The exact `dotmac-deployment-control` version that issued it. A receipt
    #: is only as meaningful as the rules that produced it, and those live in a
    #: versioned package that is currently moving repositories.
    control_version: str
    #: Which operation Control authorized — ``deploy`` or ``rollback``, never
    #: both. A receipt that did not name one would let a single approval both
    #: make a change and erase it; see `authorization.py`. Required, because a
    #: default here would silently re-create exactly that.
    operation: str

    def __post_init__(self) -> None:
        """Refuse a structurally incomplete receipt — NOT an unapproved one.

        The distinction matters and is easy to lose. Checking that
        `decision_ref` is present is checking that this value is a receipt at
        all; a receipt with no decision behind it is a malformed input, in the
        same way a socket listing with no ports is. Checking whether the
        decision was *favourable* would be evaluating authorization, which
        belongs to Control and is refused here by omission.
        """
        for name in ("plan_id", "target_ref", "policy_code", "decision_ref"):
            if not str(getattr(self, name)).strip():
                raise SpecError(
                    f"AuthorizationReceipt.{name} is empty. A receipt without "
                    "it cannot be traced back to the decision it claims, so it "
                    "is not a receipt — this is a malformed input, not a "
                    "refusal of the approval itself"
                )
        if not str(self.approved_at).strip():
            raise SpecError(
                "AuthorizationReceipt.approved_at is empty: a receipt that "
                "cannot say WHEN it was issued cannot be aged out or ordered "
                "against a later one"
            )
        if not str(self.control_version).strip():
            raise SpecError(
                "AuthorizationReceipt.control_version is empty. The receipt's "
                "meaning depends on the rules that produced it, and those are "
                "versioned — an unversioned receipt cannot be re-checked later"
            )
        approved = _instant(self.approved_at, field="approved_at")
        expires = _instant(self.expires_at, field="expires_at")
        if expires <= approved:
            raise SpecError(
                f"AuthorizationReceipt expires at {self.expires_at}, which is "
                f"not after it was approved at {self.approved_at}. An approval "
                "with no duration authorizes nothing and would refuse every "
                "run, which reads as a broken deployment rather than as a "
                "malformed receipt"
            )
        if not str(self.execution_plan_digest).strip():
            raise SpecError(
                "AuthorizationReceipt.execution_plan_digest is empty. This is "
                "the term Control actually froze, and it is the only one both "
                "sides agree on without either re-deriving the other's "
                "document. A receipt without it authorizes a descriptor and a "
                "target but says nothing about what will be DONE to them"
            )
        if not str(self.control_plan_digest).strip():
            raise SpecError(
                "AuthorizationReceipt.control_plan_digest is empty. It is "
                "never compared against anything this facility computes, but "
                "it is what makes a receipt traceable back into Control's own "
                "records, and a receipt that cannot be traced there is not one"
            )
        if self.operation not in ("deploy", "rollback"):
            raise SpecError(
                f"AuthorizationReceipt.operation must be 'deploy' or "
                f"'rollback', got {self.operation!r}. An unnamed operation "
                "would make one approval cover both making a change and "
                "rolling it back"
            )
        if int(self.policy_version) < 1:
            raise SpecError(
                f"AuthorizationReceipt.policy_version must be at least 1, got "
                f"{self.policy_version!r}"
            )

    def require_live(self, *, now: datetime) -> None:
        """Refuse an approval whose window has closed.

        `now` is INJECTED rather than read here — the same rule
        `build_provenance` follows and for the same reason: a value that reads a
        clock cannot be re-derived from stored JSON months later, and a test
        that cannot move time tests nothing about expiry.
        """
        expires = _instant(self.expires_at, field="expires_at")
        if now >= expires:
            raise PreconditionFailed(
                f"this authorization expired at {self.expires_at} and it is now "
                f"{now.isoformat()}. An expired approval is not a weak "
                "approval: whatever was true about the target when it was "
                "granted has had the whole window to stop being true"
            )

    @property
    def execution_plan_digest_normalized(self) -> str:
        """The frozen `ExecutionPlanDigestV1`, in this facility's spelling."""
        return normalize_digest(
            self.execution_plan_digest,
            where="AuthorizationReceipt.execution_plan_digest",
        )

    @property
    def descriptor_digest_normalized(self) -> str:
        """The digest in this facility's canonical `sha256:<hex>` spelling.

        Control writes bare hex; this facility writes prefixed. Comparing the
        two raw is the digest-format trap this module's docstring warns about,
        so every comparison goes through here rather than through `==` on the
        stored string.
        """
        return normalize_digest(
            self.descriptor_digest, where="AuthorizationReceipt.descriptor_digest"
        )

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> AuthorizationReceipt:
        """Parse a receipt the assembly read out of Control and wrote to disk.

        Strict about unknown keys. A receipt carrying a field this version does
        not understand may have been issued under rules this version cannot
        evaluate, and silently ignoring it would mean executing against an
        approval we only partly read.
        """
        known = {
            "plan_id",
            "target_ref",
            "descriptor_digest",
            "expires_at",
            "execution_plan_digest",
            "control_plan_digest",
            "policy_code",
            "policy_version",
            "decision_ref",
            "approved_at",
            "control_version",
            "operation",
        }
        unknown = sorted(set(document) - known - {"schema"})
        if unknown:
            raise UnknownFieldError(
                f"AuthorizationReceipt has unknown field(s) {unknown}. This "
                "receipt may have been issued under rules this version cannot "
                "evaluate; refusing rather than reading it partly"
            )
        missing = sorted(known - set(document))
        if missing:
            raise SpecError(
                f"AuthorizationReceipt is missing required field(s) {missing}"
            )
        return cls(
            plan_id=str(document["plan_id"]),
            target_ref=str(document["target_ref"]),
            descriptor_digest=str(document["descriptor_digest"]),
            expires_at=str(document["expires_at"]),
            execution_plan_digest=str(document["execution_plan_digest"]),
            control_plan_digest=str(document["control_plan_digest"]),
            policy_code=str(document["policy_code"]),
            policy_version=int(document["policy_version"]),
            decision_ref=str(document["decision_ref"]),
            approved_at=str(document["approved_at"]),
            control_version=str(document["control_version"]),
            operation=str(document["operation"]),
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "target_ref": self.target_ref,
            "operation": self.operation,
            "descriptor_digest": self.descriptor_digest_normalized,
            "execution_plan_digest": self.execution_plan_digest_normalized,
            # Recorded EXACTLY as Control wrote it. Not normalized, because
            # normalizing implies this facility understands the value well
            # enough to restate it, and the whole point is that it does not.
            "control_plan_digest": str(self.control_plan_digest),
            "policy_code": self.policy_code,
            "policy_version": int(self.policy_version),
            "decision_ref": self.decision_ref,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
            "control_version": self.control_version,
        }


class _Attestation:
    """Proof that :func:`verify_authorization` produced this value."""

    __slots__ = ()


_ATTESTED: Final = _Attestation()


@dataclasses.dataclass(frozen=True, slots=True)
class VerifiedAuthorization:
    """A receipt an injected verifier has ATTESTED — the typed verified terms.

    The same shape as `ExecutionGrant`, for the same reason and one layer
    earlier. `ExecutionGrant` made "execute on a flag" unexpressible; this makes
    "execute on unattested material" unexpressible.

    Without it, raw envelope material reached the executor through
    :meth:`AuthorizationReceipt.from_document` — a public classmethod any caller
    can hand a dict. Structural parsing is not attestation: it proves the JSON
    has the right KEYS, and says nothing about whether Control signed it. A
    verifier that a caller can route around is decoration.

    So the witness is module-private and only :func:`verify_authorization`
    holds it. A caller who skips the verifier has nothing to construct this
    with, which is the difference between a guard and a convention. As with the
    grant, this does not stop someone importing `_ATTESTED`; it makes the bypass
    one grep and one obviously-wrong import rather than an omission that reads
    like ordinary code.
    """

    #: Positional and first, with no default, so a hand-built value cannot be
    #: mistaken for an ordinary constructor call in review.
    witness: _Attestation
    receipt: AuthorizationReceipt

    def __post_init__(self) -> None:
        if self.witness is not _ATTESTED:
            raise PreconditionFailed(
                "a VerifiedAuthorization may only be produced by "
                "verify_authorization(). Constructing one directly is an "
                "authorization that attested itself — the exact failure this "
                "type exists to make impossible to write by accident"
            )


@runtime_checkable
class AuthorizationVerifier(Protocol):
    """Attest raw authorization-envelope material, or raise.

    The PRODUCT supplies this. This facility declares zero runtime dependencies
    (ADR-0070), so it ships no signature library and must not grow one; a weak
    in-house verifier would be worse than none, because it would look like
    coverage. What the facility owns is that attestation is UNAVOIDABLE, not
    how it is performed.

    `attest` returns the receipt document it vouches for — deliberately the
    document rather than a `VerifiedAuthorization`, so a product implementation
    cannot mint verified terms either. Only :func:`verify_authorization`
    does that, after this returns.
    """

    def attest(self, material: Mapping[str, Any]) -> Mapping[str, Any]: ...


def verify_authorization(
    material: Mapping[str, Any], *, verifier: AuthorizationVerifier
) -> VerifiedAuthorization:
    """The ONLY route from raw envelope material to verified terms.

    Two steps that must stay separate: the injected verifier decides whether
    the material is authentic, and this module decides whether the attested
    document is a structurally complete receipt. Collapsing them would let a
    product's verifier also define what a receipt IS.
    """
    if verifier is None:  # pragma: no cover - defensive, typed non-optional
        raise PreconditionFailed(
            "verify_authorization requires an AuthorizationVerifier. Raw "
            "authorization material has no other way in"
        )
    document = verifier.attest(material)
    if not isinstance(document, Mapping):
        raise SpecError(
            "AuthorizationVerifier.attest must return the receipt document it "
            f"vouches for, got {type(document).__name__}"
        )
    return VerifiedAuthorization(
        _ATTESTED, receipt=AuthorizationReceipt.from_document(document)
    )


@dataclasses.dataclass(frozen=True, slots=True)
class DeploymentProvenanceV1:
    """The six bindings, canonicalized, with a digest over the whole set.

    Frozen and digest-bearing for the same reason the descriptor document is:
    a reader with only the stored JSON can re-derive the digest and check that
    the record was not edited after the fact.
    """

    content: dict[str, Any]

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def sha256_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def descriptor_digest(self) -> str:
        return str(self.content["descriptor_digest"])

    @property
    def source_revision(self) -> str:
        return str(self.content["source_revision"])

    @property
    def service_roster(self) -> tuple[str, ...]:
        return tuple(self.content["service_roster"])


def _check_roster(
    spec: ProductDeploymentSpec, roster: Sequence[str]
) -> tuple[str, ...]:
    """The roster must be EXACTLY the descriptor's roles.

    Not a subset and not a superset. A subset would let a provenance record
    describe a deployment that quietly dropped a worker; a superset would let
    it claim a service the descriptor never composed. Either way the record
    would describe a deployment that is not the one the digest covers.
    """
    declared = tuple(sorted(role.code for role in spec.roles))
    supplied = tuple(sorted(str(name) for name in roster))
    if supplied != declared:
        missing = sorted(set(declared) - set(supplied))
        extra = sorted(set(supplied) - set(declared))
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unexpected {extra}")
        raise SpecError(
            "the service roster does not match the descriptor's roles "
            f"({'; '.join(detail) or 'ordering only'}). The roster names what "
            "actually composed; if it differs from the descriptor, the digest "
            "covers a different deployment than the one that ran"
        )
    return declared


def _check_images(
    spec: ProductDeploymentSpec, images: Mapping[str, str]
) -> dict[str, str]:
    """Every composed role has a digest-pinned image, and nothing else does."""
    declared = {role.code for role in spec.roles}
    supplied = set(images)
    if supplied != declared:
        missing = sorted(declared - supplied)
        extra = sorted(supplied - declared)
        raise SpecError(
            f"image digests must cover exactly the descriptor's roles: "
            f"missing {missing}, unexpected {extra}"
        )
    out: dict[str, str] = {}
    for role in sorted(images):
        reference = str(images[role]).strip()
        if not _DIGEST_PINNED.match(reference):
            raise SpecError(
                f"role {role!r} image {reference!r} is not digest-pinned. A tag "
                "is a MUTABLE POINTER: it can be moved to different bytes after "
                "the approval and before the deploy, and this record would "
                "still read as true. Use `<repository>@sha256:<64 hex>`"
            )
        out[role] = reference
    return out


def _check_rendered(digests: Mapping[str, str]) -> dict[str, str]:
    """One normalized digest per rendered asset path.

    The Compose file is the important one — it is the document the engine
    actually acts on — but every rendered asset is included, because `render
    --check` already treats them as one set and a provenance record that covered
    only some of them would let the others be hand-edited undetected.
    """
    if not digests:
        raise SpecError(
            "rendered_digests is empty. A deployment renders at least a Compose "
            "file, and a provenance record over no rendered bytes cannot detect "
            "a hand-edit — which is the specific failure `render --check` exists "
            "to prevent"
        )
    out: dict[str, str] = {}
    for path in sorted(digests):
        if not str(path).strip():
            raise SpecError("a rendered asset path is empty")
        out[str(path)] = normalize_digest(
            str(digests[path]), where=f"rendered_digests[{path!r}]"
        )
    return out


def build_provenance(
    spec: ProductDeploymentSpec,
    *,
    rendered_digests: Mapping[str, str],
    image_digests: Mapping[str, str],
    source_revision: str,
    service_roster: Sequence[str],
    authorization: VerifiedAuthorization,
) -> DeploymentProvenanceV1:
    """Bind the six, or refuse.

    Nothing here reads a clock, an environment variable, a filesystem or a
    network. Same inputs in, same bytes out — so the digest is a property of
    what was bound and of this facility version, and two runs that disagree
    disagree about the deployment rather than about the recording of it.
    """
    revision = str(source_revision).strip().lower()
    if not _COMMIT.match(revision):
        raise SpecError(
            f"source_revision {source_revision!r} is not a full commit. A branch "
            "name moves, and an abbreviated hash is a lookup that can become "
            "ambiguous as the repository grows — neither identifies the source "
            "months later. Expected 40 hex characters"
        )

    document = build_canonical_document(spec)
    descriptor_digest = document.sha256_digest()

    receipt = authorization.receipt
    # ONLY the descriptor term is compared, and only against a descriptor
    # digest. `control_plan_digest` is never brought into this comparison: it
    # measures Control's own snapshot, and asserting it equals a Foundation
    # digest is the defect this separation removes.
    receipt_digest = receipt.descriptor_digest_normalized
    if receipt_digest != descriptor_digest:
        raise SpecError(
            "the authorization does not cover this descriptor: the receipt "
            f"cites {receipt_digest} and the descriptor in hand digests to "
            f"{descriptor_digest}. This facility is not judging the approval — "
            "it is refusing to execute something OTHER than what was "
            "authorized. Re-propose the plan against this descriptor, or "
            "deploy the descriptor that was approved"
        )

    content: dict[str, Any] = {
        "schema": PROVENANCE_SCHEMA,
        "foundation_version": VERSION,
        "descriptor_digest": descriptor_digest,
        "descriptor_schema": document.schema,
        # All three terms persisted under their own names. A reader months
        # later can tell which measurement each one is without inferring it
        # from a field that was named for a different thing.
        "execution_plan_digest": receipt.execution_plan_digest_normalized,
        "control_plan_digest": str(receipt.control_plan_digest),
        "rendered_digests": _check_rendered(rendered_digests),
        "image_digests": _check_images(spec, image_digests),
        "source_revision": revision,
        "service_roster": list(_check_roster(spec, service_roster)),
        "authorization": receipt.as_document(),
    }
    return DeploymentProvenanceV1(content=content)
