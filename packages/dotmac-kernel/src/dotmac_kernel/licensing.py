"""Signed-licence verification (WS8) — the kernel slice of licence delivery.

Design brief: ``docs/superpowers/reviews/2026-08-01-ws8-signed-licence-design.md``.
Division of labour (ruled C4, 2026-07-31): this module **verifies only**. The
vendor control plane owns issuance and private-key custody; a product data
plane verifies a delivered envelope here, projects the verified capabilities
into its OWN local WS2 grants (``grant_entitlement``), and acknowledges the
applied ``(licence_id, licence_version, digest)``. No private signing key ever
enters the kernel; the only signer in this repo is the ephemeral test fake in
``dotmac_kernel.testing.licensing``.

Format: a DSSE-style envelope — signatures are computed over the exact payload
**bytes** (there is no canonical-JSON step to disagree on), and the payload is
parsed only after a signature verifies. The digest of those bytes is the
licence's identity for replay protection and acknowledgement.

Everything fails closed, and verification is deterministic and offline: every
input (envelope, keyring, clock, applied record, revocation set) is a
parameter; the module performs no I/O and never reads the wall clock.

Dependency: Ed25519 verification needs the ``cryptography`` package, installed
via the ``licensing`` extra (``pip install dotmac-kernel[licensing]``). The
import is lazy so this module (types, parsing, digests) works without it;
signature verification without it raises ``VerificationUnavailableError``.
"""

from __future__ import annotations

import base64
import binascii
import enum
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

ENVELOPE_SCHEMA = "dotmac-licence-envelope/1"
LICENCE_SCHEMA = "dotmac-licence/1"
REVOCATION_SCHEMA = "dotmac-licence-revocation/1"
APPLIED_STATE_SCHEMA = "dotmac-licence-applied-state/1"
APPLIED_STATE_ENVELOPE_SCHEMA = "dotmac-applied-state-envelope/1"

# Domain separation (ADR-0007). Every applied-state signature is computed over
# this prefix followed by the exact payload bytes, so a signature the
# deployment's key produced for ANY other purpose cannot be replayed as an
# applied-state report. Without it, any protocol that has a deployment sign
# caller-influenced bytes becomes a forgery oracle for this one.
#
# The trailing NUL is what makes the prefix unambiguous: it cannot occur in the
# label, so no payload can be crafted whose leading bytes complete a different
# domain string. Changing this value is a wire-compatibility break.
APPLIED_STATE_DOMAIN = b"dotmac-ws8-applied-state/1\x00"

# The possession challenge (ADR-0007 §2) is signed by the same deployment key,
# so it needs its OWN domain. Sharing one would defeat the separation: a
# challenge nonce is a value the vendor chooses and the deployment signs
# blindly, which is exactly the shape of a forgery oracle — with a single
# domain, a "challenge" whose bytes happen to be a valid applied-state payload
# would yield a signature that verifies as a report the deployment never made.
DEPLOYMENT_CHALLENGE_DOMAIN = b"dotmac-ws8-deployment-challenge/1\x00"
DEPLOYMENT_CHALLENGE_SCHEMA = "dotmac-deployment-challenge/1"

# The ANSWER travels under its own schema. It shares the challenge's domain
# separator on purpose — both are the same signature over the same bytes, and
# the response adds no bytes of its own — but it is a distinct wire structure,
# because a challenge and its answer are different documents with different
# authors and must be told apart by a reader that holds only one of them.
DEPLOYMENT_RESPONSE_SCHEMA = "dotmac-deployment-possession-response/1"

# A challenge nonce shorter than this is guessable enough that a response
# could be precomputed before the challenge is even issued.
_MIN_NONCE_BYTES = 16

# The digest a receiver reports when an envelope never validated far enough to
# have one. A sentinel rather than an empty string so it can never be mistaken
# for a real digest, and so an `applied` claim carrying it is rejectable.
UNKNOWN_DIGEST = "unknown"

# A real digest is exactly what `payload_digest` produces. Validating the SHAPE
# (not just non-emptiness) stops a report claiming applied state under a
# placeholder that merely looks populated.
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

_ALGORITHM = "ed25519"


# ── Errors (all fail-closed outcomes share one base) ────────────────────────


class LicenceError(Exception):
    """Base for every licence verification failure. The subclass name is the
    stable machine-readable rejection reason an acknowledgement carries."""


class MalformedLicenceError(LicenceError):
    """Envelope or payload is structurally invalid (unknown schema/algorithm,
    missing/mistyped field, bad encoding, naive timestamp, inverted window)."""


class UnknownKeyError(LicenceError):
    """No signature references a key present in the keyring — the keyring is a
    closed world; a document may never bring its own key."""


class RevokedKeyError(LicenceError):
    """The only keyring coverage is a revoked key. A revoked key never
    verifies anything, even a cryptographically valid signature."""


class BadSignatureError(LicenceError):
    """No signature cryptographically verifies under an acceptable key."""


class RevokedLicenceError(LicenceError):
    """The licence id is on the imported revocation set."""


class DeploymentMismatchError(LicenceError):
    """Deployment binding failed: bound to a different deployment, bound with
    no expected id to check against, or unbound while binding is required."""


class LicenceNotYetValidError(LicenceError):
    """The injected clock is before the document's ``not_before``."""


class LicenceExpiredError(LicenceError):
    """The injected clock is past ``expires_at`` plus the grace window."""


class StaleLicenceError(LicenceError):
    """The document's version is lower than the receiver's applied record —
    an old document cannot roll a deployment back after re-issuance."""


class LicenceConflictError(LicenceError):
    """Same lineage and version as the applied record but a different digest —
    two distinct documents claiming one version is an issuer-side integrity
    failure; never pick one."""


class StaleRevocationListError(LicenceError):
    """The revocation list's version is lower than the applied list version —
    a stale list cannot un-revoke."""


class VerificationUnavailableError(LicenceError):
    """Signature verification was attempted without the ``cryptography``
    package — install the ``dotmac-kernel[licensing]`` extra. Fail closed:
    an unverifiable envelope grants nothing."""


class DuplicateKeyError(LicenceError):
    """A keyring was constructed with two keys sharing a ``key_id``."""


class MalformedAppliedStateError(LicenceError):
    """A receiver-applied-state report is structurally invalid. Raised by
    BOTH direct construction and parsing — the two paths share one validator,
    so a producer can never build a report the other plane would reject.
    Fail closed: a report that cannot be trusted is rejected here rather than
    half-interpreted by whichever plane reads it next."""


# ── Keyring ─────────────────────────────────────────────────────────────────


class KeyStatus(enum.StrEnum):
    """Rotation state of a verification key. ``ACTIVE`` verifies and may sign
    new documents (issuer-side rule); ``RETIRED`` still verifies (rotation
    overlap for the installed base) but must sign nothing new; ``REVOKED``
    never verifies anything."""

    ACTIVE = "active"
    RETIRED = "retired"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class LicenceKey:
    """One public verification key (public material only — the kernel never
    holds private keys). ``public_key_b64`` is the base64url raw 32-byte
    Ed25519 public key."""

    key_id: str
    public_key_b64: str
    algorithm: str = _ALGORITHM
    status: KeyStatus = KeyStatus.ACTIVE


class LicenceKeyRing:
    """The verifier's closed-world trust store: the set of keys a deployment
    accepts, by unique ``key_id``. Construction fails closed on a duplicate."""

    def __init__(self, keys: Iterable[LicenceKey]) -> None:
        self._keys: dict[str, LicenceKey] = {}
        for key in keys:
            if key.key_id in self._keys:
                raise DuplicateKeyError(f"duplicate key_id in keyring: {key.key_id!r}")
            self._keys[key.key_id] = key

    def get(self, key_id: str) -> LicenceKey | None:
        return self._keys.get(key_id)

    @property
    def key_ids(self) -> frozenset[str]:
        return frozenset(self._keys)


# ── Document / verification value objects ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class LicenceSubject:
    """Who the licence is for. ``customer`` is the opaque commercial identity
    (vendor-plane vocabulary); ``deployment_id`` is the optional deployment
    binding — present means the licence verifies only on that deployment."""

    customer: str
    deployment_id: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """One capability the licence conveys. ``code`` is a WS1 capability code
    declared by the receiving product's modules — the RECEIVER resolves it at
    projection time (``grant_entitlement`` refuses undeclared codes); the
    verifier carries it uninterpreted. ``limits`` flows through to the WS2
    grant's explainable limits."""

    code: str
    limits: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LicenceDocument:
    """The parsed, verified licence payload (``dotmac-licence/1``).
    ``licence_id`` identifies the lineage; ``licence_version`` is strictly
    monotonic within it. ``expires_at`` of ``None`` means perpetual.
    ``constraints`` is contracted operational semantics (e.g. HA/node counts)
    carried verified but never interpreted by the kernel."""

    licence_id: str
    licence_version: int
    issuer: str
    product: str
    subject: LicenceSubject
    capabilities: tuple[CapabilityGrant, ...]
    issued_at: datetime
    edition: str | None = None
    not_before: datetime | None = None
    expires_at: datetime | None = None
    grace_days: int = 0
    constraints: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AppliedLicence:
    """The receiver's durable record of the last licence it applied for a
    lineage — the replay/rollback guard's reference point."""

    licence_id: str
    licence_version: int
    digest: str


@dataclass(frozen=True, slots=True)
class VerifiedLicence:
    """A successful verification. ``validity`` is ``"valid"`` or ``"in_grace"``
    (explicitly degraded — past expiry but inside the grace window);
    ``reapplied`` marks an idempotent redelivery of the already-applied
    version+digest."""

    document: LicenceDocument
    digest: str
    validity: str
    reapplied: bool = False


@dataclass(frozen=True, slots=True)
class RevocationList:
    """A verified revocation list: the licence ids the issuer has revoked, at
    a monotonic ``list_version``."""

    list_version: int
    issued_at: datetime
    revoked_licence_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class LicenceAcknowledgement:
    """The shared cross-plane acknowledgement vocabulary: what the receiver
    applied (or rejected), identified by exact version + payload digest.
    ``reason`` is the stable rejection code (a ``LicenceError`` subclass
    name). Transport is vendor/product-owned; the kernel defines the value
    object so neither plane invents a local variant."""

    licence_id: str
    licence_version: int
    digest: str
    status: str
    reason: str | None = None
    deployment_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("applied", "rejected"):
            raise ValueError("status must be 'applied' or 'rejected'")


@dataclass(frozen=True, slots=True)
class ReceiverAppliedState:
    """What a deployment reports about the licence state it is running.

    The cross-plane channel behind three gaps the vendor cannot close alone: an
    acknowledgement it can authenticate, and the two uptake versions — keyring
    generation and applied revocation-list version — that "we published it"
    says nothing about. Delivery proves a transport moved bytes; only this says
    what a deployment actually holds.

    Every field is a CLAIM. The vendor authenticates the reporter and matches
    `digest` against what it issued; nothing is trusted on the report alone.
    `report_id` is the idempotency key, because delivery is at-least-once and
    two scheduled observations of unchanged state are genuinely DISTINCT
    reports — deduping on content would make a healthy deployment look silent.

    `revocation_list_version` of ``None`` means NO list imported, deliberately
    distinct from version 0: "never received one" and "received an empty one"
    are different operational states, and conflating them hides a deployment
    that has never been reached.

    `keyring_generation` of ``None`` means NO keyring provisioned, mirroring the
    same distinction. That state is real and worth reporting: a deployment
    without a keyring can verify nothing, so it can only ever report
    rejections — and an operator needs to see the difference between "never
    provisioned" and "provisioned but failing". It is therefore REQUIRED to be
    a real generation on an `applied` report: nothing can have been applied
    without a keyring that verified it.

    Status semantics — both outcomes are first-class. ``status="applied"``
    asserts a COMMITTED ``(licence_id, licence_version, digest)``:
    ``licence_version`` >= 1 and a real digest (never ``UNKNOWN_DIGEST``).
    ``status="rejected"`` requires a ``reason`` (a stable ``LicenceError``
    subclass name) and stays representable when the envelope never validated:
    ``licence_version=0`` (no verified version claim) with
    ``digest=UNKNOWN_DIGEST`` when the payload bytes could not be recovered —
    exactly the identity the reference receiver's rejected acknowledgements
    already carry. The timestamp is ``observed_at`` — when the deployment
    recorded the state it reports — deliberately NOT "applied_at", because a
    rejected attempt applied nothing.

    Validation lives HERE, in one place, so a report built directly carries
    exactly the guarantees of one parsed off the wire — there is no
    constructable-but-unparseable report a producer could serialise and have
    the other plane reject.
    """

    report_id: str
    deployment_ref: str
    licence_id: str
    licence_version: int
    digest: str
    keyring_generation: int | None
    revocation_list_version: int | None
    observed_at: datetime
    status: str
    reason: str | None = None

    def __post_init__(self) -> None:
        _text_field("report_id", self.report_id)
        _text_field("deployment_ref", self.deployment_ref)
        _text_field("licence_id", self.licence_id)
        _text_field("digest", self.digest)
        if self.keyring_generation is not None:
            _count_field("keyring_generation", self.keyring_generation, minimum=1)
        if self.revocation_list_version is not None:
            _count_field(
                "revocation_list_version", self.revocation_list_version, minimum=0
            )
        if not isinstance(self.observed_at, datetime):
            raise MalformedAppliedStateError("'observed_at' must be a datetime")
        if self.observed_at.utcoffset() is None:
            raise MalformedAppliedStateError("'observed_at' must be timezone-aware")
        if self.status not in ("applied", "rejected"):
            raise MalformedAppliedStateError(
                f"'status' must be 'applied' or 'rejected', got {self.status!r}"
            )
        if self.status == "applied":
            # An `applied` claim asserts a COMMITTED (version, digest). Version
            # 0 and the unknown-digest sentinel are what a receiver reports when
            # nothing verified, so allowing them here would let a non-event be
            # recorded as applied state.
            _count_field("licence_version", self.licence_version, minimum=1)
            if not _DIGEST_PATTERN.match(self.digest):
                raise MalformedAppliedStateError(
                    "an 'applied' report needs a real digest "
                    "('sha256:' + 64 hex chars); a placeholder that merely "
                    "looks populated would assert applied state for nothing"
                )
            if self.keyring_generation is None:
                raise MalformedAppliedStateError(
                    "an 'applied' report needs a keyring generation — nothing "
                    "can have been applied without a keyring that verified it"
                )
            # A REASON on an applied report is a contradiction: `reason` is the
            # rejection code, so carrying one would let a report be simultaneously
            # accepted and explained-away, and readers would disagree about which
            # half to believe.
            if self.reason is not None:
                raise MalformedAppliedStateError(
                    "an 'applied' report must not carry a 'reason' — that field "
                    "explains a REJECTION, and a report cannot be both"
                )
        else:
            _count_field("licence_version", self.licence_version, minimum=0)
            # A rejection with no reason is unexplainable at the other end, and
            # the receiver always has one (a stable kernel error-class name).
            if not isinstance(self.reason, str) or not self.reason:
                raise MalformedAppliedStateError(
                    "a 'rejected' report must carry a non-empty 'reason'"
                )
            if self.digest == UNKNOWN_DIGEST and self.licence_version != 0:
                # No verified identity means no verified VERSION either; a
                # positive version beside the sentinel is a claim the receiver
                # could not have established.
                raise MalformedAppliedStateError(
                    "a rejection carrying the unknown-digest sentinel must "
                    "report licence_version 0 — an unverified envelope yields "
                    "no version to claim"
                )
            if self.digest != UNKNOWN_DIGEST and not _DIGEST_PATTERN.match(self.digest):
                raise MalformedAppliedStateError(
                    "a rejection's digest must be either the unknown-digest "
                    "sentinel or a real 'sha256:' + 64 hex digest"
                )

    @property
    def acknowledgement(self) -> LicenceAcknowledgement:
        """The narrower ack this report subsumes, so the existing
        acknowledgement path keeps working unchanged — valid for rejected
        reports too, including the no-verified-identity case."""
        return LicenceAcknowledgement(
            licence_id=self.licence_id,
            licence_version=self.licence_version,
            digest=self.digest,
            status=self.status,
            reason=self.reason,
            deployment_id=self.deployment_ref,
        )


def _text_field(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise MalformedAppliedStateError(f"'{name}' must be a non-empty string")
    return value


def _count_field(name: str, value: object, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise MalformedAppliedStateError(f"'{name}' must be an integer >= {minimum}")
    return value


def applied_state_payload(state: ReceiverAppliedState) -> dict[str, object]:
    """Serialise for transport. Every value is JSON-safe — a datetime left in
    place would fail at whichever transport carries this."""
    return {
        "schema": APPLIED_STATE_SCHEMA,
        "report_id": state.report_id,
        "deployment_ref": state.deployment_ref,
        "licence_id": state.licence_id,
        "licence_version": state.licence_version,
        "digest": state.digest,
        "keyring_generation": state.keyring_generation,
        "revocation_list_version": state.revocation_list_version,
        "observed_at": state.observed_at.isoformat(),
        "status": state.status,
        "reason": state.reason,
    }


def parse_applied_state(payload: Mapping[str, object]) -> ReceiverAppliedState:
    """Strictly parse a report.

    Structural checks happen here; FIELD validity is delegated to the value
    object's own validator, so the wire path and the construction path cannot
    drift apart. Unknown fields are IGNORED — a newer receiver must not break
    an older vendor — but never absorbed into the record.
    """
    if not isinstance(payload, Mapping):
        raise MalformedAppliedStateError("applied-state payload must be an object")
    if payload.get("schema") != APPLIED_STATE_SCHEMA:
        raise MalformedAppliedStateError(
            f"unknown applied-state schema: {payload.get('schema')!r}"
        )

    observed_raw = payload.get("observed_at")
    if not isinstance(observed_raw, str):
        raise MalformedAppliedStateError("'observed_at' must be an ISO-8601 string")
    try:
        observed_at = datetime.fromisoformat(observed_raw)
    except ValueError as exc:
        raise MalformedAppliedStateError(
            "'observed_at' is not a valid timestamp"
        ) from exc

    status = payload.get("status")
    return ReceiverAppliedState(
        report_id=payload.get("report_id"),  # type: ignore[arg-type]
        deployment_ref=payload.get("deployment_ref"),  # type: ignore[arg-type]
        licence_id=payload.get("licence_id"),  # type: ignore[arg-type]
        licence_version=payload.get("licence_version"),  # type: ignore[arg-type]
        digest=payload.get("digest"),  # type: ignore[arg-type]
        keyring_generation=payload.get("keyring_generation"),  # type: ignore[arg-type]
        revocation_list_version=payload.get(  # type: ignore[arg-type]
            "revocation_list_version"
        ),
        observed_at=observed_at,
        status=status,  # type: ignore[arg-type]
        reason=payload.get("reason"),  # type: ignore[arg-type]
    )


# ── Digest ──────────────────────────────────────────────────────────────────


def payload_digest(payload: bytes) -> str:
    """The licence identity used by replay protection and acknowledgement:
    ``sha256:`` + hex digest of the exact signed payload bytes."""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ── Applied-state envelope (ADR-0007) ───────────────────────────────────────
#
# The envelope a DEPLOYMENT signs to prove who is reporting — the mirror image
# of the licence envelope the vendor signs to prove what was issued. They are
# deliberately separate structures: they travel in opposite directions and are
# signed by different parties with different key custody, so one trust
# structure covering both would let either party's key speak for the other.
#
# The kernel owns this contract and the vectors that prove both planes agree.
# It ships NO production signer, exactly as it ships none for licences.


def _length_prefixed(*parts: bytes) -> bytes:
    """Unambiguously concatenate fields for signing.

    Each part is preceded by its 4-byte big-endian length, so no two different
    field tuples can ever produce the same bytes. Plain concatenation is
    ambiguous — ``key_id="a"`` + ``payload=b"bc"`` and ``key_id="ab"`` +
    ``payload=b"c"`` would be indistinguishable, which is a signature-splicing
    hole rather than a theoretical nicety.
    """
    return b"".join(len(part).to_bytes(4, "big") + part for part in parts)


def applied_state_signing_input(key_id: str, payload: bytes) -> bytes:
    """The exact bytes an applied-state signature is computed over.

    Binds `key_id` INTO the signature. It cannot merely travel beside the
    payload: `key_id` is what resolves to a deployment identity, so leaving it
    unsigned makes the identity forgeable by anyone who can get one public key
    registered twice.

    The concrete attack, before this binding existed: register the SAME public
    key under a second `key_id` that maps to a different deployment, then
    replay a captured report with `key_id` swapped. The signature still
    verified — the key material was identical — and the report was attributed
    to the attacker's chosen deployment. Signing `key_id` makes that
    substitution fail, because the signed bytes no longer match.

    A function rather than an inline concatenation so the vendor's verifier and
    the receiver's signer cannot drift: both call this, and the conformance
    vector pins its output.
    """
    return APPLIED_STATE_DOMAIN + _length_prefixed(key_id.encode("utf-8"), payload)


@dataclass(frozen=True, slots=True)
class DeploymentVerificationKey:
    """A registered deployment key, as the verifier needs it.

    Carries the `deployment_ref` because resolving `key_id` → deployment IS the
    identity decision (ADR-0007 §4). Returning a bare "signature ok" and
    leaving the caller to look the ref up separately is what allows the two to
    disagree; bundling them means a verified report always names the identity
    the key is registered to.

    Only keys the registry considers usable RIGHT NOW should be constructed
    here: status, validity windows and revocation are the registry's decisions,
    not this module's.
    """

    key_id: str
    public_key_b64: str
    deployment_ref: str

    def __post_init__(self) -> None:
        for name in ("key_id", "public_key_b64", "deployment_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise MalformedAppliedStateError(
                    f"a deployment verification key needs a non-empty {name!r}"
                )


@dataclass(frozen=True, slots=True)
class AppliedStateEnvelope:
    """A sealed applied-state report: `key_id`, exact payload bytes, signature.

    Immutable, and it carries BYTES rather than a parsed object on purpose —
    the signature is over those exact bytes, so anything that re-serialises a
    parsed payload to verify it has already lost the guarantee.
    """

    key_id: str
    payload: bytes
    signature: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not self.key_id:
            raise MalformedAppliedStateError(
                "an applied-state envelope needs a non-empty 'key_id'"
            )
        for name in ("payload", "signature"):
            value = getattr(self, name)
            if not isinstance(value, bytes) or not value:
                raise MalformedAppliedStateError(
                    f"an applied-state envelope needs non-empty {name!r} bytes"
                )

    def to_wire(self) -> dict[str, str]:
        """The JSON-safe transport form. Every value is a string, so it
        survives any JSON transport without a custom encoder."""
        return {
            "schema": APPLIED_STATE_ENVELOPE_SCHEMA,
            "key_id": self.key_id,
            "payload_b64": _b64url_encode(self.payload),
            "signature_b64": _b64url_encode(self.signature),
        }

    @classmethod
    def from_wire(
        cls, wire: Mapping[str, object] | str | bytes
    ) -> AppliedStateEnvelope:
        """Structurally validate a transport form. Fail-closed on every path.

        Deliberately separate from verification: the caller must read `key_id`
        BEFORE it can look up a key, and keeping the two apart makes that
        ordering explicit instead of hiding a partial parse in the verifier.
        """
        if isinstance(wire, str | bytes):
            try:
                parsed = json.loads(wire)
            except (ValueError, UnicodeDecodeError) as exc:
                raise MalformedAppliedStateError(
                    f"applied-state envelope is not valid JSON: {exc}"
                ) from exc
            wire = parsed
        if not isinstance(wire, Mapping):
            raise MalformedAppliedStateError("applied-state envelope must be an object")
        if wire.get("schema") != APPLIED_STATE_ENVELOPE_SCHEMA:
            raise MalformedAppliedStateError(
                f"unknown applied-state envelope schema: {wire.get('schema')!r}"
            )

        key_id = _required_wire_str(wire, "key_id")
        try:
            payload = _b64url_decode(_required_wire_str(wire, "payload_b64"))
            signature = _b64url_decode(_required_wire_str(wire, "signature_b64"))
        except MalformedLicenceError as exc:
            # Re-typed: a malformed applied-state envelope is an applied-state
            # fault, so callers catch ONE error type for this whole path.
            raise MalformedAppliedStateError(str(exc)) from None
        return cls(key_id=key_id, payload=payload, signature=signature)


@dataclass(frozen=True, slots=True)
class VerifiedAppliedState:
    """A report whose signature verified, plus the key that verified it.

    `deployment_ref` is the PROVEN identity, resolved from the signed `key_id`.
    `state.deployment_ref` is the reporter's own claim, carried so the vendor
    can compare the two and quarantine a contradiction — never so it can be
    used in place of the resolved identity.
    """

    state: ReceiverAppliedState
    key: DeploymentVerificationKey
    payload: bytes

    @property
    def deployment_ref(self) -> str:
        """The identity the signature PROVED."""
        return self.key.deployment_ref

    @property
    def key_id(self) -> str:
        return self.key.key_id

    @property
    def claim_matches_proof(self) -> bool:
        """Whether the report's own `deployment_ref` agrees with the proven
        one. False is a quarantine case (ADR-0007 §4), not a rejection to
        resolve in the caller's favour."""
        return self.state.deployment_ref == self.key.deployment_ref


def _required_wire_str(
    wire: Mapping[str, object], name: str, context: str = "applied-state envelope"
) -> str:
    value = wire.get(name)
    if not isinstance(value, str) or not value:
        raise MalformedAppliedStateError(f"{context} needs a non-empty {name!r}")
    return value


def verify_applied_state(
    envelope: AppliedStateEnvelope | Mapping[str, object] | str | bytes,
    *,
    keys: Iterable[DeploymentVerificationKey],
) -> VerifiedAppliedState:
    """Verify a deployment's applied-state report.

    `keys` are the registrations the caller considers usable RIGHT NOW — key
    status, validity windows and revocation are the registry's decisions, and
    passing a revoked key here would verify it.

    Fails closed on every path: unknown key, bad signature, or a payload that
    is not a valid report.
    """
    sealed = (
        envelope
        if isinstance(envelope, AppliedStateEnvelope)
        else AppliedStateEnvelope.from_wire(envelope)
    )

    registered = {key.key_id: key for key in keys}
    key = registered.get(sealed.key_id)
    if key is None:
        raise UnknownKeyError(f"no registered applied-state key {sealed.key_id!r}")

    signing_input = applied_state_signing_input(sealed.key_id, sealed.payload)
    licence_key = LicenceKey(key_id=key.key_id, public_key_b64=key.public_key_b64)
    if not _ed25519_verifies(licence_key, sealed.signature, signing_input):
        raise BadSignatureError(
            f"applied-state signature did not verify under key {sealed.key_id!r}"
        )

    try:
        document = json.loads(sealed.payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedAppliedStateError(
            f"applied-state payload is not valid JSON: {exc}"
        ) from exc
    return VerifiedAppliedState(
        state=parse_applied_state(document), key=key, payload=sealed.payload
    )


class DeploymentSigner(Protocol):
    """A deployment's signing IDENTITY — both halves of it.

    It owns `key_id` **and** `deployment_ref` independently, because a signer
    that knows only its key id cannot tell whether the thing it is about to
    sign speaks for the right deployment. Everything the deployment signs binds
    both, so a signer holding only one of them can be walked into attesting to
    the other: hand it a challenge naming its own `key_id` but a foreign
    `deployment_ref` and it produces a signed statement that this key proved
    possession for someone else's deployment. That artifact is *portable
    signed evidence* (ADR-0007 §1) — checkable by any third party — which makes
    it worse than a local mistake.

    Hence the rule this protocol exists to make expressible: **never sign a
    statement you cannot attest to.** Both `seal_applied_state` and
    `answer_possession_challenge` verify both halves BEFORE calling `sign`, so
    the private key is never applied to a foreign claim at all — refusing after
    signing would be too late, the signature would already exist.

    No key export and no rotation control: those are custody concerns owned by
    the product receiver, which loads the private key once from a configured
    file materialised from OpenBao. The kernel never sees where it came from.
    """

    @property
    def key_id(self) -> str: ...

    @property
    def deployment_ref(self) -> str: ...

    def sign(self, data: bytes) -> bytes: ...


def _assert_signer_owns(
    signer: DeploymentSigner, *, key_id: str, deployment_ref: str, what: str
) -> None:
    """Refuse to sign anything the signer's identity does not cover.

    Checked BEFORE `sign` is ever called — see `DeploymentSigner`. Both halves,
    independently: matching `key_id` alone is what let a challenge for a
    foreign `deployment_ref` be signed.
    """
    if signer.key_id != key_id:
        raise DeploymentMismatchError(
            f"{what} is for key {key_id!r}, but the signer holds " f"{signer.key_id!r}"
        )
    if signer.deployment_ref != deployment_ref:
        raise DeploymentMismatchError(
            f"{what} is for deployment {deployment_ref!r}, but the signer "
            f"speaks for {signer.deployment_ref!r} — a deployment never signs "
            "a statement about another deployment"
        )


def seal_applied_state(
    state: ReceiverAppliedState, *, signer: DeploymentSigner
) -> AppliedStateEnvelope:
    """Serialise and sign a report.

    Refuses a report claiming another deployment, before signing: a receiver
    reports its OWN state, and sealing a foreign claim would mint signed
    evidence for a deployment this key does not speak for. The verifier would
    quarantine it (`claim_matches_proof`), but the artifact should never exist.

    Serialisation is CANONICAL (`sort_keys`, no incidental whitespace) so the
    same report seals to the same bytes everywhere. That is not what makes
    verification safe — the envelope carries the exact signed bytes, so the
    verifier never re-encodes anything — but it makes the conformance vectors
    reproducible across processes and languages.
    """
    _assert_signer_owns(
        signer,
        key_id=signer.key_id,
        deployment_ref=state.deployment_ref,
        what="this report",
    )
    payload = json.dumps(
        applied_state_payload(state), sort_keys=True, separators=(",", ":")
    ).encode()
    signature = signer.sign(applied_state_signing_input(signer.key_id, payload))
    return AppliedStateEnvelope(
        key_id=signer.key_id, payload=payload, signature=signature
    )


# ── Possession challenge (ADR-0007 §2) ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeploymentPossessionChallenge:
    """A one-time, expiry-bound proof-of-possession request.

    Registration proves only that someone submitted a public key; this is what
    proves the deployment holds the matching private half, and a key stays
    `pending` until it succeeds.

    Every binding here is load-bearing:

    - `key_id` and `deployment_ref` — a signed response is evidence for THIS
      key on THIS deployment, so a response cannot be carried to another
      registration.
    - `challenge_id` — the single-use handle the issuer retires on completion,
      so a captured response cannot be replayed into a later activation.
    - `expires_at` — bounds how long a captured response stays useful. It is
      signed, so a stale challenge cannot be silently extended by the caller.

    The kernel pins only the SHAPE and how it is signed. Issuing, storing,
    retiring and expiring belong to the vendor's credential registry, so no
    credential state enters the kernel.
    """

    challenge_id: str
    key_id: str
    deployment_ref: str
    nonce: bytes
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("challenge_id", "key_id", "deployment_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise MalformedAppliedStateError(
                    f"a possession challenge needs a non-empty {name!r}"
                )
        if not isinstance(self.nonce, bytes) or len(self.nonce) < _MIN_NONCE_BYTES:
            raise MalformedAppliedStateError(
                f"a possession challenge needs at least {_MIN_NONCE_BYTES} "
                "bytes of nonce — a short or predictable nonce lets a response "
                "be precomputed"
            )
        if not isinstance(self.expires_at, datetime):
            raise MalformedAppliedStateError("'expires_at' must be a datetime")
        if self.expires_at.tzinfo is None:
            raise MalformedAppliedStateError(
                "'expires_at' must be timezone-aware — a naive expiry silently "
                "means whichever zone the reader happens to be in"
            )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def signing_input(self) -> bytes:
        """The exact bytes the deployment signs to answer this challenge.

        Domain-separated from applied state and length-delimited across every
        binding, so a challenge response can never be presented as a report and
        no two different challenges share signing bytes.
        """
        return DEPLOYMENT_CHALLENGE_DOMAIN + _length_prefixed(
            DEPLOYMENT_CHALLENGE_SCHEMA.encode("utf-8"),
            self.challenge_id.encode("utf-8"),
            self.key_id.encode("utf-8"),
            self.deployment_ref.encode("utf-8"),
            self.nonce,
            self.expires_at.isoformat().encode("utf-8"),
        )

    def to_wire(self) -> dict[str, str]:
        return {
            "schema": DEPLOYMENT_CHALLENGE_SCHEMA,
            "challenge_id": self.challenge_id,
            "key_id": self.key_id,
            "deployment_ref": self.deployment_ref,
            "nonce_b64": _b64url_encode(self.nonce),
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_wire(
        cls, wire: Mapping[str, object] | str | bytes
    ) -> DeploymentPossessionChallenge:
        if isinstance(wire, str | bytes):
            try:
                wire = json.loads(wire)
            except (ValueError, UnicodeDecodeError) as exc:
                raise MalformedAppliedStateError(
                    f"possession challenge is not valid JSON: {exc}"
                ) from exc
        if not isinstance(wire, Mapping):
            raise MalformedAppliedStateError("possession challenge must be an object")
        if wire.get("schema") != DEPLOYMENT_CHALLENGE_SCHEMA:
            raise MalformedAppliedStateError(
                f"unknown possession-challenge schema: {wire.get('schema')!r}"
            )
        context = "possession challenge"
        expires_raw = _required_wire_str(wire, "expires_at", context)
        try:
            expires_at = datetime.fromisoformat(expires_raw)
        except ValueError as exc:
            raise MalformedAppliedStateError(
                "'expires_at' is not a valid timestamp"
            ) from exc
        try:
            nonce = _b64url_decode(_required_wire_str(wire, "nonce_b64", context))
        except MalformedLicenceError as exc:
            raise MalformedAppliedStateError(str(exc)) from None
        return cls(
            challenge_id=_required_wire_str(wire, "challenge_id", context),
            key_id=_required_wire_str(wire, "key_id", context),
            deployment_ref=_required_wire_str(wire, "deployment_ref", context),
            nonce=nonce,
            expires_at=expires_at,
        )


@dataclass(frozen=True, slots=True)
class DeploymentPossessionResponse:
    """A deployment's answer to one challenge: which challenge, which key, and
    the signature over that challenge's signing input.

    It carries **nothing else**, and that is the contract. The nonce, the
    deployment and the expiry live in the challenge the vendor issued and
    STORED; the vendor loads its own copy to verify. A response that also
    carried them would invite a verifier to read a binding from the answer
    instead of from the record — the same substitution of a claim for a proof
    that ADR-0007 §4 rules out for applied state. `from_wire` therefore
    REJECTS those fields rather than ignoring them: a field accepted today and
    ignored is a field something reads tomorrow.

    `challenge_id` and `key_id` are not new authority either. They are routing
    — they tell the vendor which stored challenge to load — and verification
    then requires them to match that record exactly. The signature itself
    commits to both, because the challenge's signing input binds them.
    """

    challenge_id: str
    key_id: str
    signature: bytes

    #: Wire fields a response must never carry — see the class docstring.
    _FORBIDDEN_WIRE_FIELDS = ("nonce_b64", "nonce", "deployment_ref", "expires_at")

    def __post_init__(self) -> None:
        for name in ("challenge_id", "key_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise MalformedAppliedStateError(
                    f"a possession response needs a non-empty {name!r}"
                )
        if not isinstance(self.signature, bytes) or not self.signature:
            raise MalformedAppliedStateError(
                "a possession response needs non-empty 'signature' bytes"
            )

    def to_wire(self) -> dict[str, str]:
        return {
            "schema": DEPLOYMENT_RESPONSE_SCHEMA,
            "challenge_id": self.challenge_id,
            "key_id": self.key_id,
            "signature_b64": _b64url_encode(self.signature),
        }

    @classmethod
    def from_wire(
        cls, wire: Mapping[str, object] | str | bytes
    ) -> DeploymentPossessionResponse:
        if isinstance(wire, str | bytes):
            try:
                wire = json.loads(wire)
            except (ValueError, UnicodeDecodeError) as exc:
                raise MalformedAppliedStateError(
                    f"possession response is not valid JSON: {exc}"
                ) from exc
        if not isinstance(wire, Mapping):
            raise MalformedAppliedStateError("possession response must be an object")
        if wire.get("schema") != DEPLOYMENT_RESPONSE_SCHEMA:
            raise MalformedAppliedStateError(
                f"unknown possession-response schema: {wire.get('schema')!r}"
            )
        echoed = [name for name in cls._FORBIDDEN_WIRE_FIELDS if name in wire]
        if echoed:
            raise MalformedAppliedStateError(
                f"possession response must not carry {', '.join(echoed)} — the "
                "issued challenge is authoritative for the nonce, deployment "
                "and expiry, and a response that restates them invites a "
                "verifier to trust the answer over the record"
            )
        context = "possession response"
        try:
            signature = _b64url_decode(
                _required_wire_str(wire, "signature_b64", context)
            )
        except MalformedLicenceError as exc:
            raise MalformedAppliedStateError(str(exc)) from None
        return cls(
            challenge_id=_required_wire_str(wire, "challenge_id", context),
            key_id=_required_wire_str(wire, "key_id", context),
            signature=signature,
        )


@dataclass(frozen=True, slots=True)
class VerifiedDeploymentPossession:
    """A possession proof that held — the successful result of
    `verify_possession`, which returns this instead of `None`.

    A returned value rather than "it did not raise" so the caller's next two
    steps have something to act on: consuming the challenge and activating the
    key both need the identity that was PROVEN, and reading it back off the
    inputs re-opens the question of which input was authoritative.
    """

    challenge: DeploymentPossessionChallenge
    key: DeploymentVerificationKey
    verified_at: datetime

    @property
    def challenge_id(self) -> str:
        """The single-use handle the vendor must now consume, atomically with
        activating the key — the kernel proves possession, it does not retire
        anything."""
        return self.challenge.challenge_id

    @property
    def key_id(self) -> str:
        return self.key.key_id

    @property
    def deployment_ref(self) -> str:
        """The identity the signature PROVED."""
        return self.key.deployment_ref


def answer_possession_challenge(
    challenge: DeploymentPossessionChallenge, *, signer: DeploymentSigner
) -> DeploymentPossessionResponse:
    """Sign a challenge with the deployment's key.

    The receiver-side counterpart to `verify_possession`. It exists so a
    receiver never hand-assembles the response and gets `key_id` wrong: a
    response naming a key other than the one that signed it is refused by the
    verifier, and that failure is far cheaper to prevent here than to diagnose
    from the other side of the wire.

    It checks BOTH halves of the signer's identity before signing. Checking
    `key_id` alone was a real defect: a challenge naming this signer's key but
    a foreign `deployment_ref` was signed happily, minting portable evidence
    that this key proved possession for another deployment. The verifier would
    refuse to activate on it, but the deployment must not produce it at all.
    """
    _assert_signer_owns(
        signer,
        key_id=challenge.key_id,
        deployment_ref=challenge.deployment_ref,
        what=f"challenge {challenge.challenge_id!r}",
    )
    return DeploymentPossessionResponse(
        challenge_id=challenge.challenge_id,
        key_id=challenge.key_id,
        signature=signer.sign(challenge.signing_input()),
    )


def verify_possession(
    challenge: DeploymentPossessionChallenge,
    response: DeploymentPossessionResponse | Mapping[str, object] | str | bytes,
    *,
    key: DeploymentVerificationKey,
    now: datetime,
) -> VerifiedDeploymentPossession:
    """Check a challenge response; raise on every failure.

    `challenge` is the vendor's OWN stored record, not anything the caller
    supplied alongside the response — that is what makes the response's two
    identifiers mere routing.

    Checks run structural → temporal → cryptographic, so a failure names the
    thing that is actually wrong:

    1. The challenge's bindings against the key being activated. A response is
       evidence for the key and deployment named IN the challenge; accepting
       it elsewhere would let one valid response activate an unrelated key.
    2. The response's identifiers against that challenge — a response for a
       different challenge or key is a mismatch, not a bad signature.
    3. Expiry, BEFORE the signature, so an expired challenge is reported as
       expired: the two send an operator to completely different places.
    4. The signature.
    """
    answer = (
        response
        if isinstance(response, DeploymentPossessionResponse)
        else DeploymentPossessionResponse.from_wire(response)
    )

    if challenge.key_id != key.key_id or challenge.deployment_ref != key.deployment_ref:
        raise DeploymentMismatchError(
            f"challenge {challenge.challenge_id!r} was issued for "
            f"{challenge.key_id!r}/{challenge.deployment_ref!r}, not "
            f"{key.key_id!r}/{key.deployment_ref!r}"
        )
    if (
        answer.challenge_id != challenge.challenge_id
        or answer.key_id != challenge.key_id
    ):
        raise DeploymentMismatchError(
            f"possession response names {answer.challenge_id!r}/{answer.key_id!r}, "
            f"but the stored challenge is {challenge.challenge_id!r}/"
            f"{challenge.key_id!r}"
        )
    if challenge.is_expired(now):
        raise LicenceExpiredError(
            f"possession challenge {challenge.challenge_id!r} expired at "
            f"{challenge.expires_at.isoformat()}"
        )
    licence_key = LicenceKey(key_id=key.key_id, public_key_b64=key.public_key_b64)
    if not _ed25519_verifies(licence_key, answer.signature, challenge.signing_input()):
        raise BadSignatureError(
            f"possession response for {challenge.challenge_id!r} did not verify"
        )
    return VerifiedDeploymentPossession(challenge=challenge, key=key, verified_at=now)


# ── Envelope handling ───────────────────────────────────────────────────────


def _b64url_encode(data: bytes) -> str:
    """Unpadded base64url — the encoding every kernel envelope field uses."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    """Strict base64url decode (padding optional, no foreign characters —
    a sloppy decode would let a 'corrupt' envelope alias a different one)."""
    padded = value + "=" * (-len(value) % 4)
    translated = padded.replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(translated.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise MalformedLicenceError(f"invalid base64url field: {exc}") from exc


def _load_envelope(
    envelope: Mapping[str, object] | str | bytes,
) -> Mapping[str, object]:
    if isinstance(envelope, str | bytes):
        try:
            parsed = json.loads(envelope)
        except (ValueError, UnicodeDecodeError) as exc:
            raise MalformedLicenceError(f"envelope is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise MalformedLicenceError("envelope JSON must be an object")
        return parsed
    if not isinstance(envelope, Mapping):
        raise MalformedLicenceError("envelope must be a JSON object")
    return envelope


@dataclass(frozen=True, slots=True)
class _Signature:
    key_id: str
    signature: bytes


def _validated_signatures(envelope: Mapping[str, object]) -> list[_Signature]:
    raw = envelope.get("signatures")
    if not isinstance(raw, list) or not raw:
        raise MalformedLicenceError("envelope needs a non-empty 'signatures' list")
    entries: list[_Signature] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise MalformedLicenceError("signature entry must be an object")
        key_id = entry.get("key_id")
        algorithm = entry.get("algorithm")
        signature_b64 = entry.get("signature_b64")
        if not isinstance(key_id, str) or not key_id:
            raise MalformedLicenceError("signature entry needs a 'key_id'")
        if algorithm != _ALGORITHM:
            # No algorithm negotiation an attacker can steer — anything but
            # ed25519 is a different (future) envelope schema, not a fallback.
            raise MalformedLicenceError(
                f"unsupported signature algorithm: {algorithm!r}"
            )
        if not isinstance(signature_b64, str) or not signature_b64:
            raise MalformedLicenceError("signature entry needs a 'signature_b64'")
        entries.append(
            _Signature(key_id=key_id, signature=_b64url_decode(signature_b64))
        )
    return entries


def _ed25519_verifies(key: LicenceKey, signature: bytes, payload: bytes) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError as exc:
        raise VerificationUnavailableError(
            "signature verification needs the 'cryptography' package — install "
            "the dotmac-kernel[licensing] extra"
        ) from exc
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            _b64url_decode(key.public_key_b64)
        )
        public_key.verify(signature, payload)
    except InvalidSignature:
        return False
    except (ValueError, MalformedLicenceError):
        # A malformed PUBLIC key is a keyring configuration error; fail closed
        # as non-verifying rather than treating it as a document problem.
        return False
    return True


def _verify_envelope(
    envelope: Mapping[str, object] | str | bytes, keyring: LicenceKeyRing
) -> bytes:
    """Structural check + signature verification. Returns the payload bytes —
    the ONLY route to them, so nothing downstream sees an unverified payload."""
    parsed = _load_envelope(envelope)
    if parsed.get("schema") != ENVELOPE_SCHEMA:
        raise MalformedLicenceError(
            f"unknown envelope schema: {parsed.get('schema')!r}"
        )
    payload_b64 = parsed.get("payload_b64")
    if not isinstance(payload_b64, str) or not payload_b64:
        raise MalformedLicenceError("envelope needs a 'payload_b64'")
    payload = _b64url_decode(payload_b64)
    signatures = _validated_signatures(parsed)

    saw_revoked = False
    saw_acceptable = False
    saw_unknown = False
    for entry in signatures:
        key = keyring.get(entry.key_id)
        if key is None:
            saw_unknown = True
            continue
        if key.status is KeyStatus.REVOKED:
            saw_revoked = True
            continue
        saw_acceptable = True
        if _ed25519_verifies(key, entry.signature, payload):
            return payload
    if saw_revoked and not saw_acceptable:
        raise RevokedKeyError("licence is covered only by a revoked key")
    if saw_unknown and not saw_acceptable:
        raise UnknownKeyError("no signature references a key in the keyring")
    raise BadSignatureError("no signature verifies under an acceptable key")


# ── Payload parsing (strict, fail-closed) ───────────────────────────────────


def _require_str(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise MalformedLicenceError(f"'{name}' must be a non-empty string")
    return value


def _optional_str(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise MalformedLicenceError(f"'{name}' must be a non-empty string or null")
    return value


def _strict_int(value: object, name: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise MalformedLicenceError(f"'{name}' must be an integer >= {minimum}")
    return value


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise MalformedLicenceError(f"'{name}' must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MalformedLicenceError(f"'{name}' is not a valid timestamp") from exc
    if parsed.utcoffset() is None:
        raise MalformedLicenceError(f"'{name}' must be timezone-aware")
    return parsed


def _optional_aware_datetime(
    payload: Mapping[str, object], name: str
) -> datetime | None:
    value = payload.get(name)
    if value is None:
        return None
    return _aware_datetime(value, name)


def _parse_subject(value: object) -> LicenceSubject:
    if not isinstance(value, Mapping):
        raise MalformedLicenceError("'subject' must be an object")
    customer = value.get("customer")
    if not isinstance(customer, str) or not customer:
        raise MalformedLicenceError("'subject.customer' must be a non-empty string")
    deployment_id = value.get("deployment_id")
    if deployment_id is not None and (
        not isinstance(deployment_id, str) or not deployment_id
    ):
        raise MalformedLicenceError(
            "'subject.deployment_id' must be a non-empty string or null"
        )
    return LicenceSubject(customer=customer, deployment_id=deployment_id)


def _parse_capabilities(value: object) -> tuple[CapabilityGrant, ...]:
    if not isinstance(value, list):
        raise MalformedLicenceError("'capabilities' must be a list")
    grants: list[CapabilityGrant] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            raise MalformedLicenceError("capability entry must be an object")
        code = entry.get("code")
        if not isinstance(code, str) or not code:
            raise MalformedLicenceError("capability entry needs a non-empty 'code'")
        limits = entry.get("limits", {})
        if not isinstance(limits, Mapping):
            raise MalformedLicenceError("capability 'limits' must be an object")
        grants.append(CapabilityGrant(code=code, limits=dict(limits)))
    return tuple(grants)


def _parse_document(payload: bytes) -> LicenceDocument:
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedLicenceError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedLicenceError("payload JSON must be an object")
    if data.get("schema") != LICENCE_SCHEMA:
        raise MalformedLicenceError(f"unknown licence schema: {data.get('schema')!r}")
    constraints = data.get("constraints", {})
    if not isinstance(constraints, Mapping):
        raise MalformedLicenceError("'constraints' must be an object")
    document = LicenceDocument(
        licence_id=_require_str(data, "licence_id"),
        licence_version=_strict_int(
            data.get("licence_version"), "licence_version", minimum=1
        ),
        issuer=_require_str(data, "issuer"),
        product=_require_str(data, "product"),
        subject=_parse_subject(data.get("subject")),
        capabilities=_parse_capabilities(data.get("capabilities")),
        issued_at=_aware_datetime(data.get("issued_at"), "issued_at"),
        edition=_optional_str(data, "edition"),
        not_before=_optional_aware_datetime(data, "not_before"),
        expires_at=_optional_aware_datetime(data, "expires_at"),
        grace_days=_strict_int(data.get("grace_days", 0), "grace_days", minimum=0),
        constraints=dict(constraints),
    )
    if (
        document.not_before is not None
        and document.expires_at is not None
        and document.not_before > document.expires_at
    ):
        raise MalformedLicenceError("'not_before' is after 'expires_at'")
    return document


# ── Verification ────────────────────────────────────────────────────────────


def verify_licence(
    envelope: Mapping[str, object] | str | bytes,
    *,
    keyring: LicenceKeyRing,
    now: datetime,
    expected_deployment_id: str | None = None,
    require_binding: bool = False,
    applied: AppliedLicence | None = None,
    revoked_licence_ids: frozenset[str] = frozenset(),
) -> VerifiedLicence:
    """Verify a delivered licence envelope, fail-closed and fully offline.

    Check order is part of the contract (nothing leaks from an unverified
    document): envelope shape → signature → payload parse → licence revocation
    → deployment binding → validity against the injected ``now`` (explicit
    ``in_grace`` state; ``expires_at`` absent = perpetual) → replay/rollback
    against the receiver's ``applied`` record (same lineage only). Raises a
    ``LicenceError`` subclass on any failure; returns ``VerifiedLicence``.
    """
    if now.utcoffset() is None:
        raise ValueError("'now' must be timezone-aware")

    payload = _verify_envelope(envelope, keyring)
    document = _parse_document(payload)
    digest = payload_digest(payload)

    if document.licence_id in revoked_licence_ids:
        raise RevokedLicenceError(f"licence {document.licence_id!r} is revoked")

    bound_to = document.subject.deployment_id
    if bound_to is not None:
        if expected_deployment_id is None or bound_to != expected_deployment_id:
            raise DeploymentMismatchError(
                f"licence is bound to deployment {bound_to!r}"
            )
    elif require_binding:
        raise DeploymentMismatchError(
            "an unbound licence is not acceptable when binding is required"
        )

    if document.not_before is not None and now < document.not_before:
        raise LicenceNotYetValidError(
            f"licence is not valid before {document.not_before.isoformat()}"
        )
    validity = "valid"
    if document.expires_at is not None and now > document.expires_at:
        grace_end = document.expires_at + timedelta(days=document.grace_days)
        if now > grace_end:
            raise LicenceExpiredError(
                f"licence expired {document.expires_at.isoformat()} "
                f"(grace ended {grace_end.isoformat()})"
            )
        validity = "in_grace"

    reapplied = False
    if applied is not None and applied.licence_id == document.licence_id:
        if document.licence_version < applied.licence_version:
            raise StaleLicenceError(
                f"licence version {document.licence_version} is older than the "
                f"applied version {applied.licence_version}"
            )
        if document.licence_version == applied.licence_version:
            if digest != applied.digest:
                raise LicenceConflictError(
                    f"version {document.licence_version} was already applied "
                    "with a different digest"
                )
            reapplied = True

    return VerifiedLicence(
        document=document, digest=digest, validity=validity, reapplied=reapplied
    )


def verify_revocation_list(
    envelope: Mapping[str, object] | str | bytes,
    *,
    keyring: LicenceKeyRing,
    applied_list_version: int | None = None,
) -> RevocationList:
    """Verify a signed revocation list (same envelope mechanics, payload schema
    ``dotmac-licence-revocation/1``) and enforce monotonic ``list_version``
    against the receiver's last applied list — a stale list cannot un-revoke;
    an equal version is an idempotent re-import."""
    payload = _verify_envelope(envelope, keyring)
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedLicenceError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedLicenceError("payload JSON must be an object")
    if data.get("schema") != REVOCATION_SCHEMA:
        raise MalformedLicenceError(
            f"unknown revocation schema: {data.get('schema')!r}"
        )
    list_version = _strict_int(data.get("list_version"), "list_version", minimum=1)
    issued_at = _aware_datetime(data.get("issued_at"), "issued_at")
    raw_ids = data.get("revoked_licence_ids")
    if not isinstance(raw_ids, list):
        raise MalformedLicenceError("'revoked_licence_ids' must be a list")
    revoked: set[str] = set()
    for licence_id in raw_ids:
        if not isinstance(licence_id, str) or not licence_id:
            raise MalformedLicenceError(
                "'revoked_licence_ids' entries must be non-empty strings"
            )
        revoked.add(licence_id)
    if applied_list_version is not None and list_version < applied_list_version:
        raise StaleRevocationListError(
            f"revocation list version {list_version} is older than the applied "
            f"version {applied_list_version}"
        )
    return RevocationList(
        list_version=list_version,
        issued_at=issued_at,
        revoked_licence_ids=frozenset(revoked),
    )


__all__ = [
    # schemas
    "ENVELOPE_SCHEMA",
    "LICENCE_SCHEMA",
    "REVOCATION_SCHEMA",
    # keyring
    "KeyStatus",
    "LicenceKey",
    "LicenceKeyRing",
    # document / results
    "LicenceSubject",
    "CapabilityGrant",
    "LicenceDocument",
    "AppliedLicence",
    "VerifiedLicence",
    "RevocationList",
    "LicenceAcknowledgement",
    "ReceiverAppliedState",
    "APPLIED_STATE_SCHEMA",
    "UNKNOWN_DIGEST",
    # applied-state envelope (ADR-0007)
    "APPLIED_STATE_ENVELOPE_SCHEMA",
    "APPLIED_STATE_DOMAIN",
    "DEPLOYMENT_CHALLENGE_DOMAIN",
    "DEPLOYMENT_CHALLENGE_SCHEMA",
    "DEPLOYMENT_RESPONSE_SCHEMA",
    "AppliedStateEnvelope",
    "DeploymentSigner",
    "DeploymentPossessionChallenge",
    "DeploymentPossessionResponse",
    "DeploymentVerificationKey",
    "VerifiedAppliedState",
    "VerifiedDeploymentPossession",
    "applied_state_signing_input",
    "answer_possession_challenge",
    "seal_applied_state",
    "verify_applied_state",
    "verify_possession",
    "applied_state_payload",
    "parse_applied_state",
    # operations
    "payload_digest",
    "verify_licence",
    "verify_revocation_list",
    # errors
    "LicenceError",
    "MalformedLicenceError",
    "UnknownKeyError",
    "RevokedKeyError",
    "BadSignatureError",
    "RevokedLicenceError",
    "DeploymentMismatchError",
    "LicenceNotYetValidError",
    "LicenceExpiredError",
    "StaleLicenceError",
    "LicenceConflictError",
    "StaleRevocationListError",
    "VerificationUnavailableError",
    "DuplicateKeyError",
    "MalformedAppliedStateError",
]
