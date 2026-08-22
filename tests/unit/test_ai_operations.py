"""Provider-neutral AI policy, attempt and advisory-insight lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_ai_operations import (
    AIOperationRefused,
    AttemptInput,
    InsightInput,
    acknowledge_insight,
    activate_policy_version,
    create_insight,
    create_policy,
    publish_policy_version,
    record_attempt,
    start_operation,
)
from dotmac_ai_operations.models import TENANT_MODELS
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_aiops": None}},
    )
    Base.metadata.create_all(
        engine, tables=[Tenant.__table__, *(m.__table__ for m in TENANT_MODELS)]
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session):
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(row)
    db.flush()
    return row


def test_policy_version_and_operation_emit_provider_neutral_intent(db: Session) -> None:
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("transcription", "classification"),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, intent = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="call:42",
        policy_version_id=version.id,
        operation_kind="transcription",
        input_ref="file:opaque",
        input_digest="a" * 64,
        started_at=at,
    )
    assert intent.capability == "ai.transcription.execute"
    assert intent.operation_id == operation.id and not hasattr(intent, "provider")
    with pytest.raises(AIOperationRefused, match="allowed"):
        start_operation(
            db,
            tenant_id=tenant.id,
            operation_key="call:43",
            policy_version_id=version.id,
            operation_kind="summarization",
            input_ref="file:2",
            input_digest="b" * 64,
            started_at=at,
        )


def test_attempt_observations_are_immutable_and_insights_remain_advisory(
    db: Session,
) -> None:
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:7",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:7",
        input_digest="a" * 64,
        started_at=at,
    )
    attempt = record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=AttemptInput(
            "attempt:1",
            "succeeded",
            "output:7",
            "b" * 64,
            "provider-observed",
            "model-observed",
            "request-observed",
            None,
        ),
        observed_at=at,
    )
    assert operation.status == "succeeded"
    assert (
        record_attempt(
            db,
            tenant_id=tenant.id,
            operation_id=operation.id,
            command=AttemptInput(
                "attempt:1",
                "succeeded",
                "output:7",
                "b" * 64,
                "provider-observed",
                "model-observed",
                "request-observed",
                None,
            ),
            observed_at=at,
        ).id
        == attempt.id
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            "insight:7", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    assert insight.status == "advisory"
    acknowledge_insight(
        db,
        tenant_id=tenant.id,
        insight_id=insight.id,
        actor_ref="agent:2",
        action_evidence_ref="ticket:42",
        acknowledged_at=at,
    )
    assert (
        insight.status == "acknowledged" and insight.action_evidence_ref == "ticket:42"
    )
