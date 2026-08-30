"""The human credential lifecycle: one owner, typed verdicts, no caller-supplied
password material.

This is the stateless engine for what a product does to a HUMAN password
credential — provision it, verify it, complete an individually authorized
reset, and force-reset an approved cohort. It holds no session, no ORM model, no
provider client and no network reach; every persistent or external effect goes
through a typed product port, and every database effect happens inside the
CALLER's transaction.

## Why this exists at all

`dotmac_sub` is the qualifying product-first source (hard rule 24): it is the
only Dotmac product running the full lifecycle in production. The census at
`docs/inventories/credential-lifecycle-sources.md` measured it, and two numbers
are the reason for this module:

* verification has FOUR owners in Sub — `auth_flow.py`,
  `web_system_db_inspector.py`, `web_system_profiles.py`,
  `web_customer_auth.py`, ten call sites — each deciding for itself what
  active/locked/reset-required means and each returning a bare boolean; and
* `hash_password` has ELEVEN non-test call sites, two of which are seed
  scripts, so a guard scoped to `app/` would have missed two of them.

The shape that made an incident possible was narrower than either count.
`reseller_onboarding._create_credential` took a caller-supplied `password`
parameter that no supported caller ever passed, and that unused parameter is how
one value reached 24 external organisations. Sub removed the parameter
(PR #2826). Removal is not the same as impossibility, and this module exists to
make the shape UNREPRESENTABLE: `provision` accepts no secret, generates its own
material from an injectable cryptographic source, hashes it with the kernel's
canonical primitive, and discards it. There is no return path, no log line, no
exception message, no `repr` and no receipt field through which the generated
value can leave.

That is also why provisioning emits a RECOVERY INTENT instead of delivering a
password. The subject recovers through the product's proven recovery channel;
the material this module generates is written once as a hash and never exists
anywhere else. A facility that cannot hand you the secret cannot leak it.

## What the engine deliberately does not know

Never: `reseller_user` / `subscriber` / `system_user` / any ERP principal kind;
product tables or ORM models; email or SMS providers; OpenBao clients; Redis or
any network client; HTTP status codes; product permissions or roles. Principal
kinds, applications, credentials and approval decisions are all OPAQUE strings —
this module never parses, splits, case-folds or interprets one.

It also never issues a session. Verification returns a typed verdict; mapping
that verdict to an HTTP response and deciding whether to mint a session are the
product adapter's decisions, made once, in one place, with the verdict in hand.

## Legacy-hash rehash is NEW behaviour, not extracted behaviour

`password_needs_rehash` returns zero hits across `dotmac_sub`, `dotmac_erp` and
`dotmac_vendor_control_plane`. No product upgrades a legacy hash on successful
login today. `CredentialVerificationResult.replacement_hash` is therefore built
and tested here as new behaviour and must not be described as ported.

## Authority

This is PRODUCT SECURITY authority. `dotmac-deployment-control` owns fleet
deployment intent and must not authorize an account mutation; a force-reset plan
is approved by a product approval policy, named on the plan and re-checked
against the authorization.
"""

from __future__ import annotations

import hashlib
import json
import secrets as _secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, Protocol

from dotmac_kernel.security import (
    hash_password,
    password_needs_rehash,
    verify_password,
)

CREDENTIAL_LIFECYCLE_SCHEMA_VERSION: Final[int] = 1

#: What a verification can conclude. A boolean cannot carry four of these, which
#: is why every one of Sub's four verification owners had to re-derive the other
#: three from fields it read itself.
CredentialVerificationVerdict = Literal[
    "accepted",
    "reset_required",
    "invalid",
    "locked",
    "disabled",
]

#: The masked stand-in printed wherever a hash or a secret would otherwise be
#: rendered. A constant, so a test can assert the mask is present rather than
#: assert the secret is absent — absence passes for the wrong reason when the
#: field was simply dropped.
REDACTED: Final[str] = "<redacted>"

#: Bytes of entropy in generated material. 32 bytes is 256 bits; the value is
#: never shown to a human, so length carries no usability cost.
_GENERATED_SECRET_BYTES: Final[int] = 32


# ── Errors ──────────────────────────────────────────────────────────────────


class CredentialLifecycleError(Exception):
    """Base class. No subclass ever carries secret material in its message."""


class CredentialPolicyViolation(CredentialLifecycleError):
    """The product-installed password policy refused the material."""


class CredentialPlanError(CredentialLifecycleError):
    """A cohort reset plan is empty, duplicated, or malformed."""


class CredentialAuthorizationError(CredentialLifecycleError):
    """An authorization is missing, expired, single-use-spent, or does not
    match the plan it is presented with."""


class CredentialCohortDrift(CredentialLifecycleError):
    """A target changed, vanished, or was never there between planning and
    applying. The whole plan is refused; a partially applied cohort is not a
    smaller cohort, it is an unrecorded one."""


class CredentialEffectNotRecorded(CredentialLifecycleError):
    """A product port reported no durable intent for an effect this module
    requires. Treated as a failure rather than a warning: a reset with no
    session revocation leaves the old credential's sessions alive."""


def _require_opaque(value: str, field_name: str) -> None:
    """Non-empty, and otherwise uninterpreted.

    Deliberately NOT stripped, case-folded, split on a separator or validated
    against a grammar: every one of these strings names something a product
    owns, and a kernel that imposes a shape on it has started to know what it
    must not know.
    """
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


# ── Opaque references and observed state ────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class CredentialSubjectRef:
    """Who a credential belongs to, said entirely in the product's own words.

    `principal_kind` is where `reseller_user`, `subscriber`, `system_user` and
    every ERP principal live — as opaque text this module stores, sorts and
    hands back, never as a branch.
    """

    application: str
    principal_kind: str
    principal_ref: str
    credential_ref: str

    def __post_init__(self) -> None:
        _require_opaque(self.application, "application")
        _require_opaque(self.principal_kind, "principal kind")
        _require_opaque(self.principal_ref, "principal reference")
        _require_opaque(self.credential_ref, "credential reference")


@dataclass(frozen=True, slots=True)
class CredentialSnapshot:
    """What a product adapter observed about one stored credential.

    The stored hash is not the secret, but it is still material a log line has
    no business holding — so `__repr__` masks it. Every other field is a
    lifecycle fact the engine needs and a reviewer should see.
    """

    subject: CredentialSubjectRef
    password_hash: str
    credential_version: int
    is_active: bool = True
    is_locked: bool = False
    reset_required: bool = False

    def __post_init__(self) -> None:
        _require_opaque(self.password_hash, "password hash")
        if self.credential_version < 0:
            raise ValueError("credential version must not be negative")

    def __repr__(self) -> str:  # pragma: no cover - exercised via secret tests
        return (
            "CredentialSnapshot("
            f"subject={self.subject!r}, password_hash={REDACTED!r}, "
            f"credential_version={self.credential_version}, "
            f"is_active={self.is_active}, is_locked={self.is_locked}, "
            f"reset_required={self.reset_required})"
        )


@dataclass(frozen=True, slots=True)
class CredentialVerificationResult:
    """One verdict, from one owner.

    `replacement_hash` is a REQUEST, never a write: the engine has the raw
    material only during verification, so it is the only place a legacy hash can
    be upgraded — but the write belongs to the product adapter, inside the
    caller's transaction.
    """

    verdict: CredentialVerificationVerdict
    subject: CredentialSubjectRef
    credential_version: int | None = None
    replacement_hash: str | None = None

    @property
    def accepted(self) -> bool:
        """True only for `accepted`. `reset_required` is a successful password
        check that must not become a session — collapsing the two is how a
        forced reset becomes advisory."""
        return self.verdict == "accepted"

    @property
    def rehash_requested(self) -> bool:
        return self.replacement_hash is not None

    def __repr__(self) -> str:  # pragma: no cover - exercised via secret tests
        shown = REDACTED if self.replacement_hash is not None else None
        return (
            "CredentialVerificationResult("
            f"verdict={self.verdict!r}, subject={self.subject!r}, "
            f"credential_version={self.credential_version}, "
            f"replacement_hash={shown!r})"
        )


# ── The plan digest: typed, and owned by this side ──────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class CredentialResetPlanDigestV1:
    """The canonical identity of one cohort reset plan.

    Typed rather than a bare `str`, because a digest that crosses a boundary as
    text can be compared against the wrong thing and nothing complains. It is
    owned HERE and by this module alone: it is not a universal kernel digest, it
    is not `dotmac-deployment-control`'s plan digest, and neither is
    interchangeable with it. Two digest types that happen to be SHA-256 are two
    contracts, not one.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or len(self.value) != 64:
            raise ValueError("plan digest must be 64 hexadecimal characters")
        if any(character not in "0123456789abcdef" for character in self.value):
            raise ValueError("plan digest must be lowercase hexadecimal")

    def __str__(self) -> str:
        return self.value


# ── Cohort force reset ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class CredentialResetTargetV1:
    """One credential in a cohort, with the version the plan was built against.

    The expected version is what makes a stale plan detectable: a credential the
    owner changed after approval is drift, and drift refuses the plan rather
    than silently resetting a credential nobody approved in that state.
    """

    subject: CredentialSubjectRef
    expected_credential_version: int

    def __post_init__(self) -> None:
        if self.expected_credential_version < 0:
            raise ValueError("expected credential version must not be negative")


@dataclass(frozen=True, slots=True)
class CredentialResetPlanV1:
    """An approved-in-principle cohort reset, canonical and self-identifying."""

    application: str
    product: str
    reason_code: str
    approval_policy_code: str
    approval_policy_version: int
    expires_at: datetime
    targets: tuple[CredentialResetTargetV1, ...]
    schema_version: int = CREDENTIAL_LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_opaque(self.application, "application")
        _require_opaque(self.product, "product")
        _require_opaque(self.reason_code, "reason code")
        _require_opaque(self.approval_policy_code, "approval policy code")
        _require_aware(self.expires_at, "plan expiry")
        if self.approval_policy_version < 1:
            raise ValueError("approval policy version must be positive")
        targets = tuple(sorted(self.targets))
        if not targets:
            raise CredentialPlanError(
                "a cohort reset plan must name at least one target; an empty "
                "plan would produce an approved receipt for no work"
            )
        subjects = [target.subject for target in targets]
        if len(set(subjects)) != len(subjects):
            duplicates = sorted(
                {
                    subject.credential_ref
                    for subject in subjects
                    if subjects.count(subject) > 1
                }
            )
            raise CredentialPlanError(
                "a cohort reset plan names a credential twice: "
                + ", ".join(repr(item) for item in duplicates)
            )
        object.__setattr__(self, "targets", targets)

    @property
    def digest(self) -> CredentialResetPlanDigestV1:
        """SHA-256 over the canonical document. Sorted keys and compact
        separators, so field order and incidental whitespace cannot change the
        identity of an approved plan."""
        document = {
            "schema_version": self.schema_version,
            "application": self.application,
            "product": self.product,
            "reason_code": self.reason_code,
            "approval_policy_code": self.approval_policy_code,
            "approval_policy_version": self.approval_policy_version,
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "targets": [
                {
                    "application": target.subject.application,
                    "principal_kind": target.subject.principal_kind,
                    "principal_ref": target.subject.principal_ref,
                    "credential_ref": target.subject.credential_ref,
                    "expected_credential_version": (target.expected_credential_version),
                }
                for target in self.targets
            ],
        }
        payload = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return CredentialResetPlanDigestV1(hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class CredentialResetAuthorizationV1:
    """The separate fact that an approval policy decided this exact plan.

    Separate from the plan on purpose: a plan that carries its own approval is a
    plan that can be approved by whoever builds it. There is deliberately NO
    `approved_by` field — an approval DECISION is a product record with its own
    owner, actor, quorum and audit trail, and `approval_decision_ref` points at
    it. Copying an actor name in here would create a second, weaker claim about
    who approved, and the weaker one is the one an automated reader would
    believe.
    """

    plan_digest: CredentialResetPlanDigestV1
    approval_policy_code: str
    approval_policy_version: int
    approval_decision_ref: str
    approved_at: datetime
    schema_version: int = CREDENTIAL_LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.plan_digest, CredentialResetPlanDigestV1):
            raise TypeError(
                "plan_digest must be a CredentialResetPlanDigestV1; a bare "
                "string is how a digest gets compared against the wrong thing"
            )
        _require_opaque(self.approval_policy_code, "approval policy code")
        _require_opaque(self.approval_decision_ref, "approval decision reference")
        _require_aware(self.approved_at, "approval timestamp")
        if self.approval_policy_version < 1:
            raise ValueError("approval policy version must be positive")


@dataclass(frozen=True, slots=True)
class CredentialResetOutcomeV1:
    """What happened to one credential. Secret-free by construction: there is
    no field a value could occupy."""

    subject: CredentialSubjectRef
    previous_credential_version: int
    credential_version: int
    reset_required: bool = True
    sessions_revoked: bool = True


@dataclass(frozen=True, slots=True)
class CredentialResetReceiptV1:
    """Append-only evidence that an authorized plan was applied.

    Also the idempotency record: a retry presents the same authorization, the
    audit port returns the stored receipt, and nothing is generated, written or
    revoked a second time.
    """

    plan_digest: CredentialResetPlanDigestV1
    approval_decision_ref: str
    approval_policy_code: str
    approval_policy_version: int
    reason_code: str
    applied_at: datetime
    outcomes: tuple[CredentialResetOutcomeV1, ...]
    schema_version: int = CREDENTIAL_LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_aware(self.applied_at, "receipt timestamp")
        if not self.outcomes:
            raise ValueError("a receipt must record at least one outcome")


# ── Individual reset, provisioning, and the intents they emit ───────────────


@dataclass(frozen=True, slots=True)
class CredentialRecoveryAuthorizationV1:
    """An ALREADY-PROVEN, single-use recovery authorization.

    This module does not prove it. Proving that a recovery link, one-time code
    or step-up challenge belongs to this subject is the product's job, because
    only the product knows the channel. What this module requires is that the
    proof be spent atomically — `RecoveryIntentPort.consume` returns False for
    an authorization that was already used, and a second reset completion on the
    same proof is refused.
    """

    subject: CredentialSubjectRef
    recovery_ref: str
    proven_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_opaque(self.recovery_ref, "recovery reference")
        _require_aware(self.proven_at, "recovery proof timestamp")
        _require_aware(self.expires_at, "recovery expiry")
        if self.expires_at <= self.proven_at:
            raise ValueError("recovery authorization expires before it was proven")


@dataclass(frozen=True, slots=True)
class CredentialRecoveryIntentV1:
    """A durable request that the product start its recovery flow for a subject.

    It carries NO credential material — not the generated value, not the hash,
    not a hint. That is the whole point: provisioning and force reset make a
    credential nobody can present, and the subject reaches a usable one through
    the product's own proven recovery channel.
    """

    subject: CredentialSubjectRef
    reason_code: str
    credential_version: int
    requested_at: datetime
    correlation_ref: str

    def __post_init__(self) -> None:
        _require_opaque(self.reason_code, "reason code")
        _require_opaque(self.correlation_ref, "correlation reference")
        _require_aware(self.requested_at, "intent timestamp")


@dataclass(frozen=True, slots=True)
class CredentialSessionRevocationIntentV1:
    """A durable request that every session for a principal be revoked.

    Returned BY the port rather than merely passed to it, so the engine can tell
    "the adapter wrote a durable intent" from "the adapter returned None". The
    session store is external and reconciled idempotently; a revocation that was
    never recorded is the failure mode this type exists to make visible.
    """

    subject: CredentialSubjectRef
    reason_code: str
    credential_version: int
    requested_at: datetime
    intent_ref: str

    def __post_init__(self) -> None:
        _require_opaque(self.reason_code, "reason code")
        _require_opaque(self.intent_ref, "revocation intent reference")
        _require_aware(self.requested_at, "intent timestamp")


@dataclass(frozen=True, slots=True)
class CredentialAuditEntryV1:
    """One append-only lifecycle fact. No field can hold credential material."""

    subject: CredentialSubjectRef
    action: str
    reason_code: str
    credential_version: int
    occurred_at: datetime
    correlation_ref: str
    detail: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_opaque(self.action, "audit action")
        _require_opaque(self.reason_code, "reason code")
        _require_opaque(self.correlation_ref, "correlation reference")
        _require_aware(self.occurred_at, "audit timestamp")


@dataclass(frozen=True, slots=True)
class CredentialProvisioningReceiptV1:
    """Proof that a credential exists and that nobody was told what it is.

    There is no `password`, `secret`, `temporary_password` or `token` field, and
    adding one would be the defect this module was built to prevent. The only
    way anybody reaches the credential is `recovery_intent_ref`.
    """

    subject: CredentialSubjectRef
    credential_version: int
    reset_required: bool
    recovery_intent_ref: str
    provisioned_at: datetime
    schema_version: int = CREDENTIAL_LIFECYCLE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CredentialResetCompletionReceiptV1:
    """Proof that a subject set a new credential through a spent recovery proof."""

    subject: CredentialSubjectRef
    previous_credential_version: int
    credential_version: int
    recovery_ref: str
    completed_at: datetime
    schema_version: int = CREDENTIAL_LIFECYCLE_SCHEMA_VERSION


# ── Product ports ───────────────────────────────────────────────────────────


class CredentialStorePort(Protocol):
    """The product's credential rows, in the CALLER's transaction.

    Every method runs inside the transaction the caller already owns. This
    module never commits, never rolls back, and never opens a session — so
    "all targets atomically or none" is a property of the caller's transaction
    boundary, which is the only place it can honestly live.
    """

    def load(self, subject: CredentialSubjectRef) -> CredentialSnapshot | None:
        """The current state, or None when no such credential exists."""

    def lock_for_update(
        self, subject: CredentialSubjectRef
    ) -> CredentialSnapshot | None:
        """The current state, row-locked for the rest of the transaction."""

    def create(
        self,
        subject: CredentialSubjectRef,
        *,
        password_hash: str,
        reset_required: bool,
    ) -> CredentialSnapshot:
        """Insert a credential and return its persisted state."""

    def write_hash(
        self,
        subject: CredentialSubjectRef,
        *,
        password_hash: str,
        credential_version: int,
        reset_required: bool,
    ) -> None:
        """Replace the stored hash and advance the credential version."""


class SessionRevocationPort(Protocol):
    """Durable revocation intents for an EXTERNAL session store.

    Returns the intent it recorded. A port that returns None has not recorded
    one, and the engine refuses the operation rather than completing a reset
    whose old sessions are still live.
    """

    def request_revocation(
        self,
        subject: CredentialSubjectRef,
        *,
        reason_code: str,
        credential_version: int,
        correlation_ref: str,
    ) -> CredentialSessionRevocationIntentV1 | None: ...


class RecoveryIntentPort(Protocol):
    """The product's recovery channel — as intents in, and proofs spent."""

    def emit(self, intent: CredentialRecoveryIntentV1) -> str | None:
        """Record a durable recovery intent; return its reference."""

    def consume(self, authorization: CredentialRecoveryAuthorizationV1) -> bool:
        """Atomically spend a single-use proof. False if already spent."""


class PasswordPolicyPort(Protocol):
    """The product's password rules.

    Raise `CredentialPolicyViolation` to refuse. The kernel ships no policy: a
    length, dictionary or breach rule is a product decision, and a default here
    would quietly become everyone's policy.
    """

    def validate(self, subject: CredentialSubjectRef, secret: str) -> None: ...


class CredentialAuditPort(Protocol):
    """Append-only evidence, and the idempotency record for a cohort reset."""

    def record_event(self, entry: CredentialAuditEntryV1) -> None: ...

    def record_receipt(self, receipt: CredentialResetReceiptV1) -> None: ...

    def find_receipt(
        self, plan_digest: CredentialResetPlanDigestV1
    ) -> CredentialResetReceiptV1 | None: ...


#: A cryptographic source of new credential material, injectable so a test can
#: observe uniqueness without the production path gaining a seam that returns
#: the value to a caller.
SecretSource = Callable[[], str]


def default_secret_source() -> str:
    """256 bits from `secrets.token_urlsafe`. Never logged, never returned."""
    return _secrets.token_urlsafe(_GENERATED_SECRET_BYTES)


def _utcnow() -> datetime:
    return datetime.now(UTC)


_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    """A hash of throwaway material, so verifying against a credential that does
    not exist costs the same as verifying against one that does.

    Computed lazily and cached for the process: Argon2id at the OWASP parameters
    is deliberately expensive, and an engine built per request must not pay for
    a fresh one. Cached at module level rather than per instance for exactly
    that reason.
    """
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(default_secret_source())
    return _DUMMY_HASH


# ── The engine ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CredentialLifecycle:
    """The single owner of human credential decisions.

    Construct one per product adapter, with that product's ports. It holds no
    state between calls, so it is safe to build per request or once at startup.
    """

    store: CredentialStorePort
    sessions: SessionRevocationPort
    recovery: RecoveryIntentPort
    audit: CredentialAuditPort
    policy: PasswordPolicyPort | None = None
    secret_source: SecretSource = default_secret_source
    clock: Callable[[], datetime] = _utcnow

    # ── provisioning ────────────────────────────────────────────────────────

    def provision(
        self,
        subject: CredentialSubjectRef,
        *,
        reason_code: str,
        correlation_ref: str,
    ) -> CredentialProvisioningReceiptV1:
        """Create a credential nobody was told, and ask the product to start
        recovery.

        There is no parameter through which caller-supplied material could
        arrive, and no return value through which generated material could
        leave. That is the entire security property, and it is a property of the
        SIGNATURE rather than of anyone's discipline.
        """
        _require_opaque(reason_code, "reason code")
        _require_opaque(correlation_ref, "correlation reference")
        if self.store.load(subject) is not None:
            raise CredentialLifecycleError(
                "a credential already exists for this subject; provisioning "
                "must not silently overwrite one"
            )

        secret = self._generate(subject)
        try:
            snapshot = self.store.create(
                subject,
                password_hash=hash_password(secret),
                reset_required=True,
            )
        finally:
            del secret

        if not snapshot.reset_required:
            raise CredentialLifecycleError(
                "the credential store returned a provisioned credential that "
                "is not reset-required; provisioned material is unknown to its "
                "owner and must never be usable as a standing password"
            )

        now = self.clock()
        intent_ref = self.recovery.emit(
            CredentialRecoveryIntentV1(
                subject=subject,
                reason_code=reason_code,
                credential_version=snapshot.credential_version,
                requested_at=now,
                correlation_ref=correlation_ref,
            )
        )
        if not intent_ref:
            raise CredentialEffectNotRecorded(
                "no durable recovery intent was recorded for a provisioned "
                "credential; the subject would have no way to reach it"
            )
        self.audit.record_event(
            CredentialAuditEntryV1(
                subject=subject,
                action="credential.provisioned",
                reason_code=reason_code,
                credential_version=snapshot.credential_version,
                occurred_at=now,
                correlation_ref=correlation_ref,
            )
        )
        return CredentialProvisioningReceiptV1(
            subject=subject,
            credential_version=snapshot.credential_version,
            reset_required=True,
            recovery_intent_ref=intent_ref,
            provisioned_at=now,
        )

    # ── verification ────────────────────────────────────────────────────────

    def verify(
        self, subject: CredentialSubjectRef, secret: str
    ) -> CredentialVerificationResult:
        """The ONE verification owner. Returns a verdict, never a boolean, and
        never a session.

        Order is deliberate. The password is always checked first — against the
        real hash, or against throwaway material when no credential exists — so
        a missing, disabled or locked credential costs the same work as a live
        one. Only then are the lifecycle facts read, which also means `locked`
        and `disabled` are disclosed only to somebody who already presented the
        correct password.
        """
        snapshot = self.store.load(subject)
        if snapshot is None:
            verify_password(secret, _dummy_hash())
            return CredentialVerificationResult(verdict="invalid", subject=subject)

        matched = verify_password(secret, snapshot.password_hash)
        if not matched:
            return CredentialVerificationResult(
                verdict="invalid",
                subject=subject,
                credential_version=snapshot.credential_version,
            )

        replacement: str | None = None
        if password_needs_rehash(snapshot.password_hash):
            replacement = hash_password(secret)

        if not snapshot.is_active:
            verdict: CredentialVerificationVerdict = "disabled"
        elif snapshot.is_locked:
            verdict = "locked"
        elif snapshot.reset_required:
            verdict = "reset_required"
        else:
            verdict = "accepted"

        # A rehash is only worth requesting where the product will act on it. A
        # disabled or locked credential is not about to be written.
        if verdict in {"disabled", "locked"}:
            replacement = None

        return CredentialVerificationResult(
            verdict=verdict,
            subject=subject,
            credential_version=snapshot.credential_version,
            replacement_hash=replacement,
        )

    # ── individual reset completion ─────────────────────────────────────────

    def complete_reset(
        self,
        authorization: CredentialRecoveryAuthorizationV1,
        secret: str,
        *,
        reason_code: str = "credential.reset.completed",
    ) -> CredentialResetCompletionReceiptV1:
        """Accept new material — only behind a proven, single-use proof.

        This is one of exactly two entry points that take a raw value, and the
        only one that takes a CALLER-CHOSEN one. The proof is spent before the
        material is looked at, so a replayed proof cannot even reach the policy.
        """
        subject = authorization.subject
        now = self.clock()
        if authorization.expires_at <= now:
            raise CredentialAuthorizationError("the recovery authorization has expired")
        if not self.recovery.consume(authorization):
            raise CredentialAuthorizationError(
                "the recovery authorization was already spent; a single-use "
                "proof does not become reusable because the first attempt "
                "failed downstream"
            )

        snapshot = self.store.lock_for_update(subject)
        if snapshot is None:
            raise CredentialCohortDrift(
                "no credential exists for the subject named by a proven "
                "recovery authorization"
            )
        if self.policy is not None:
            self.policy.validate(subject, secret)

        next_version = snapshot.credential_version + 1
        self.store.write_hash(
            subject,
            password_hash=hash_password(secret),
            credential_version=next_version,
            reset_required=False,
        )
        self._revoke_sessions(
            subject,
            reason_code=reason_code,
            credential_version=next_version,
            correlation_ref=authorization.recovery_ref,
        )
        self.audit.record_event(
            CredentialAuditEntryV1(
                subject=subject,
                action="credential.reset.completed",
                reason_code=reason_code,
                credential_version=next_version,
                occurred_at=now,
                correlation_ref=authorization.recovery_ref,
            )
        )
        return CredentialResetCompletionReceiptV1(
            subject=subject,
            previous_credential_version=snapshot.credential_version,
            credential_version=next_version,
            recovery_ref=authorization.recovery_ref,
            completed_at=now,
        )

    # ── cohort force reset ──────────────────────────────────────────────────

    def plan_force_reset(
        self,
        *,
        application: str,
        product: str,
        reason_code: str,
        approval_policy_code: str,
        approval_policy_version: int,
        expires_at: datetime,
        targets: Iterable[CredentialResetTargetV1],
    ) -> CredentialResetPlanV1:
        """Build a canonical plan. Empty, duplicated and malformed cohorts are
        refused here, before anyone is asked to approve one."""
        return CredentialResetPlanV1(
            application=application,
            product=product,
            reason_code=reason_code,
            approval_policy_code=approval_policy_code,
            approval_policy_version=approval_policy_version,
            expires_at=expires_at,
            targets=tuple(targets),
        )

    def apply_force_reset(
        self,
        plan: CredentialResetPlanV1,
        authorization: CredentialResetAuthorizationV1,
    ) -> CredentialResetReceiptV1:
        """Apply an authorized plan, all targets or none.

        Every refusal happens before the first mutation: the digest is
        recalculated from the plan in hand and compared with the authorized one,
        the policy identity must match, the authorization must be unexpired, and
        every target is locked and version-checked. Only then does anything get
        written — so a cohort that drifted is refused intact rather than half
        applied.
        """
        now = self.clock()
        digest = plan.digest
        if digest != authorization.plan_digest:
            raise CredentialAuthorizationError(
                "the plan does not match the authorized digest; a plan that "
                "changed after approval is a different plan"
            )
        if (
            authorization.approval_policy_code != plan.approval_policy_code
            or authorization.approval_policy_version != plan.approval_policy_version
        ):
            raise CredentialAuthorizationError(
                "the authorization names a different approval policy than the "
                "plan it authorizes"
            )
        # Idempotency is checked BEFORE expiry, and the order is deliberate.
        # Returning a stored receipt performs no effect, so a worker retrying
        # after the approval window closed should get the evidence rather than a
        # refusal — a false "expired" on work that already happened is how an
        # operator comes to re-plan and reset a cohort a second time.
        existing = self.audit.find_receipt(digest)
        if existing is not None:
            return existing

        if plan.expires_at <= now:
            raise CredentialAuthorizationError(
                "the authorized plan has expired; re-plan against current "
                "credential versions rather than extending an approval"
            )

        # Phase 1 — lock and check EVERY target before mutating any of them.
        locked: list[tuple[CredentialResetTargetV1, CredentialSnapshot]] = []
        for target in plan.targets:
            snapshot = self.store.lock_for_update(target.subject)
            if snapshot is None:
                raise CredentialCohortDrift(
                    "a planned target no longer exists: "
                    f"{target.subject.credential_ref!r}"
                )
            if snapshot.credential_version != target.expected_credential_version:
                raise CredentialCohortDrift(
                    "a planned target changed after approval: "
                    f"{target.subject.credential_ref!r} expected version "
                    f"{target.expected_credential_version}, found "
                    f"{snapshot.credential_version}"
                )
            locked.append((target, snapshot))

        # Phase 2 — mutate. Any exception here propagates to the caller, whose
        # transaction rolls back every write made so far.
        outcomes: list[CredentialResetOutcomeV1] = []
        for target, snapshot in locked:
            subject = target.subject
            secret = self._generate(subject)
            try:
                next_version = snapshot.credential_version + 1
                self.store.write_hash(
                    subject,
                    password_hash=hash_password(secret),
                    credential_version=next_version,
                    reset_required=True,
                )
            finally:
                del secret
            self._revoke_sessions(
                subject,
                reason_code=plan.reason_code,
                credential_version=next_version,
                correlation_ref=digest.value,
            )
            intent_ref = self.recovery.emit(
                CredentialRecoveryIntentV1(
                    subject=subject,
                    reason_code=plan.reason_code,
                    credential_version=next_version,
                    requested_at=now,
                    correlation_ref=digest.value,
                )
            )
            if not intent_ref:
                raise CredentialEffectNotRecorded(
                    "no durable recovery intent was recorded for "
                    f"{subject.credential_ref!r}; the subject would be locked "
                    "out of a credential nobody knows"
                )
            self.audit.record_event(
                CredentialAuditEntryV1(
                    subject=subject,
                    action="credential.force_reset.applied",
                    reason_code=plan.reason_code,
                    credential_version=next_version,
                    occurred_at=now,
                    correlation_ref=digest.value,
                    detail=(
                        ("approval_decision_ref", authorization.approval_decision_ref),
                    ),
                )
            )
            outcomes.append(
                CredentialResetOutcomeV1(
                    subject=subject,
                    previous_credential_version=snapshot.credential_version,
                    credential_version=next_version,
                )
            )

        receipt = CredentialResetReceiptV1(
            plan_digest=digest,
            approval_decision_ref=authorization.approval_decision_ref,
            approval_policy_code=authorization.approval_policy_code,
            approval_policy_version=authorization.approval_policy_version,
            reason_code=plan.reason_code,
            applied_at=now,
            outcomes=tuple(outcomes),
        )
        self.audit.record_receipt(receipt)
        return receipt

    # ── internals ───────────────────────────────────────────────────────────

    def _generate(self, subject: CredentialSubjectRef) -> str:
        """New material from the injected source, checked against the product
        policy.

        A policy that refuses the generator's own output is a product defect and
        fails LOUDLY and deterministically here. Retrying until the policy
        happens to be satisfied would turn a misconfiguration into an
        intermittent one, which is strictly worse to diagnose.
        """
        secret = self.secret_source()
        if not isinstance(secret, str) or not secret:
            raise CredentialLifecycleError(
                "the injected secret source produced no material"
            )
        if self.policy is not None:
            self.policy.validate(subject, secret)
        return secret

    def _revoke_sessions(
        self,
        subject: CredentialSubjectRef,
        *,
        reason_code: str,
        credential_version: int,
        correlation_ref: str,
    ) -> None:
        intent = self.sessions.request_revocation(
            subject,
            reason_code=reason_code,
            credential_version=credential_version,
            correlation_ref=correlation_ref,
        )
        if intent is None:
            raise CredentialEffectNotRecorded(
                "no durable session-revocation intent was recorded for "
                f"{subject.credential_ref!r}; the previous credential's "
                "sessions would survive its replacement"
            )

    def __repr__(self) -> str:  # pragma: no cover - exercised via secret tests
        return (
            "CredentialLifecycle("
            f"store={type(self.store).__name__}, "
            f"sessions={type(self.sessions).__name__}, "
            f"recovery={type(self.recovery).__name__}, "
            f"audit={type(self.audit).__name__}, "
            f"policy={type(self.policy).__name__ if self.policy else None}, "
            f"secret_source={REDACTED!r})"
        )


__all__ = [
    "CREDENTIAL_LIFECYCLE_SCHEMA_VERSION",
    "REDACTED",
    "CredentialAuditEntryV1",
    "CredentialAuditPort",
    "CredentialAuthorizationError",
    "CredentialCohortDrift",
    "CredentialEffectNotRecorded",
    "CredentialLifecycle",
    "CredentialLifecycleError",
    "CredentialPlanError",
    "CredentialPolicyViolation",
    "CredentialProvisioningReceiptV1",
    "CredentialRecoveryAuthorizationV1",
    "CredentialRecoveryIntentV1",
    "CredentialResetAuthorizationV1",
    "CredentialResetCompletionReceiptV1",
    "CredentialResetOutcomeV1",
    "CredentialResetPlanDigestV1",
    "CredentialResetPlanV1",
    "CredentialResetReceiptV1",
    "CredentialResetTargetV1",
    "CredentialSessionRevocationIntentV1",
    "CredentialSnapshot",
    "CredentialStorePort",
    "CredentialSubjectRef",
    "CredentialVerificationResult",
    "CredentialVerificationVerdict",
    "PasswordPolicyPort",
    "RecoveryIntentPort",
    "SecretSource",
    "SessionRevocationPort",
    "default_secret_source",
]
