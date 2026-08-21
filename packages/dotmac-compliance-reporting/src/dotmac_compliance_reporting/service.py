"""Flush-only exact-pack and filing lifecycle owner."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from uuid import UUID

from dotmac_compliance_reporting.contracts import AcknowledgementInput, EvidenceSectionInput, SectionState
from dotmac_compliance_reporting.models import ClassificationRevision, EvidencePack, EvidenceSection, FilingSubmission, RegulatorAcknowledgement, ReportingObligation
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class ComplianceRefused(ValueError):
    """A reporting transition cannot preserve its evidence contract."""


def _aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None: raise ValueError(f"{name} must be timezone-aware")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def create_obligation(db: Session, *, tenant_id: UUID, code: str, jurisdiction: str, title: str) -> ReportingObligation:
    if not code.strip() or not jurisdiction.strip() or not title.strip(): raise ComplianceRefused("code, jurisdiction and title are required")
    row = ReportingObligation(tenant_id=tenant_id, code=code, jurisdiction=jurisdiction, title=title, active=True); db.add(row); db.flush(); return row


def publish_classification(db: Session, *, tenant_id: UUID, obligation_id: UUID, section_codes: tuple[str, ...], effective_from: date, published_at: datetime) -> ClassificationRevision:
    _aware(published_at, "published_at"); obligation = db.scalar(select(ReportingObligation).where(ReportingObligation.tenant_id == tenant_id, ReportingObligation.id == obligation_id, ReportingObligation.active.is_(True)))
    codes = sorted(set(section_codes))
    if obligation is None or not codes: raise ComplianceRefused("active obligation and section vocabulary are required")
    version = int(db.scalar(select(func.max(ClassificationRevision.version)).where(ClassificationRevision.tenant_id == tenant_id, ClassificationRevision.obligation_id == obligation_id)) or 0) + 1
    digest = _digest({"obligation": obligation.code, "version": version, "sections": codes, "effective_from": effective_from.isoformat()})
    row = ClassificationRevision(tenant_id=tenant_id, obligation_id=obligation_id, version=version, section_codes=codes, content_digest=digest, effective_from=effective_from, published_at=published_at); db.add(row); db.flush(); return row


def assemble_pack(db: Session, *, tenant_id: UUID, obligation_id: UUID, classification_revision_id: UUID, period_start: date, period_end: date, sections: tuple[EvidenceSectionInput, ...], assembled_at: datetime) -> EvidencePack:
    _aware(assembled_at, "assembled_at")
    if period_end < period_start: raise ComplianceRefused("reporting period is inverted")
    classification = db.scalar(select(ClassificationRevision).where(ClassificationRevision.tenant_id == tenant_id, ClassificationRevision.id == classification_revision_id, ClassificationRevision.obligation_id == obligation_id))
    if classification is None: raise ComplianceRefused("classification revision not found")
    by_code = {section.section_code: section for section in sections}
    if set(by_code) != set(classification.section_codes) or len(by_code) != len(sections): raise ComplianceRefused("pack must state every classified section exactly once")
    snapshot = []
    for code in classification.section_codes:
        section = by_code[code]
        if section.state is SectionState.PRESENT:
            if not section.evidence_ref or not section.evidence_digest or section.unavailable_reason: raise ComplianceRefused("present evidence needs exact reference/digest only")
        elif section.evidence_ref or section.evidence_digest or not section.unavailable_reason:
            raise ComplianceRefused("missing evidence must never fabricate a reference or digest")
        snapshot.append({"section_code": code, "source_owner": section.source_owner, "state": section.state.value, "evidence_ref": section.evidence_ref, "evidence_digest": section.evidence_digest, "unavailable_reason": section.unavailable_reason})
    pack_digest = _digest({"classification_digest": classification.content_digest, "period_start": period_start.isoformat(), "period_end": period_end.isoformat(), "sections": snapshot})
    pack = EvidencePack(tenant_id=tenant_id, obligation_id=obligation_id, classification_revision_id=classification.id, period_start=period_start, period_end=period_end, pack_digest=pack_digest, status="assembled", assembled_at=assembled_at); db.add(pack); db.flush()
    for section in snapshot: db.add(EvidenceSection(tenant_id=tenant_id, pack_id=pack.id, **section))
    db.flush(); db.refresh(pack, attribute_names=["sections"]); return pack


def submit_pack(db: Session, *, tenant_id: UUID, pack_id: UUID, submitted_pack_digest: str, submission_ref: str, submitted_at: datetime) -> FilingSubmission:
    _aware(submitted_at, "submitted_at"); pack = db.scalar(select(EvidencePack).where(EvidencePack.tenant_id == tenant_id, EvidencePack.id == pack_id))
    if pack is None or pack.status != "assembled": raise ComplianceRefused("assembled pack not found")
    if pack.pack_digest != submitted_pack_digest: raise ComplianceRefused("submission digest does not bind the exact pack")
    row = FilingSubmission(tenant_id=tenant_id, pack_id=pack.id, submitted_pack_digest=submitted_pack_digest, submission_ref=submission_ref, status="submitted", submitted_at=submitted_at); pack.status = "submitted"; db.add(row); db.flush(); return row


def acknowledge_submission(db: Session, *, tenant_id: UUID, submission_id: UUID, command: AcknowledgementInput) -> RegulatorAcknowledgement:
    _aware(command.acknowledged_at, "acknowledged_at")
    if command.outcome not in {"accepted", "rejected"}: raise ComplianceRefused("acknowledgement outcome must be accepted or rejected")
    submission = db.scalar(select(FilingSubmission).where(FilingSubmission.tenant_id == tenant_id, FilingSubmission.id == submission_id))
    if submission is None or submission.status != "submitted": raise ComplianceRefused("open submission not found")
    row = RegulatorAcknowledgement(tenant_id=tenant_id, submission_id=submission.id, acknowledgement_key=command.acknowledgement_key, outcome=command.outcome, acknowledged_at=command.acknowledged_at, evidence_ref=command.evidence_ref); submission.status = command.outcome; db.add(row); db.flush(); return row

__all__ = ["ComplianceRefused", "acknowledge_submission", "assemble_pack", "create_obligation", "publish_classification", "submit_pack"]
