"""Qualification decisions; callers own authorization and transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_qualification.contracts import (
    Conflict,
    OpenQualification,
    RecordDecision,
    RecordEvidence,
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


__all__ = ["open_qualification", "record_decision", "record_evidence"]
