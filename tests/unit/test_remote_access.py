"""Approval-bound finite remote-access behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_remote_access import AccessRefused, RemoteAccessRequestInput, admit_request, create_request, expire_grants, record_observation, revoke_grant
from dotmac_remote_access.models import TENANT_MODELS, RemoteAccessObservation
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", execution_options={"schema_translate_map": {"mod_remoteaccess": None}})
    Base.metadata.create_all(engine, tables=[Tenant.__table__, *(m.__table__ for m in TENANT_MODELS)])
    session = Session(engine)
    try:
        yield session
    finally:
        session.close(); engine.dispose()


def _tenant(db: Session):
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant"); db.add(row); db.flush(); return row


def test_admission_binds_exact_approval_and_emits_provider_neutral_intent(db: Session) -> None:
    tenant = _tenant(db); at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    request = create_request(db, tenant_id=tenant.id, command=RemoteAccessRequestInput("access:1", "ont:opaque:7", "diagnose optical levels", ("diagnostics.read",), "operator:2"), requested_at=at)
    with pytest.raises(AccessRefused, match="digest"):
        admit_request(db, tenant_id=tenant.id, request_id=request.id, approval_evidence_ref="approval:1", approved_request_digest="f" * 64, duration=timedelta(minutes=30), admitted_at=at)
    grant, intent = admit_request(db, tenant_id=tenant.id, request_id=request.id, approval_evidence_ref="approval:1", approved_request_digest=request.request_digest, duration=timedelta(minutes=30), admitted_at=at)
    assert intent.action == "activate" and intent.target_ref == "ont:opaque:7"
    assert intent.grant_id == grant.id and not hasattr(intent, "provider")


def test_expiry_and_revocation_fail_closed_and_observations_are_idempotent(db: Session) -> None:
    tenant = _tenant(db); at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    first = create_request(db, tenant_id=tenant.id, command=RemoteAccessRequestInput("a:1", "device:1", "repair", ("shell.read",), "op:1"), requested_at=at)
    grant, _ = admit_request(db, tenant_id=tenant.id, request_id=first.id, approval_evidence_ref="approval", approved_request_digest=first.request_digest, duration=timedelta(minutes=5), admitted_at=at)
    intents = expire_grants(db, tenant_id=tenant.id, as_of=at + timedelta(minutes=6))
    assert intents[0].action == "revoke" and grant.status == "expired"
    obs = record_observation(db, tenant_id=tenant.id, grant_id=grant.id, observation_key="receipt:1", action="revoke", outcome="applied", observed_at=at + timedelta(minutes=7), evidence_ref="network:receipt:1")
    assert record_observation(db, tenant_id=tenant.id, grant_id=grant.id, observation_key="receipt:1", action="revoke", outcome="applied", observed_at=at + timedelta(minutes=7), evidence_ref="network:receipt:1").id == obs.id
    assert len(db.scalars(select(RemoteAccessObservation)).all()) == 1
    second = create_request(db, tenant_id=tenant.id, command=RemoteAccessRequestInput("a:2", "device:2", "repair", ("shell.read",), "op:1"), requested_at=at)
    active, _ = admit_request(db, tenant_id=tenant.id, request_id=second.id, approval_evidence_ref="approval:2", approved_request_digest=second.request_digest, duration=timedelta(minutes=5), admitted_at=at)
    intent = revoke_grant(db, tenant_id=tenant.id, grant_id=active.id, revoked_at=at + timedelta(minutes=1), actor_ref="op:2", reason="done")
    assert active.status == "revoked" and intent.action == "revoke"
