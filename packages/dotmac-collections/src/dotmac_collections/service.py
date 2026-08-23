"""Canonical flush-only writers for tenant and platform Collections facts.

Every command receives the caller's :class:`~sqlalchemy.orm.Session`, mutates
only ``mod_coll`` rows (plus the kernel idempotency ledger), and flushes.  The
kernel boundary remains the only transaction authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from dotmac_kernel.cache import Scope, TenantScope, scope_segment
from dotmac_kernel.idempotency import (
    IdempotentOutcome,
    Operation,
    execute_once,
    execute_once_platform,
    fingerprint_of,
)
from dotmac_kernel.messaging import enqueue_event, enqueue_platform_event
from dotmac_kernel.money import Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_collections import models
from dotmac_collections._validation import require_aware, require_text
from dotmac_collections.actions import (
    ActionApplied,
    ActionDeferred,
    ActionFailed,
    ActionReceipt,
    ActionReceiptConflict,
    CollectionActionRequestedV1,
)
from dotmac_collections.arrangements import PaymentArrangementDraftV1
from dotmac_collections.contracts import AssessCollectionExposureV1
from dotmac_collections.grace import GraceGrantV1
from dotmac_collections.notices import (
    CollectionNoticeRequestedV1,
    NoticeAccepted,
    NoticeFailed,
    NoticeReceipt,
    NoticeUnavailable,
)
from dotmac_collections.policies import (
    PolicyPublicationV1,
    PolicyVersionDraftV1,
    publish_policy_version,
)
from dotmac_collections.receivables import (
    PositionAuthorityMismatch,
    PositionReadOk,
    PositionUnavailable,
    PositionUnknown,
    ReceivableObservationV1,
    ReceivablesReader,
)
from dotmac_collections.timers import (
    STEP_DUE_EVENT_TYPE,
    AlreadyFired,
    Canceled,
    CancelTimerV1,
    CollectionsTimer,
    Current,
    NothingScheduled,
    Stale,
    TimerIdentityV1,
    TimerRequestV1,
    TimerTriggerV1,
)

CaseLifecycle = Literal["active", "paused", "resolved", "cancelled"]

_PolicyRow = models.CollectionPolicy | models.PlatformCollectionPolicy
_PolicyVersionRow = (
    models.CollectionPolicyVersion | models.PlatformCollectionPolicyVersion
)
_PolicyStepRow = models.CollectionPolicyStep | models.PlatformCollectionPolicyStep
_CaseRow = models.CollectionCase | models.PlatformCollectionCase
_ActionRequestRow = (
    models.CollectionActionRequest | models.PlatformCollectionActionRequest
)
_NoticeRequestRow = (
    models.CollectionNoticeRequest | models.PlatformCollectionNoticeRequest
)
_ArrangementRow = models.PaymentArrangement | models.PlatformPaymentArrangement


@dataclass(frozen=True, slots=True)
class _PlaneModels:
    # SQLAlchemy mapped attributes do not type-narrow through mirrored class
    # unions. Keep that dynamic boundary private; public contracts remain typed.
    policy: Any
    policy_version: Any
    policy_step: Any
    case: Any
    case_exposure: Any
    case_transition: Any
    step_attempt: Any
    arrangement: Any
    arrangement_exposure: Any
    arrangement_installment: Any
    arrangement_settlement: Any
    grace: Any
    notice_request: Any
    notice_receipt: Any
    action_request: Any
    action_receipt: Any
    reconciliation: Any


_TENANT = _PlaneModels(
    models.CollectionPolicy,
    models.CollectionPolicyVersion,
    models.CollectionPolicyStep,
    models.CollectionCase,
    models.CollectionCaseExposure,
    models.CollectionCaseTransition,
    models.CollectionStepAttempt,
    models.PaymentArrangement,
    models.PaymentArrangementExposure,
    models.PaymentArrangementInstallment,
    models.PaymentArrangementSettlementReceipt,
    models.CollectionGraceGrant,
    models.CollectionNoticeRequest,
    models.CollectionNoticeReceipt,
    models.CollectionActionRequest,
    models.CollectionActionReceipt,
    models.CollectionReconciliation,
)
_PLATFORM = _PlaneModels(
    models.PlatformCollectionPolicy,
    models.PlatformCollectionPolicyVersion,
    models.PlatformCollectionPolicyStep,
    models.PlatformCollectionCase,
    models.PlatformCollectionCaseExposure,
    models.PlatformCollectionCaseTransition,
    models.PlatformCollectionStepAttempt,
    models.PlatformPaymentArrangement,
    models.PlatformPaymentArrangementExposure,
    models.PlatformPaymentArrangementInstallment,
    models.PlatformPaymentArrangementSettlementReceipt,
    models.PlatformCollectionGraceGrant,
    models.PlatformCollectionNoticeRequest,
    models.PlatformCollectionNoticeReceipt,
    models.PlatformCollectionActionRequest,
    models.PlatformCollectionActionReceipt,
    models.PlatformCollectionReconciliation,
)


def _models(scope: Scope) -> _PlaneModels:
    return _TENANT if isinstance(scope, TenantScope) else _PLATFORM


def _values(scope: Scope, values: dict[str, object]) -> dict[str, object]:
    if isinstance(scope, TenantScope):
        return {"tenant_id": scope.tenant_id, **values}
    return values


def _where_scope(statement: Any, scope: Scope, model: Any) -> Any:
    if isinstance(scope, TenantScope):
        return statement.where(model.tenant_id == scope.tenant_id)
    return statement


def _idempotent(
    db: Session,
    *,
    scope: Scope,
    operation_scope: str,
    key: str,
    fingerprint: str,
    operation: Operation,
    correlation_id: str | None = None,
) -> IdempotentOutcome:
    if isinstance(scope, TenantScope):
        return execute_once(
            db,
            tenant_id=scope.tenant_id,
            scope=operation_scope,
            key=key,
            fingerprint=fingerprint,
            correlation_id=correlation_id,
            operation=operation,
        )
    return execute_once_platform(
        db,
        scope=operation_scope,
        key=key,
        fingerprint=fingerprint,
        correlation_id=correlation_id,
        operation=operation,
    )


def _emit(
    db: Session,
    *,
    scope: Scope,
    event_type: str,
    payload: Mapping[str, object],
    correlation_id: str | None = None,
) -> None:
    """Write one consequence intent in the caller-owned transaction."""

    if isinstance(scope, TenantScope):
        enqueue_event(
            db,
            tenant_id=scope.tenant_id,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )
    else:
        enqueue_platform_event(
            db,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )


class CollectionsServiceError(ValueError):
    """A closed, caller-visible Collections refusal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CollectionsConflict(CollectionsServiceError):
    """Persisted evidence conflicts with the supplied immutable contract."""


class CollectionsNotFound(CollectionsServiceError):
    """A tenant-scoped owner row was not found."""


@dataclass(frozen=True, slots=True)
class CreateCollectionPolicyV1:
    policy_id: UUID
    scope: Scope
    policy_code: str
    description: str

    def __post_init__(self) -> None:
        require_text("policy_code", self.policy_code)
        require_text("description", self.description)


@dataclass(frozen=True, slots=True)
class PolicyWriteResult:
    policy_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class PolicyVersionWriteResult:
    policy_version_id: UUID
    version_fingerprint: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class AssessmentBlocked:
    reason_code: str
    retry_after: datetime | None


@dataclass(frozen=True, slots=True)
class AssessmentNoCase:
    reason_code: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class CaseAssessed:
    case_id: UUID
    lifecycle: CaseLifecycle
    source_version: int
    position_fingerprint: str
    opened: bool
    replayed: bool


CaseAssessmentResult = AssessmentBlocked | AssessmentNoCase | CaseAssessed


@dataclass(frozen=True, slots=True)
class RequestWriteResult:
    request_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class ReceiptWriteResult:
    request_id: UUID
    receipt_fingerprint: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ProcessCollectionStepDueV1:
    scope: Scope
    trigger: TimerTriggerV1
    processed_at: datetime

    def __post_init__(self) -> None:
        require_aware("processed_at", self.processed_at)
        identity = self.trigger.identity
        if identity.scope != self.scope:
            raise ValueError("timer trigger belongs to another scope")
        if (
            identity.owner != "collections.case"
            or identity.entity_kind != "collection_case"
            or identity.purpose != "next_step"
            or self.trigger.output_event_type != STEP_DUE_EVENT_TYPE
        ):
            raise ValueError("timer trigger is not a Collections step wake-up")


@dataclass(frozen=True, slots=True)
class StepDueIgnored:
    case_id: UUID
    reason_code: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class StepDueBlocked:
    case_id: UUID
    reason_code: str
    retry_at: datetime | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class StepRequestWritten:
    case_id: UUID
    policy_step_code: str
    request_kind: Literal["notice", "action"]
    attempt_ordinal: int
    request_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class StepCaseResolved:
    case_id: UUID
    source_version: int
    position_fingerprint: str
    replayed: bool


StepDueResult = StepDueIgnored | StepDueBlocked | StepRequestWritten | StepCaseResolved


@dataclass(frozen=True, slots=True)
class CaseLifecycleCommandV1:
    command_id: UUID
    idempotency_key: str
    scope: Scope
    case_id: UUID
    actor_ref: str
    reason_code: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        require_text("idempotency_key", self.idempotency_key)
        require_text("actor_ref", self.actor_ref)
        require_text("reason_code", self.reason_code)
        require_aware("occurred_at", self.occurred_at)


@dataclass(frozen=True, slots=True)
class ArrangementLifecycleCommandV1:
    command_id: UUID
    idempotency_key: str
    scope: Scope
    arrangement_id: UUID
    reason_code: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        require_text("idempotency_key", self.idempotency_key)
        require_text("reason_code", self.reason_code)
        require_aware("occurred_at", self.occurred_at)


@dataclass(frozen=True, slots=True)
class ArrangementSettlementReceiptV1:
    receipt_id: UUID
    scope: Scope
    arrangement_id: UUID
    source_owner: str
    settlement_ref: str
    source_version: int
    receipt_fingerprint: str
    settled_amount: Money
    settled_at: datetime

    def __post_init__(self) -> None:
        require_text("source_owner", self.source_owner)
        require_text("settlement_ref", self.settlement_ref)
        require_text("receipt_fingerprint", self.receipt_fingerprint)
        if self.source_version < 1:
            raise ValueError("source_version must be positive")
        if not isinstance(self.settled_amount, Money):
            raise TypeError("settled_amount must be Money")
        if not self.settled_amount.is_positive:
            raise ValueError("settled_amount must be positive")
        require_aware("settled_at", self.settled_at)


@dataclass(frozen=True, slots=True)
class ArrangementWriteResult:
    arrangement_id: UUID
    lifecycle: str
    replayed: bool


GraceRecordKind = Literal["grant", "supersession", "revocation"]


@dataclass(frozen=True, slots=True)
class PersistGraceV1:
    grant: GraceGrantV1
    kind: GraceRecordKind = "grant"
    supersedes_grant_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"grant", "supersession", "revocation"}:
            raise ValueError("grace record kind is unsupported")
        if self.kind == "grant" and self.supersedes_grant_id is not None:
            raise ValueError("an initial grant cannot supersede another grant")
        if self.kind != "grant" and self.supersedes_grant_id is None:
            raise ValueError("supersession and revocation require the prior grant")
        if self.kind == "revocation" and self.grant.duration.total_seconds() != 0:
            raise ValueError("a revocation must carry zero duration")


@dataclass(frozen=True, slots=True)
class GraceWriteResult:
    grant_id: UUID
    kind: GraceRecordKind
    replayed: bool


@dataclass(frozen=True, slots=True)
class ReconcileCollectionCaseV1:
    reconciliation_id: UUID
    scope: Scope
    case_id: UUID
    source_owner: str
    exposure_ref: str
    source_version: int
    source_fingerprint: str
    rebuilt_fingerprint: str
    reconciled_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "source_owner",
            "exposure_ref",
            "source_fingerprint",
            "rebuilt_fingerprint",
        ):
            require_text(field_name, cast(str, getattr(self, field_name)))
        if self.source_version < 1:
            raise ValueError("source_version must be positive")
        require_aware("reconciled_at", self.reconciled_at)


@dataclass(frozen=True, slots=True)
class ReconciliationWriteResult:
    reconciliation_id: UUID
    outcome: Literal["match", "drift"]
    replayed: bool


def _json(value: object) -> dict[str, object]:
    encoded = json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise CollectionsServiceError(
            "invalid_evidence", "Collections evidence must encode as an object."
        )
    return cast(dict[str, object], decoded)


def _stored_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round-trip without weakening ingress."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _lock_key(*parts: object) -> str:
    return "".join(f"{len(str(part))}:{part}" for part in parts)


def _serialize_identity(db: Session, scope: Scope, *parts: object) -> None:
    """Serialize even first-row decisions without inventing a lock table."""
    if db.get_bind().dialect.name != "postgresql":
        return
    key = _lock_key("collections", scope_segment(scope), *parts)
    db.scalar(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


def _policy(
    db: Session,
    scope: Scope,
    *,
    policy_id: UUID | None = None,
    code: str | None = None,
) -> _PolicyRow | None:
    model = _models(scope).policy
    statement = _where_scope(select(model), scope, model)
    if policy_id is not None:
        statement = statement.where(model.id == policy_id)
    if code is not None:
        statement = statement.where(model.policy_code == code)
    return cast(_PolicyRow | None, db.execute(statement).scalar_one_or_none())


def _policy_version(
    db: Session,
    scope: Scope,
    policy_version_id: UUID,
    *,
    lock: bool = False,
) -> _PolicyVersionRow:
    model = _models(scope).policy_version
    statement = _where_scope(
        select(model).where(model.id == policy_version_id), scope, model
    )
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise CollectionsNotFound(
            "policy_version_not_found", "The policy version was not found."
        )
    return cast(_PolicyVersionRow, row)


def _live_case(
    db: Session,
    scope: Scope,
    *,
    source_owner: str,
    exposure_ref: str,
    lock: bool = False,
) -> _CaseRow | None:
    model = _models(scope).case
    statement = _where_scope(
        select(model).where(
            model.source_owner == source_owner,
            model.exposure_ref == exposure_ref,
            model.lifecycle.in_(("active", "paused")),
        ),
        scope,
        model,
    )
    if lock:
        statement = statement.with_for_update()
    return cast(_CaseRow | None, db.execute(statement).scalar_one_or_none())


def _case(db: Session, scope: Scope, case_id: UUID, *, lock: bool = False) -> _CaseRow:
    model = _models(scope).case
    statement = _where_scope(select(model).where(model.id == case_id), scope, model)
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise CollectionsNotFound(
            "case_not_found", "The collection case was not found."
        )
    return cast(_CaseRow, row)


def _position_document(position: ReceivableObservationV1) -> dict[str, object]:
    def money(value: object) -> dict[str, object]:
        amount = value.amount  # type: ignore[attr-defined]
        currency = value.currency  # type: ignore[attr-defined]
        return {
            "amount": str(amount),
            "currency": currency.code,
            "minor_units": currency.minor_units,
        }

    return {
        "source_owner": position.source_owner,
        "exposure_ref": position.exposure_ref,
        "source_version": position.source_version,
        "state_fingerprint": position.state_fingerprint,
        "subject_ref": position.subject_ref,
        "service_ref": position.service_ref,
        "collection_timing": position.collection_timing,
        "reason_code": position.reason_code,
        "collectible_receivable": money(position.collectible_receivable),
        "service_period_status": position.service_period_status,
        "service_period_starts_at": (
            position.service_period_starts_at.isoformat()
            if position.service_period_starts_at
            else None
        ),
        "service_period_ends_at": (
            position.service_period_ends_at.isoformat()
            if position.service_period_ends_at
            else None
        ),
        "due_at": position.due_at.isoformat() if position.due_at else None,
        "due_date_status": position.due_date_status,
        "financial_state": position.financial_state,
        "source_authority": position.source_authority,
        "projection_mode": position.projection_mode,
        "completeness": position.completeness,
        "completeness_reason_code": position.completeness_reason_code,
        "observed_at": position.observed_at.isoformat(),
    }


def _action_request_document(
    request: CollectionActionRequestedV1,
) -> dict[str, object]:
    return {
        "request_id": str(request.request_id),
        "idempotency_key": request.idempotency_key,
        "case_id": str(request.case_id),
        "policy_version_id": str(request.policy_version_id),
        "policy_step_code": request.policy_step_code,
        "step_attempt_ordinal": request.step_attempt_ordinal,
        "source_owner": request.source_owner,
        "exposure_ref": request.exposure_ref,
        "source_version": request.source_version,
        "position_fingerprint": request.position_fingerprint,
        "subject_ref": request.subject_ref,
        "service_ref": request.service_ref,
        "action_code": request.action_code,
        "effect_scope": request.effect_scope,
        "decision_evidence": _position_document(request.decision_evidence),
        "requested_at": request.requested_at.isoformat(),
    }


def _notice_request_document(
    request: CollectionNoticeRequestedV1,
) -> dict[str, object]:
    return {
        "request_id": str(request.request_id),
        "idempotency_key": request.idempotency_key,
        "case_id": str(request.case_id),
        "policy_version_id": str(request.policy_version_id),
        "policy_step_code": request.policy_step_code,
        "step_attempt_ordinal": request.step_attempt_ordinal,
        "source_owner": request.source_owner,
        "exposure_ref": request.exposure_ref,
        "source_version": request.source_version,
        "position_fingerprint": request.position_fingerprint,
        "subject_ref": request.subject_ref,
        "service_ref": request.service_ref,
        "purpose_code": request.purpose_code,
        "decision_evidence": _position_document(request.decision_evidence),
        "requested_at": request.requested_at.isoformat(),
    }


def _append_transition(
    db: Session,
    *,
    scope: Scope,
    row: _CaseRow,
    from_state: str | None,
    to_state: CaseLifecycle,
    reason_code: str,
    actor_ref: str,
    transitioned_at: datetime,
) -> None:
    model = _models(scope).case_transition
    ordinal = db.scalar(
        _where_scope(
            select(func.coalesce(func.max(model.transition_ordinal), 0)).where(
                model.case_id == row.id
            ),
            scope,
            model,
        )
    )
    db.add(
        model(
            **_values(
                scope,
                {
                    "id": uuid4(),
                    "case_id": row.id,
                    "transition_ordinal": int(ordinal or 0) + 1,
                    "from_state": from_state,
                    "to_state": to_state,
                    "reason_code": reason_code,
                    "actor_ref": actor_ref,
                    "transitioned_at": transitioned_at,
                },
            )
        )
    )


class CollectionPolicyService:
    """Sole persistent writer for policies and immutable publications."""

    @staticmethod
    def create(db: Session, command: CreateCollectionPolicyV1) -> PolicyWriteResult:
        _serialize_identity(db, command.scope, "policy", command.policy_code)
        by_id = _policy(db, command.scope, policy_id=command.policy_id)
        by_code = _policy(db, command.scope, code=command.policy_code)
        existing = by_id or by_code
        if existing is not None:
            if (
                existing.id != command.policy_id
                or existing.policy_code != command.policy_code
                or existing.description != command.description
            ):
                raise CollectionsConflict(
                    "policy_identity_conflict",
                    "The policy identity already names different content.",
                )
            return PolicyWriteResult(existing.id, True)
        model = _models(command.scope).policy
        db.add(
            model(
                **_values(
                    command.scope,
                    {
                        "id": command.policy_id,
                        "policy_code": command.policy_code,
                        "description": command.description,
                    },
                )
            )
        )
        db.flush()
        return PolicyWriteResult(command.policy_id, False)

    @staticmethod
    def publish(
        db: Session,
        *,
        scope: Scope,
        draft: PolicyVersionDraftV1,
        publication: PolicyPublicationV1,
    ) -> PolicyVersionWriteResult:
        published = publish_policy_version(draft, publication)
        plane = _models(scope)
        _serialize_identity(db, scope, "policy", draft.policy_code)
        policy = _policy(db, scope, code=draft.policy_code)
        if policy is None:
            raise CollectionsNotFound("policy_not_found", "The policy was not found.")
        statement = select(plane.policy_version).where(
            (plane.policy_version.id == publication.policy_version_id)
            | (
                (plane.policy_version.policy_id == policy.id)
                & (plane.policy_version.version == publication.version)
            )
        )
        existing = db.execute(
            _where_scope(statement, scope, plane.policy_version)
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.id != publication.policy_version_id
                or existing.version_fingerprint != published.version_fingerprint
            ):
                raise CollectionsConflict(
                    "policy_version_conflict",
                    "The policy version identity already names different content.",
                )
            return PolicyVersionWriteResult(
                existing.id, existing.version_fingerprint, True
            )
        grace = (
            None
            if published.grace is None
            else {
                "duration_seconds": int(published.grace.duration.total_seconds()),
                "anchor": published.grace.anchor,
            }
        )
        version_row = plane.policy_version(
            **_values(
                scope,
                {
                    "id": published.policy_version_id,
                    "policy_id": policy.id,
                    "version": published.version,
                    "reason_code": published.reason_code,
                    "collection_timing": published.collection_timing,
                    "grace": grace,
                    "effective_from": published.effective_from,
                    "actor_ref": published.actor_ref,
                    "publication_reason": published.reason,
                    "published_at": published.published_at,
                    "version_fingerprint": published.version_fingerprint,
                },
            )
        )
        db.add(version_row)
        # The mapped facts intentionally expose no ORM relationships. Flush the
        # parent before its composite-FK steps so PostgreSQL, not unit-of-work
        # guesswork, remains the ordering authority.
        db.flush()
        for step in published.steps:
            db.add(
                plane.policy_step(
                    **_values(
                        scope,
                        {
                            "id": uuid4(),
                            "policy_version_id": published.policy_version_id,
                            "step_code": step.code,
                            "ordinal": step.ordinal,
                            "offset_seconds": int(step.offset.total_seconds()),
                            "offset_anchor": step.offset_anchor,
                            "request_kind": step.request_kind,
                            "action_code": step.action_code,
                            "purpose_code": step.purpose_code,
                            "effect_scope": step.effect_scope,
                            "receipt_required": step.receipt_required,
                            "retry_offsets_seconds": [
                                int(offset.total_seconds())
                                for offset in step.retry_offsets
                            ],
                        },
                    )
                )
            )
        db.flush()
        return PolicyVersionWriteResult(
            published.policy_version_id, published.version_fingerprint, False
        )


def _blocked(result: object) -> AssessmentBlocked | None:
    if isinstance(result, PositionUnavailable):
        return AssessmentBlocked(result.reason_code, result.retry_after)
    if isinstance(result, PositionUnknown):
        return AssessmentBlocked("position_unknown", None)
    if isinstance(result, PositionAuthorityMismatch):
        return AssessmentBlocked("position_authority_mismatch", None)
    return None


def _validate_position(
    command: AssessCollectionExposureV1, position: ReceivableObservationV1
) -> None:
    if position.scope != command.scope:
        raise CollectionsConflict(
            "position_scope_conflict",
            "The receivable position belongs to another scope.",
        )
    values = (
        position.source_owner == command.source_owner,
        position.exposure_ref == command.exposure_ref,
        position.subject_ref == command.subject_ref,
        position.service_ref == command.service_ref,
        position.collection_timing == command.collection_timing,
        position.reason_code == command.reason_code,
    )
    if not all(values):
        raise CollectionsConflict(
            "position_identity_conflict",
            "The receivable position does not match the assessment identity.",
        )


def _assessment_from_result(
    result: Mapping[str, object], *, replayed: bool
) -> CaseAssessmentResult:
    kind = result.get("kind")
    if kind == "blocked":
        retry_after = result.get("retry_after")
        return AssessmentBlocked(
            str(result["reason_code"]),
            datetime.fromisoformat(str(retry_after)) if retry_after else None,
        )
    if kind == "no_case":
        return AssessmentNoCase(str(result["reason_code"]), replayed)
    if kind != "case":
        raise CollectionsServiceError(
            "invalid_replay", "The stored assessment result is invalid."
        )
    lifecycle = str(result["lifecycle"])
    if lifecycle not in {"active", "paused", "resolved", "cancelled"}:
        raise CollectionsServiceError(
            "invalid_replay", "The stored case lifecycle is invalid."
        )
    return CaseAssessed(
        case_id=UUID(str(result["case_id"])),
        lifecycle=cast(CaseLifecycle, lifecycle),
        source_version=int(str(result["source_version"])),
        position_fingerprint=str(result["position_fingerprint"]),
        opened=bool(result["opened"]),
        replayed=replayed,
    )


def _case_timer_identity(scope: Scope, case_id: UUID) -> TimerIdentityV1:
    return TimerIdentityV1(
        scope=scope,
        owner="collections.case",
        entity_kind="collection_case",
        entity_id=str(case_id),
        purpose="next_step",
    )


def _schedule_case_timer(
    db: Session,
    *,
    timer: CollectionsTimer,
    scope: Scope,
    case_id: UUID,
    due_at: datetime,
    recorded_at: datetime,
    expected_source_version: int,
) -> None:
    timer.schedule(
        db,
        TimerRequestV1(
            identity=_case_timer_identity(scope, case_id),
            due_at=due_at,
            recorded_at=recorded_at,
            output_event_type=STEP_DUE_EVENT_TYPE,
            expected_source_version=expected_source_version,
        ),
    )


def _cancel_case_timer(
    db: Session,
    *,
    timer: CollectionsTimer,
    scope: Scope,
    case_id: UUID,
    recorded_at: datetime,
) -> None:
    identity = _case_timer_identity(scope, case_id)
    current = timer.current(db, identity)
    if current is None:
        return
    decision = timer.cancel(
        db,
        CancelTimerV1(
            identity=identity,
            observed_generation=current.generation,
            recorded_at=recorded_at,
        ),
    )
    if isinstance(decision, Stale):
        raise CollectionsConflict(
            "timer_generation_conflict",
            "The collection-case timer changed during cancellation.",
        )


def _step_due_from_result(
    result: Mapping[str, object], *, replayed: bool
) -> StepDueResult:
    kind = str(result.get("kind"))
    case_id = UUID(str(result["case_id"]))
    if kind == "ignored":
        return StepDueIgnored(case_id, str(result["reason_code"]), replayed)
    if kind == "blocked":
        retry_at = result.get("retry_at")
        return StepDueBlocked(
            case_id,
            str(result["reason_code"]),
            datetime.fromisoformat(str(retry_at)) if retry_at else None,
            replayed,
        )
    if kind == "request":
        request_kind = str(result["request_kind"])
        if request_kind not in {"notice", "action"}:
            raise CollectionsServiceError(
                "invalid_replay", "The stored request kind is invalid."
            )
        return StepRequestWritten(
            case_id=case_id,
            policy_step_code=str(result["policy_step_code"]),
            request_kind=cast(Literal["notice", "action"], request_kind),
            attempt_ordinal=int(str(result["attempt_ordinal"])),
            request_id=UUID(str(result["request_id"])),
            replayed=replayed,
        )
    if kind == "resolved":
        return StepCaseResolved(
            case_id=case_id,
            source_version=int(str(result["source_version"])),
            position_fingerprint=str(result["position_fingerprint"]),
            replayed=replayed,
        )
    raise CollectionsServiceError(
        "invalid_replay", "The stored step-due result is invalid."
    )


class CollectionCaseService:
    """Sole writer for case membership and lifecycle transitions."""

    @staticmethod
    def assess(
        db: Session,
        *,
        command: AssessCollectionExposureV1,
        policy_version_id: UUID,
        reader: ReceivablesReader,
        timer: CollectionsTimer | None = None,
        assessed_at: datetime,
    ) -> CaseAssessmentResult:
        require_aware("assessed_at", assessed_at)
        plane = _models(command.scope)
        fingerprint = fingerprint_of(
            {"command": asdict(command), "policy_version_id": policy_version_id}
        )

        def operation(session: Session) -> Mapping[str, object]:
            read = reader.read(
                scope=command.scope,
                source_owner=command.source_owner,
                exposure_ref=command.exposure_ref,
                as_of=assessed_at,
            )
            blocked = _blocked(read)
            if blocked is not None:
                return {
                    "kind": "blocked",
                    "reason_code": blocked.reason_code,
                    "retry_after": (
                        blocked.retry_after.isoformat() if blocked.retry_after else None
                    ),
                }
            if not isinstance(read, PositionReadOk):
                raise CollectionsServiceError(
                    "unsupported_read_result", "The receivables result is unsupported."
                )
            position = read.position
            _validate_position(command, position)
            closed = position.financial_state in {"resolved", "cancelled"}
            closed = closed or position.collectible_receivable.is_zero
            blocker = position.automated_collection_blocker(as_of=assessed_at)
            if blocker is not None and not closed:
                return {
                    "kind": "blocked",
                    "reason_code": blocker,
                    "retry_after": None,
                }
            policy = _policy_version(session, command.scope, policy_version_id)
            if (
                policy.reason_code != command.reason_code
                or policy.collection_timing != command.collection_timing
                or _stored_utc(policy.effective_from) > assessed_at.astimezone(UTC)
            ):
                raise CollectionsConflict(
                    "policy_not_applicable",
                    "The pinned policy version does not apply to this assessment.",
                )
            _serialize_identity(
                session,
                command.scope,
                "case",
                command.source_owner,
                command.exposure_ref,
            )
            row = _live_case(
                session,
                command.scope,
                source_owner=command.source_owner,
                exposure_ref=command.exposure_ref,
                lock=True,
            )
            if closed and row is None:
                return {"kind": "no_case", "reason_code": "no_live_exposure"}
            if row is not None:
                if position.source_version < row.source_version:
                    raise CollectionsConflict(
                        "stale_position", "The receivable position version regressed."
                    )
                if (
                    position.source_version == row.source_version
                    and position.state_fingerprint != row.position_fingerprint
                ):
                    raise CollectionsConflict(
                        "position_fingerprint_conflict",
                        "One receivable version has different fingerprints.",
                    )
            source_advanced = (
                row is None or position.source_version > row.source_version
            )
            opened = row is None
            if row is None:
                row = plane.case(
                    **_values(
                        command.scope,
                        {
                            "id": command.command_id,
                            "policy_version_id": policy_version_id,
                            "source_owner": command.source_owner,
                            "exposure_ref": command.exposure_ref,
                            "subject_ref": command.subject_ref,
                            "service_ref": command.service_ref,
                            "collection_timing": command.collection_timing,
                            "reason_code": command.reason_code,
                            "lifecycle": "active",
                            "source_version": position.source_version,
                            "position_fingerprint": position.state_fingerprint,
                            "opened_at": assessed_at,
                            "resolved_at": None,
                        },
                    )
                )
                session.add(row)
                _append_transition(
                    session,
                    scope=command.scope,
                    row=row,
                    from_state=None,
                    to_state="active",
                    reason_code=command.reason_code,
                    actor_ref=f"trigger:{command.trigger.kind}:{command.trigger.trigger_id}",
                    transitioned_at=assessed_at,
                )
            if position.source_version > row.source_version or opened:
                session.add(
                    plane.case_exposure(
                        **_values(
                            command.scope,
                            {
                                "id": uuid4(),
                                "case_id": row.id,
                                "source_owner": position.source_owner,
                                "exposure_ref": position.exposure_ref,
                                "source_version": position.source_version,
                                "position_fingerprint": position.state_fingerprint,
                                "position_snapshot": _position_document(position),
                                "observed_at": position.observed_at,
                            },
                        )
                    )
                )
            row.source_version = position.source_version
            row.position_fingerprint = position.state_fingerprint
            if closed:
                previous = row.lifecycle
                row.lifecycle = "resolved"
                row.resolved_at = assessed_at
                _append_transition(
                    session,
                    scope=command.scope,
                    row=row,
                    from_state=previous,
                    to_state="resolved",
                    reason_code="receivable_resolved",
                    actor_ref=f"trigger:{command.trigger.kind}:{command.trigger.trigger_id}",
                    transitioned_at=assessed_at,
                )
            session.flush()
            if timer is not None:
                if closed:
                    _cancel_case_timer(
                        session,
                        timer=timer,
                        scope=command.scope,
                        case_id=row.id,
                        recorded_at=assessed_at,
                    )
                elif source_advanced:
                    _schedule_next_policy_step(
                        session,
                        timer=timer,
                        scope=command.scope,
                        case_row=row,
                        recorded_at=assessed_at,
                    )
            return {
                "kind": "case",
                "case_id": str(row.id),
                "lifecycle": row.lifecycle,
                "source_version": row.source_version,
                "position_fingerprint": row.position_fingerprint,
                "opened": opened,
            }

        outcome = _idempotent(
            db,
            scope=command.scope,
            operation_scope="collections.assess_exposure",
            key=command.idempotency_key,
            fingerprint=fingerprint,
            correlation_id=str(command.correlation_id),
            operation=operation,
        )
        return _assessment_from_result(outcome.result, replayed=outcome.replayed)

    @staticmethod
    def transition(
        db: Session,
        *,
        command: CaseLifecycleCommandV1,
        to_state: CaseLifecycle,
        timer: CollectionsTimer | None = None,
    ) -> CaseAssessed:
        allowed: dict[CaseLifecycle, frozenset[CaseLifecycle]] = {
            "active": frozenset({"paused", "resolved", "cancelled"}),
            "paused": frozenset({"active", "resolved", "cancelled"}),
            "resolved": frozenset(),
            "cancelled": frozenset(),
        }
        fingerprint = fingerprint_of({"command": asdict(command), "to_state": to_state})

        def operation(session: Session) -> Mapping[str, object]:
            _serialize_identity(session, command.scope, "case", command.case_id)
            row = _case(session, command.scope, command.case_id, lock=True)
            current = cast(CaseLifecycle, row.lifecycle)
            if to_state == current:
                return {
                    "case_id": str(row.id),
                    "lifecycle": row.lifecycle,
                    "source_version": row.source_version,
                    "position_fingerprint": row.position_fingerprint,
                }
            if to_state not in allowed[current]:
                raise CollectionsConflict(
                    "case_transition_refused",
                    f"Collection case cannot transition from {current} to {to_state}.",
                )
            row.lifecycle = to_state
            row.resolved_at = (
                command.occurred_at if to_state in {"resolved", "cancelled"} else None
            )
            _append_transition(
                session,
                scope=command.scope,
                row=row,
                from_state=current,
                to_state=to_state,
                reason_code=command.reason_code,
                actor_ref=command.actor_ref,
                transitioned_at=command.occurred_at,
            )
            session.flush()
            if timer is not None:
                if to_state == "active":
                    _schedule_next_policy_step(
                        session,
                        timer=timer,
                        scope=command.scope,
                        case_row=row,
                        recorded_at=command.occurred_at,
                    )
                else:
                    _cancel_case_timer(
                        session,
                        timer=timer,
                        scope=command.scope,
                        case_id=row.id,
                        recorded_at=command.occurred_at,
                    )
            return {
                "case_id": str(row.id),
                "lifecycle": row.lifecycle,
                "source_version": row.source_version,
                "position_fingerprint": row.position_fingerprint,
            }

        outcome = _idempotent(
            db,
            scope=command.scope,
            operation_scope="collections.transition_case",
            key=command.idempotency_key,
            fingerprint=fingerprint,
            operation=operation,
        )
        lifecycle = str(outcome.result["lifecycle"])
        if lifecycle not in {"active", "paused", "resolved", "cancelled"}:
            raise CollectionsServiceError(
                "invalid_replay", "The stored case lifecycle is invalid."
            )
        return CaseAssessed(
            case_id=UUID(str(outcome.result["case_id"])),
            lifecycle=cast(CaseLifecycle, lifecycle),
            source_version=int(str(outcome.result["source_version"])),
            position_fingerprint=str(outcome.result["position_fingerprint"]),
            opened=False,
            replayed=outcome.replayed,
        )

    @staticmethod
    def process_step_due(
        db: Session,
        *,
        command: ProcessCollectionStepDueV1,
        reader: ReceivablesReader,
        timer: CollectionsTimer,
    ) -> StepDueResult:
        if command.processed_at < command.trigger.due_at:
            raise CollectionsConflict(
                "timer_fired_early", "A collection timer cannot fire before due_at."
            )
        try:
            case_id = UUID(command.trigger.identity.entity_id)
        except ValueError as exc:
            raise CollectionsServiceError(
                "invalid_timer_case", "The timer entity is not a collection case UUID."
            ) from exc
        fingerprint = fingerprint_of(asdict(command))

        def operation(session: Session) -> Mapping[str, object]:
            _serialize_identity(session, command.scope, "case", case_id)
            row = _case(session, command.scope, case_id, lock=True)
            accepted = timer.accept_trigger(
                session,
                command.trigger,
                accepted_at=command.processed_at,
            )
            if isinstance(accepted, Stale | Canceled | AlreadyFired | NothingScheduled):
                return {
                    "kind": "ignored",
                    "case_id": str(case_id),
                    "reason_code": type(accepted).__name__,
                }
            if not isinstance(accepted, Current):
                raise CollectionsServiceError(
                    "unsupported_timer_decision",
                    "The timer returned an unsupported acceptance decision.",
                )
            if row.lifecycle != "active":
                return {
                    "kind": "ignored",
                    "case_id": str(case_id),
                    "reason_code": "case_not_active",
                }
            expected_source_version = command.trigger.expected_source_version
            if (
                expected_source_version is not None
                and expected_source_version != row.source_version
            ):
                raise CollectionsConflict(
                    "timer_source_version_conflict",
                    "The current timer was scheduled for different case evidence.",
                )
            read = reader.read(
                scope=command.scope,
                source_owner=row.source_owner,
                exposure_ref=row.exposure_ref,
                as_of=command.processed_at,
            )
            blocked = _blocked(read)
            if blocked is not None:
                if blocked.retry_after is not None:
                    _schedule_case_timer(
                        session,
                        timer=timer,
                        scope=command.scope,
                        case_id=case_id,
                        due_at=blocked.retry_after,
                        recorded_at=command.processed_at,
                        expected_source_version=row.source_version,
                    )
                return {
                    "kind": "blocked",
                    "case_id": str(case_id),
                    "reason_code": blocked.reason_code,
                    "retry_at": (
                        blocked.retry_after.isoformat()
                        if blocked.retry_after is not None
                        else None
                    ),
                }
            if not isinstance(read, PositionReadOk):
                raise CollectionsServiceError(
                    "unsupported_read_result", "The receivables result is unsupported."
                )
            position = read.position
            if (
                position.scope != command.scope
                or position.source_owner != row.source_owner
                or position.exposure_ref != row.exposure_ref
                or position.subject_ref != row.subject_ref
                or position.service_ref != row.service_ref
                or position.collection_timing != row.collection_timing
                or position.reason_code != row.reason_code
            ):
                raise CollectionsConflict(
                    "position_identity_conflict",
                    "The current receivable does not match the locked case.",
                )
            if position.source_version < row.source_version:
                raise CollectionsConflict(
                    "stale_position", "The receivable position version regressed."
                )
            if (
                position.source_version == row.source_version
                and position.state_fingerprint != row.position_fingerprint
            ):
                raise CollectionsConflict(
                    "position_fingerprint_conflict",
                    "One receivable version has different fingerprints.",
                )
            if position.source_version > row.source_version:
                session.add(
                    _models(command.scope).case_exposure(
                        **_values(
                            command.scope,
                            {
                                "id": uuid4(),
                                "case_id": row.id,
                                "source_owner": position.source_owner,
                                "exposure_ref": position.exposure_ref,
                                "source_version": position.source_version,
                                "position_fingerprint": position.state_fingerprint,
                                "position_snapshot": _position_document(position),
                                "observed_at": position.observed_at,
                            },
                        )
                    )
                )
                row.source_version = position.source_version
                row.position_fingerprint = position.state_fingerprint
            closed = position.financial_state in {"resolved", "cancelled"}
            closed = closed or position.collectible_receivable.is_zero
            if closed:
                previous = row.lifecycle
                row.lifecycle = "resolved"
                row.resolved_at = command.processed_at
                _append_transition(
                    session,
                    scope=command.scope,
                    row=row,
                    from_state=previous,
                    to_state="resolved",
                    reason_code="receivable_resolved",
                    actor_ref=f"timer:{command.trigger.timer_id}",
                    transitioned_at=command.processed_at,
                )
                session.flush()
                return {
                    "kind": "resolved",
                    "case_id": str(case_id),
                    "source_version": row.source_version,
                    "position_fingerprint": row.position_fingerprint,
                }
            blocker = position.automated_collection_blocker(as_of=command.processed_at)
            if blocker is not None:
                session.flush()
                return {
                    "kind": "blocked",
                    "case_id": str(case_id),
                    "reason_code": blocker,
                    "retry_at": None,
                }
            progress = _step_progress(session, command.scope, row)
            step = progress.step
            if step is None:
                return {
                    "kind": "ignored",
                    "case_id": str(case_id),
                    "reason_code": "policy_ladder_complete",
                }
            if progress.request_pending:
                latest = _latest_step_request(
                    session,
                    scope=command.scope,
                    case_id=case_id,
                    step=step,
                )
                if (
                    latest is not None
                    and _request_receipt(
                        session,
                        scope=command.scope,
                        step=step,
                        request_id=latest.id,
                    )
                    is None
                ):
                    return {
                        "kind": "ignored",
                        "case_id": str(case_id),
                        "reason_code": "owner_receipt_pending",
                    }
            attempt_ordinal = _next_attempt_ordinal(
                session,
                scope=command.scope,
                case_id=case_id,
                step_code=step.step_code,
            )
            request_key = (
                f"collections:{scope_segment(command.scope)}:{case_id}:"
                f"{step.step_code}:{attempt_ordinal}"
            )
            request_id = uuid5(NAMESPACE_URL, request_key)
            if step.request_kind == "action":
                action_request = CollectionActionRequestedV1(
                    request_id=request_id,
                    idempotency_key=request_key,
                    case_id=case_id,
                    policy_version_id=row.policy_version_id,
                    policy_step_code=step.step_code,
                    step_attempt_ordinal=attempt_ordinal,
                    source_owner=position.source_owner,
                    exposure_ref=position.exposure_ref,
                    source_version=position.source_version,
                    position_fingerprint=position.state_fingerprint,
                    subject_ref=position.subject_ref,
                    service_ref=position.service_ref,
                    action_code=str(step.action_code),
                    effect_scope=str(step.effect_scope),
                    decision_evidence=position,
                    requested_at=command.processed_at,
                )
                CollectionActionService.request(
                    session, scope=command.scope, request=action_request
                )
            else:
                notice_request = CollectionNoticeRequestedV1(
                    request_id=request_id,
                    idempotency_key=request_key,
                    case_id=case_id,
                    policy_version_id=row.policy_version_id,
                    policy_step_code=step.step_code,
                    step_attempt_ordinal=attempt_ordinal,
                    source_owner=position.source_owner,
                    exposure_ref=position.exposure_ref,
                    source_version=position.source_version,
                    position_fingerprint=position.state_fingerprint,
                    subject_ref=position.subject_ref,
                    service_ref=position.service_ref,
                    purpose_code=str(step.purpose_code),
                    decision_evidence=position,
                    requested_at=command.processed_at,
                )
                CollectionNoticeService.request(
                    session, scope=command.scope, request=notice_request
                )
            session.flush()
            if not step.receipt_required:
                _schedule_next_policy_step(
                    session,
                    timer=timer,
                    scope=command.scope,
                    case_row=row,
                    recorded_at=command.processed_at,
                )
            return {
                "kind": "request",
                "case_id": str(case_id),
                "policy_step_code": step.step_code,
                "request_kind": step.request_kind,
                "attempt_ordinal": attempt_ordinal,
                "request_id": str(request_id),
            }

        outcome = _idempotent(
            db,
            scope=command.scope,
            operation_scope="collections.process_step_due",
            key=(
                f"timer:{command.trigger.timer_id}:"
                f"generation:{command.trigger.generation}"
            ),
            fingerprint=fingerprint,
            correlation_id=str(command.trigger.timer_id),
            operation=operation,
        )
        return _step_due_from_result(outcome.result, replayed=outcome.replayed)


def _step(
    db: Session,
    scope: Scope,
    *,
    policy_version_id: UUID,
    step_code: str,
) -> _PolicyStepRow:
    model = _models(scope).policy_step
    row = db.execute(
        _where_scope(
            select(model).where(
                model.policy_version_id == policy_version_id,
                model.step_code == step_code,
            ),
            scope,
            model,
        )
    ).scalar_one_or_none()
    if row is None:
        raise CollectionsNotFound(
            "policy_step_not_found", "The policy step was not found."
        )
    return cast(_PolicyStepRow, row)


def _policy_steps(
    db: Session, scope: Scope, policy_version_id: UUID
) -> tuple[_PolicyStepRow, ...]:
    model = _models(scope).policy_step
    rows = db.execute(
        _where_scope(
            select(model)
            .where(model.policy_version_id == policy_version_id)
            .order_by(model.ordinal),
            scope,
            model,
        )
    ).scalars()
    return tuple(cast(_PolicyStepRow, row) for row in rows)


@dataclass(frozen=True, slots=True)
class _StepProgress:
    step: _PolicyStepRow | None
    request_pending: bool
    request_at: datetime | None
    accepted_notice_receipt_at: datetime | None


def _latest_step_request(
    db: Session,
    *,
    scope: Scope,
    case_id: UUID,
    step: _PolicyStepRow,
) -> _ActionRequestRow | _NoticeRequestRow | None:
    plane = _models(scope)
    model = (
        plane.action_request if step.request_kind == "action" else plane.notice_request
    )
    statement = _where_scope(
        select(model)
        .where(model.case_id == case_id, model.policy_step_code == step.step_code)
        .order_by(model.attempt_ordinal.desc())
        .limit(1),
        scope,
        model,
    )
    return cast(_ActionRequestRow | _NoticeRequestRow | None, db.scalar(statement))


def _request_receipt(
    db: Session,
    *,
    scope: Scope,
    step: _PolicyStepRow,
    request_id: UUID,
) -> Any | None:
    plane = _models(scope)
    model = (
        plane.action_receipt if step.request_kind == "action" else plane.notice_receipt
    )
    return db.scalar(
        _where_scope(
            select(model).where(model.request_id == request_id),
            scope,
            model,
        )
    )


def _step_progress(db: Session, scope: Scope, case_row: _CaseRow) -> _StepProgress:
    request_at: datetime | None = None
    accepted_notice_receipt_at: datetime | None = None
    for step in _policy_steps(db, scope, case_row.policy_version_id):
        request = _latest_step_request(
            db,
            scope=scope,
            case_id=case_row.id,
            step=step,
        )
        if request is None:
            return _StepProgress(step, False, request_at, accepted_notice_receipt_at)
        if not step.receipt_required:
            request_at = _stored_utc(request.requested_at)
            continue
        receipt = _request_receipt(
            db,
            scope=scope,
            step=step,
            request_id=request.id,
        )
        accepted_kind = (
            "ActionApplied" if step.request_kind == "action" else "NoticeAccepted"
        )
        if receipt is None or receipt.receipt_kind != accepted_kind:
            return _StepProgress(step, True, request_at, accepted_notice_receipt_at)
        request_at = _stored_utc(request.requested_at)
        if step.request_kind == "notice":
            accepted_notice_receipt_at = _stored_utc(receipt.observed_at)
    return _StepProgress(None, False, request_at, accepted_notice_receipt_at)


def _step_due_at(case_row: _CaseRow, progress: _StepProgress) -> datetime:
    step = progress.step
    if step is None:
        raise CollectionsServiceError(
            "policy_ladder_complete", "The collection policy ladder is complete."
        )
    anchor: datetime | None
    if step.offset_anchor == "exposure_at":
        anchor = _stored_utc(case_row.opened_at)
    elif step.offset_anchor == "request_at":
        anchor = progress.request_at
    else:
        anchor = progress.accepted_notice_receipt_at
    if anchor is None:
        raise CollectionsConflict(
            "policy_anchor_unavailable",
            f"Policy step {step.step_code} has no {step.offset_anchor} anchor.",
        )
    return anchor + timedelta(seconds=step.offset_seconds)


def _schedule_next_policy_step(
    db: Session,
    *,
    timer: CollectionsTimer,
    scope: Scope,
    case_row: _CaseRow,
    recorded_at: datetime,
) -> None:
    progress = _step_progress(db, scope, case_row)
    if progress.step is None or progress.request_pending:
        return
    _schedule_case_timer(
        db,
        timer=timer,
        scope=scope,
        case_id=case_row.id,
        due_at=_step_due_at(case_row, progress),
        recorded_at=recorded_at,
        expected_source_version=case_row.source_version,
    )


def _next_attempt_ordinal(
    db: Session, *, scope: Scope, case_id: UUID, step_code: str
) -> int:
    model = _models(scope).step_attempt
    highest = db.scalar(
        _where_scope(
            select(func.max(model.attempt_ordinal)).where(
                model.case_id == case_id,
                model.policy_step_code == step_code,
            ),
            scope,
            model,
        )
    )
    return 1 if highest is None else int(highest) + 1


def _validate_request_case(
    db: Session,
    scope: Scope,
    request: CollectionActionRequestedV1 | CollectionNoticeRequestedV1,
) -> tuple[_CaseRow, _PolicyStepRow]:
    if request.decision_evidence.scope != scope:
        raise CollectionsConflict(
            "request_scope_conflict", "The decision evidence belongs to another scope."
        )
    row = _case(db, scope, request.case_id, lock=True)
    if row.lifecycle != "active":
        raise CollectionsConflict(
            "case_not_active", "Only an active collection case may request a step."
        )
    if (
        row.policy_version_id != request.policy_version_id
        or row.source_owner != request.source_owner
        or row.exposure_ref != request.exposure_ref
        or row.source_version != request.source_version
        or row.position_fingerprint != request.position_fingerprint
    ):
        raise CollectionsConflict(
            "stale_step_decision",
            "The request does not match the locked case evidence.",
        )
    step = _step(
        db,
        scope,
        policy_version_id=request.policy_version_id,
        step_code=request.policy_step_code,
    )
    if isinstance(request, CollectionActionRequestedV1):
        if step.effect_scope != request.effect_scope:
            raise CollectionsConflict(
                "policy_effect_scope_conflict",
                "The action effect scope is not declared by the policy step.",
            )
        if request.effect_scope == "service" and request.service_ref is None:
            raise CollectionsConflict(
                "service_scope_without_service",
                "A service-scoped action requires a service reference.",
            )
        if request.effect_scope in {"contract", "subject"} and request.service_ref:
            raise CollectionsConflict(
                "consequence_scope_too_broad",
                "One service exposure cannot authorize a broader consequence.",
            )
    elif step.purpose_code != request.purpose_code:
        raise CollectionsConflict(
            "policy_notice_purpose_conflict",
            "The notice purpose is not declared by the policy step.",
        )
    return row, step


def _request_replay(
    existing: _ActionRequestRow | _NoticeRequestRow,
    *,
    request_id: UUID,
    fingerprint: str,
) -> RequestWriteResult:
    if existing.id != request_id or existing.request_fingerprint != fingerprint:
        raise CollectionsConflict(
            "request_identity_conflict",
            "The request identity already names different decision evidence.",
        )
    return RequestWriteResult(existing.id, True)


def _append_attempt(
    db: Session,
    *,
    scope: Scope,
    case_id: UUID,
    policy_step_code: str,
    attempt_ordinal: int,
    request_kind: Literal["notice", "action"],
    request_id: UUID,
    fingerprint: str,
    attempted_at: datetime,
) -> None:
    model = _models(scope).step_attempt
    existing = db.execute(
        _where_scope(
            select(model).where(
                model.case_id == case_id,
                model.policy_step_code == policy_step_code,
                model.attempt_ordinal == attempt_ordinal,
            ),
            scope,
            model,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.request_kind != request_kind
            or existing.request_id != request_id
            or existing.decision_fingerprint != fingerprint
        ):
            raise CollectionsConflict(
                "step_attempt_conflict",
                "One policy-step attempt names different decision evidence.",
            )
        return
    db.add(
        model(
            **_values(
                scope,
                {
                    "id": uuid4(),
                    "case_id": case_id,
                    "policy_step_code": policy_step_code,
                    "attempt_ordinal": attempt_ordinal,
                    "request_kind": request_kind,
                    "request_id": request_id,
                    "decision_fingerprint": fingerprint,
                    "attempted_at": attempted_at,
                },
            )
        )
    )


def _receipt_values(
    receipt: ActionReceipt | NoticeReceipt,
) -> tuple[str, str, str, datetime, dict[str, object]]:
    kind = type(receipt).__name__
    owner_code = receipt.owner_code
    owner_receipt_id = receipt.owner_receipt_id
    if isinstance(receipt, ActionApplied | NoticeAccepted):
        observed_at = (
            receipt.applied_at
            if isinstance(receipt, ActionApplied)
            else receipt.accepted_at
        )
    else:
        observed_at = receipt.observed_at
    return kind, owner_code, owner_receipt_id, observed_at, _json(asdict(receipt))


def _receipt_succeeded(receipt: ActionReceipt | NoticeReceipt) -> bool:
    return isinstance(receipt, ActionApplied | NoticeAccepted)


def _receipt_retry_hint(
    receipt: ActionReceipt | NoticeReceipt,
) -> tuple[bool, datetime | None]:
    if isinstance(receipt, ActionDeferred | NoticeUnavailable):
        return True, receipt.retry_at
    if isinstance(receipt, ActionFailed | NoticeFailed):
        return receipt.retryable, None
    return False, None


def _advance_after_receipt(
    db: Session,
    *,
    scope: Scope,
    request: _ActionRequestRow | _NoticeRequestRow,
    receipt: ActionReceipt | NoticeReceipt,
    observed_at: datetime,
    timer: CollectionsTimer,
) -> None:
    case_row = _case(db, scope, request.case_id, lock=True)
    if case_row.lifecycle != "active":
        return
    step = _step(
        db,
        scope,
        policy_version_id=request.policy_version_id,
        step_code=request.policy_step_code,
    )
    if _receipt_succeeded(receipt):
        _schedule_next_policy_step(
            db,
            timer=timer,
            scope=scope,
            case_row=case_row,
            recorded_at=observed_at,
        )
        return
    retryable, owner_retry_at = _receipt_retry_hint(receipt)
    retry_index = request.attempt_ordinal - 1
    retry_offsets = tuple(int(value) for value in step.retry_offsets_seconds)
    if not retryable or retry_index >= len(retry_offsets):
        return
    retry_at = observed_at + timedelta(seconds=retry_offsets[retry_index])
    if owner_retry_at is not None and owner_retry_at > retry_at:
        retry_at = owner_retry_at
    _schedule_case_timer(
        db,
        timer=timer,
        scope=scope,
        case_id=case_row.id,
        due_at=retry_at,
        recorded_at=observed_at,
        expected_source_version=case_row.source_version,
    )


class CollectionActionService:
    """Sole writer for product-action requests and owner receipts."""

    @staticmethod
    def request(
        db: Session,
        *,
        scope: Scope,
        request: CollectionActionRequestedV1,
    ) -> RequestWriteResult:
        fingerprint = fingerprint_of(asdict(request))
        plane = _models(scope)
        _serialize_identity(db, scope, "case", request.case_id)
        existing = db.execute(
            _where_scope(
                select(plane.action_request).where(
                    (plane.action_request.id == request.request_id)
                    | (plane.action_request.idempotency_key == request.idempotency_key),
                ),
                scope,
                plane.action_request,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _request_replay(
                existing, request_id=request.request_id, fingerprint=fingerprint
            )
        _, step = _validate_request_case(db, scope, request)
        if step.request_kind != "action" or step.action_code != request.action_code:
            raise CollectionsConflict(
                "policy_step_kind_conflict",
                "The policy step does not declare this product action.",
            )
        _append_attempt(
            db,
            scope=scope,
            case_id=request.case_id,
            policy_step_code=request.policy_step_code,
            attempt_ordinal=request.step_attempt_ordinal,
            request_kind="action",
            request_id=request.request_id,
            fingerprint=fingerprint,
            attempted_at=request.requested_at,
        )
        db.add(
            plane.action_request(
                **_values(
                    scope,
                    {
                        "id": request.request_id,
                        "case_id": request.case_id,
                        "idempotency_key": request.idempotency_key,
                        "request_fingerprint": fingerprint,
                        "policy_version_id": request.policy_version_id,
                        "policy_step_code": request.policy_step_code,
                        "attempt_ordinal": request.step_attempt_ordinal,
                        "action_code": request.action_code,
                        "effect_scope": request.effect_scope,
                        "decision_evidence": _position_document(
                            request.decision_evidence
                        ),
                        "requested_at": request.requested_at,
                    },
                )
            )
        )
        db.flush()
        _emit(
            db,
            scope=scope,
            event_type="collections.action.requested.v1",
            payload=_action_request_document(request),
            correlation_id=str(request.request_id),
        )
        return RequestWriteResult(request.request_id, False)

    @staticmethod
    def record_receipt(
        db: Session,
        *,
        scope: Scope,
        receipt: ActionReceipt,
        timer: CollectionsTimer | None = None,
    ) -> ReceiptWriteResult:
        plane = _models(scope)
        _serialize_identity(db, scope, "action_receipt", receipt.request_id)
        request = db.execute(
            _where_scope(
                select(plane.action_request).where(
                    plane.action_request.id == receipt.request_id
                ),
                scope,
                plane.action_request,
            )
        ).scalar_one_or_none()
        if request is None:
            raise CollectionsNotFound(
                "action_request_not_found", "The action request was not found."
            )
        fingerprint = fingerprint_of(asdict(receipt))
        existing = db.execute(
            _where_scope(
                select(plane.action_receipt).where(
                    plane.action_receipt.request_id == receipt.request_id
                ),
                scope,
                plane.action_receipt,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.receipt_fingerprint != fingerprint:
                raise ActionReceiptConflict(
                    "request id has different action receipt evidence"
                )
            return ReceiptWriteResult(receipt.request_id, fingerprint, True)
        kind, owner, owner_receipt, observed_at, evidence = _receipt_values(receipt)
        db.add(
            plane.action_receipt(
                **_values(
                    scope,
                    {
                        "id": uuid4(),
                        "request_id": receipt.request_id,
                        "receipt_kind": kind,
                        "owner_code": owner,
                        "owner_receipt_id": owner_receipt,
                        "receipt_fingerprint": fingerprint,
                        "receipt_evidence": evidence,
                        "observed_at": observed_at,
                    },
                )
            )
        )
        db.flush()
        if timer is not None:
            _advance_after_receipt(
                db,
                scope=scope,
                request=cast(_ActionRequestRow, request),
                receipt=receipt,
                observed_at=observed_at,
                timer=timer,
            )
        return ReceiptWriteResult(receipt.request_id, fingerprint, False)


class CollectionNoticeService:
    """Sole writer for notice requests and delivery-owner receipts."""

    @staticmethod
    def request(
        db: Session,
        *,
        scope: Scope,
        request: CollectionNoticeRequestedV1,
    ) -> RequestWriteResult:
        fingerprint = fingerprint_of(asdict(request))
        plane = _models(scope)
        _serialize_identity(db, scope, "case", request.case_id)
        existing = db.execute(
            _where_scope(
                select(plane.notice_request).where(
                    (plane.notice_request.id == request.request_id)
                    | (plane.notice_request.idempotency_key == request.idempotency_key),
                ),
                scope,
                plane.notice_request,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _request_replay(
                existing, request_id=request.request_id, fingerprint=fingerprint
            )
        _, step = _validate_request_case(db, scope, request)
        if step.request_kind != "notice":
            raise CollectionsConflict(
                "policy_step_kind_conflict", "The policy step is not a notice."
            )
        _append_attempt(
            db,
            scope=scope,
            case_id=request.case_id,
            policy_step_code=request.policy_step_code,
            attempt_ordinal=request.step_attempt_ordinal,
            request_kind="notice",
            request_id=request.request_id,
            fingerprint=fingerprint,
            attempted_at=request.requested_at,
        )
        db.add(
            plane.notice_request(
                **_values(
                    scope,
                    {
                        "id": request.request_id,
                        "case_id": request.case_id,
                        "idempotency_key": request.idempotency_key,
                        "request_fingerprint": fingerprint,
                        "policy_version_id": request.policy_version_id,
                        "policy_step_code": request.policy_step_code,
                        "attempt_ordinal": request.step_attempt_ordinal,
                        "purpose_code": request.purpose_code,
                        "decision_evidence": _position_document(
                            request.decision_evidence
                        ),
                        "requested_at": request.requested_at,
                    },
                )
            )
        )
        db.flush()
        _emit(
            db,
            scope=scope,
            event_type="collections.notice.requested.v1",
            payload=_notice_request_document(request),
            correlation_id=str(request.request_id),
        )
        return RequestWriteResult(request.request_id, False)

    @staticmethod
    def record_receipt(
        db: Session,
        *,
        scope: Scope,
        receipt: NoticeReceipt,
        timer: CollectionsTimer | None = None,
    ) -> ReceiptWriteResult:
        plane = _models(scope)
        _serialize_identity(db, scope, "notice_receipt", receipt.request_id)
        request = db.execute(
            _where_scope(
                select(plane.notice_request).where(
                    plane.notice_request.id == receipt.request_id
                ),
                scope,
                plane.notice_request,
            )
        ).scalar_one_or_none()
        if request is None:
            raise CollectionsNotFound(
                "notice_request_not_found", "The notice request was not found."
            )
        fingerprint = fingerprint_of(asdict(receipt))
        existing = db.execute(
            _where_scope(
                select(plane.notice_receipt).where(
                    plane.notice_receipt.request_id == receipt.request_id
                ),
                scope,
                plane.notice_receipt,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.receipt_fingerprint != fingerprint:
                raise CollectionsConflict(
                    "notice_receipt_conflict",
                    "The notice request has different receipt evidence.",
                )
            return ReceiptWriteResult(receipt.request_id, fingerprint, True)
        kind, owner, owner_receipt, observed_at, evidence = _receipt_values(receipt)
        db.add(
            plane.notice_receipt(
                **_values(
                    scope,
                    {
                        "id": uuid4(),
                        "request_id": receipt.request_id,
                        "receipt_kind": kind,
                        "owner_code": owner,
                        "owner_receipt_id": owner_receipt,
                        "receipt_fingerprint": fingerprint,
                        "receipt_evidence": evidence,
                        "observed_at": observed_at,
                    },
                )
            )
        )
        db.flush()
        if timer is not None:
            _advance_after_receipt(
                db,
                scope=scope,
                request=cast(_NoticeRequestRow, request),
                receipt=receipt,
                observed_at=observed_at,
                timer=timer,
            )
        return ReceiptWriteResult(receipt.request_id, fingerprint, False)


def _arrangement(
    db: Session,
    scope: Scope,
    arrangement_id: UUID,
    *,
    lock: bool = False,
) -> _ArrangementRow:
    model = _models(scope).arrangement
    statement = _where_scope(
        select(model).where(model.id == arrangement_id), scope, model
    )
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise CollectionsNotFound(
            "arrangement_not_found", "The payment arrangement was not found."
        )
    return cast(_ArrangementRow, row)


class PaymentArrangementService:
    """Sole writer for exact arrangement membership and settlement evidence."""

    @staticmethod
    def propose(
        db: Session, draft: PaymentArrangementDraftV1
    ) -> ArrangementWriteResult:
        fingerprint = fingerprint_of(asdict(draft))
        plane = _models(draft.scope)
        _serialize_identity(db, draft.scope, "arrangement", draft.arrangement_id)
        existing = db.execute(
            _where_scope(
                select(plane.arrangement).where(
                    plane.arrangement.id == draft.arrangement_id
                ),
                draft.scope,
                plane.arrangement,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.arrangement_fingerprint != fingerprint:
                raise CollectionsConflict(
                    "arrangement_identity_conflict",
                    "The arrangement identity already names different membership.",
                )
            return ArrangementWriteResult(existing.id, existing.lifecycle, True)
        currency = draft.installments[0].amount.currency
        arrangement_row = plane.arrangement(
            **_values(
                draft.scope,
                {
                    "id": draft.arrangement_id,
                    "arrangement_ref": str(draft.arrangement_id),
                    "subject_ref": draft.subject_ref,
                    "currency": currency.code,
                    "minor_units": currency.minor_units,
                    "arrangement_fingerprint": fingerprint,
                    "lifecycle": "proposed",
                    "proposed_at": draft.proposed_at,
                    "accepted_at": None,
                },
            )
        )
        db.add(arrangement_row)
        db.flush()
        for exposure in draft.exposures:
            db.add(
                plane.arrangement_exposure(
                    **_values(
                        draft.scope,
                        {
                            "id": uuid4(),
                            "arrangement_id": draft.arrangement_id,
                            "source_owner": exposure.source_owner,
                            "exposure_ref": exposure.exposure_ref,
                            "source_version": exposure.source_version,
                            "position_fingerprint": exposure.position_fingerprint,
                            "subject_ref": exposure.subject_ref,
                            "service_ref": exposure.service_ref,
                            "admitted_amount": exposure.admitted_amount.amount,
                        },
                    )
                )
            )
        for installment in draft.installments:
            db.add(
                plane.arrangement_installment(
                    **_values(
                        draft.scope,
                        {
                            "id": uuid4(),
                            "arrangement_id": draft.arrangement_id,
                            "ordinal": installment.ordinal,
                            "amount": installment.amount.amount,
                            "due_at": installment.due_at,
                        },
                    )
                )
            )
        db.flush()
        return ArrangementWriteResult(draft.arrangement_id, "proposed", False)

    @staticmethod
    def transition(
        db: Session,
        *,
        command: ArrangementLifecycleCommandV1,
        to_state: Literal["accepted", "completed", "cancelled", "defaulted"],
    ) -> ArrangementWriteResult:
        allowed: dict[str, frozenset[str]] = {
            "proposed": frozenset({"accepted", "cancelled"}),
            "accepted": frozenset({"completed", "cancelled", "defaulted"}),
            "completed": frozenset(),
            "cancelled": frozenset(),
            "defaulted": frozenset(),
        }
        fingerprint = fingerprint_of({"command": asdict(command), "to_state": to_state})

        def operation(session: Session) -> Mapping[str, object]:
            _serialize_identity(
                session, command.scope, "arrangement", command.arrangement_id
            )
            row = _arrangement(
                session, command.scope, command.arrangement_id, lock=True
            )
            if to_state != row.lifecycle:
                if to_state not in allowed[row.lifecycle]:
                    raise CollectionsConflict(
                        "arrangement_transition_refused",
                        "Arrangement cannot transition from "
                        f"{row.lifecycle} to {to_state}.",
                    )
                row.lifecycle = to_state
                if to_state == "accepted":
                    row.accepted_at = command.occurred_at
                session.flush()
            return {"arrangement_id": str(row.id), "lifecycle": row.lifecycle}

        outcome = _idempotent(
            db,
            scope=command.scope,
            operation_scope="collections.transition_arrangement",
            key=command.idempotency_key,
            fingerprint=fingerprint,
            operation=operation,
        )
        return ArrangementWriteResult(
            UUID(str(outcome.result["arrangement_id"])),
            str(outcome.result["lifecycle"]),
            outcome.replayed,
        )

    @staticmethod
    def record_settlement(
        db: Session, receipt: ArrangementSettlementReceiptV1
    ) -> ArrangementWriteResult:
        plane = _models(receipt.scope)
        _serialize_identity(db, receipt.scope, "arrangement", receipt.arrangement_id)
        row = _arrangement(db, receipt.scope, receipt.arrangement_id, lock=True)
        existing = db.execute(
            _where_scope(
                select(plane.arrangement_settlement).where(
                    plane.arrangement_settlement.arrangement_id
                    == receipt.arrangement_id,
                    plane.arrangement_settlement.source_owner == receipt.source_owner,
                    plane.arrangement_settlement.settlement_ref
                    == receipt.settlement_ref,
                ),
                receipt.scope,
                plane.arrangement_settlement,
            )
        ).scalar_one_or_none()
        if existing is not None:
            exact = (
                existing.id == receipt.receipt_id
                and existing.source_version == receipt.source_version
                and existing.receipt_fingerprint == receipt.receipt_fingerprint
                and existing.settled_amount == receipt.settled_amount.amount
                and _stored_utc(existing.settled_at)
                == receipt.settled_at.astimezone(UTC)
            )
            if not exact:
                raise CollectionsConflict(
                    "arrangement_settlement_conflict",
                    "The settlement source identity has different evidence.",
                )
            return ArrangementWriteResult(row.id, row.lifecycle, True)
        if row.lifecycle != "accepted":
            raise CollectionsConflict(
                "arrangement_not_accepted",
                "Only an accepted arrangement may receive settlement evidence.",
            )
        if (
            row.currency != receipt.settled_amount.currency.code
            or row.minor_units != receipt.settled_amount.currency.minor_units
        ):
            raise CollectionsConflict(
                "arrangement_currency_conflict",
                "Settlement evidence must use the arrangement currency.",
            )
        total_due = db.scalar(
            _where_scope(
                select(
                    func.coalesce(func.sum(plane.arrangement_installment.amount), 0)
                ).where(
                    plane.arrangement_installment.arrangement_id
                    == receipt.arrangement_id
                ),
                receipt.scope,
                plane.arrangement_installment,
            )
        )
        total_settled = db.scalar(
            _where_scope(
                select(
                    func.coalesce(
                        func.sum(plane.arrangement_settlement.settled_amount), 0
                    )
                ).where(
                    plane.arrangement_settlement.arrangement_id
                    == receipt.arrangement_id
                ),
                receipt.scope,
                plane.arrangement_settlement,
            )
        )
        due = Decimal(total_due or 0)
        settled = Decimal(total_settled or 0) + receipt.settled_amount.amount
        if settled > due:
            raise CollectionsConflict(
                "arrangement_overpayment",
                "Settlement evidence exceeds the exact arrangement schedule.",
            )
        db.add(
            plane.arrangement_settlement(
                **_values(
                    receipt.scope,
                    {
                        "id": receipt.receipt_id,
                        "arrangement_id": receipt.arrangement_id,
                        "source_owner": receipt.source_owner,
                        "settlement_ref": receipt.settlement_ref,
                        "source_version": receipt.source_version,
                        "receipt_fingerprint": receipt.receipt_fingerprint,
                        "settled_amount": receipt.settled_amount.amount,
                        "settled_at": receipt.settled_at,
                    },
                )
            )
        )
        if settled == due:
            row.lifecycle = "completed"
        db.flush()
        return ArrangementWriteResult(row.id, row.lifecycle, False)


class CollectionGraceService:
    """Sole append-only writer for grant, supersession and revocation evidence."""

    @staticmethod
    def record(db: Session, command: PersistGraceV1) -> GraceWriteResult:
        grant = command.grant
        model = _models(grant.scope).grace
        fingerprint = fingerprint_of(asdict(command))
        _serialize_identity(db, grant.scope, "case", grant.case_id, "grace")
        existing = db.execute(
            _where_scope(
                select(model).where(model.id == grant.grant_id),
                grant.scope,
                model,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.grant_fingerprint != fingerprint:
                raise CollectionsConflict(
                    "grace_identity_conflict",
                    "The grace record identity already names different evidence.",
                )
            return GraceWriteResult(
                existing.id, cast(GraceRecordKind, existing.grant_kind), True
            )
        _case(db, grant.scope, grant.case_id, lock=True)
        if command.supersedes_grant_id is not None:
            prior = db.execute(
                _where_scope(
                    select(model).where(model.id == command.supersedes_grant_id),
                    grant.scope,
                    model,
                )
            ).scalar_one_or_none()
            if prior is None or prior.case_id != grant.case_id:
                raise CollectionsConflict(
                    "grace_supersession_conflict",
                    "The superseded grace does not belong to this case.",
                )
            successor = db.execute(
                _where_scope(
                    select(model).where(
                        model.supersedes_grant_id == command.supersedes_grant_id
                    ),
                    grant.scope,
                    model,
                )
            ).scalar_one_or_none()
            if successor is not None:
                raise CollectionsConflict(
                    "grace_already_superseded",
                    "The prior grace already has a superseding record.",
                )
        db.add(
            model(
                **_values(
                    grant.scope,
                    {
                        "id": grant.grant_id,
                        "case_id": grant.case_id,
                        "grant_kind": command.kind,
                        "supersedes_grant_id": command.supersedes_grant_id,
                        "grant_fingerprint": fingerprint,
                        "anchor_kind": grant.anchor_kind,
                        "anchor_at": grant.anchor_at,
                        "duration_seconds": int(grant.duration.total_seconds()),
                        "actor_ref": grant.actor_ref,
                        "reason_code": grant.reason_code,
                        "granted_at": grant.granted_at,
                    },
                )
            )
        )
        db.flush()
        return GraceWriteResult(grant.grant_id, command.kind, False)


class CollectionReconciliationService:
    """Sole append-only writer for rebuild/hash comparison evidence."""

    @staticmethod
    def record(
        db: Session, command: ReconcileCollectionCaseV1
    ) -> ReconciliationWriteResult:
        model = _models(command.scope).reconciliation
        _serialize_identity(db, command.scope, "case", command.case_id)
        row = _case(db, command.scope, command.case_id, lock=True)
        if (
            row.source_owner != command.source_owner
            or row.exposure_ref != command.exposure_ref
        ):
            raise CollectionsConflict(
                "reconciliation_identity_conflict",
                "The reconciliation source does not match the case.",
            )
        outcome: Literal["match", "drift"] = (
            "match"
            if command.source_fingerprint == command.rebuilt_fingerprint
            else "drift"
        )
        existing = db.execute(
            _where_scope(
                select(model).where(
                    model.case_id == command.case_id,
                    model.source_version == command.source_version,
                ),
                command.scope,
                model,
            )
        ).scalar_one_or_none()
        if existing is not None:
            exact = (
                existing.id == command.reconciliation_id
                and existing.source_owner == command.source_owner
                and existing.exposure_ref == command.exposure_ref
                and existing.source_fingerprint == command.source_fingerprint
                and existing.rebuilt_fingerprint == command.rebuilt_fingerprint
                and existing.outcome == outcome
                and _stored_utc(existing.reconciled_at)
                == command.reconciled_at.astimezone(UTC)
            )
            if not exact:
                raise CollectionsConflict(
                    "reconciliation_conflict",
                    "One source version has different reconciliation evidence.",
                )
            return ReconciliationWriteResult(existing.id, outcome, True)
        db.add(
            model(
                **_values(
                    command.scope,
                    {
                        "id": command.reconciliation_id,
                        "case_id": command.case_id,
                        "source_owner": command.source_owner,
                        "exposure_ref": command.exposure_ref,
                        "source_version": command.source_version,
                        "source_fingerprint": command.source_fingerprint,
                        "rebuilt_fingerprint": command.rebuilt_fingerprint,
                        "outcome": outcome,
                        "reconciled_at": command.reconciled_at,
                    },
                )
            )
        )
        db.flush()
        return ReconciliationWriteResult(command.reconciliation_id, outcome, False)


__all__ = [
    "ArrangementLifecycleCommandV1",
    "ArrangementSettlementReceiptV1",
    "ArrangementWriteResult",
    "AssessmentBlocked",
    "AssessmentNoCase",
    "CaseAssessed",
    "CaseAssessmentResult",
    "CaseLifecycleCommandV1",
    "CollectionActionService",
    "CollectionCaseService",
    "CollectionNoticeService",
    "CollectionPolicyService",
    "CollectionGraceService",
    "CollectionReconciliationService",
    "CollectionsConflict",
    "CollectionsNotFound",
    "CollectionsServiceError",
    "CreateCollectionPolicyV1",
    "GraceRecordKind",
    "GraceWriteResult",
    "PaymentArrangementService",
    "PersistGraceV1",
    "ProcessCollectionStepDueV1",
    "PolicyVersionWriteResult",
    "PolicyWriteResult",
    "ReceiptWriteResult",
    "ReconcileCollectionCaseV1",
    "ReconciliationWriteResult",
    "RequestWriteResult",
    "StepCaseResolved",
    "StepDueBlocked",
    "StepDueIgnored",
    "StepDueResult",
    "StepRequestWritten",
]
