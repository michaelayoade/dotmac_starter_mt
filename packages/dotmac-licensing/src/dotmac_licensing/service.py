"""Issuing, transitioning and revoking licences — the issuer half of WS8.

Ported product-first from `dotmac_vendor_control_plane:src/vendor_cp/licensing/`
(3,689 LOC across thirteen modules, migrations `v006` to `v011`) under ADR-0033 § 2,
with the delivery half deliberately left behind and three couplings cut at the
ports in `ports.py`.

## The single most valuable line in the source, kept

Every envelope this module produces is round-tripped through the **pinned
kernel's own `verify_licence`** before the issuance is recorded, and a failure is
fatal. The kernel is the receiver; if the receiver would reject the document, the
issuer must not record it. That check is what keeps two halves of one protocol
from drifting, and it costs one verification per issuance to guarantee something
no amount of shared test data can.

ADR-0006 **D2** makes kernel WS8 the sole target licence protocol and **D4**
makes the `dotmac-*` schema ids permanent. This module issues what the kernel
already verifies; it introduces no second format and reimplements no verifier.

## What is NOT here, and where it went

The source's `transport.py` (616 LOC), `delivery_models.py` (283) and the
delivery half of `projection.py` own connection refs, transport names, attempt
counters, retry outcomes and error codes. **All of that is the Integrator's**
(ADR-0024, hard rule 28): a product declares a provider-neutral domain port and
Integrator owns transport evidence. This module ends at a signed envelope and
resumes at an acknowledgement; the wire between them is not its concern.

The `credentials.py`/`admission.py` deployment-identity half went to
`dotmac-deployment-control` (ADR-0033 § 3), not here. A licence names a
deployment; it does not enrol one.

## Transaction authority (hard rule 8)

Receives a `Session`; only `add` and `flush`. Never commits, never rolls back,
never constructs a session.

## At-most-once (hard rule 23, ADR-0014)

Every command that changes state goes through `process_once_platform`. Issuance
is additionally idempotent on `allocation_ref` at the DATABASE level: one issued
version per staged allocation, enforced by a unique constraint rather than only
by a check, because the service cannot police a path that never calls it.

## Signing material

Named, never held. `LicenceSigner` is a port; this package ships no
implementation, reads no file, and consults no environment variable. See
`ports.py` for why an `ephemeral` default in a *library* is a real hazard even
though it was correct in the source *product*.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from dotmac_kernel.audit import write_platform_audit_event
from dotmac_kernel.licensing import (
    ENVELOPE_SCHEMA,
    LICENCE_SCHEMA,
    REVOCATION_SCHEMA,
    KeyStatus,
    LicenceError,
    LicenceKey,
    LicenceKeyRing,
    payload_digest,
    verify_licence,
    verify_revocation_list,
)
from dotmac_kernel.messaging import enqueue_platform_event, process_once_platform
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_licensing import facts
from dotmac_licensing.models import (
    TERMINAL_ISSUANCE_STATUSES,
    AcknowledgementOutcome,
    IssuanceStatus,
    Licence,
    LicenceAcknowledgement,
    LicenceIssuance,
    Revocation,
    RevocationList,
    SigningKey,
    SigningKeyStatus,
)
from dotmac_licensing.ports import (
    AcknowledgementRefusedError,
    EmptyGrantError,
    ExpectedStateError,
    InstallationReport,
    LicenceSigner,
    LicensableGrant,
    RevocationSupersessionError,
    TransitionRefusedError,
    UnverifiableIssuanceError,
    require_usable_signers,
)

#: The audit actions this module declares and writes. Three, not one, and the
#: split is by WHO acted rather than by verb: the issuer signed something, the
#: issuer changed a licence's standing, or a remote party reported. An operator
#: reading an audit trail is answering "did we do this or did they?", and a
#: single code would make that question unanswerable without opening every
#: detail blob.
AUDIT_ACTION_ISSUED: str = "licence.issued"
AUDIT_ACTION_TRANSITIONED: str = "licence.transitioned"
AUDIT_ACTION_ACKNOWLEDGED: str = "licence.acknowledged"

#: Idempotency scopes name the OPERATION, never an HTTP route (ADR-0014).
SCOPE_ISSUE = "licence.issue"
SCOPE_ACTIVATE = "licence.activate"
SCOPE_SUSPEND = "licence.suspend"
SCOPE_REINSTATE = "licence.reinstate"
SCOPE_REVOKE = "licence.revoke"
SCOPE_EXPIRE = "licence.expire"
SCOPE_ACKNOWLEDGE = "licence.acknowledge"
SCOPE_PUBLISH_REVOCATIONS = "licence.publish_revocation_list"

_ENTITY_ISSUANCE = "licence_issuance"
_ENTITY_LICENCE = "licence"

#: The issuer name written into every payload. A caller may override it per
#: command; this is the default a single-issuer deployment never has to think
#: about.
DEFAULT_ISSUER = "dotmac"


# ── Commands ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IssueCommand:
    """Sign and freeze the next licence version for an allocated grant."""

    command_id: str
    grant: LicensableGrant
    issuer: str = DEFAULT_ISSUER
    actor_ref: str | None = None


@dataclass(frozen=True, slots=True)
class IssuanceTransitionCommand:
    """A guarded transition on one issued version.

    `expected_status` / `expected_version` are the optimistic-concurrency pair.
    `expected_version=None` opts a caller with no prior read out of the version
    check while `expected_status` still applies — opting out of one is never
    opting out of the guard.
    """

    command_id: str
    issuance_id: UUID
    expected_status: str | None = None
    expected_version: int | None = None
    reason: str | None = None
    actor_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RevokeCommand:
    """Revoke a whole LINEAGE. Permanent, by contract.

    Deliberately keyed on the licence rather than an issuance: revocation is by
    `licence_id` in the wire protocol, so revoking "a version" is not a thing a
    receiver can act on. Every issued version of the lineage becomes `revoked`.
    """

    command_id: str
    licence_id: UUID
    reason: str
    actor_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AcknowledgeCommand:
    """Record what a deployment reported doing with a licence."""

    command_id: str
    report: InstallationReport
    actor_ref: str | None = None


# ── Key registry ────────────────────────────────────────────────────────────


def register_signing_key(
    db: Session,
    *,
    key_id: str,
    public_key_b64: str,
    status: SigningKeyStatus = SigningKeyStatus.ACTIVE,
) -> SigningKey:
    """Record a signing key's PUBLIC material, idempotent on `key_id`.

    This registry is what the distributed verification keyring is built from. It
    never holds private material — the column does not exist.
    """
    existing = db.execute(
        select(SigningKey).where(SigningKey.key_id == key_id)
    ).scalar_one_or_none()
    if existing is not None:
        existing.public_key_b64 = public_key_b64
        existing.status = status.value
        db.flush()
        return existing
    row = SigningKey(key_id=key_id, public_key_b64=public_key_b64, status=status.value)
    db.add(row)
    db.flush()
    return row


def set_key_status(db: Session, *, key_id: str, status: SigningKeyStatus) -> None:
    """Rotate a key's state.

    `active` → `retired` keeps the installed base verifying while the fleet
    updates. `active` → `revoked` is for compromise: nothing that key signed
    verifies any more, and every affected lineage must be re-issued at a higher
    version. This module changes the registry; finding and re-issuing the
    affected lineages is the assembly's operation, driven from `issuances_by_key`.
    """
    row = db.execute(
        select(SigningKey).where(SigningKey.key_id == key_id)
    ).scalar_one_or_none()
    if row is None:
        raise TransitionRefusedError(f"signing key {key_id!r} is not registered")
    row.status = status.value
    db.flush()


def build_keyring(db: Session) -> LicenceKeyRing:
    """The verification keyring as distributed to deployments.

    Every registered key with its rotation status, public material only. This is
    also what the round-trip check below verifies against, which is why the
    public half must be registered BEFORE signing: a keyring that did not yet
    know the key would reject the issuer's own fresh document.
    """
    rows = db.execute(select(SigningKey)).scalars().all()
    return LicenceKeyRing(
        [
            LicenceKey(
                key_id=row.key_id,
                public_key_b64=row.public_key_b64,
                status=KeyStatus(row.status),
            )
            for row in rows
        ]
    )


def issuances_by_key(db: Session, key_id: str) -> tuple[facts.IssuanceView, ...]:
    """Every issuance signed by one key, for the post-revocation re-issue sweep.

    A read, not a command. Revoking a key is a decision; deciding which lineages
    to re-issue and in what order is the operator's, and this returns the list
    they need to make it.
    """
    rows = (
        db.execute(
            select(LicenceIssuance)
            .where(LicenceIssuance.key_id == key_id)
            .order_by(LicenceIssuance.created_at)
        )
        .scalars()
        .all()
    )
    return tuple(_issuance_view(row) for row in rows)


# ── Internals ───────────────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _load_issuance(db: Session, issuance_id: UUID) -> LicenceIssuance:
    row = db.get(LicenceIssuance, issuance_id)
    if row is None:
        raise TransitionRefusedError(f"issuance {issuance_id} not found")
    return row


def _require_expected(
    row: LicenceIssuance, *, expected_status: str | None, expected_version: int | None
) -> None:
    status_ok = expected_status is None or row.status == expected_status
    version_ok = expected_version is None or row.record_version == expected_version
    if not (status_ok and version_ok):
        raise ExpectedStateError(
            str(row.id),
            expected_status=expected_status,
            actual_status=row.status,
            expected_version=expected_version,
            actual_version=row.record_version,
        )


def _require_status(row: LicenceIssuance, allowed: frozenset[str]) -> None:
    if row.status not in allowed:
        raise TransitionRefusedError(
            f"issuance {row.id} is {row.status!r}; this transition requires one "
            f"of {sorted(allowed)}"
        )


def _is_revoked(db: Session, licence_id: UUID) -> bool:
    """Read the revocation FACT from its own table.

    Deliberately a query rather than a call into a revocation service: the
    dependency runs one way (revocation depends on issuance), and a service call
    the other way would make the two mutually recursive.
    """
    return (
        db.execute(
            select(Revocation.id).where(Revocation.licence_id == licence_id)
        ).scalar_one_or_none()
        is not None
    )


def _lineage(db: Session, *, subject_ref: str, product_code: str) -> Licence:
    """Resolve the lineage to issue into, minting a new generation if needed.

    Reuses the current generation unless it has been REVOKED. Continuing to
    issue into a revoked lineage produces documents every deployment must
    refuse — the recovery path is a new generation, which is the only reason
    `generation` exists.
    """
    current = db.execute(
        select(Licence)
        .where(
            Licence.subject_ref == subject_ref,
            Licence.product_code == product_code,
        )
        .order_by(Licence.generation.desc())
        .limit(1)
    ).scalar_one_or_none()
    if current is not None and not _is_revoked(db, current.id):
        return current
    row = Licence(
        subject_ref=subject_ref,
        product_code=product_code,
        generation=(current.generation + 1) if current is not None else 1,
    )
    db.add(row)
    db.flush()
    return row


def _next_version(db: Session, licence_id: UUID) -> int:
    highest = db.execute(
        select(func.max(LicenceIssuance.version)).where(
            LicenceIssuance.licence_id == licence_id
        )
    ).scalar()
    return int(highest or 0) + 1


def _build_payload(
    *,
    licence: Licence,
    version: int,
    grant: LicensableGrant,
    issuer: str,
    issued_at: datetime,
) -> bytes:
    """The `dotmac-licence/1` payload, serialised ONCE.

    These exact bytes are what gets signed, digested and shipped. Serialising
    twice — once to sign, once to store — is how a digest and a signature come to
    describe different documents, and the failure only shows up at a receiver.
    """
    subject: dict[str, object] = {"customer": licence.subject_ref}
    # Present only when the contract binds the licence. An ABSENT key is a
    # portable licence; a null one would be a bound licence with no target.
    if grant.deployment_ref is not None:
        subject["deployment_id"] = grant.deployment_ref
    document: dict[str, object] = {
        "schema": LICENCE_SCHEMA,
        "licence_id": str(licence.id),
        "licence_version": version,
        "issuer": issuer,
        "product": licence.product_code,
        "edition": grant.edition,
        "subject": subject,
        "capabilities": [
            {"code": capability.code, "limits": dict(capability.limits)}
            for capability in grant.capabilities
        ],
        "issued_at": issued_at.isoformat(),
        "not_before": grant.valid_from.isoformat() if grant.valid_from else None,
        "expires_at": grant.valid_until.isoformat() if grant.valid_until else None,
        "grace_days": grant.grace_days,
        "constraints": dict(grant.constraints),
    }
    return json.dumps(document).encode()


def _envelope(payload: bytes, signers: Sequence[LicenceSigner]) -> dict[str, object]:
    """One envelope, one signature per signer.

    More than one signer is a ROTATION OVERLAP: a deployment holding either
    keyring verifies the same document, which is what makes rotation
    non-breaking.
    """
    return {
        "schema": ENVELOPE_SCHEMA,
        "payload_b64": _b64url(payload),
        "signatures": [
            {
                "key_id": signer.key_id,
                "algorithm": "ed25519",
                "signature_b64": _b64url(signer.sign(payload)),
            }
            for signer in signers
        ],
    }


def _issuance_view(row: LicenceIssuance) -> facts.IssuanceView:
    return facts.IssuanceView(
        id=row.id,
        licence_id=row.licence_id,
        version=row.version,
        status=row.status,
        digest=row.digest,
        key_id=row.key_id,
        agreement_ref=row.agreement_ref,
        allocation_ref=row.allocation_ref,
        record_version=row.record_version,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        grace_days=row.grace_days,
        deployment_ref=row.deployment_ref,
        activated_at=row.activated_at,
        replaced_by_version=row.replaced_by_version,
        envelope=dict(row.envelope),
    )


def _audit_and_emit(
    session: Session,
    *,
    action: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor_ref: str | None,
    details: Mapping[str, Any],
) -> None:
    """The atomic consequence: a platform audit record AND an outbox fact.

    Both in the caller's transaction, or neither. A state change without the
    fact leaves every consumer permanently unaware; a fact without the audit
    leaves an operator unable to say who caused it.

    `actor_ref` is a string here and the kernel wants a `UUID | None`. It is
    parsed rather than cast: an actor reference that is not a platform admin id
    is recorded in the details and the audit actor is left null, which is honest
    about who the kernel's audit trail can actually attribute to.
    """
    actor_admin_id: UUID | None = None
    payload_details = dict(details)
    if actor_ref:
        try:
            actor_admin_id = UUID(actor_ref)
        except ValueError:
            payload_details["actor_ref"] = actor_ref
    write_platform_audit_event(
        session,
        actor_admin_id=actor_admin_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=payload_details,
    )
    enqueue_platform_event(
        session,
        event_type=event_type,
        payload={**payload_details, "id": entity_id},
        correlation_id=entity_id,
    )


def _advance(row: LicenceIssuance, to: IssuanceStatus) -> str:
    previous = row.status
    row.status = to.value
    row.record_version += 1
    return previous


# ── Issuance ────────────────────────────────────────────────────────────────


def issue_licence(
    db: Session,
    command: IssueCommand,
    *,
    signers: Sequence[LicenceSigner],
    now: datetime | None = None,
) -> facts.IssuanceView:
    """Sign and freeze the next licence version for an allocated grant.

    The ordering is the invariant, and it is not the obvious one:

    1. Validate the grant and the signer set — **before** any row exists.
    2. Return an existing issuance for this `allocation_ref` unchanged. An
       allocation already issued is immutable history, and re-signing it would
       mint a second version authorising the same entitlement twice.
    3. Register every signer's PUBLIC half, so the keyring can verify what is
       about to be signed — including, during an overlap, the second key.
    4. Resolve the lineage (minting a generation if the current one is revoked)
       and take the next version.
    5. Build the payload ONCE, sign it, digest those exact bytes.
    6. **Round-trip through the pinned kernel verifier.** Fail fatally if it
       would be rejected.
    7. Only now write the issuance, the audit record and the fact.

    Step 6 before step 7 is the whole design. An implementation that recorded
    first and verified afterwards would leave a licence in the database that no
    deployment can apply, and the operator would discover it at the receiver.
    """
    if not command.grant.capabilities:
        raise EmptyGrantError(
            "a grant with no capabilities would produce a licence that every "
            "deployment applies successfully and that authorises nothing"
        )
    require_usable_signers(signers)
    issued_at = now or datetime.now(UTC)

    def handler(session: Session) -> Mapping[str, object]:
        existing = session.execute(
            select(LicenceIssuance).where(
                LicenceIssuance.allocation_ref == command.grant.allocation_ref
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"id": str(existing.id)}

        for signer in signers:
            register_signing_key(
                session, key_id=signer.key_id, public_key_b64=signer.public_key_b64
            )

        licence = _lineage(
            session,
            subject_ref=command.grant.subject_ref,
            product_code=command.grant.product_code,
        )
        version = _next_version(session, licence.id)
        payload = _build_payload(
            licence=licence,
            version=version,
            grant=command.grant,
            issuer=command.issuer,
            issued_at=issued_at,
        )
        envelope = _envelope(payload, signers)
        digest = payload_digest(payload)

        try:
            verified = verify_licence(
                envelope,
                keyring=build_keyring(session),
                now=issued_at,
                expected_deployment_id=command.grant.deployment_ref,
            )
        except LicenceError as exc:
            raise UnverifiableIssuanceError(
                f"the envelope this issuer just produced fails the pinned kernel "
                f"verifier ({type(exc).__name__}: {exc}); refusing to record a "
                "licence no deployment could apply"
            ) from exc
        if verified.digest != digest:
            raise UnverifiableIssuanceError(
                "the verifier's digest disagrees with the issued payload digest; "
                "the payload was serialised more than once"
            )

        # Supersede the previous current version of this lineage. `REPLACED`
        # rather than `EXPIRED`: the old version was fine, it is simply no
        # longer current, and an operator asking "would re-issuing help?" needs
        # to be able to tell those apart.
        previous = session.execute(
            select(LicenceIssuance)
            .where(
                LicenceIssuance.licence_id == licence.id,
                LicenceIssuance.status.in_(
                    (IssuanceStatus.ISSUED.value, IssuanceStatus.ACTIVE.value)
                ),
            )
            .order_by(LicenceIssuance.version.desc())
        ).scalars()
        for row in previous:
            row.status = IssuanceStatus.REPLACED.value
            row.replaced_by_version = version
            row.record_version += 1

        issuance = LicenceIssuance(
            licence_id=licence.id,
            version=version,
            agreement_ref=command.grant.agreement_ref,
            allocation_ref=command.grant.allocation_ref,
            digest=digest,
            key_id=signers[0].key_id,
            envelope=envelope,
            status=IssuanceStatus.ISSUED.value,
            record_version=1,
            valid_from=command.grant.valid_from,
            valid_until=command.grant.valid_until,
            grace_days=command.grant.grace_days,
            deployment_ref=command.grant.deployment_ref,
        )
        session.add(issuance)
        session.flush()

        _audit_and_emit(
            session,
            action=AUDIT_ACTION_ISSUED,
            event_type=facts.LICENCE_ISSUED_V1,
            entity_type=_ENTITY_ISSUANCE,
            entity_id=str(issuance.id),
            actor_ref=command.actor_ref,
            details={
                "licence_id": str(licence.id),
                "licence_version": version,
                "generation": licence.generation,
                "digest": digest,
                "key_id": signers[0].key_id,
                "subject_ref": licence.subject_ref,
                "product_code": licence.product_code,
                "agreement_ref": command.grant.agreement_ref,
                "allocation_ref": command.grant.allocation_ref,
                "deployment_ref": command.grant.deployment_ref,
                "capabilities": [c.code for c in command.grant.capabilities],
            },
        )
        return {"id": str(issuance.id)}

    outcome = process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_ISSUE,
        handler=handler,
    )
    return _issuance_view(_load_issuance(db, UUID(str(outcome.result["id"]))))


# ── Lifecycle ───────────────────────────────────────────────────────────────


def _transition(
    db: Session,
    command: IssuanceTransitionCommand,
    *,
    scope: str,
    allowed: frozenset[str],
    to: IssuanceStatus,
    event_type: str,
    reason_field: str | None = None,
    stamp_activated: bool = False,
) -> facts.IssuanceView:
    def handler(session: Session) -> Mapping[str, object]:
        row = _load_issuance(session, command.issuance_id)
        _require_expected(
            row,
            expected_status=command.expected_status,
            expected_version=command.expected_version,
        )
        _require_status(row, allowed)
        if reason_field is not None:
            setattr(row, reason_field, command.reason)
        if stamp_activated and row.activated_at is None:
            row.activated_at = datetime.now(UTC)
        previous = _advance(row, to)
        session.flush()
        _audit_and_emit(
            session,
            action=AUDIT_ACTION_TRANSITIONED,
            event_type=event_type,
            entity_type=_ENTITY_ISSUANCE,
            entity_id=str(row.id),
            actor_ref=command.actor_ref,
            details={
                "licence_id": str(row.licence_id),
                "licence_version": row.version,
                "digest": row.digest,
                "from_status": previous,
                "to_status": row.status,
                "reason": command.reason,
                "record_version": row.record_version,
            },
        )
        return {"id": str(row.id)}

    process_once_platform(
        db, command_id=command.command_id, command_type=scope, handler=handler
    )
    return _issuance_view(_load_issuance(db, command.issuance_id))


def activate(db: Session, command: IssuanceTransitionCommand) -> facts.IssuanceView:
    """`issued → active`. The deployment confirmed it applied this version.

    Normally reached through `acknowledge`, which checks the report against the
    issuance first. This command exists for the case where activation is
    established out of band — an air-gapped import confirmed by an operator —
    and it is deliberately a separate, audited decision rather than something an
    unverified report can trigger.
    """
    return _transition(
        db,
        command,
        scope=SCOPE_ACTIVATE,
        allowed=frozenset({IssuanceStatus.ISSUED.value}),
        to=IssuanceStatus.ACTIVE,
        event_type=facts.LICENCE_ACTIVATED_V1,
        stamp_activated=True,
    )


def suspend(db: Session, command: IssuanceTransitionCommand) -> facts.IssuanceView:
    """`issued | active → suspended`. Withheld, not destroyed.

    The contracted response to a payment hold, and deliberately reversible.
    Suspension never deletes the issuance, the envelope or the acknowledgement
    record: ADR-0003 is explicit that payment failure never implicitly deletes a
    counterparty's data, and a licence record is that data.
    """
    return _transition(
        db,
        command,
        scope=SCOPE_SUSPEND,
        allowed=frozenset({IssuanceStatus.ISSUED.value, IssuanceStatus.ACTIVE.value}),
        to=IssuanceStatus.SUSPENDED,
        event_type=facts.LICENCE_SUSPENDED_V1,
        reason_field="suspended_reason",
    )


def reinstate(db: Session, command: IssuanceTransitionCommand) -> facts.IssuanceView:
    """`suspended → active`."""
    return _transition(
        db,
        command,
        scope=SCOPE_REINSTATE,
        allowed=frozenset({IssuanceStatus.SUSPENDED.value}),
        to=IssuanceStatus.ACTIVE,
        event_type=facts.LICENCE_REINSTATED_V1,
        stamp_activated=True,
    )


def expire(
    db: Session, command: IssuanceTransitionCommand, *, as_of: datetime
) -> facts.IssuanceView:
    """`issued | active | suspended → expired`, guarded on the clock.

    `as_of` is injected rather than read from the system clock, so the decision
    is reproducible in a test and in a replay. The guard matters: a
    mis-scheduled sweep that trusted its caller's word about the date would
    expire live licences early, and every affected deployment would lose
    authority at once.

    A PERPETUAL licence (`valid_until IS NULL`) can never expire, and asking is
    refused rather than silently ignored.
    """

    def handler(session: Session) -> Mapping[str, object]:
        row = _load_issuance(session, command.issuance_id)
        _require_expected(
            row,
            expected_status=command.expected_status,
            expected_version=command.expected_version,
        )
        _require_status(
            row,
            frozenset(
                {
                    IssuanceStatus.ISSUED.value,
                    IssuanceStatus.ACTIVE.value,
                    IssuanceStatus.SUSPENDED.value,
                }
            ),
        )
        if row.valid_until is None:
            raise TransitionRefusedError(
                f"issuance {row.id} is perpetual; a licence with no expiry "
                "cannot expire, and treating it as expired would revoke "
                "authority nobody time-limited"
            )
        # Naive/aware normalisation is deliberate rather than incidental: the
        # column is `timezone=True`, but a dialect without a tz-aware type
        # returns it naive, and comparing across the two raises. The stored
        # instant is UTC by construction.
        deadline = row.valid_until
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if as_of <= deadline:
            raise TransitionRefusedError(
                f"issuance {row.id} is valid until {deadline.isoformat()}; it is "
                f"not expired as of {as_of.isoformat()}"
            )
        previous = _advance(row, IssuanceStatus.EXPIRED)
        session.flush()
        _audit_and_emit(
            session,
            action=AUDIT_ACTION_TRANSITIONED,
            event_type=facts.LICENCE_EXPIRED_V1,
            entity_type=_ENTITY_ISSUANCE,
            entity_id=str(row.id),
            actor_ref=command.actor_ref,
            details={
                "licence_id": str(row.licence_id),
                "licence_version": row.version,
                "from_status": previous,
                "to_status": row.status,
                "valid_until": deadline.isoformat(),
                "as_of": as_of.isoformat(),
            },
        )
        return {"id": str(row.id)}

    process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_EXPIRE,
        handler=handler,
    )
    return _issuance_view(_load_issuance(db, command.issuance_id))


def revoke_licence(db: Session, command: RevokeCommand) -> facts.LicenceView:
    """Revoke a whole lineage. Permanent, and every version goes with it.

    Keyed on the licence rather than an issuance because revocation is by
    `licence_id` in the wire protocol — a receiver has no way to act on
    "version 3 is revoked". Revoking twice is idempotent: the unique constraint
    on `revocations.licence_id` makes the second attempt a no-op rather than a
    duplicated fact.

    There is no un-revoke. Recovery is re-issuance under a new generation, which
    `_lineage` mints automatically on the next issue.
    """

    def handler(session: Session) -> Mapping[str, object]:
        licence = session.get(Licence, command.licence_id)
        if licence is None:
            raise TransitionRefusedError(f"licence {command.licence_id} not found")
        if _is_revoked(session, licence.id):
            return {"id": str(licence.id)}

        session.add(
            Revocation(
                licence_id=licence.id,
                reason=command.reason,
                actor_ref=command.actor_ref,
            )
        )
        rows = (
            session.execute(
                select(LicenceIssuance).where(
                    LicenceIssuance.licence_id == licence.id,
                    LicenceIssuance.status.notin_(tuple(TERMINAL_ISSUANCE_STATUSES)),
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            _advance(row, IssuanceStatus.REVOKED)
        session.flush()

        _audit_and_emit(
            session,
            action=AUDIT_ACTION_TRANSITIONED,
            event_type=facts.LICENCE_REVOKED_V1,
            entity_type=_ENTITY_LICENCE,
            entity_id=str(licence.id),
            actor_ref=command.actor_ref,
            details={
                "licence_id": str(licence.id),
                "subject_ref": licence.subject_ref,
                "product_code": licence.product_code,
                "generation": licence.generation,
                "reason": command.reason,
                "revoked_versions": [row.version for row in rows],
            },
        )
        return {"id": str(licence.id)}

    process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_REVOKE,
        handler=handler,
    )
    return licence_view(db, command.licence_id)  # type: ignore[return-value]


# ── Acknowledgement ─────────────────────────────────────────────────────────


def acknowledge(db: Session, command: AcknowledgeCommand) -> facts.IssuanceView:
    """Record what a deployment reported, after checking it against our records.

    **Fail closed.** The report's `(licence_ref, licence_version, digest)` triple
    must identify an issuance this module actually produced. A report naming a
    digest nobody signed is refused — otherwise a target could mark itself
    activated on a document that does not exist, which is the whole attack an
    acknowledgement is supposed to make visible rather than enable.

    An `applied` report on an `issued` licence activates it. A `rejected` report
    is recorded and changes no status: rejection is information for an operator,
    not a licence-state transition, and auto-suspending on a receiver's say-so
    would let one misconfigured deployment withdraw its own authority.
    """
    report = command.report

    def handler(session: Session) -> Mapping[str, object]:
        try:
            licence_uuid = UUID(report.licence_ref)
        except ValueError as exc:
            raise AcknowledgementRefusedError(
                f"licence_ref {report.licence_ref!r} is not a licence id this "
                "issuer could have produced"
            ) from exc

        issuance = session.execute(
            select(LicenceIssuance).where(
                LicenceIssuance.licence_id == licence_uuid,
                LicenceIssuance.version == report.licence_version,
            )
        ).scalar_one_or_none()
        if issuance is None:
            raise AcknowledgementRefusedError(
                f"no issuance for licence {report.licence_ref} version "
                f"{report.licence_version}; this issuer never produced it"
            )
        if issuance.digest != report.digest:
            raise AcknowledgementRefusedError(
                f"acknowledgement names digest {report.digest!r} but licence "
                f"{report.licence_ref} version {report.licence_version} was "
                f"issued as {issuance.digest!r}; the report describes a document "
                "this issuer did not sign"
            )
        if report.outcome not in {
            AcknowledgementOutcome.APPLIED.value,
            AcknowledgementOutcome.REJECTED.value,
        }:
            raise AcknowledgementRefusedError(
                f"outcome {report.outcome!r} is not part of the shared "
                "acknowledgement vocabulary"
            )

        already = session.execute(
            select(LicenceAcknowledgement).where(
                LicenceAcknowledgement.issuance_id == issuance.id,
                LicenceAcknowledgement.outcome == report.outcome,
                LicenceAcknowledgement.reported_deployment_ref
                == (report.authenticated_deployment_ref or ""),
            )
        ).scalar_one_or_none()
        if already is None:
            session.add(
                LicenceAcknowledgement(
                    issuance_id=issuance.id,
                    licence_version=report.licence_version,
                    digest=report.digest,
                    outcome=report.outcome,
                    reason=report.reason,
                    reported_at=report.reported_at,
                    reported_deployment_ref=(report.authenticated_deployment_ref or ""),
                    authenticated_deployment_ref=(report.authenticated_deployment_ref),
                )
            )

        if (
            report.outcome == AcknowledgementOutcome.APPLIED.value
            and issuance.status == IssuanceStatus.ISSUED.value
        ):
            issuance.activated_at = report.reported_at
            _advance(issuance, IssuanceStatus.ACTIVE)
        session.flush()

        _audit_and_emit(
            session,
            action=AUDIT_ACTION_ACKNOWLEDGED,
            event_type=facts.LICENCE_ACKNOWLEDGED_V1,
            entity_type=_ENTITY_ISSUANCE,
            entity_id=str(issuance.id),
            actor_ref=command.actor_ref,
            details={
                "licence_id": str(issuance.licence_id),
                "licence_version": issuance.version,
                "digest": issuance.digest,
                "outcome": report.outcome,
                "reason": report.reason,
                "status": issuance.status,
                "authenticated": report.authenticated_deployment_ref is not None,
            },
        )
        return {"id": str(issuance.id)}

    outcome = process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_ACKNOWLEDGE,
        handler=handler,
    )
    return _issuance_view(_load_issuance(db, UUID(str(outcome.result["id"]))))


# ── Revocation lists ────────────────────────────────────────────────────────


def publish_revocation_list(
    db: Session,
    *,
    command_id: str,
    signers: Sequence[LicenceSigner],
    now: datetime | None = None,
    actor_ref: str | None = None,
) -> facts.RevocationListView:
    """Publish an immutable signed snapshot of the FULL revoked set.

    **The cumulative rule**, ported because the failure it prevents is silent:
    every published snapshot must be a SUPERSET of the one before it. Version
    monotonicity alone does not prevent un-revocation — a higher version that
    quietly omits an earlier id restores access while looking perfectly
    well-ordered to every receiver, and no deployment would report anything
    wrong. Recovery from a mistaken revocation is re-issuance under a new
    generation, never removal from the list.

    The check is against the PREVIOUS published list rather than a running set,
    because the previous list is what the fleet actually holds.
    """
    require_usable_signers(signers)
    issued_at = now or datetime.now(UTC)

    def handler(session: Session) -> Mapping[str, object]:
        revoked = sorted(
            str(row) for row in session.execute(select(Revocation.licence_id)).scalars()
        )
        previous = session.execute(
            select(RevocationList).order_by(RevocationList.list_version.desc()).limit(1)
        ).scalar_one_or_none()
        if previous is not None:
            prior_ids = set(_revoked_ids_of(previous))
            missing = sorted(prior_ids - set(revoked))
            if missing:
                raise RevocationSupersessionError(
                    f"revocation list {previous.list_version + 1} would omit "
                    f"{len(missing)} id(s) present in list {previous.list_version} "
                    f"(first: {missing[0]}); a published list may only grow, "
                    "because a receiver treats an omission as restored access"
                )
        list_version = 1 if previous is None else previous.list_version + 1

        for signer in signers:
            register_signing_key(
                session, key_id=signer.key_id, public_key_b64=signer.public_key_b64
            )

        payload = json.dumps(
            {
                "schema": REVOCATION_SCHEMA,
                "list_version": list_version,
                "issued_at": issued_at.isoformat(),
                "revoked_licence_ids": revoked,
            }
        ).encode()
        envelope = _envelope(payload, signers)
        digest = payload_digest(payload)

        try:
            # `applied_list_version=None` on purpose: the kernel's monotonicity
            # check is a RECEIVER's check against what it last applied, and this
            # issuer is not a receiver. The issuer's own ordering is already
            # guaranteed by `list_version = previous + 1` above, and passing our
            # own previous version here would only re-assert arithmetic we just
            # performed. What this round trip is for is the same thing the
            # licence round trip is for — proving the artifact we are about to
            # publish is one the pinned verifier accepts.
            verify_revocation_list(envelope, keyring=build_keyring(session))
        except LicenceError as exc:
            raise UnverifiableIssuanceError(
                f"the revocation list this issuer just produced fails the pinned "
                f"kernel verifier ({type(exc).__name__}: {exc}); refusing to "
                "publish a list no deployment could import"
            ) from exc

        row = RevocationList(
            list_version=list_version,
            digest=digest,
            key_id=signers[0].key_id,
            entry_count=len(revoked),
            envelope=envelope,
        )
        session.add(row)
        session.flush()
        _audit_and_emit(
            session,
            action=AUDIT_ACTION_ISSUED,
            event_type=facts.REVOCATION_LIST_PUBLISHED_V1,
            entity_type="licence_revocation_list",
            entity_id=str(row.id),
            actor_ref=actor_ref,
            details={
                "list_version": list_version,
                "entry_count": len(revoked),
                "digest": digest,
                "key_id": signers[0].key_id,
            },
        )
        return {"id": str(row.id)}

    outcome = process_once_platform(
        db,
        command_id=command_id,
        command_type=SCOPE_PUBLISH_REVOCATIONS,
        handler=handler,
    )
    row = db.get(RevocationList, UUID(str(outcome.result["id"])))
    if row is None:  # pragma: no cover - the row was written above
        raise UnverifiableIssuanceError(
            "the revocation list vanished between write and read"
        )
    return facts.RevocationListView(
        id=row.id,
        list_version=row.list_version,
        digest=row.digest,
        key_id=row.key_id,
        entry_count=row.entry_count,
        envelope=dict(row.envelope),
    )


def _revoked_ids_of(row: RevocationList) -> tuple[str, ...]:
    """The ids inside a published list's own signed payload.

    Read from the ENVELOPE rather than recomputed from the `revocations` table,
    because the published artifact is what the fleet holds and the supersession
    rule is about that artifact. Recomputing would compare the new list against
    the current table — which is trivially a superset of itself, and would make
    the guard vacuous.
    """
    payload_b64 = str(row.envelope.get("payload_b64", ""))
    padding = "=" * (-len(payload_b64) % 4)
    document = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    ids = document.get("revoked_licence_ids", [])
    return tuple(str(value) for value in ids)


# ── Reads ───────────────────────────────────────────────────────────────────


def get_issuance(db: Session, issuance_id: UUID) -> facts.IssuanceView | None:
    row = db.get(LicenceIssuance, issuance_id)
    return _issuance_view(row) if row is not None else None


def licence_view(db: Session, licence_id: UUID) -> facts.LicenceView | None:
    row = db.get(Licence, licence_id)
    if row is None:
        return None
    return facts.LicenceView(
        id=row.id,
        subject_ref=row.subject_ref,
        product_code=row.product_code,
        generation=row.generation,
        revoked=_is_revoked(db, row.id),
        issuances=tuple(_issuance_view(issuance) for issuance in row.issuances),
    )


def current_issuance(db: Session, licence_id: UUID) -> facts.IssuanceView | None:
    """The highest-versioned issuance of a lineage, whatever its status."""
    row = db.execute(
        select(LicenceIssuance)
        .where(LicenceIssuance.licence_id == licence_id)
        .order_by(LicenceIssuance.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _issuance_view(row) if row is not None else None


def acknowledgements(
    db: Session, issuance_id: UUID
) -> tuple[facts.AcknowledgementView, ...]:
    rows = (
        db.execute(
            select(LicenceAcknowledgement)
            .where(LicenceAcknowledgement.issuance_id == issuance_id)
            .order_by(LicenceAcknowledgement.reported_at)
        )
        .scalars()
        .all()
    )
    return tuple(
        facts.AcknowledgementView(
            issuance_id=row.issuance_id,
            licence_version=row.licence_version,
            digest=row.digest,
            outcome=row.outcome,
            reason=row.reason,
            reported_at=row.reported_at,
            reported_deployment_ref=row.reported_deployment_ref,
            authenticated=row.authenticated_deployment_ref is not None,
        )
        for row in rows
    )


def inspect_issued_envelope(
    db: Session,
    envelope: Mapping[str, object] | str | bytes,
    *,
    now: datetime,
    expected_deployment_id: str | None = None,
) -> facts.InspectionResult:
    """The typed validation/inspection API: what would a deployment see?

    Runs the caller's envelope through the SAME kernel verifier a receiver runs,
    against this issuer's live keyring and its live revoked set. It answers the
    support question — *"why is this customer's licence not working?"* — without
    anyone reading a database by hand or trusting a screenshot.

    Deliberately never raises for an invalid licence. A verification failure IS
    the answer, so it is returned as a typed result with the kernel's own stable
    error name; raising would make the caller's error handler the place where the
    diagnosis lives.
    """
    revoked = frozenset(
        str(value) for value in db.execute(select(Revocation.licence_id)).scalars()
    )
    # The kernel keys revocation on the licence_id STRING inside the document,
    # which is the lineage id — the same value `Revocation.licence_id` stores.
    try:
        verified = verify_licence(
            envelope,
            keyring=build_keyring(db),
            now=now,
            expected_deployment_id=expected_deployment_id,
            revoked_licence_ids=revoked,
        )
    except LicenceError as exc:
        return facts.InspectionResult(
            valid=False,
            reason=type(exc).__name__,
            detail=str(exc),
        )
    return facts.InspectionResult(
        valid=True,
        validity=verified.validity,
        licence_id=verified.document.licence_id,
        licence_version=verified.document.licence_version,
        digest=verified.digest,
        product=verified.document.product,
        capabilities=tuple(
            capability.code for capability in verified.document.capabilities
        ),
    )


__all__ = [
    "AUDIT_ACTION_ACKNOWLEDGED",
    "AUDIT_ACTION_ISSUED",
    "AUDIT_ACTION_TRANSITIONED",
    "DEFAULT_ISSUER",
    "SCOPE_ACKNOWLEDGE",
    "SCOPE_ACTIVATE",
    "SCOPE_EXPIRE",
    "SCOPE_ISSUE",
    "SCOPE_PUBLISH_REVOCATIONS",
    "SCOPE_REINSTATE",
    "SCOPE_REVOKE",
    "SCOPE_SUSPEND",
    "AcknowledgeCommand",
    "IssuanceTransitionCommand",
    "IssueCommand",
    "RevokeCommand",
    "acknowledge",
    "acknowledgements",
    "activate",
    "build_keyring",
    "current_issuance",
    "expire",
    "get_issuance",
    "inspect_issued_envelope",
    "issuances_by_key",
    "issue_licence",
    "licence_view",
    "publish_revocation_list",
    "register_signing_key",
    "reinstate",
    "revoke_licence",
    "set_key_status",
    "suspend",
]
