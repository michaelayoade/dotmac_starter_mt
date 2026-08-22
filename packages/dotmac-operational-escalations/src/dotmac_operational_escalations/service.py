"""Escalation policy versions and instances.

Ported from `dotmac_sub`'s `app/models/operational_escalation.py`. Two source
behaviours are deliberately NOT ported:

* **Deliveries.** Sub keeps `operational_escalation_deliveries` beside the
  policy, which makes the escalation owner also the delivery owner. Messaging
  and Integrator own transport; this module records only that an escalation was
  raised, acknowledged, resolved or cancelled.
* **A mutable policy row.** Editing Sub's policy rewrites the terms every
  already-open escalation was raised under. Here terms are immutable per
  version and an instance binds the exact version.

Cooldown is enforced as a REFUSAL to raise, not as a suppressed delivery, since
suppression is a transport decision and "should this escalation exist" is ours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_operational_escalations.contracts import (
    Conflict,
    DraftPolicyVersion,
    EscalationStatus,
    PolicyVersionState,
    RaiseEscalation,
    RegisterPolicy,
    SettleEscalation,
)
from dotmac_operational_escalations.models import (
    EscalationInstance,
    EscalationPolicy,
    EscalationPolicyVersion,
)

_LIVE = frozenset({EscalationStatus.OPEN, EscalationStatus.ACKNOWLEDGED})


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-operational-escalations requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _policy(db: Session, tenant_id: UUID, policy_id: UUID) -> EscalationPolicy:
    row = db.scalar(
        select(EscalationPolicy).where(
            EscalationPolicy.tenant_id == tenant_id, EscalationPolicy.id == policy_id
        )
    )
    if row is None:
        raise Conflict("escalation policy was not found in the tenant")
    return row


def register_policy(
    db: Session, *, scope: TenantScope, command: RegisterPolicy
) -> EscalationPolicy:
    tenant_id = _tenant(scope)
    code = _required(command.code, "code").upper()
    existing = db.scalar(
        select(EscalationPolicy).where(
            EscalationPolicy.tenant_id == tenant_id, EscalationPolicy.code == code
        )
    )
    if existing is not None:
        raise Conflict("escalation policy code is already registered in the tenant")
    row = EscalationPolicy(
        tenant_id=tenant_id,
        code=code,
        name=_required(command.name, "name"),
        subject_type=_required(command.subject_type, "subject type").upper(),
        trigger=_required(command.trigger, "trigger").upper(),
    )
    db.add(row)
    db.flush()
    return row


def draft_policy_version(
    db: Session, *, scope: TenantScope, command: DraftPolicyVersion
) -> EscalationPolicyVersion:
    tenant_id = _tenant(scope)
    policy = _policy(db, tenant_id, command.policy_id)
    if command.level < 1:
        raise Conflict("escalation level starts at 1")
    if command.cooldown_seconds < 0:
        raise Conflict("cooldown must not be negative")
    if not command.channels:
        raise Conflict("a policy version must request at least one channel")
    highest = db.scalar(
        select(EscalationPolicyVersion.version)
        .where(
            EscalationPolicyVersion.tenant_id == tenant_id,
            EscalationPolicyVersion.policy_id == policy.id,
        )
        .order_by(EscalationPolicyVersion.version.desc())
        .limit(1)
    )
    row = EscalationPolicyVersion(
        tenant_id=tenant_id,
        policy_id=policy.id,
        version=(highest or 0) + 1,
        level=command.level,
        channels=[
            _required(channel, "channel").upper() for channel in command.channels
        ],
        minimum_severity=command.minimum_severity,
        unowned_after_seconds=command.unowned_after_seconds,
        unresolved_after_seconds=command.unresolved_after_seconds,
        cooldown_seconds=command.cooldown_seconds,
        state=PolicyVersionState.DRAFT,
    )
    db.add(row)
    db.flush()
    return row


def activate_policy_version(
    db: Session, *, scope: TenantScope, version_id: UUID, at: datetime | None = None
) -> EscalationPolicyVersion:
    """Make one version current; retire whichever version it replaces.

    Exactly one ACTIVE version per policy, so "which terms apply now" has a
    single answer rather than being resolved by ordering.
    """
    tenant_id = _tenant(scope)
    version = db.scalar(
        select(EscalationPolicyVersion).where(
            EscalationPolicyVersion.tenant_id == tenant_id,
            EscalationPolicyVersion.id == version_id,
        )
    )
    if version is None:
        raise Conflict("escalation policy version was not found in the tenant")
    if version.state is not PolicyVersionState.DRAFT:
        raise Conflict("only a draft policy version can be activated")
    moment = at or datetime.now(UTC)
    current = _active_version(db, tenant_id, version.policy_id)
    if current is not None:
        current.state = PolicyVersionState.RETIRED
        current.retired_at = moment
    version.state = PolicyVersionState.ACTIVE
    version.activated_at = moment
    db.flush()
    return version


def _active_version(
    db: Session, tenant_id: UUID, policy_id: UUID
) -> EscalationPolicyVersion | None:
    return db.scalar(
        select(EscalationPolicyVersion).where(
            EscalationPolicyVersion.tenant_id == tenant_id,
            EscalationPolicyVersion.policy_id == policy_id,
            EscalationPolicyVersion.state == PolicyVersionState.ACTIVE,
        )
    )


def raise_escalation(
    db: Session, *, scope: TenantScope, command: RaiseEscalation
) -> EscalationInstance:
    tenant_id = _tenant(scope)
    policy = _policy(db, tenant_id, command.policy_id)
    version = _active_version(db, tenant_id, policy.id)
    if version is None:
        raise Conflict("escalation policy has no active version")

    dedup_key = _required(command.dedup_key, "dedup key")
    replay = db.scalar(
        select(EscalationInstance).where(
            EscalationInstance.tenant_id == tenant_id,
            EscalationInstance.dedup_key == dedup_key,
        )
    )
    if replay is not None:
        return replay

    raised_at = command.raised_at or datetime.now(UTC)
    subject_reference = _required(command.subject_reference, "subject reference")
    if version.cooldown_seconds:
        window_start = raised_at - timedelta(seconds=version.cooldown_seconds)
        recent = db.scalar(
            select(EscalationInstance.id).where(
                EscalationInstance.tenant_id == tenant_id,
                EscalationInstance.subject_reference == subject_reference,
                EscalationInstance.trigger == policy.trigger,
                EscalationInstance.raised_at >= window_start,
            )
        )
        if recent is not None:
            raise Conflict("escalation is within its policy cooldown for this subject")

    row = EscalationInstance(
        tenant_id=tenant_id,
        policy_version_id=version.id,
        subject_type=policy.subject_type,
        subject_reference=subject_reference,
        trigger=policy.trigger,
        level=version.level,
        severity=command.severity,
        dedup_key=dedup_key,
        status=EscalationStatus.OPEN,
        raised_at=raised_at,
    )
    db.add(row)
    db.flush()
    return row


def _instance(db: Session, tenant_id: UUID, escalation_id: UUID) -> EscalationInstance:
    row = db.scalar(
        select(EscalationInstance).where(
            EscalationInstance.tenant_id == tenant_id,
            EscalationInstance.id == escalation_id,
        )
    )
    if row is None:
        raise Conflict("escalation was not found in the tenant")
    return row


def acknowledge_escalation(
    db: Session, *, scope: TenantScope, command: SettleEscalation
) -> EscalationInstance:
    instance = _instance(db, _tenant(scope), command.escalation_id)
    if instance.status is not EscalationStatus.OPEN:
        raise Conflict("only an open escalation can be acknowledged")
    instance.status = EscalationStatus.ACKNOWLEDGED
    instance.acknowledged_at = command.at or datetime.now(UTC)
    instance.acknowledged_by_reference = _required(
        command.actor_reference, "actor reference"
    )
    db.flush()
    return instance


def resolve_escalation(
    db: Session, *, scope: TenantScope, command: SettleEscalation
) -> EscalationInstance:
    instance = _instance(db, _tenant(scope), command.escalation_id)
    if instance.status not in _LIVE:
        raise Conflict("only a live escalation can be resolved")
    instance.status = EscalationStatus.RESOLVED
    instance.settled_at = command.at or datetime.now(UTC)
    instance.settlement_reason = command.reason
    db.flush()
    return instance


def cancel_escalation(
    db: Session, *, scope: TenantScope, command: SettleEscalation
) -> EscalationInstance:
    """Cancel an escalation that should never have been raised.

    Distinct from resolving: resolution says the underlying condition ended,
    cancellation says the escalation itself was wrong. Collapsing the two loses
    the only signal that a policy is misconfigured.
    """
    instance = _instance(db, _tenant(scope), command.escalation_id)
    if instance.status not in _LIVE:
        raise Conflict("only a live escalation can be cancelled")
    instance.status = EscalationStatus.CANCELLED
    instance.settled_at = command.at or datetime.now(UTC)
    instance.settlement_reason = _required(command.reason or "", "cancellation reason")
    db.flush()
    return instance


__all__ = [
    "acknowledge_escalation",
    "activate_policy_version",
    "cancel_escalation",
    "draft_policy_version",
    "raise_escalation",
    "register_policy",
    "resolve_escalation",
]
