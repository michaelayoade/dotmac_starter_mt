"""Purpose-bound finite-grant behavior for Support Access."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.models import Base
from dotmac_support_access import (
    AccessMode,
    AccessRefused,
    SupportRequestInput,
    admit_request,
    create_request,
    expire_grants,
    revoke_grant,
)
from dotmac_support_access.models import PLATFORM_MODELS, SupportAccessEvent, SupportAccessGrant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", execution_options={"schema_translate_map": {"mod_supportaccess": None}})
    Base.metadata.create_all(engine, tables=[m.__table__ for m in PLATFORM_MODELS])
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _request(db: Session, *, key: str = "request:1"):
    return create_request(db, SupportRequestInput(key, "case:42", "diagnose packet loss", "deployment:7", "supporter:9", ("subscriber.read", "session.inspect")), requested_at=datetime(2026, 8, 21, 8, tzinfo=UTC))


def test_admission_binds_exact_approval_and_returns_no_credential(db: Session) -> None:
    request = _request(db)
    grant = admit_request(db, request_id=request.id, approval_evidence_ref="approval:8", approved_request_digest=request.request_digest, mode=AccessMode.CONSENT, duration=timedelta(minutes=30), admitted_at=datetime(2026, 8, 21, 8, 5, tzinfo=UTC), consent_evidence_ref="consent:2")
    descriptor = grant.descriptor()
    assert descriptor.purpose == "diagnose packet loss"
    assert descriptor.case_ref == "case:42"
    assert descriptor.capabilities == ("session.inspect", "subscriber.read")
    assert not hasattr(descriptor, "token")
    assert not hasattr(descriptor, "credential")
    with pytest.raises(AccessRefused, match="already admitted"):
        admit_request(db, request_id=request.id, approval_evidence_ref="approval:8", approved_request_digest=request.request_digest, mode=AccessMode.CONSENT, duration=timedelta(minutes=10), admitted_at=datetime(2026, 8, 21, 8, 6, tzinfo=UTC), consent_evidence_ref="consent:2")


def test_changed_content_break_glass_ceiling_and_missing_consent_fail_closed(db: Session) -> None:
    request = _request(db)
    admitted_at = datetime(2026, 8, 21, 8, 5, tzinfo=UTC)
    with pytest.raises(AccessRefused, match="digest"):
        admit_request(db, request_id=request.id, approval_evidence_ref="approval", approved_request_digest="f" * 64, mode=AccessMode.CONSENT, duration=timedelta(minutes=5), admitted_at=admitted_at, consent_evidence_ref="consent")
    with pytest.raises(AccessRefused, match="consent"):
        admit_request(db, request_id=request.id, approval_evidence_ref="approval", approved_request_digest=request.request_digest, mode=AccessMode.CONSENT, duration=timedelta(minutes=5), admitted_at=admitted_at)
    with pytest.raises(AccessRefused, match="break-glass"):
        admit_request(db, request_id=request.id, approval_evidence_ref="approval", approved_request_digest=request.request_digest, mode=AccessMode.BREAK_GLASS, duration=timedelta(minutes=16), admitted_at=admitted_at, break_glass_reason="incident")


def test_revoke_is_immediate_and_expiry_never_renews(db: Session) -> None:
    request = _request(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    grant = admit_request(db, request_id=request.id, approval_evidence_ref="approval", approved_request_digest=request.request_digest, mode=AccessMode.BREAK_GLASS, duration=timedelta(minutes=10), admitted_at=at, break_glass_reason="restore service")
    revoke_grant(db, grant_id=grant.id, revoked_at=at + timedelta(minutes=1), actor_ref="operator:2", reason="case resolved")
    assert grant.status == "revoked"
    second = _request(db, key="request:2")
    expiring = admit_request(db, request_id=second.id, approval_evidence_ref="approval:2", approved_request_digest=second.request_digest, mode=AccessMode.CONSENT, duration=timedelta(minutes=5), admitted_at=at, consent_evidence_ref="consent:2")
    assert expire_grants(db, as_of=at + timedelta(minutes=6)) == 1
    assert expiring.status == "expired"
    assert db.scalar(select(SupportAccessGrant).where(SupportAccessGrant.id == expiring.id)) is expiring
    assert len(db.scalars(select(SupportAccessEvent)).all()) >= 4

