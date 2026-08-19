"""The module's inbound contract, and the one thing it deliberately cannot hold.

Two value types, one protocol, and a rule that shapes the whole package:
**this module names signing material; it never holds it.**

## The signer is a protocol, and there is no implementation in this package

`LicenceSigner` has three members — `key_id`, `public_key_b64`, `sign` — and
this distribution ships **no class that satisfies it**. Not an ephemeral one for
tests, not a file-reading one, not a null one. That is not an omission to fill
in later; it is the property that makes a database dump, a wheel, a source
checkout and a stack trace structurally incapable of leaking a private key.

The source implementation (`dotmac_vendor_control_plane:src/vendor_cp/licensing/
signer.py`) shipped two modes behind an env var — an in-memory `ephemeral`
keypair and a `configured` reader that opened a file path. Both are correct
*for a product*, and neither belongs in a shared module:

- an `ephemeral` default in a library is a default that ships. The source's own
  docstring says it exists "because a missing configuration must not silently
  become a real issuer" — a rule a product can enforce at startup and a library
  cannot enforce at all.
- a file-reading mode makes the module know about paths, permissions and
  materialisation, which is custody. ADR-0009 (hard rule 20) puts custody in
  the PRODUCT: it reads the material through
  `dotmac_kernel.secret_sources.SecretSource` and installs a signer built from
  it. Loaded once, rotated by an explicit refresh, never logged.

So the product implements `LicenceSigner` over whatever holds its key, and
passes it per call. Nothing in this package reads a file, an environment
variable, or a network.

## Rotation overlap is a SEQUENCE of signers, not a mode

`issue_licence(..., signers=(primary, previous))` signs one payload with both
keys, so a deployment holding either keyring verifies the same envelope. That
is what makes rotation non-breaking, and expressing it as a sequence rather than
a flag means the module needs no notion of "which one is primary" beyond
"the first one is the one recorded on the issuance".

## Agreement and allocation arrive as FACTS

`LicensableGrant` is what the assembly passes in: the counterparty, the product,
the capabilities with their limits, the validity window, and opaque references
to the agreement and allocation the grant came from. This module does not import
`dotmac-commercial-agreements` or `dotmac-entitlement-allocation` (ADR-0024), and
it does not re-derive entitlement from agreement lines — the source's rule,
kept: *issuance never re-derives entitlement*.

`agreement_ref` and `allocation_ref` are bare strings with no foreign key. A
licence is enforceable authority that must stay verifiable after the agreement
row is archived and the allocation's retention has passed; ADR-0006 D1 also
forbids the cross-lineage foreign key that would splice three module lineages
into one release unit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

# ── Errors ──────────────────────────────────────────────────────────────────


class LicensingError(ValueError):
    """Base: this licence cannot be issued or transitioned as asked."""


class EmptyGrantError(LicensingError):
    """A grant with no capabilities. An empty licence grants nothing, and
    issuing one produces a document every deployment applies successfully and
    that authorises nothing — a silent outage rather than a loud refusal."""


class SignerRefusedError(LicensingError):
    """A signer could not be used, or the set of signers is unusable.

    Raised before anything is written. An issuance recorded against a key the
    keyring cannot verify is a document no deployment can apply, which is worse
    than no document at all.
    """


class UnverifiableIssuanceError(LicensingError):
    """The envelope this module just produced fails the kernel's own verifier.

    Always an issuance defect, never a caller error, and deliberately fatal: the
    alternative is recording a licence that the receiving half of the same
    protocol will reject. The source implementation had this check and it is the
    single most valuable line it contained.
    """


class TransitionRefusedError(LicensingError):
    """The licence is not in a state from which this transition is legal."""


class ExpectedStateError(LicensingError):
    """The caller's expected status or record version does not match.

    Distinct from `TransitionRefusedError`: this one means the caller's view is
    stale, that one means the command is wrong for the licence. A caller
    deciding whether to retry needs to tell them apart.
    """

    def __init__(
        self,
        licence_ref: str,
        *,
        expected_status: str | None,
        actual_status: str,
        expected_version: int | None,
        actual_version: int,
    ) -> None:
        self.licence_ref = licence_ref
        self.expected_status = expected_status
        self.actual_status = actual_status
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"licence {licence_ref} has moved: caller expected "
            f"status={expected_status!r} version={expected_version} but it is "
            f"status={actual_status!r} version={actual_version}"
        )


class AcknowledgementRefusedError(LicensingError):
    """An acknowledgement does not match any issued version+digest.

    Fail closed. An acknowledgement is a claim by a remote deployment about what
    it installed; accepting one that names a digest this issuer never produced
    would let a target mark itself activated on a document nobody signed.
    """


class RevocationSupersessionError(LicensingError):
    """A published revocation list is not a superset of the one before it.

    The cumulative rule, ported verbatim in intent. Version monotonicity alone
    does not prevent un-revocation: a higher version that silently omits an
    earlier id restores access while looking perfectly well-ordered to every
    receiver. Recovery from a mistaken revocation is re-issuance under a NEW
    generation, never quiet removal from the list.
    """


# ── Inbound values ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LicensedCapability:
    """One capability and its limits, exactly as the allocation fixed them.

    `limits` is an opaque mapping this module never interprets — quantity, node
    count, seat count, whatever the product's own catalogue means by it. Putting
    a schema on it here would make this module a second authority on what a
    capability limit is, which belongs to the products (ADR-0008).
    """

    code: str
    limits: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LicensableGrant:
    """What the assembly hands in: an active agreement's allocated entitlement.

    Every reference here is OPAQUE. This module resolves none of them, joins to
    none of them, and holds no foreign key to any of them.
    """

    subject_ref: str
    product_code: str
    capabilities: tuple[LicensedCapability, ...]
    #: Provenance. Opaque strings — see the module docstring for why there is no
    #: foreign key and why that is deliberate rather than a limitation.
    agreement_ref: str
    allocation_ref: str
    #: The validity window from the agreement's term. `valid_until=None` is a
    #: PERPETUAL licence, which is a different contractual choice from an
    #: expired one and must stay expressible.
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    #: Days past `valid_until` during which the receiver reports `in_grace`
    #: rather than refusing. A versioned commercial-policy value the assembly
    #: supplies; never an evaluator guess made here.
    grace_days: int = 0
    edition: str | None = None
    #: Binds the document to one deployment. Absent means a deliberately
    #: PORTABLE licence — again a contractual choice, not a missing value.
    deployment_ref: str | None = None
    #: Contracted operational semantics (HA topology, node counts) that nothing
    #: on this path interprets. Carried into the signed payload verbatim.
    constraints: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstallationReport:
    """What a remote deployment says it did with a licence it received.

    Checked against this issuer's own records before it is stored: the
    `(licence_ref, licence_version, digest)` triple must match an issuance this
    module produced. A report that names a digest nobody signed is refused, not
    recorded with a warning.
    """

    licence_ref: str
    licence_version: int
    digest: str
    #: `applied` | `rejected` — the receiver's own outcome, in the kernel's
    #: shared acknowledgement vocabulary rather than a local variant.
    outcome: str
    reported_at: datetime
    #: The stable rejection code when `outcome == "rejected"` — a
    #: `LicenceError` subclass name from the kernel verifier.
    reason: str | None = None
    #: The deployment as the TRANSPORT authenticated it, when it did. Distinct
    #: from anything the report claims about itself: a self-declared identity
    #: inside a payload is not authentication, and conflating the two is how a
    #: target acknowledges on another's behalf.
    authenticated_deployment_ref: str | None = None


# ── The one port ────────────────────────────────────────────────────────────


@runtime_checkable
class LicenceSigner(Protocol):
    """What issuance needs from a signer, and nothing more.

    Deliberately no key export, no rotation control, no lifecycle: those belong
    to custody, which is the product's (ADR-0009, hard rule 20).

    `public_key_b64` is required alongside `sign` for one reason: this module
    registers the public half BEFORE it signs with the private half, so the
    keyring it distributes can always verify what it issued. A signer that could
    sign without publishing its public key would produce documents nothing could
    verify — including this module's own round-trip check.
    """

    @property
    def key_id(self) -> str: ...

    @property
    def public_key_b64(self) -> str: ...

    def sign(self, payload: bytes) -> bytes: ...


def require_usable_signers(signers: Sequence[LicenceSigner]) -> None:
    """Refuse an unusable signer set before anything is written.

    Three failures, all of which produce documents that verify against nothing:
    no signer at all, a signer with a blank `key_id` or `public_key_b64`, and
    two signers claiming one `key_id` with different material.

    Raises `SignerRefusedError` and never names, logs, or includes key material
    in the message — only the `key_id`, which is public by construction.
    """
    if not signers:
        raise SignerRefusedError(
            "issuance needs at least one signer; the product installs it "
            "through its own secret seam (ADR-0009)"
        )
    seen: dict[str, str] = {}
    for signer in signers:
        key_id = (signer.key_id or "").strip()
        public = (signer.public_key_b64 or "").strip()
        if not key_id:
            raise SignerRefusedError("a signer has no key_id")
        if not public:
            raise SignerRefusedError(
                f"signer {key_id!r} publishes no public key; the keyring could "
                "not verify what it signs"
            )
        if key_id in seen and seen[key_id] != public:
            raise SignerRefusedError(
                f"two signers claim key_id {key_id!r} with different public "
                "material; a receiver cannot tell which one signed"
            )
        seen[key_id] = public


__all__ = [
    "AcknowledgementRefusedError",
    "EmptyGrantError",
    "ExpectedStateError",
    "InstallationReport",
    "LicensableGrant",
    "LicenceSigner",
    "LicensedCapability",
    "LicensingError",
    "RevocationSupersessionError",
    "SignerRefusedError",
    "TransitionRefusedError",
    "UnverifiableIssuanceError",
    "require_usable_signers",
]
