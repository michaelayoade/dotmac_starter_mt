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
from typing import Any, Final

from .document import build_canonical_document
from .errors import SpecError
from .spec import ProductDeploymentSpec
from .version import VERSION

__all__ = [
    "PROVENANCE_SCHEMA",
    "AuthorizationReceipt",
    "DeploymentProvenanceV1",
    "build_provenance",
    "normalize_digest",
]

PROVENANCE_SCHEMA: Final = "DeploymentProvenance.v1"

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

    `descriptor_digest` is the binding. It is Control's `plan_digest` /
    `ApprovalEvidence.content_digest`, which Control has already proven equal to
    the digest of the plan snapshot it froze. This facility re-checks it against
    the descriptor actually in hand, because the two could only agree by the
    caller having passed the same descriptor to both.
    """

    #: Control's `DeploymentPlan.id`, so the receipt is traceable to one plan.
    plan_id: str
    #: Control's `DeploymentTarget.target_ref` — which target was authorized.
    target_ref: str
    #: `ApprovalEvidence.content_digest`, in either spelling.
    descriptor_digest: str
    #: `ApprovalEvidence.policy_code` and `.policy_version`.
    policy_code: str
    policy_version: int
    #: `ApprovalEvidence.decision_ref` — the approvals decision this rests on.
    decision_ref: str
    #: `DeploymentPlan.approved_at`, ISO-8601 UTC. A string, because a digest
    #: must be re-derivable from stored JSON months later (document.py rule 1).
    approved_at: str
    #: The exact `dotmac-deployment-control` version that issued it. A receipt
    #: is only as meaningful as the rules that produced it, and those live in a
    #: versioned package that is currently moving repositories.
    control_version: str

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
        if int(self.policy_version) < 1:
            raise SpecError(
                f"AuthorizationReceipt.policy_version must be at least 1, got "
                f"{self.policy_version!r}"
            )

    def as_document(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "target_ref": self.target_ref,
            "descriptor_digest": normalize_digest(
                self.descriptor_digest, where="AuthorizationReceipt.descriptor_digest"
            ),
            "policy_code": self.policy_code,
            "policy_version": int(self.policy_version),
            "decision_ref": self.decision_ref,
            "approved_at": self.approved_at,
            "control_version": self.control_version,
        }


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
    authorization: AuthorizationReceipt,
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

    receipt_digest = normalize_digest(
        authorization.descriptor_digest,
        where="AuthorizationReceipt.descriptor_digest",
    )
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
        "rendered_digests": _check_rendered(rendered_digests),
        "image_digests": _check_images(spec, image_digests),
        "source_revision": revision,
        "service_roster": list(_check_roster(spec, service_roster)),
        "authorization": authorization.as_document(),
    }
    return DeploymentProvenanceV1(content=content)
