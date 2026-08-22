"""Flush-only provider-neutral AI policy and evidence owner."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_ai_operations.contracts import AIOperationIntent, AttemptInput, InsightInput
from dotmac_ai_operations.models import (
    AIExecutionAttempt,
    AIInsight,
    AIOperation,
    AIPolicy,
    AIPolicyVersion,
)


class AIOperationRefused(ValueError):
    """An AI operation cannot preserve the published policy/evidence contract."""


def _aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def create_policy(db: Session, *, tenant_id: UUID, code: str, title: str) -> AIPolicy:
    if not code.strip() or not title.strip():
        raise AIOperationRefused("policy code and title are required")
    row = AIPolicy(tenant_id=tenant_id, code=code, title=title, active=True)
    db.add(row)
    db.flush()
    return row


def publish_policy_version(
    db: Session,
    *,
    tenant_id: UUID,
    policy_id: UUID,
    allowed_operation_kinds: tuple[str, ...],
    input_contract_ref: str,
    published_at: datetime,
) -> AIPolicyVersion:
    _aware(published_at, "published_at")
    policy = db.scalar(
        select(AIPolicy).where(
            AIPolicy.tenant_id == tenant_id,
            AIPolicy.id == policy_id,
            AIPolicy.active.is_(True),
        )
    )
    kinds = sorted(set(allowed_operation_kinds))
    if policy is None or not kinds or not input_contract_ref.strip():
        raise AIOperationRefused(
            "active policy, operation kinds and input contract are required"
        )
    version = (
        int(
            db.scalar(
                select(func.max(AIPolicyVersion.version)).where(
                    AIPolicyVersion.tenant_id == tenant_id,
                    AIPolicyVersion.policy_id == policy_id,
                )
            )
            or 0
        )
        + 1
    )
    digest = _digest(
        {
            "policy_code": policy.code,
            "version": version,
            "operation_kinds": kinds,
            "input_contract_ref": input_contract_ref,
        }
    )
    row = AIPolicyVersion(
        tenant_id=tenant_id,
        policy_id=policy_id,
        version=version,
        allowed_operation_kinds=kinds,
        input_contract_ref=input_contract_ref,
        policy_digest=digest,
        active=False,
        published_at=published_at,
    )
    db.add(row)
    db.flush()
    return row


def activate_policy_version(
    db: Session, *, tenant_id: UUID, version_id: UUID, activated_at: datetime
) -> AIPolicyVersion:
    _aware(activated_at, "activated_at")
    version = db.scalar(
        select(AIPolicyVersion).where(
            AIPolicyVersion.tenant_id == tenant_id, AIPolicyVersion.id == version_id
        )
    )
    if version is None:
        raise AIOperationRefused("policy version not found")
    for row in db.scalars(
        select(AIPolicyVersion).where(
            AIPolicyVersion.tenant_id == tenant_id,
            AIPolicyVersion.policy_id == version.policy_id,
            AIPolicyVersion.active.is_(True),
        )
    ).all():
        row.active = False
    version.active = True
    version.activated_at = activated_at
    db.flush()
    return version


def start_operation(
    db: Session,
    *,
    tenant_id: UUID,
    operation_key: str,
    policy_version_id: UUID,
    operation_kind: str,
    input_ref: str,
    input_digest: str,
    started_at: datetime,
) -> tuple[AIOperation, AIOperationIntent]:
    _aware(started_at, "started_at")
    version = db.scalar(
        select(AIPolicyVersion).where(
            AIPolicyVersion.tenant_id == tenant_id,
            AIPolicyVersion.id == policy_version_id,
            AIPolicyVersion.active.is_(True),
        )
    )
    if version is None:
        raise AIOperationRefused("active policy version not found")
    if operation_kind not in version.allowed_operation_kinds:
        raise AIOperationRefused("operation kind is not allowed by the active policy")
    fingerprint = _digest(
        {
            "policy_version_id": str(policy_version_id),
            "operation_kind": operation_kind,
            "input_ref": input_ref,
            "input_digest": input_digest,
        }
    )
    existing = db.scalar(
        select(AIOperation).where(
            AIOperation.tenant_id == tenant_id,
            AIOperation.operation_key == operation_key,
        )
    )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise AIOperationRefused("operation key reused with different content")
        return existing, _intent(existing, version.policy_digest)
    operation = AIOperation(
        tenant_id=tenant_id,
        operation_key=operation_key,
        request_fingerprint=fingerprint,
        policy_version_id=version.id,
        operation_kind=operation_kind,
        input_ref=input_ref,
        input_digest=input_digest,
        status="pending",
        started_at=started_at,
    )
    db.add(operation)
    db.flush()
    return operation, _intent(operation, version.policy_digest)


def _intent(operation: AIOperation, policy_digest: str) -> AIOperationIntent:
    return AIOperationIntent(
        f"ai-operation:{operation.id}",
        operation.id,
        f"ai.{operation.operation_kind}.execute",
        operation.input_ref,
        operation.input_digest,
        policy_digest,
    )


def record_attempt(
    db: Session,
    *,
    tenant_id: UUID,
    operation_id: UUID,
    command: AttemptInput,
    observed_at: datetime,
) -> AIExecutionAttempt:
    _aware(observed_at, "observed_at")
    if command.outcome not in {"succeeded", "failed"}:
        raise AIOperationRefused("attempt outcome must be succeeded or failed")
    if command.outcome == "succeeded" and (
        not command.output_ref or not command.output_digest
    ):
        raise AIOperationRefused(
            "successful attempt requires output reference and digest"
        )
    digest = _digest(
        [
            str(operation_id),
            command.attempt_key,
            command.outcome,
            command.output_ref,
            command.output_digest,
            command.provider_observation,
            command.model_observation,
            command.request_observation,
            command.error_code,
        ]
    )
    existing = db.scalar(
        select(AIExecutionAttempt).where(
            AIExecutionAttempt.tenant_id == tenant_id,
            AIExecutionAttempt.attempt_key == command.attempt_key,
        )
    )
    if existing:
        if existing.observation_digest != digest:
            raise AIOperationRefused("attempt key reused with different observation")
        return existing
    operation = db.scalar(
        select(AIOperation).where(
            AIOperation.tenant_id == tenant_id, AIOperation.id == operation_id
        )
    )
    if operation is None or operation.status not in {"pending", "failed"}:
        raise AIOperationRefused("operation is not awaiting an attempt")
    row = AIExecutionAttempt(
        tenant_id=tenant_id,
        operation_id=operation.id,
        attempt_key=command.attempt_key,
        observation_digest=digest,
        outcome=command.outcome,
        output_ref=command.output_ref,
        output_digest=command.output_digest,
        provider_observation=command.provider_observation,
        model_observation=command.model_observation,
        request_observation=command.request_observation,
        error_code=command.error_code,
        observed_at=observed_at,
    )
    operation.status = command.outcome
    operation.completed_at = observed_at if command.outcome == "succeeded" else None
    db.add(row)
    db.flush()
    return row


def create_insight(
    db: Session,
    *,
    tenant_id: UUID,
    operation_id: UUID,
    command: InsightInput,
    created_at: datetime,
) -> AIInsight:
    _aware(created_at, "created_at")
    operation = db.scalar(
        select(AIOperation).where(
            AIOperation.tenant_id == tenant_id,
            AIOperation.id == operation_id,
            AIOperation.status == "succeeded",
        )
    )
    matching = db.scalar(
        select(AIExecutionAttempt.id).where(
            AIExecutionAttempt.tenant_id == tenant_id,
            AIExecutionAttempt.operation_id == operation_id,
            AIExecutionAttempt.output_digest == command.source_output_digest,
            AIExecutionAttempt.outcome == "succeeded",
        )
    )
    if operation is None or matching is None:
        raise AIOperationRefused("insight must bind a successful observed output")
    if command.confidence is not None and not 0 <= command.confidence <= 1:
        raise AIOperationRefused("confidence must be between zero and one")
    row = AIInsight(
        tenant_id=tenant_id,
        operation_id=operation_id,
        insight_key=command.insight_key,
        insight_type=command.insight_type,
        advisory_value=command.advisory_value,
        confidence=command.confidence,
        source_output_digest=command.source_output_digest,
        status="advisory",
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def acknowledge_insight(
    db: Session,
    *,
    tenant_id: UUID,
    insight_id: UUID,
    actor_ref: str,
    action_evidence_ref: str | None,
    acknowledged_at: datetime,
) -> AIInsight:
    _aware(acknowledged_at, "acknowledged_at")
    insight = db.scalar(
        select(AIInsight).where(
            AIInsight.tenant_id == tenant_id,
            AIInsight.id == insight_id,
            AIInsight.status == "advisory",
        )
    )
    if insight is None or not actor_ref.strip():
        raise AIOperationRefused("advisory insight and actor are required")
    insight.status = "acknowledged"
    insight.acknowledged_by_ref = actor_ref
    insight.acknowledged_at = acknowledged_at
    insight.action_evidence_ref = action_evidence_ref
    db.flush()
    return insight


__all__ = [
    "AIOperationRefused",
    "acknowledge_insight",
    "activate_policy_version",
    "create_insight",
    "create_policy",
    "publish_policy_version",
    "record_attempt",
    "start_operation",
]
