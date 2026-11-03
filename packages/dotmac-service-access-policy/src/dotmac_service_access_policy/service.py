"""Desired service-access policy; callers own authorization and transactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from dotmac_service_access_policy.contracts import (
    AccessInputDecision,
    AccessSignal,
    DesiredAccess,
    RecordAccessInput,
    RecordAccessInputV2,
    ResolveDesiredAccess,
)
from dotmac_service_access_policy.models import (
    DesiredAccessDecision,
    ServiceAccessInput,
)
from dotmac_service_access_policy.versioning import (
    StoredAccessStamp,
    decide_access_input,
    decide_unversioned_write,
    validate_source_version,
)

REFUSAL_REASON_CODES: dict[AccessInputDecision, str] = {
    AccessInputDecision.REFUSE_STALE: "ACCESS_INPUT_STALE",
    AccessInputDecision.REFUSE_CONFLICT: "ACCESS_INPUT_VERSION_CONFLICT",
    AccessInputDecision.REFUSE_UNVERSIONED: "ACCESS_INPUT_UNVERSIONED_WRITE",
}


class AccessInputRefused(ValueError):
    """An input was refused; nothing was written. ``reason_code`` is stable."""

    def __init__(self, reason_code: str, decision: AccessInputDecision) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.decision = decision


@dataclass(frozen=True, slots=True)
class RecordAccessResultV2:
    """APPLY or IDEMPOTENT; every refusal raises ``AccessInputRefused``."""

    decision: AccessInputDecision
    row: ServiceAccessInput


_DENY_PRECEDENCE = (
    AccessSignal.ADMIN_HOLD,
    AccessSignal.COLLECTIONS_HOLD,
    AccessSignal.PREPAID_DEPLETED,
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-service-access-policy requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _locked_input(
    db: Session,
    tenant_id: UUID,
    service: str,
    signal: AccessSignal,
) -> ServiceAccessInput | None:
    # FOR UPDATE serializes writers per key; populate_existing makes a writer
    # that waited see the row the winner committed, not a stale identity-map copy.
    return db.scalar(
        select(ServiceAccessInput)
        .where(
            ServiceAccessInput.tenant_id == tenant_id,
            ServiceAccessInput.service_reference == service,
            ServiceAccessInput.signal == signal,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _stamp(row: ServiceAccessInput) -> StoredAccessStamp:
    return StoredAccessStamp(
        source_version=row.source_version,
        active=row.active,
        source_reference=row.source_reference,
        observed_at=row.observed_at,
        policy_version=row.policy_version,
    )


def _refuse(decision: AccessInputDecision) -> AccessInputRefused:
    return AccessInputRefused(REFUSAL_REASON_CODES[decision], decision)


def record_access_input(
    db: Session, *, scope: TenantScope, command: RecordAccessInput
) -> ServiceAccessInput:
    """V1 contract: unconditional overwrite, but never onto a versioned key."""
    if not isinstance(command, RecordAccessInput):
        raise TypeError("record_access_input takes RecordAccessInput (use V2 API)")
    tenant_id = _tenant(scope)
    service = _required(command.service_reference, "service reference")
    source = _required(command.source_reference, "source reference")
    row = _locked_input(db, tenant_id, service, command.signal)
    decision = decide_unversioned_write(None if row is None else _stamp(row))
    if decision is not AccessInputDecision.APPLY:
        raise _refuse(decision)
    if row is None:
        row = ServiceAccessInput(
            tenant_id=tenant_id,
            service_reference=service,
            signal=command.signal,
            active=command.active,
            source_reference=source,
            observed_at=command.observed_at,
        )
        db.add(row)
    else:
        row.active = command.active
        row.source_reference = source
        row.observed_at = command.observed_at
    db.flush()
    return row


def _insert_if_absent(db: Session, values: dict[str, object]) -> bool:
    """INSERT .. ON CONFLICT DO NOTHING RETURNING id; True when this call inserted."""
    table = cast(sa.Table, ServiceAccessInput.__table__)
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        pg_stmt = postgresql.insert(table).values(**values)
        returning = pg_stmt.on_conflict_do_nothing(
            index_elements=["tenant_id", "service_reference", "signal"]
        ).returning(table.c.id)
    elif dialect == "sqlite":
        lite_stmt = sqlite.insert(table).values(**values)
        returning = lite_stmt.on_conflict_do_nothing(
            index_elements=["tenant_id", "service_reference", "signal"]
        ).returning(table.c.id)
    else:
        raise NotImplementedError(f"unsupported dialect {dialect}")
    return db.execute(returning).first() is not None


def record_access_input_v2(
    db: Session, *, scope: TenantScope, command: RecordAccessInputV2
) -> RecordAccessResultV2:
    """Versioned write. Ordering rule: see ``versioning`` (strictly newer wins).

    Concurrent writers for one key are serialized: the insert is
    ``ON CONFLICT DO NOTHING`` (a loser waits for the winner's transaction),
    and an existing row is read ``FOR UPDATE`` before the rule is applied, so
    the rule always runs against the latest committed state.
    """
    if not isinstance(command, RecordAccessInputV2):
        raise TypeError("record_access_input_v2 takes RecordAccessInputV2")
    tenant_id = _tenant(scope)
    service = _required(command.service_reference, "service reference")
    source = _required(command.source_reference, "source reference")
    policy = _required(command.policy_version, "policy version")
    if len(policy) > 60:
        raise ValueError("policy version must be at most 60 characters")
    version = validate_source_version(command.source_version)
    if command.observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    normalized = RecordAccessInputV2(
        service,
        command.signal,
        command.active,
        source,
        command.observed_at,
        version,
        policy,
    )
    inserted = _insert_if_absent(
        db,
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "service_reference": service,
            "signal": command.signal,
            "active": command.active,
            "source_reference": source,
            "observed_at": command.observed_at,
            "source_version": version,
            "policy_version": policy,
        },
    )
    row = _locked_input(db, tenant_id, service, command.signal)
    if row is None:  # pragma: no cover - row exists or was just inserted
        raise RuntimeError("access input vanished after insert")
    if inserted:
        return RecordAccessResultV2(AccessInputDecision.APPLY, row)
    decision = decide_access_input(_stamp(row), normalized)
    if decision is AccessInputDecision.IDEMPOTENT:
        return RecordAccessResultV2(decision, row)
    if decision is not AccessInputDecision.APPLY:
        raise _refuse(decision)
    row.active = command.active
    row.source_reference = source
    row.observed_at = command.observed_at
    row.source_version = version
    row.policy_version = policy
    db.flush()
    return RecordAccessResultV2(decision, row)


def resolve_desired_access(
    db: Session, *, scope: TenantScope, command: ResolveDesiredAccess
) -> DesiredAccessDecision:
    tenant_id = _tenant(scope)
    service = _required(command.service_reference, "service reference")
    active = set(
        db.scalars(
            select(ServiceAccessInput.signal).where(
                ServiceAccessInput.tenant_id == tenant_id,
                ServiceAccessInput.service_reference == service,
                ServiceAccessInput.active.is_(True),
            )
        ).all()
    )
    matched = next((signal for signal in _DENY_PRECEDENCE if signal in active), None)
    if matched is not None:
        desired, reason = DesiredAccess.DENY, matched.value
    elif AccessSignal.FUP_EXHAUSTED in active:
        desired, reason = DesiredAccess.RESTRICT, AccessSignal.FUP_EXHAUSTED.value
    else:
        desired, reason = DesiredAccess.ALLOW, "POLICY_CLEAR"
    row = db.scalar(
        select(DesiredAccessDecision).where(
            DesiredAccessDecision.tenant_id == tenant_id,
            DesiredAccessDecision.service_reference == service,
        )
    )
    if row is None:
        row = DesiredAccessDecision(
            tenant_id=tenant_id,
            service_reference=service,
            desired_access=desired,
            reason_code=reason,
            decided_at=command.decided_at,
        )
        db.add(row)
    else:
        row.desired_access = desired
        row.reason_code = reason
        row.decided_at = command.decided_at
    db.flush()
    return row


__all__ = [
    "REFUSAL_REASON_CODES",
    "AccessInputRefused",
    "RecordAccessResultV2",
    "record_access_input",
    "record_access_input_v2",
    "resolve_desired_access",
]
