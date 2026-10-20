"""The human credential lifecycle: one owner, typed verdicts, no caller-supplied
password material.

This is the stateless engine for what a product does to a HUMAN password
credential — provision it, verify it, complete an individually authorized
reset, reset an account's complete current membership, and force-reset an
approved cohort. It holds no session, no ORM model, no
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
    """The state an authorization was granted against is not the state found.

    A cohort target changed, vanished, or was never there between planning and
    applying — and the whole plan is then refused, because a partially applied
    cohort is not a smaller cohort, it is an unrecorded one. The individual
    reset path raises the same error for the same reason when the subject a
    proven recovery authorization names has no credential: one subject or a
    hundred, the world moved under an approved decision and the correct answer
    is to refuse rather than to improvise.
    """


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


@dataclass(frozen=True, slots=True, order=True)
class CredentialAccountRef:
    """A product principal, independent of any one credential.

    The product must bind principal_ref to its tenant and principal together;
    the kernel treats that reference as opaque, as with CredentialSubjectRef.
    """

    application: str
    principal_kind: str
    principal_ref: str

    def __post_init__(self) -> None:
        _require_opaque(self.application, "application")
        _require_opaque(self.principal_kind, "principal kind")
        _require_opaque(self.principal_ref, "tenant-bound principal reference")


@dataclass(frozen=True, slots=True)
class CredentialAccountRecoveryAuthorizationV1:
    """A proven single-use principal proof for ALL its current credentials.

    No caller-selected subset or credential generation is authorized here.
    The account port must validate and consume the genuine product proof.
    """

    account: CredentialAccountRef
    recovery_ref: str
    proven_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.account, CredentialAccountRef):
            raise TypeError("account must be a CredentialAccountRef")
        _require_opaque(self.recovery_ref, "recovery reference")
        _require_aware(self.proven_at, "recovery proof timestamp")
        _require_aware(self.expires_at, "recovery expiry")
        if self.expires_at < self.proven_at:
            raise ValueError("account recovery expires before it was proven")


@dataclass(frozen=True, slots=True)
class CredentialAccountSessionRevocationCompletionV1:
    """Completed synchronous principal-session revocation, never an intent.

    Zero sessions is a valid completed result. The reference identifies the
    product's actual effect record in the caller's transaction.
    """

    account: CredentialAccountRef
    completion_ref: str
    completed_at: datetime
    revoked_session_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.account, CredentialAccountRef):
            raise TypeError("account must be a CredentialAccountRef")
        _require_opaque(self.completion_ref, "revocation completion reference")
        _require_aware(self.completed_at, "revocation completion timestamp")
        if (
            type(self.revoked_session_count) is not int
            or self.revoked_session_count < 0
        ):
            raise ValueError("revoked session count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class CredentialAccountResetCompletionReceiptV1:
    """Material-free evidence of a complete current account reset."""

    account: CredentialAccountRef
    recovery_ref: str
    completed_at: datetime
    outcomes: tuple[CredentialResetOutcomeV1, ...]
    revocation: CredentialAccountSessionRevocationCompletionV1
    schema_version: int = CREDENTIAL_LIFECYCLE_SCHEMA_VERSION


# ── Product ports ───────────────────────────────────────────────────────────


class CredentialAccountResetPort(Protocol):
    """Account-specific effects in the SAME caller-owned transaction.

    This additive method port does not change the lifecycle constructor's five
    existing ports. It must validate the actual tenant/principal/credential
    relationship, spend a genuine principal proof once, and lock the principal
    plus its COMPLETE current credential membership against additions/removals
    until transaction end. Returning a caller subset is a contract violation.
    Store writes remain explicit per member through CredentialStorePort;
    implementations must CAS from credential_version-1 to credential_version.
    No method may commit or hide credential fan-out behind a single write.
    """

    def consume(self, authorization: CredentialAccountRecoveryAuthorizationV1) -> bool:
        """Atomically validate/spend the actual principal proof; False if refused."""

    def lock_current_credentials(
        self, account: CredentialAccountRef
    ) -> tuple[CredentialSnapshot, ...]:
        """Principal and full membership lock, with current persisted snapshots."""

    def clear_failure_state(self, subject: CredentialSubjectRef) -> None:
        """Clear failed attempts and lockout for exactly this named credential."""

    def revoke_sessions(
        self,
        account: CredentialAccountRef,
        *,
        reason_code: str,
        correlation_ref: str,
        completed_at: datetime,
    ) -> CredentialAccountSessionRevocationCompletionV1:
        """Revoke all principal sessions synchronously and return completed facts."""


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


# ── Product-authorized supplied-password assignment ─────────────────────────


class CredentialAssignmentError(CredentialLifecycleError):
    """Assignment refused or incomplete; the caller must roll back all effects."""


_ASSIGNMENT_MAX_VERSION: Final[int] = (1 << 63) - 1


def _assignment_opaque(value: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError("assignment reference must be a non-empty string")


def _assignment_version(value: object) -> None:
    if type(value) is not int or not 0 <= value <= _ASSIGNMENT_MAX_VERSION:
        raise ValueError(
            "assignment version must be a nonnegative signed 64-bit integer"
        )


def _assignment_subject(value: CredentialSubjectRef) -> None:
    if type(value) is not CredentialSubjectRef:
        raise TypeError("assignment subject must be a CredentialSubjectRef")
    for reference in (
        value.application,
        value.principal_kind,
        value.principal_ref,
        value.credential_ref,
    ):
        _assignment_opaque(reference)


def _assignment_time(value: datetime) -> None:
    if type(value) is not datetime:
        raise TypeError("assignment timestamp must be a datetime")
    _require_aware(value, "assignment timestamp")


def _assignment_command_fields(
    target_subject: CredentialSubjectRef,
    tenant_ref: str,
    purpose_ref: str,
    intended_role_refs: tuple[str, ...],
    reason_code: str,
    correlation_ref: str,
) -> None:
    _assignment_subject(target_subject)
    for reference in (tenant_ref, purpose_ref, reason_code, correlation_ref):
        _assignment_opaque(reference)
    if type(intended_role_refs) is not tuple or not intended_role_refs:
        raise ValueError("assignment roles must be a non-empty sorted tuple")
    for reference in intended_role_refs:
        _assignment_opaque(reference)
    if tuple(sorted(intended_role_refs)) != intended_role_refs or len(
        set(intended_role_refs)
    ) != len(intended_role_refs):
        raise ValueError("assignment roles must be sorted and unique")


@dataclass(frozen=True, slots=True)
class CredentialCreateAbsentV1:
    """Create absent state; product authority resolves opaque tenant and roles."""

    target_subject: CredentialSubjectRef
    tenant_ref: str
    purpose_ref: str
    intended_role_refs: tuple[str, ...]
    reason_code: str
    correlation_ref: str

    def __post_init__(self) -> None:
        _assignment_command_fields(
            self.target_subject,
            self.tenant_ref,
            self.purpose_ref,
            self.intended_role_refs,
            self.reason_code,
            self.correlation_ref,
        )


@dataclass(frozen=True, slots=True)
class CredentialCreateAbsentFromProofV1:
    """Create an account's first credential from genuine product-validated proof.

    These opaque references are an observation, never authority by themselves.
    No roles, actor, proof timestamps or credential material belong in this DTO.
    """

    target_subject: CredentialSubjectRef
    tenant_ref: str
    purpose_ref: str
    proof_ref: str
    reason_code: str
    correlation_ref: str

    def __post_init__(self) -> None:
        _assignment_subject(self.target_subject)
        for reference in (
            self.tenant_ref,
            self.purpose_ref,
            self.proof_ref,
            self.reason_code,
            self.correlation_ref,
        ):
            _assignment_opaque(reference)


@dataclass(frozen=True, slots=True)
class CredentialReplaceSelectedV1:
    """CAS the selected prior subject/version, allowing an authorized Party change.

    Tenant membership cannot be inferred from opaque principal text. The installed
    authority and store resolve both Parties in the command's actual tenant.
    """

    target_subject: CredentialSubjectRef
    tenant_ref: str
    purpose_ref: str
    intended_role_refs: tuple[str, ...]
    reason_code: str
    correlation_ref: str
    expected_previous_subject: CredentialSubjectRef
    expected_credential_version: int

    def __post_init__(self) -> None:
        _assignment_command_fields(
            self.target_subject,
            self.tenant_ref,
            self.purpose_ref,
            self.intended_role_refs,
            self.reason_code,
            self.correlation_ref,
        )
        _assignment_subject(self.expected_previous_subject)
        _assignment_version(self.expected_credential_version)
        previous = self.expected_previous_subject
        target = self.target_subject
        if (
            previous.application != target.application
            or previous.principal_kind != target.principal_kind
            or previous.credential_ref != target.credential_ref
        ):
            raise ValueError("assignment prior and target credential envelopes differ")


CredentialAssignmentCommandV1 = (
    CredentialCreateAbsentV1
    | CredentialCreateAbsentFromProofV1
    | CredentialReplaceSelectedV1
)


def _assignment_command(command: CredentialAssignmentCommandV1) -> None:
    if type(command) not in (
        CredentialCreateAbsentV1,
        CredentialCreateAbsentFromProofV1,
        CredentialReplaceSelectedV1,
    ):
        raise TypeError("assignment command must be a closed assignment variant")
    command.__post_init__()


@dataclass(frozen=True, slots=True)
class CredentialAssignmentAuthorityEvidenceV1:
    """Material-free observation, NOT intrinsic proof of authority.

    Only an installed product port may issue this for the actual locked context.
    Its bound audit adapter must reject copies, stale/cross-root context and
    changed outcomes against private Session/transaction/command/actor state.
    """

    command: CredentialAssignmentCommandV1
    actor_kind: Literal["user", "service", "api_key", "system"]
    actor_ref: str
    authority_evidence_ref: str

    def __post_init__(self) -> None:
        _assignment_command(self.command)
        if type(self.actor_kind) is not str or self.actor_kind not in (
            "user",
            "service",
            "api_key",
            "system",
        ):
            raise ValueError("assignment actor kind is invalid")
        _assignment_opaque(self.actor_ref)
        _assignment_opaque(self.authority_evidence_ref)
        if (
            type(self.command) is CredentialCreateAbsentFromProofV1
            and self.authority_evidence_ref != self.command.proof_ref
        ):
            raise ValueError("assignment authority proof reference differs")


@dataclass(frozen=True, slots=True)
class CredentialAssignmentAuditEntryV1:
    """Current assignment audit input; no snapshots or material-bearing fields."""

    command: CredentialAssignmentCommandV1
    authority: CredentialAssignmentAuthorityEvidenceV1
    previous_credential_version: int | None
    credential_version: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        _assignment_command(self.command)
        if type(self.authority) is not CredentialAssignmentAuthorityEvidenceV1:
            raise TypeError("assignment authority evidence is invalid")
        self.authority.__post_init__()
        if self.authority.command is not self.command:
            raise ValueError("assignment authority command differs")
        _assignment_version(self.credential_version)
        if isinstance(
            self.command, CredentialCreateAbsentV1 | CredentialCreateAbsentFromProofV1
        ):
            if self.previous_credential_version is not None:
                raise ValueError("creation has no previous credential version")
        else:
            _assignment_version(self.previous_credential_version)
            if self.previous_credential_version is None:
                raise ValueError("replacement requires a previous version")
            if (
                self.previous_credential_version
                != self.command.expected_credential_version
                or self.credential_version != self.previous_credential_version + 1
            ):
                raise ValueError("assignment audit versions differ from selected CAS")
        _assignment_time(self.occurred_at)

    @property
    def action(self) -> Literal["credential.password.assigned"]:
        return "credential.password.assigned"


@dataclass(frozen=True, slots=True)
class CredentialAssignmentReceiptV1:
    """Material-free outcome; prior/target refs and correlation are in command."""

    command: CredentialAssignmentCommandV1
    authority: CredentialAssignmentAuthorityEvidenceV1
    previous_credential_version: int | None
    credential_version: int
    audit_ref: str
    occurred_at: datetime
    reset_required: Literal[False] = False
    schema_version: int = CREDENTIAL_LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        CredentialAssignmentAuditEntryV1(
            self.command,
            self.authority,
            self.previous_credential_version,
            self.credential_version,
            self.occurred_at,
        )
        _assignment_opaque(self.audit_ref)
        if self.reset_required is not False:
            raise ValueError("assigned credential must persist reset_required=False")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("assignment schema version is invalid")


class CredentialAssignmentAuthorityPort(Protocol):
    """Resolve actual authority/tenant/grants and hold real participating locks.

    Authority and audit are the same bound product adapter. No public DTO, URL,
    supplied password, caller boolean or actor ID establishes actual authority.

    Proof creation requires actual valid single-use proof consumption inside the
    caller's whole public savepoint BEFORE evidence issuance. Lock actual Party,
    required subtype/profile, token and complete credential-membership state in
    product order before reading clocks or checking inclusive expiry. Bind the
    exact command, consumed proof, genuine actor and current private Session/root
    to evidence; proof_ref equals authority_evidence_ref, never an actor identity.
    A verified bearer may authenticate the actual user subject; a target ID alone
    cannot. Reject unsupported variants rather than borrowing role-based checks.
    """

    def authorize_and_lock(
        self, command: CredentialAssignmentCommandV1
    ) -> CredentialAssignmentAuthorityEvidenceV1: ...


class CredentialAssignmentStorePort(Protocol):
    """Caller-transaction store; enforce actual tenant and selected Party/version CAS.

    All writers must participate in the real lock order. Return actual persisted
    state, preserving selected credential ID and unrelated state. Creation owns
    its initial version; replacement must advance the expected version exactly once.

    Proof creation requires absence of all credentials for the account, not
    merely an unused new credential UUID. Preserve actual product eligibility,
    Party/profile status,
    grants and unrelated state; no implicit activation, role or session changes.
    Its adapter supplies actual initial state/version rather than kernel defaults.
    """

    def lock_current(
        self, command: CredentialAssignmentCommandV1
    ) -> CredentialSnapshot | None: ...

    def create_absent(
        self,
        command: CredentialCreateAbsentV1 | CredentialCreateAbsentFromProofV1,
        *,
        password_hash: str,
        reset_required: Literal[False],
    ) -> CredentialSnapshot: ...

    def replace_selected(
        self,
        command: CredentialReplaceSelectedV1,
        *,
        password_hash: str,
        credential_version: int,
        reset_required: Literal[False],
    ) -> CredentialSnapshot: ...


class CredentialAssignmentAuditPort(Protocol):
    """Validate exact issued evidence against private bound authority/root context.

    Verify actual persisted outcome, derive actual actor, flush a current audit
    row, return its non-empty ID and clear context on completion/failure. Product
    assembly declares the consumed action. No-op/fabricated IDs are not completion.
    For proof creation verify exact consumed-proof evidence and command identity
    against the private current Session/root, never trust a copied DTO. Clear
    private context on every exit, including failures before this audit is called.
    """

    def record_assignment(self, entry: CredentialAssignmentAuditEntryV1) -> str: ...


def _assignment_snapshot(snapshot: CredentialSnapshot) -> None:
    if type(snapshot) is not CredentialSnapshot:
        raise TypeError("assignment store did not return a credential snapshot")
    _assignment_subject(snapshot.subject)
    _assignment_version(snapshot.credential_version)
    _assignment_opaque(snapshot.password_hash)
    if any(
        type(value) is not bool
        for value in (snapshot.is_active, snapshot.is_locked, snapshot.reset_required)
    ):
        raise ValueError("assignment snapshot state must be boolean")


class CredentialAuthenticatedAccountPasswordChangeError(CredentialLifecycleError):
    """Change incomplete; the caller must roll back the whole operation."""


class CredentialAuthenticatedAccountPasswordPolicyRefused(CredentialPolicyViolation):
    """Deferred private product confirmation/policy refusal, without material."""

    def __init__(
        self, code: Literal["confirmation_mismatch", "policy_rejected"]
    ) -> None:
        if type(code) is not str or code not in (
            "confirmation_mismatch",
            "policy_rejected",
        ):
            raise ValueError("invalid authenticated password policy refusal")
        self.code: Literal["confirmation_mismatch", "policy_rejected"] = code
        super().__init__(code)


class CredentialAuthenticatedAccountPasswordChangeRefused(
    CredentialAuthenticatedAccountPasswordChangeError
):
    """Closed expected refusal; fixed code is safe to map to product messages."""

    def __init__(
        self,
        code: Literal[
            "current_password_invalid", "confirmation_mismatch", "policy_rejected"
        ],
    ) -> None:
        if type(code) is not str or code not in (
            "current_password_invalid",
            "confirmation_mismatch",
            "policy_rejected",
        ):
            raise ValueError("invalid authenticated password change refusal")
        self.code = code
        super().__init__(code)


def _authenticated_change_account(account: CredentialAccountRef) -> None:
    if type(account) is not CredentialAccountRef:
        raise TypeError("authenticated change needs an exact account reference")
    for reference in (
        account.application,
        account.principal_kind,
        account.principal_ref,
    ):
        _assignment_opaque(reference)


def _authenticated_change_subject(
    account: CredentialAccountRef, subject: CredentialSubjectRef
) -> None:
    _assignment_subject(subject)
    if (subject.application, subject.principal_kind, subject.principal_ref) != (
        account.application,
        account.principal_kind,
        account.principal_ref,
    ):
        raise ValueError("authenticated change credential is outside account")


@dataclass(frozen=True, slots=True)
class CredentialAuthenticatedAccountPasswordChangeV1:
    """Change the authenticated account's complete credential membership."""

    account: CredentialAccountRef
    reason_code: str
    correlation_ref: str

    def __post_init__(self) -> None:
        _authenticated_change_account(self.account)
        _assignment_opaque(self.reason_code)
        _assignment_opaque(self.correlation_ref)


def _authenticated_change_command(
    command: CredentialAuthenticatedAccountPasswordChangeV1,
) -> None:
    if type(command) is not CredentialAuthenticatedAccountPasswordChangeV1:
        raise TypeError("authenticated password change command is a closed type")
    command.__post_init__()


@dataclass(frozen=True, slots=True)
class CredentialAuthenticatedAccountPasswordChangeAuthorityV1:
    """Observation only; exact issued instance requires private cookie/root binding.

    An actual self-cookie authenticates this user, independently of credential
    eligibility flags. session_ref is a material-free session ID, never a cookie.
    verification_subject is the actual unordered first credential selected by
    the product query, not a canonical alias, caller choice or any-match search.
    None represents no credential; it can only lead to a current-password refusal.
    """

    command: CredentialAuthenticatedAccountPasswordChangeV1
    actor_kind: Literal["user"]
    actor_ref: str
    session_ref: str
    verification_subject: CredentialSubjectRef | None

    def __post_init__(self) -> None:
        _authenticated_change_command(self.command)
        if type(self.actor_kind) is not str or self.actor_kind != "user":
            raise ValueError("authenticated change requires genuine user authority")
        _assignment_opaque(self.actor_ref)
        _assignment_opaque(self.session_ref)
        if self.verification_subject is not None:
            _authenticated_change_subject(
                self.command.account, self.verification_subject
            )


@dataclass(frozen=True, slots=True)
class CredentialAuthenticatedAccountPasswordChangeOutcomeV1:
    """A material-free named member CAS outcome."""

    subject: CredentialSubjectRef
    previous_credential_version: int
    credential_version: int

    def __post_init__(self) -> None:
        _assignment_subject(self.subject)
        _assignment_version(self.previous_credential_version)
        _assignment_version(self.credential_version)
        if self.credential_version != self.previous_credential_version + 1:
            raise ValueError("authenticated change outcome is not one CAS advance")


@dataclass(frozen=True, slots=True)
class CredentialAccountResetTokenInvalidationCompletionV1:
    """Completed invalidation of every unused reset token, including expired."""

    account: CredentialAccountRef
    completion_ref: str
    completed_at: datetime
    invalidated_token_count: int

    def __post_init__(self) -> None:
        _authenticated_change_account(self.account)
        _assignment_opaque(self.completion_ref)
        _assignment_time(self.completed_at)
        if (
            type(self.invalidated_token_count) is not int
            or self.invalidated_token_count < 0
        ):
            raise ValueError("invalidated reset token count must be nonnegative")


@dataclass(frozen=True, slots=True)
class CredentialAuthenticatedAccountPasswordChangeAuditEntryV1:
    """Bound complete account change facts, without hashes or passwords."""

    command: CredentialAuthenticatedAccountPasswordChangeV1
    authority: CredentialAuthenticatedAccountPasswordChangeAuthorityV1
    outcomes: tuple[CredentialAuthenticatedAccountPasswordChangeOutcomeV1, ...]
    revocation: CredentialAccountSessionRevocationCompletionV1
    invalidation: CredentialAccountResetTokenInvalidationCompletionV1
    occurred_at: datetime

    def __post_init__(self) -> None:
        _authenticated_change_command(self.command)
        if (
            type(self.authority)
            is not CredentialAuthenticatedAccountPasswordChangeAuthorityV1
        ):
            raise TypeError("invalid authenticated change authority")
        self.authority.__post_init__()
        if self.authority.command is not self.command:
            raise ValueError("authenticated change authority command differs")
        if type(self.outcomes) is not tuple or not self.outcomes:
            raise ValueError("authenticated change requires complete outcomes")
        seen: set[CredentialSubjectRef] = set()
        for outcome in self.outcomes:
            if (
                type(outcome)
                is not CredentialAuthenticatedAccountPasswordChangeOutcomeV1
            ):
                raise TypeError("invalid authenticated change outcome")
            outcome.__post_init__()
            _authenticated_change_subject(self.command.account, outcome.subject)
            if outcome.subject in seen:
                raise ValueError("duplicate authenticated change outcome")
            seen.add(outcome.subject)
        if self.authority.verification_subject not in seen:
            raise ValueError("authenticated change verification subject was omitted")
        _assignment_time(self.occurred_at)
        if type(self.revocation) is not CredentialAccountSessionRevocationCompletionV1:
            raise TypeError("authenticated change needs completed session revocation")
        self.revocation.__post_init__()
        _authenticated_change_account(self.revocation.account)
        _assignment_opaque(self.revocation.completion_ref)
        _assignment_time(self.revocation.completed_at)
        if (
            type(self.invalidation)
            is not CredentialAccountResetTokenInvalidationCompletionV1
        ):
            raise TypeError("authenticated change needs completed reset invalidation")
        self.invalidation.__post_init__()
        for completion in (self.revocation, self.invalidation):
            if (
                completion.account != self.command.account
                or completion.completed_at != self.occurred_at
            ):
                raise ValueError("authenticated change completion differs")

    @property
    def action(self) -> Literal["credential.password.changed"]:
        return "credential.password.changed"


@dataclass(frozen=True, slots=True)
class CredentialAuthenticatedAccountPasswordChangeReceiptV1:
    """Material-free receipt; public facts alone do not establish authority."""

    command: CredentialAuthenticatedAccountPasswordChangeV1
    authority: CredentialAuthenticatedAccountPasswordChangeAuthorityV1
    outcomes: tuple[CredentialAuthenticatedAccountPasswordChangeOutcomeV1, ...]
    revocation: CredentialAccountSessionRevocationCompletionV1
    invalidation: CredentialAccountResetTokenInvalidationCompletionV1
    occurred_at: datetime
    audit_ref: str
    schema_version: int = CREDENTIAL_LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        CredentialAuthenticatedAccountPasswordChangeAuditEntryV1(
            self.command,
            self.authority,
            self.outcomes,
            self.revocation,
            self.invalidation,
            self.occurred_at,
        )
        _assignment_opaque(self.audit_ref)
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("authenticated change receipt schema is invalid")


class CredentialAuthenticatedAccountPasswordChangePort(Protocol):
    """Private request-local authority/effects in one caller-owned public savepoint.

    Bind actual Session/root, exact command and issued evidence instance to the
    real user/cookie/session and full tenant-bound principal membership. Acquire
    Party, required subtype/profile, session and complete credential locks in the
    product order. Membership reads MUST NOT normalize aliases or mutate state.
    All account/credential/session/token writers participate in the same locks.
    Public DTOs, actor IDs and equal/copied evidence never establish authority.
    No method commits or rolls back. A caught failure requires rollback of the
    whole operation's public savepoint: aliases, credentials, policies, sessions,
    tokens and audit. The caller must prove recovery within the same root;
    otherwise it must roll back the caller root.

    This port and the lifecycle's self.store MUST share the same private actual
    Session/root, tenant and complete locked cohort context. Each self.store
    lock/write targets exactly the NAMED member. write_hash must atomically CAS
    that member's expected version n to n+1, refusing any mismatch; it must never
    hide fan-out to other credentials. The generic CredentialStorePort contract
    alone does not establish these stronger operation-specific guarantees.
    """

    def authorize_and_lock(
        self, command: CredentialAuthenticatedAccountPasswordChangeV1
    ) -> CredentialAuthenticatedAccountPasswordChangeAuthorityV1: ...

    def lock_current_credentials(
        self, account: CredentialAccountRef
    ) -> tuple[CredentialSnapshot, ...]:
        """Fresh complete membership, read-only; no alias synchronization.

        Empty membership is permitted only with verification_subject=None and
        leads to current_password_invalid. Nonempty membership with None, or
        empty membership with a selected subject, is invalid authority.
        """

    def revalidate_before_change(
        self,
        authority: CredentialAuthenticatedAccountPasswordChangeAuthorityV1,
        credentials: tuple[CredentialSnapshot, ...],
    ) -> bool:
        """Reject copied/cross-root evidence or any omitted/added/changed member.

        Sample fresh product clock AFTER every blocking acquisition, then verify
        actual self-cookie/session and Party/profile authority again. True only
        for the exact privately issued evidence and locked complete cohort.
        """

    def synchronize_account_aliases(
        self, authority: CredentialAuthenticatedAccountPasswordChangeAuthorityV1
    ) -> None:
        """After checks normalize actual account email/compatible alias only.

        Preserve all credential IDs, membership, unrelated aliases and statuses.
        """

    def clear_failure_state(self, subject: CredentialSubjectRef) -> None:
        """Clear failed attempts/lockout for exactly this member, preserving state."""

    def revoke_sessions(
        self,
        account: CredentialAccountRef,
        *,
        reason_code: str,
        correlation_ref: str,
        completed_at: datetime,
    ) -> CredentialAccountSessionRevocationCompletionV1:
        """Synchronously revoke every nonrevoked session, including expired ones."""

    def invalidate_outstanding_reset_tokens(
        self, account: CredentialAccountRef, *, completed_at: datetime
    ) -> CredentialAccountResetTokenInvalidationCompletionV1:
        """Consume every unused password-reset token, including expired ones."""

    def record_change(
        self, entry: CredentialAuthenticatedAccountPasswordChangeAuditEntryV1
    ) -> str:
        """Verify exact private evidence, full outcomes, failure state/completions.

        Check actual persisted state and record actual material-free user audit;
        return its ID. Do not require the deliberately revoked session to remain
        unrevoked. No other token, grant, status or recovery effect is implied.
        """

    def clear_context(self) -> None:
        """Clear private authority/root/material context on every exit; never raise."""


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
    assignment_authority: CredentialAssignmentAuthorityPort | None = None
    assignment_store: CredentialAssignmentStorePort | None = None
    assignment_audit: CredentialAssignmentAuditPort | None = None

    def assign_authorized_password(
        self, command: CredentialAssignmentCommandV1, secret: str
    ) -> CredentialAssignmentReceiptV1:
        """Assign supplied material through genuine installed product authority.

        Every error is material-masked. This method owns no transaction: callers
        MUST roll back ALL account/role/credential/audit effects after any failure,
        including errors caught for a normal response. Runtime authority, tenant
        semantics and lock participation remain the installed product's duty.
        """
        try:
            _assignment_command(command)
            if type(secret) is not str:
                raise TypeError("assignment material must be a string")
            authority_port = self.assignment_authority
            store = self.assignment_store
            audit = self.assignment_audit
            policy = self.policy
            if (
                authority_port is None
                or store is None
                or audit is None
                or policy is None
            ):
                raise ValueError("assignment requires all installed ports and policy")

            authority = authority_port.authorize_and_lock(command)
            if type(authority) is not CredentialAssignmentAuthorityEvidenceV1:
                raise TypeError("assignment authority returned invalid evidence")
            authority.__post_init__()
            if authority.command is not command:
                raise ValueError("assignment authority returned a different command")

            current = store.lock_current(command)
            previous_version: int | None = None
            next_version: int | None = None
            if isinstance(
                command, CredentialCreateAbsentV1 | CredentialCreateAbsentFromProofV1
            ):
                if current is not None:
                    raise ValueError("assignment expected absent state")
            else:
                if current is None:
                    raise ValueError("assignment selected credential is absent")
                _assignment_snapshot(current)
                if (
                    current.subject != command.expected_previous_subject
                    or current.credential_version != command.expected_credential_version
                ):
                    raise ValueError("assignment selected credential changed")
                previous_version = current.credential_version
                if previous_version == _ASSIGNMENT_MAX_VERSION:
                    raise ValueError("assignment version exhausted")
                next_version = previous_version + 1

            # Only the supplied settings-backed product policy decides strength.
            policy.validate(command.target_subject, secret)
            password_hash = hash_password(secret)
            _assignment_opaque(password_hash)
            if isinstance(
                command, CredentialCreateAbsentV1 | CredentialCreateAbsentFromProofV1
            ):
                persisted = store.create_absent(
                    command,
                    password_hash=password_hash,
                    reset_required=False,
                )
            else:
                # Replacement always has a checked prior version above.
                if next_version is None:
                    raise ValueError("assignment replacement version is missing")
                persisted = store.replace_selected(
                    command,
                    password_hash=password_hash,
                    credential_version=next_version,
                    reset_required=False,
                )
            _assignment_snapshot(persisted)
            if (
                persisted.subject != command.target_subject
                or persisted.password_hash != password_hash
                or persisted.reset_required is not False
                or (
                    next_version is not None
                    and persisted.credential_version != next_version
                )
            ):
                raise ValueError("assignment persisted outcome differs")

            now = self.clock()
            entry = CredentialAssignmentAuditEntryV1(
                command,
                authority,
                previous_version,
                persisted.credential_version,
                now,
            )
            audit_ref = audit.record_assignment(entry)
            _assignment_opaque(audit_ref)
            return CredentialAssignmentReceiptV1(
                command,
                authority,
                previous_version,
                persisted.credential_version,
                audit_ref,
                now,
            )
        except Exception:
            raise CredentialAssignmentError(
                "credential assignment failed; rollback required"
            ) from None
        finally:
            del secret

    def change_authenticated_account_password(
        self,
        command: CredentialAuthenticatedAccountPasswordChangeV1,
        current_secret: str,
        new_secret: str,
        *,
        account: CredentialAuthenticatedAccountPasswordChangePort,
    ) -> CredentialAuthenticatedAccountPasswordChangeReceiptV1:
        """Verify the actual selected old hash, then set one NEW hash on all aliases.

        Genuine cookie authority is separate from credential eligibility: inactive,
        locked and reset-required snapshots do not deny a valid old comparison.
        Never call login verify (which may rehash old material). Deferred product
        confirmation and configured policy run only after the old comparison.
        Every effect remains inside the caller's whole rollback boundary.
        """
        refusal: (
            Literal[
                "current_password_invalid", "confirmation_mismatch", "policy_rejected"
            ]
            | None
        ) = None
        try:
            _authenticated_change_command(command)
            if type(current_secret) is not str or type(new_secret) is not str:
                raise TypeError("authenticated change material must be strings")
            if self.policy is None:
                raise ValueError("authenticated change requires installed policy")
            authority = account.authorize_and_lock(command)
            if (
                type(authority)
                is not CredentialAuthenticatedAccountPasswordChangeAuthorityV1
            ):
                raise TypeError("invalid authenticated change authority")
            authority.__post_init__()
            if authority.command is not command:
                raise ValueError("authenticated change authority command differs")
            observed = account.lock_current_credentials(command.account)
            if type(observed) is not tuple:
                raise TypeError("authenticated change membership must be a tuple")
            if not observed:
                if authority.verification_subject is not None:
                    raise ValueError(
                        "empty cohort has a selected verification credential"
                    )
                refusal = "current_password_invalid"
                raise ValueError("no current credential")
            seen: set[CredentialSubjectRef] = set()
            locked: list[CredentialSnapshot] = []
            selected: CredentialSnapshot | None = None
            for member in observed:
                _assignment_snapshot(member)
                _authenticated_change_subject(command.account, member.subject)
                if member.subject in seen:
                    raise ValueError("duplicate authenticated change membership")
                seen.add(member.subject)
                current = self.store.lock_for_update(member.subject)
                if current is None:
                    raise ValueError("authenticated change credential disappeared")
                _assignment_snapshot(current)
                if (
                    current != member
                    or current.credential_version == _ASSIGNMENT_MAX_VERSION
                ):
                    raise ValueError(
                        "authenticated change credential drift or exhausted version"
                    )
                locked.append(current)
                if current.subject == authority.verification_subject:
                    selected = current
            if selected is None:
                raise ValueError("authenticated change selected credential omitted")
            if verify_password(current_secret, selected.password_hash) is not True:
                refusal = "current_password_invalid"
                raise ValueError("current comparison refused")
            try:
                self.policy.validate(selected.subject, new_secret)
            except CredentialAuthenticatedAccountPasswordPolicyRefused as rejected:
                if (
                    type(rejected)
                    is not CredentialAuthenticatedAccountPasswordPolicyRefused
                ):
                    raise TypeError(
                        "invalid authenticated change policy refusal"
                    ) from None
                if type(rejected.code) is not str or rejected.code not in (
                    "confirmation_mismatch",
                    "policy_rejected",
                ):
                    raise ValueError(
                        "invalid authenticated change policy code"
                    ) from None
                refusal = rejected.code
                raise ValueError("new material refused") from None
            # Complete cohort and actual cookie authority are checked after ALL
            # blocking locks, before even normalization or a new hash occurs.
            if account.revalidate_before_change(authority, tuple(locked)) is not True:
                raise ValueError("authenticated change private authority/cohort drift")
            now = self.clock()
            _assignment_time(now)
            account.synchronize_account_aliases(authority)
            synchronized = account.lock_current_credentials(command.account)
            if type(synchronized) is not tuple or len(synchronized) != len(locked):
                raise ValueError("alias synchronization changed credential membership")
            for member in synchronized:
                _assignment_snapshot(member)
            if {member.subject: member for member in synchronized} != {
                member.subject: member for member in locked
            }:
                raise ValueError(
                    "alias synchronization changed credential cohort/state"
                )
            password_hash = hash_password(new_secret)
            _assignment_opaque(password_hash)
            outcomes: list[CredentialAuthenticatedAccountPasswordChangeOutcomeV1] = []
            for current in locked:
                next_version = current.credential_version + 1
                self.store.write_hash(
                    current.subject,
                    password_hash=password_hash,
                    credential_version=next_version,
                    reset_required=False,
                )
                account.clear_failure_state(current.subject)
                persisted = self.store.lock_for_update(current.subject)
                if persisted is None:
                    raise ValueError(
                        "authenticated change persisted credential missing"
                    )
                _assignment_snapshot(persisted)
                if (
                    persisted.subject != current.subject
                    or persisted.password_hash != password_hash
                    or persisted.credential_version != next_version
                    or persisted.reset_required is not False
                    or persisted.is_locked is not False
                    or persisted.is_active is not current.is_active
                ):
                    raise ValueError("authenticated change persisted outcome differs")
                outcomes.append(
                    CredentialAuthenticatedAccountPasswordChangeOutcomeV1(
                        current.subject, current.credential_version, next_version
                    )
                )
            revocation = account.revoke_sessions(
                command.account,
                reason_code=command.reason_code,
                correlation_ref=command.correlation_ref,
                completed_at=now,
            )
            invalidation = account.invalidate_outstanding_reset_tokens(
                command.account, completed_at=now
            )
            entry = CredentialAuthenticatedAccountPasswordChangeAuditEntryV1(
                command, authority, tuple(outcomes), revocation, invalidation, now
            )
            audit_ref = account.record_change(entry)
            _assignment_opaque(audit_ref)
            return CredentialAuthenticatedAccountPasswordChangeReceiptV1(
                command,
                authority,
                entry.outcomes,
                revocation,
                invalidation,
                now,
                audit_ref,
            )
        except Exception:
            if refusal is not None:
                raise CredentialAuthenticatedAccountPasswordChangeRefused(
                    refusal
                ) from None
            raise CredentialAuthenticatedAccountPasswordChangeError(
                "authenticated password change failed; rollback required"
            ) from None
        finally:
            del current_secret, new_secret
            try:
                account.clear_context()
            except Exception:
                raise CredentialAuthenticatedAccountPasswordChangeError(
                    "authenticated password change cleanup failed; rollback required"
                ) from None

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

    # ── complete current account reset ──────────────────────────────────────

    def complete_account_reset(
        self,
        authorization: CredentialAccountRecoveryAuthorizationV1,
        secret: str,
        *,
        account: CredentialAccountResetPort,
    ) -> CredentialAccountResetCompletionReceiptV1:
        """Spend one principal proof and set one chosen hash on ALL credentials.

        The account port owns genuine proof validation and complete membership
        locking. Fresh store locks and every version are checked here before
        policy validation, one hash, or any credential write. Sessions must be
        revoked synchronously, once for the principal, rather than merely
        queued. Any failure requires the caller to roll back proof and effects.
        Product errors are masked because a port has seen caller material.
        """
        password_hash: str | None = None
        try:
            if type(authorization) is not CredentialAccountRecoveryAuthorizationV1:
                raise CredentialAuthorizationError("account proof is required")
            now = self.clock()
            _require_aware(now, "account reset timestamp")
            if authorization.expires_at < now or authorization.proven_at > now:
                raise CredentialAuthorizationError(
                    "account proof is outside its window"
                )
            if self.policy is None:
                raise CredentialPolicyViolation(
                    "account reset needs an installed policy"
                )
            if account.consume(authorization) is not True:
                raise CredentialAuthorizationError("account proof was refused or spent")

            principal = authorization.account
            observed = account.lock_current_credentials(principal)
            if type(observed) is not tuple or not observed:
                raise CredentialCohortDrift("account has no complete credential cohort")
            seen: set[CredentialSubjectRef] = set()
            locked: list[CredentialSnapshot] = []
            for member in observed:
                if not isinstance(member, CredentialSnapshot):
                    raise CredentialCohortDrift("account membership is not a snapshot")
                subject = member.subject
                if (
                    not isinstance(subject, CredentialSubjectRef)
                    or subject.application != principal.application
                    or subject.principal_kind != principal.principal_kind
                    or subject.principal_ref != principal.principal_ref
                    or subject in seen
                ):
                    raise CredentialCohortDrift(
                        "account membership is not unique and bound"
                    )
                seen.add(subject)
                current = self.store.lock_for_update(subject)
                if (
                    not isinstance(current, CredentialSnapshot)
                    or current.subject != subject
                    or type(member.credential_version) is not int
                    or type(current.credential_version) is not int
                    or current.credential_version != member.credential_version
                    or not 0 <= current.credential_version < (2**63 - 1)
                ):
                    raise CredentialCohortDrift("account credential generation drifted")
                locked.append(current)

            # Policy belongs to each real subject, never an invented credential
            # used to approximate an account-level check.
            for current in locked:
                self.policy.validate(current.subject, secret)
            password_hash = hash_password(secret)
            outcomes: list[CredentialResetOutcomeV1] = []
            for current in locked:
                next_version = current.credential_version + 1
                self.store.write_hash(
                    current.subject,
                    password_hash=password_hash,
                    credential_version=next_version,
                    reset_required=False,
                )
                account.clear_failure_state(current.subject)
                outcomes.append(
                    CredentialResetOutcomeV1(
                        subject=current.subject,
                        previous_credential_version=current.credential_version,
                        credential_version=next_version,
                        reset_required=False,
                        sessions_revoked=True,
                    )
                )

            reason_code = "credential.reset.completed"
            revocation = account.revoke_sessions(
                principal,
                reason_code=reason_code,
                correlation_ref=authorization.recovery_ref,
                completed_at=now,
            )
            if (
                type(revocation) is not CredentialAccountSessionRevocationCompletionV1
                or revocation.account != principal
                or revocation.completed_at != now
            ):
                raise CredentialEffectNotRecorded("account sessions were not revoked")
            for outcome in outcomes:
                self.audit.record_event(
                    CredentialAuditEntryV1(
                        subject=outcome.subject,
                        action="credential.reset.completed",
                        reason_code=reason_code,
                        credential_version=outcome.credential_version,
                        occurred_at=now,
                        correlation_ref=authorization.recovery_ref,
                    )
                )
            return CredentialAccountResetCompletionReceiptV1(
                account=principal,
                recovery_ref=authorization.recovery_ref,
                completed_at=now,
                outcomes=tuple(outcomes),
                revocation=revocation,
            )
        except CredentialAuthorizationError:
            raise CredentialAuthorizationError(
                "account reset authorization refused"
            ) from None
        except CredentialCohortDrift:
            raise CredentialCohortDrift(
                "account reset credential cohort refused"
            ) from None
        except CredentialPolicyViolation:
            raise CredentialPolicyViolation(
                "account reset password policy refused"
            ) from None
        except CredentialEffectNotRecorded:
            raise CredentialEffectNotRecorded(
                "account reset effect was not completed"
            ) from None
        except Exception:
            raise CredentialLifecycleError(
                "account reset failed; caller must roll back"
            ) from None
        finally:
            del password_hash
            del secret

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
    "CredentialAuthenticatedAccountPasswordChangeError",
    "CredentialAuthenticatedAccountPasswordPolicyRefused",
    "CredentialAuthenticatedAccountPasswordChangeRefused",
    "CredentialAuthenticatedAccountPasswordChangeV1",
    "CredentialAuthenticatedAccountPasswordChangeAuthorityV1",
    "CredentialAuthenticatedAccountPasswordChangeOutcomeV1",
    "CredentialAccountResetTokenInvalidationCompletionV1",
    "CredentialAuthenticatedAccountPasswordChangeAuditEntryV1",
    "CredentialAuthenticatedAccountPasswordChangeReceiptV1",
    "CredentialAuthenticatedAccountPasswordChangePort",
    "CredentialAssignmentAuditEntryV1",
    "CredentialAssignmentAuditPort",
    "CredentialAssignmentAuthorityEvidenceV1",
    "CredentialAssignmentAuthorityPort",
    "CredentialAssignmentCommandV1",
    "CredentialAssignmentError",
    "CredentialAssignmentReceiptV1",
    "CredentialAssignmentStorePort",
    "CredentialCreateAbsentFromProofV1",
    "CredentialCreateAbsentV1",
    "CredentialReplaceSelectedV1",
    "CredentialAccountRef",
    "CredentialAccountRecoveryAuthorizationV1",
    "CredentialAccountResetCompletionReceiptV1",
    "CredentialAccountResetPort",
    "CredentialAccountSessionRevocationCompletionV1",
]
