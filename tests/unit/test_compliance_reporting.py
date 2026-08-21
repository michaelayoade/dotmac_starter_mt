"""Exact-pack binding and anti-fabrication behavior from Sub's NCC pack."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from dotmac_compliance_reporting import AcknowledgementInput, ComplianceRefused, EvidenceSectionInput, SectionState, acknowledge_submission, assemble_pack, create_obligation, publish_classification, submit_pack
from dotmac_compliance_reporting.models import TENANT_MODELS
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", execution_options={"schema_translate_map": {"mod_compliance": None}})
    Base.metadata.create_all(engine, tables=[Tenant.__table__, *(m.__table__ for m in TENANT_MODELS)])
    session = Session(engine)
    try:
        yield session
    finally:
        session.close(); engine.dispose()


def _tenant(db: Session):
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant"); db.add(row); db.flush(); return row


def test_pack_preserves_explicit_missing_sections_and_exact_digests(db: Session) -> None:
    tenant = _tenant(db); at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    obligation = create_obligation(db, tenant_id=tenant.id, code="ncc.monthly", jurisdiction="NG-NCC", title="Monthly return")
    classification = publish_classification(db, tenant_id=tenant.id, obligation_id=obligation.id, section_codes=("complaints", "subscribers"), effective_from=date(2026, 8, 1), published_at=at)
    pack = assemble_pack(db, tenant_id=tenant.id, obligation_id=obligation.id, classification_revision_id=classification.id, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31), sections=(EvidenceSectionInput("complaints", "ticketing", SectionState.PRESENT, "ticketing:aug", "a" * 64, None), EvidenceSectionInput("subscribers", "subscribers", SectionState.MISSING, None, None, "source unavailable")), assembled_at=at)
    assert len(pack.pack_digest) == 64
    assert {section.state for section in pack.sections} == {"present", "missing"}
    with pytest.raises(ComplianceRefused, match="fabricate"):
        assemble_pack(db, tenant_id=tenant.id, obligation_id=obligation.id, classification_revision_id=classification.id, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), sections=(EvidenceSectionInput("complaints", "ticketing", SectionState.MISSING, "fake", "b" * 64, "unavailable"), EvidenceSectionInput("subscribers", "subscribers", SectionState.MISSING, None, None, "unavailable")), assembled_at=at)


def test_submission_and_acknowledgement_bind_the_exact_pack(db: Session) -> None:
    tenant = _tenant(db); at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    obligation = create_obligation(db, tenant_id=tenant.id, code="ncc.monthly", jurisdiction="NG-NCC", title="Monthly return")
    classification = publish_classification(db, tenant_id=tenant.id, obligation_id=obligation.id, section_codes=("complaints",), effective_from=date(2026, 8, 1), published_at=at)
    pack = assemble_pack(db, tenant_id=tenant.id, obligation_id=obligation.id, classification_revision_id=classification.id, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31), sections=(EvidenceSectionInput("complaints", "ticketing", SectionState.PRESENT, "ticketing:aug", "a" * 64, None),), assembled_at=at)
    with pytest.raises(ComplianceRefused, match="digest"):
        submit_pack(db, tenant_id=tenant.id, pack_id=pack.id, submitted_pack_digest="f" * 64, submission_ref="filing:1", submitted_at=at)
    submission = submit_pack(db, tenant_id=tenant.id, pack_id=pack.id, submitted_pack_digest=pack.pack_digest, submission_ref="filing:1", submitted_at=at)
    acknowledgement = acknowledge_submission(db, tenant_id=tenant.id, submission_id=submission.id, command=AcknowledgementInput("receipt:1", "accepted", at, "regulator:receipt:1"))
    assert submission.status == "accepted" and acknowledgement.outcome == "accepted"

