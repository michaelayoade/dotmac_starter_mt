"""Qualification decisions; callers own authorization and transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_qualification.contracts import (
    Conflict,
    DecisionOutcome,
    DecisionRead,
    OpenQualification,
    RecordDecision,
    RecordEvidence,
    WarrantRefusal,
    WarrantRefused,
)
from dotmac_qualification.models import (
    QualificationCase,
    QualificationDecision,
    QualificationEvidence,
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-qualification requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _case(db: Session, tenant_id: UUID, case_id: UUID) -> QualificationCase:
    row = db.scalar(
        select(QualificationCase).where(
            QualificationCase.tenant_id == tenant_id, QualificationCase.id == case_id
        )
    )
    if row is None:
        raise Conflict("qualification case was not found in the tenant")
    return row


def open_qualification(
    db: Session, *, scope: TenantScope, command: OpenQualification
) -> QualificationCase:
    row = QualificationCase(
        tenant_id=_tenant(scope),
        subject_reference=_required(command.subject_reference, "subject reference"),
        specification_reference=_required(
            command.specification_reference, "specification reference"
        ),
        opened_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def record_evidence(
    db: Session, *, scope: TenantScope, command: RecordEvidence
) -> QualificationEvidence:
    tenant_id = _tenant(scope)
    case = _case(db, tenant_id, command.case_id)
    if case.closed_at is not None:
        raise Conflict("qualification case is closed")
    if command.valid_until <= command.observed_at:
        raise Conflict("evidence validity must end after observation")
    if not command.facts:
        raise Conflict("evidence facts must not be empty")
    row = QualificationEvidence(
        tenant_id=tenant_id,
        case_id=case.id,
        source_type=_required(command.source_type, "source type").upper(),
        observed_at=command.observed_at,
        valid_until=command.valid_until,
        facts=dict(command.facts),
    )
    db.add(row)
    db.flush()
    return row


def record_decision(
    db: Session, *, scope: TenantScope, command: RecordDecision
) -> QualificationDecision:
    tenant_id = _tenant(scope)
    case = _case(db, tenant_id, command.case_id)
    if case.closed_at is not None:
        raise Conflict("qualification case is closed")
    if command.expires_at <= command.decided_at:
        raise Conflict("decision expiry must follow decision time")
    valid_evidence = db.scalar(
        select(QualificationEvidence.id).where(
            QualificationEvidence.tenant_id == tenant_id,
            QualificationEvidence.case_id == case.id,
            QualificationEvidence.observed_at <= command.decided_at,
            QualificationEvidence.valid_until >= command.decided_at,
        )
    )
    if valid_evidence is None:
        raise Conflict("qualification decision needs valid evidence")
    row = QualificationDecision(
        tenant_id=tenant_id,
        case_id=case.id,
        outcome=command.outcome,
        decided_at=command.decided_at,
        expires_at=command.expires_at,
        rationale=_required(command.rationale, "rationale"),
    )
    db.add(row)
    case.closed_at = command.decided_at
    db.flush()
    return row


def _utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-erasing round trip for comparisons."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def read_decision(
    db: Session, *, scope: TenantScope, decision_id: UUID
) -> DecisionRead | None:
    """Read one decision's verifier facts, or None when the tenant has none.

    Read-only: it flushes nothing and exposes no rationale or evidence fact.
    """
    tenant_id = _tenant(scope)
    row = db.execute(
        select(QualificationDecision, QualificationCase)
        .join(
            QualificationCase,
            (QualificationCase.tenant_id == QualificationDecision.tenant_id)
            & (QualificationCase.id == QualificationDecision.case_id),
        )
        .where(
            QualificationDecision.tenant_id == tenant_id,
            QualificationDecision.id == decision_id,
        )
    ).one_or_none()
    if row is None:
        return None
    decision, case = row
    return DecisionRead(
        decision_id=decision.id,
        case_id=case.id,
        subject_reference=case.subject_reference,
        specification_reference=case.specification_reference,
        outcome=decision.outcome,
        decided_at=_utc(decision.decided_at),
        expires_at=_utc(decision.expires_at),
    )


def verify_warrant(
    db: Session,
    *,
    scope: TenantScope,
    decision_id: UUID,
    subject_reference: str,
    at: datetime,
) -> DecisionRead:
    """Return the decision only if it warrants a promotion for this subject.

    Fails closed: the decision must exist in the tenant, be ELIGIBLE, be in
    force at `at` (decided_at <= at < expires_at) and name exactly
    `subject_reference`. Anything else raises `WarrantRefused` carrying a
    closed reason. `at` is the verifier's own explicit, timezone-aware instant;
    the module never reads a clock here, so the answer is reproducible.
    """
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    expected = _required(subject_reference, "subject reference")
    decision = read_decision(db, scope=scope, decision_id=decision_id)
    if decision is None:
        raise WarrantRefused(WarrantRefusal.NOT_FOUND)
    if decision.subject_reference != expected:
        raise WarrantRefused(WarrantRefusal.SUBJECT_MISMATCH)
    if decision.outcome is not DecisionOutcome.ELIGIBLE:
        raise WarrantRefused(WarrantRefusal.NOT_ELIGIBLE)
    if at < decision.decided_at:
        raise WarrantRefused(WarrantRefusal.NOT_YET_EFFECTIVE)
    if at >= decision.expires_at:
        raise WarrantRefused(WarrantRefusal.EXPIRED)
    return decision


__all__ = [
    "open_qualification",
    "read_decision",
    "record_decision",
    "record_evidence",
    "verify_warrant",
]
