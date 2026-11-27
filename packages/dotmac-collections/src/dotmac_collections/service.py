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
from sqlalchemy import exists, func, select, text
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
from dotmac_collections.holds import (
    SUBJECT_ACCESS_POLICY_VERSION,
    CollectionCaseViewV1,
    CollectionSubjectStateV1,
    HoldLifted,
    HoldNotLive,
    HoldPlaced,
    LiftCollectionHoldV1,
    PlaceCollectionHoldV1,
    RepublishSubjectStateV1,
    SubjectAccessState,
    SubjectStateRepublished,
    decide_subject_access,
)
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
    ACCESS_HOLD_DUE_EVENT_TYPE,
    EXPOSURE_DUE_EVENT_TYPE,
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

# One sequence per plane, so a service that moves between accounts never sees a
# subject-access version regress (versions are compared per service, not per
# account).
_TENANT_ACCESS_SEQUENCE = "mod_coll.collection_subject_access_version_seq"
_PLATFORM_ACCESS_SEQUENCE = "mod_coll.platform_collection_subject_access_version_seq"


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
    hold: Any
    hold_lift: Any
    subject_access: Any


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
    models.CollectionHold,
    models.CollectionHoldLift,
    models.CollectionSubjectAccess,
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
    models.PlatformCollectionHold,
    models.PlatformCollectionHoldLift,
    models.PlatformCollectionSubjectAccess,
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
class ProcessCollectionAccessHoldDueV1:
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
            or identity.purpose != "access_hold"
            or self.trigger.output_event_type != ACCESS_HOLD_DUE_EVENT_TYPE
        ):
            raise ValueError("timer trigger is not a Collections access-hold wake-up")


@dataclass(frozen=True, slots=True)
class AccessHoldIgnored:
    case_id: UUID
    reason_code: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class AccessHoldBlocked:
    case_id: UUID
    reason_code: str
    retry_at: datetime | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class AccessHoldEvaluated:
    """The case was reread and its subject's access state recomputed."""

    case_id: UUID
    subject_ref: str
    lifecycle: CaseLifecycle
    subject_state: SubjectAccessState
    source_version: int
    replayed: bool


AccessHoldDueResult = AccessHoldIgnored | AccessHoldBlocked | AccessHoldEvaluated


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


def _hold_from(position: ReceivableObservationV1) -> datetime | None:
    value = position.access_hold_from
    return None if value is None else value.astimezone(UTC)


def _due_at(position: ReceivableObservationV1) -> datetime | None:
    return None if position.due_at is None else position.due_at.astimezone(UTC)


def _apply_hold_evidence(row: _CaseRow, position: ReceivableObservationV1) -> None:
    """Store the due and hold instants; a hold instant that moved later disengages."""

    hold_from = _hold_from(position)
    row.access_hold_from = hold_from
    row.due_at = _due_at(position)
    engaged = row.access_hold_engaged_at
    if hold_from is None or (engaged is not None and _stored_utc(engaged) < hold_from):
        row.access_hold_engaged_at = None


def _exposure_wake_at(
    position: ReceivableObservationV1, blocker: str
) -> datetime | None:
    """The instant a not-yet-actionable exposure becomes assessable, if known."""

    if blocker == "receivable_not_due":
        return position.due_at
    if blocker == "service_period_not_started":
        return position.service_period_starts_at
    return None


def _exposure_timer_identity(
    scope: Scope, source_owner: str, exposure_ref: str
) -> TimerIdentityV1:
    return TimerIdentityV1(
        scope=scope,
        owner="collections.exposure",
        entity_kind="exposure",
        entity_id=f"{source_owner}:{exposure_ref}",
        purpose="due",
    )


def _hold_timer_identity(scope: Scope, case_id: UUID) -> TimerIdentityV1:
    return TimerIdentityV1(
        scope=scope,
        owner="collections.case",
        entity_kind="collection_case",
        entity_id=str(case_id),
        purpose="access_hold",
    )


def _arm_timer(
    db: Session,
    *,
    timer: CollectionsTimer,
    identity: TimerIdentityV1,
    due_at: datetime,
    recorded_at: datetime,
    event_type: str,
    expected_source_version: int,
) -> None:
    """Arm one wake; an identical current generation is left as it is."""

    current = timer.current(db, identity)
    if (
        current is not None
        and current.due_at == due_at
        and current.expected_source_version == expected_source_version
    ):
        return
    timer.schedule(
        db,
        TimerRequestV1(
            identity=identity,
            due_at=due_at,
            recorded_at=recorded_at,
            output_event_type=event_type,
            expected_source_version=expected_source_version,
        ),
    )


def _cancel_identity(
    db: Session,
    *,
    timer: CollectionsTimer,
    identity: TimerIdentityV1,
    recorded_at: datetime,
) -> None:
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
            "A collection timer changed during cancellation.",
        )


def _sync_hold_timer(
    db: Session,
    *,
    timer: CollectionsTimer,
    scope: Scope,
    case_row: _CaseRow,
    now: datetime,
) -> None:
    """Arm the access-hold wake until the hold is engaged, otherwise clear it.

    The wake is due at the later of the hold instant and now, so a case that opens
    past its instant still gets the balance pass before the hold engages.
    """

    identity = _hold_timer_identity(scope, case_row.id)
    hold_from = case_row.access_hold_from
    if (
        case_row.lifecycle == "active"
        and hold_from is not None
        and case_row.access_hold_engaged_at is None
    ):
        due_at = max(_stored_utc(hold_from), now.astimezone(UTC))
        current = timer.current(db, identity)
        if (
            current is not None
            and current.due_at <= due_at
            and current.expected_source_version == case_row.source_version
        ):
            return
        _arm_timer(
            db,
            timer=timer,
            identity=identity,
            due_at=due_at,
            recorded_at=now,
            event_type=ACCESS_HOLD_DUE_EVENT_TYPE,
            expected_source_version=case_row.source_version,
        )
    else:
        _cancel_identity(db, timer=timer, identity=identity, recorded_at=now)


def _live_hold_refs(db: Session, scope: Scope, subject_ref: str) -> tuple[str, ...]:
    plane = _models(scope)
    lifted = exists().where(
        plane.hold_lift.hold_id == plane.hold.id,
    )
    statement = _where_scope(
        select(plane.hold.external_ref).where(
            plane.hold.subject_ref == subject_ref, ~lifted
        ),
        scope,
        plane.hold,
    ).order_by(plane.hold.external_ref)
    return tuple(db.scalars(statement))


def _live_hold_by_ref(db: Session, scope: Scope, external_ref: str) -> Any | None:
    plane = _models(scope)
    lifted = exists().where(plane.hold_lift.hold_id == plane.hold.id)
    return db.scalar(
        _where_scope(
            select(plane.hold).where(plane.hold.external_ref == external_ref, ~lifted),
            scope,
            plane.hold,
        )
    )


def _next_access_version(db: Session, scope: Scope) -> int:
    if db.get_bind().dialect.name == "postgresql":
        sequence = (
            _TENANT_ACCESS_SEQUENCE
            if isinstance(scope, TenantScope)
            else _PLATFORM_ACCESS_SEQUENCE
        )
        return int(db.scalar(text(f"SELECT nextval('{sequence}')")) or 0)
    # SQLite has no sequence; the unit lane only needs a rising value.
    model = _models(scope).subject_access
    highest = db.scalar(
        _where_scope(select(func.coalesce(func.max(model.version), 0)), scope, model)
    )
    return int(highest or 0) + 1


def _recompute_subject(
    db: Session,
    *,
    scope: Scope,
    subject_ref: str,
    at: datetime,
    reason_code: str,
) -> SubjectAccessState:
    """Recompute one subject under its lock; emit once per change of ``held``.

    Lock order: a caller that holds a case lock takes it BEFORE this subject lock,
    never after, so two cases of one subject serialize here and cannot deadlock.
    """

    plane = _models(scope)
    _serialize_identity(db, scope, "subject", subject_ref)
    db.flush()
    demanded = (
        db.scalar(
            _where_scope(
                select(func.count())
                .select_from(plane.case)
                .where(
                    plane.case.subject_ref == subject_ref,
                    plane.case.lifecycle == "active",
                    plane.case.access_hold_from.is_not(None),
                    plane.case.access_hold_engaged_at.is_not(None),
                ),
                scope,
                plane.case,
            )
        )
        or 0
    ) > 0
    suppressed = bool(_live_hold_refs(db, scope, subject_ref))
    state = decide_subject_access(demanded=demanded, suppressed=suppressed)
    row = db.execute(
        _where_scope(
            select(plane.subject_access)
            .where(plane.subject_access.subject_ref == subject_ref)
            .with_for_update(),
            scope,
            plane.subject_access,
        )
    ).scalar_one_or_none()
    previous: SubjectAccessState = (
        "clear" if row is None else cast(SubjectAccessState, row.state)
    )
    if state == previous:
        return state
    version = None if row is None else row.version
    flipped = (previous == "held") != (state == "held")
    if flipped:
        version = _next_access_version(db, scope)
    if row is None:
        row = plane.subject_access(
            **_values(
                scope,
                {
                    "id": uuid4(),
                    "subject_ref": subject_ref,
                    "state": state,
                    "version": version,
                    "reason_code": reason_code,
                    "changed_at": at,
                },
            )
        )
        db.add(row)
    else:
        row.state = state
        row.version = version
        row.reason_code = reason_code
        row.changed_at = at
    db.flush()
    if flipped:
        _emit(
            db,
            scope=scope,
            event_type="collections.subject.access_hold.v1",
            payload=_access_event_payload(
                subject_ref, state == "held", version, at, reason_code
            ),
        )
    return state


def _access_event_payload(
    subject_ref: str,
    active: bool,
    version: int | None,
    at: datetime,
    reason_code: str,
) -> dict[str, object]:
    return {
        "subject_ref": subject_ref,
        "active": active,
        "source_version": version,
        "source_reference": f"collections:subject-access:{subject_ref}:{version}",
        "observed_at": at.isoformat(),
        "policy_version": SUBJECT_ACCESS_POLICY_VERSION,
        "reason_code": reason_code,
    }


def republish_subject_state(
    db: Session, command: RepublishSubjectStateV1
) -> SubjectStateRepublished:
    """Re-emit the subject's current state under the next version (idempotent).

    Takes only the subject lock. Use it when the product must apply the current
    state itself and the last flip's version could be stale for that service.
    """

    plane = _models(command.scope)
    fingerprint = fingerprint_of(asdict(command))

    def operation(session: Session) -> Mapping[str, object]:
        _serialize_identity(session, command.scope, "subject", command.subject_ref)
        row = session.execute(
            _where_scope(
                select(plane.subject_access)
                .where(plane.subject_access.subject_ref == command.subject_ref)
                .with_for_update(),
                command.scope,
                plane.subject_access,
            )
        ).scalar_one_or_none()
        state: SubjectAccessState = (
            "clear" if row is None else cast(SubjectAccessState, row.state)
        )
        version = _next_access_version(session, command.scope)
        if row is None:
            session.add(
                plane.subject_access(
                    **_values(
                        command.scope,
                        {
                            "id": uuid4(),
                            "subject_ref": command.subject_ref,
                            "state": state,
                            "version": version,
                            "reason_code": command.reason_code,
                            "changed_at": command.requested_at,
                        },
                    )
                )
            )
        else:
            row.version = version
        session.flush()
        _emit(
            session,
            scope=command.scope,
            event_type="collections.subject.access_hold.v1",
            payload=_access_event_payload(
                command.subject_ref,
                state == "held",
                version,
                command.requested_at,
                command.reason_code,
            ),
        )
        return {"state": state, "version": version}

    outcome = _idempotent(
        db,
        scope=command.scope,
        operation_scope="collections.republish_subject_state",
        key=command.idempotency_key,
        fingerprint=fingerprint,
        operation=operation,
    )
    return SubjectStateRepublished(
        command.subject_ref,
        cast(SubjectAccessState, str(outcome.result["state"])),
        int(str(outcome.result["version"])),
        outcome.replayed,
    )


def get_collection_case(
    db: Session, *, scope: Scope, case_id: UUID
) -> CollectionCaseViewV1:
    """Read one case's identity and hold evidence (read-only, no money)."""

    row = _case(db, scope, case_id)
    return CollectionCaseViewV1(
        case_id=row.id,
        subject_ref=row.subject_ref,
        source_owner=row.source_owner,
        exposure_ref=row.exposure_ref,
        service_ref=row.service_ref,
        lifecycle=row.lifecycle,
        source_version=row.source_version,
        due_at=None if row.due_at is None else _stored_utc(row.due_at),
        access_hold_from=(
            None if row.access_hold_from is None else _stored_utc(row.access_hold_from)
        ),
        access_hold_engaged_at=(
            None
            if row.access_hold_engaged_at is None
            else _stored_utc(row.access_hold_engaged_at)
        ),
    )


def get_collection_subject_state(
    db: Session, *, scope: Scope, subject_ref: str
) -> CollectionSubjectStateV1:
    """Read one subject's access state and its live holds (read-only)."""

    require_text("subject_ref", subject_ref)
    plane = _models(scope)
    row = db.execute(
        _where_scope(
            select(plane.subject_access).where(
                plane.subject_access.subject_ref == subject_ref
            ),
            scope,
            plane.subject_access,
        )
    ).scalar_one_or_none()
    refs = _live_hold_refs(db, scope, subject_ref)
    if row is None:
        return CollectionSubjectStateV1(subject_ref, "clear", None, refs, None)
    return CollectionSubjectStateV1(
        subject_ref,
        cast(SubjectAccessState, row.state),
        row.version,
        refs,
        _stored_utc(row.changed_at),
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
        exposure_wake: TimerTriggerV1 | None = None,
    ) -> CaseAssessmentResult:
        """Assess one exposure; pass the fired exposure-due wake to accept it.

        An accepted or superseded wake is consumed here, and once the exposure is
        actionable any pending exposure wake is cancelled, so no scheduled timer row
        outlives its purpose.
        """

        require_aware("assessed_at", assessed_at)
        if exposure_wake is not None and exposure_wake.identity != (
            _exposure_timer_identity(
                command.scope, command.source_owner, command.exposure_ref
            )
        ):
            raise ValueError("the wake is not this exposure's due wake")
        plane = _models(command.scope)
        fingerprint = fingerprint_of(
            {"command": asdict(command), "policy_version_id": policy_version_id}
        )

        def operation(session: Session) -> Mapping[str, object]:
            if timer is not None and exposure_wake is not None:
                timer.accept_trigger(session, exposure_wake, accepted_at=assessed_at)
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
                wake_at = _exposure_wake_at(position, blocker)
                if timer is not None and wake_at is not None:
                    _serialize_identity(
                        session,
                        command.scope,
                        "case",
                        command.source_owner,
                        command.exposure_ref,
                    )
                    _arm_timer(
                        session,
                        timer=timer,
                        identity=_exposure_timer_identity(
                            command.scope, command.source_owner, command.exposure_ref
                        ),
                        due_at=wake_at,
                        recorded_at=assessed_at,
                        event_type=EXPOSURE_DUE_EVENT_TYPE,
                        expected_source_version=position.source_version,
                    )
                return {
                    "kind": "blocked",
                    "reason_code": blocker,
                    "retry_after": None,
                }
            if timer is not None:
                _cancel_identity(
                    session,
                    timer=timer,
                    identity=_exposure_timer_identity(
                        command.scope, command.source_owner, command.exposure_ref
                    ),
                    recorded_at=assessed_at,
                )
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
                            "access_hold_from": _hold_from(position),
                            "access_hold_engaged_at": None,
                            "due_at": _due_at(position),
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
            if source_advanced:
                _apply_hold_evidence(row, position)
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
                    _cancel_identity(
                        session,
                        timer=timer,
                        identity=_hold_timer_identity(command.scope, row.id),
                        recorded_at=assessed_at,
                    )
                elif source_advanced:
                    # A live collection hold suppresses the ladder; lift re-arms it.
                    # Decide under the subject lock (case lock already held), so a
                    # concurrent last lift either finishes first or sees this case.
                    _serialize_identity(
                        session, command.scope, "subject", row.subject_ref
                    )
                    if not _live_hold_refs(session, command.scope, row.subject_ref):
                        _schedule_next_policy_step(
                            session,
                            timer=timer,
                            scope=command.scope,
                            case_row=row,
                            recorded_at=assessed_at,
                            skip_past_due=opened,
                        )
                    _sync_hold_timer(
                        session,
                        timer=timer,
                        scope=command.scope,
                        case_row=row,
                        now=assessed_at,
                    )
            if closed or source_advanced:
                _recompute_subject(
                    session,
                    scope=command.scope,
                    subject_ref=row.subject_ref,
                    at=assessed_at,
                    reason_code="receivable_resolved" if closed else "case_assessed",
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
            if to_state != "active":
                row.access_hold_engaged_at = None
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
                    _serialize_identity(
                        session, command.scope, "subject", row.subject_ref
                    )
                    if not _live_hold_refs(session, command.scope, row.subject_ref):
                        _schedule_next_policy_step(
                            session,
                            timer=timer,
                            scope=command.scope,
                            case_row=row,
                            recorded_at=command.occurred_at,
                            skip_past_due=True,
                        )
                else:
                    _cancel_case_timer(
                        session,
                        timer=timer,
                        scope=command.scope,
                        case_id=row.id,
                        recorded_at=command.occurred_at,
                    )
                _sync_hold_timer(
                    session,
                    timer=timer,
                    scope=command.scope,
                    case_row=row,
                    now=command.occurred_at,
                )
            _recompute_subject(
                session,
                scope=command.scope,
                subject_ref=row.subject_ref,
                at=command.occurred_at,
                reason_code=f"case_{to_state}",
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
                _apply_hold_evidence(row, position)
                _sync_hold_timer(
                    session,
                    timer=timer,
                    scope=command.scope,
                    case_row=row,
                    now=command.processed_at,
                )
                _recompute_subject(
                    session,
                    scope=command.scope,
                    subject_ref=row.subject_ref,
                    at=command.processed_at,
                    reason_code="case_assessed",
                )
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
                _cancel_identity(
                    session,
                    timer=timer,
                    identity=_hold_timer_identity(command.scope, row.id),
                    recorded_at=command.processed_at,
                )
                _recompute_subject(
                    session,
                    scope=command.scope,
                    subject_ref=row.subject_ref,
                    at=command.processed_at,
                    reason_code="receivable_resolved",
                )
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
            if _live_hold_refs(session, command.scope, row.subject_ref):
                # The fired wake is consumed and not re-armed; lift re-arms it.
                return {
                    "kind": "blocked",
                    "case_id": str(case_id),
                    "reason_code": "collection_hold_active",
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

    @staticmethod
    def process_access_hold_due(
        db: Session,
        *,
        command: ProcessCollectionAccessHoldDueV1,
        reader: ReceivablesReader,
        timer: CollectionsTimer,
    ) -> AccessHoldDueResult:
        """Reread the case at its hold instant and recompute its subject.

        The assembly runs the balance pass (its own earlier transaction) before
        calling this, so a customer whose credit can pay the invoice is not cut.
        """

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
            expected = command.trigger.expected_source_version
            if expected is not None and expected != row.source_version:
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
                    _arm_timer(
                        session,
                        timer=timer,
                        identity=_hold_timer_identity(command.scope, case_id),
                        due_at=blocked.retry_after,
                        recorded_at=command.processed_at,
                        event_type=ACCESS_HOLD_DUE_EVENT_TYPE,
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
                _apply_hold_evidence(row, position)
            closed = position.financial_state in {"resolved", "cancelled"}
            closed = closed or position.collectible_receivable.is_zero
            reason_code = "access_hold_due"
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
                reason_code = "receivable_resolved"
                _cancel_case_timer(
                    session,
                    timer=timer,
                    scope=command.scope,
                    case_id=case_id,
                    recorded_at=command.processed_at,
                )
            else:
                blocker = position.automated_collection_blocker(
                    as_of=command.processed_at
                )
                if blocker is not None:
                    session.flush()
                    return {
                        "kind": "blocked",
                        "case_id": str(case_id),
                        "reason_code": blocker,
                        "retry_at": None,
                    }
                hold_from = row.access_hold_from
                if (
                    hold_from is not None
                    and row.access_hold_engaged_at is None
                    and _stored_utc(hold_from) <= command.processed_at.astimezone(UTC)
                ):
                    # Only this command engages the hold, after the balance pass.
                    row.access_hold_engaged_at = command.processed_at
            session.flush()
            _sync_hold_timer(
                session,
                timer=timer,
                scope=command.scope,
                case_row=row,
                now=command.processed_at,
            )
            state = _recompute_subject(
                session,
                scope=command.scope,
                subject_ref=row.subject_ref,
                at=command.processed_at,
                reason_code=reason_code,
            )
            return {
                "kind": "evaluated",
                "case_id": str(case_id),
                "subject_ref": row.subject_ref,
                "lifecycle": row.lifecycle,
                "subject_state": state,
                "source_version": row.source_version,
            }

        outcome = _idempotent(
            db,
            scope=command.scope,
            operation_scope="collections.process_access_hold_due",
            key=(
                f"timer:{command.trigger.timer_id}:"
                f"generation:{command.trigger.generation}"
            ),
            fingerprint=fingerprint,
            correlation_id=str(command.trigger.timer_id),
            operation=operation,
        )
        result = outcome.result
        case_uuid = UUID(str(result["case_id"]))
        kind = str(result.get("kind"))
        if kind == "ignored":
            return AccessHoldIgnored(
                case_uuid, str(result["reason_code"]), outcome.replayed
            )
        if kind == "blocked":
            retry_at = result.get("retry_at")
            return AccessHoldBlocked(
                case_uuid,
                str(result["reason_code"]),
                datetime.fromisoformat(str(retry_at)) if retry_at else None,
                outcome.replayed,
            )
        if kind == "evaluated":
            return AccessHoldEvaluated(
                case_id=case_uuid,
                subject_ref=str(result["subject_ref"]),
                lifecycle=cast(CaseLifecycle, str(result["lifecycle"])),
                subject_state=cast(SubjectAccessState, str(result["subject_state"])),
                source_version=int(str(result["source_version"])),
                replayed=outcome.replayed,
            )
        raise CollectionsServiceError(
            "invalid_replay", "The stored access-hold result is invalid."
        )


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
    skipped: bool = False


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
    skipped = False
    for step in _policy_steps(db, scope, case_row.policy_version_id):
        request = _latest_step_request(
            db,
            scope=scope,
            case_id=case_row.id,
            step=step,
        )
        if request is None:
            skipped_at = _step_skipped_at(db, scope, case_row.id, step.step_code)
            if skipped_at is not None:
                # Passed over, never sent; its scheduled instant still anchors the
                # step counted from its request.
                request_at = skipped_at
                skipped = True
                continue
            return _StepProgress(
                step, False, request_at, accepted_notice_receipt_at, skipped
            )
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
            return _StepProgress(
                step, True, request_at, accepted_notice_receipt_at, skipped
            )
        request_at = _stored_utc(request.requested_at)
        if step.request_kind == "notice":
            accepted_notice_receipt_at = _stored_utc(receipt.observed_at)
    return _StepProgress(None, False, request_at, accepted_notice_receipt_at, skipped)


def _step_skipped_at(
    db: Session, scope: Scope, case_id: UUID, step_code: str
) -> datetime | None:
    """The scheduled instant of a skipped step (stored as its attempt instant)."""

    model = _models(scope).step_attempt
    value = db.scalar(
        _where_scope(
            select(model.attempted_at)
            .where(
                model.case_id == case_id,
                model.policy_step_code == step_code,
                model.request_kind == "skipped",
            )
            .limit(1),
            scope,
            model,
        )
    )
    return None if value is None else _stored_utc(value)


def _step_anchor(case_row: _CaseRow, progress: _StepProgress) -> datetime | None:
    step = progress.step
    if step is None:
        return None
    if step.offset_anchor == "exposure_at":
        # Reminders count from the due date, not from when the case happened to open.
        return _stored_utc(case_row.due_at or case_row.opened_at)
    if step.offset_anchor == "request_at":
        return progress.request_at
    return progress.accepted_notice_receipt_at


def _skip_past_due_steps(
    db: Session, *, scope: Scope, case_row: _CaseRow, now: datetime
) -> None:
    """On open or resume, pass over reminders already due before ``now``.

    Walks the ladder: a notice step whose instant is already before ``now`` is
    recorded as a ``skipped`` attempt (never sent) whose attempt instant is the moment
    it was scheduled, and that instant anchors a successor counted from its request,
    so the walk continues. It stops, without error, at the first step that is not
    due yet, is not a notice, or has no anchor (counted from a receipt, which a skip
    cannot supply).
    """

    # Each pass records one skip, so at most one pass per policy step.
    for _ in _policy_steps(db, scope, case_row.policy_version_id):
        progress = _step_progress(db, scope, case_row)
        step = progress.step
        if step is None or progress.request_pending or step.request_kind != "notice":
            return
        anchor = _step_anchor(case_row, progress)
        if anchor is None:
            return
        due = anchor + timedelta(seconds=step.offset_seconds)
        if due >= now.astimezone(UTC):
            return
        key = (
            f"collections:{scope_segment(scope)}:{case_row.id}:{step.step_code}:skipped"
        )
        _append_attempt(
            db,
            scope=scope,
            case_id=case_row.id,
            policy_step_code=step.step_code,
            attempt_ordinal=_next_attempt_ordinal(
                db, scope=scope, case_id=case_row.id, step_code=step.step_code
            ),
            request_kind="skipped",
            request_id=uuid5(NAMESPACE_URL, key),
            fingerprint=fingerprint_of(
                {"reason": "past_due_on_open", "step_due_at": due.isoformat()}
            ),
            attempted_at=due,
        )
        db.flush()


def _step_due_at(case_row: _CaseRow, progress: _StepProgress) -> datetime:
    step = progress.step
    if step is None:
        raise CollectionsServiceError(
            "policy_ladder_complete", "The collection policy ladder is complete."
        )
    anchor = _step_anchor(case_row, progress)
    if anchor is None:
        raise CollectionsConflict(
            "policy_anchor_unavailable",
            f"Policy step {step.step_code} has no {step.offset_anchor} anchor.",
        )
    return anchor + timedelta(seconds=step.offset_seconds)


_LADDER_HALT_REASON = "policy_ladder_halted_no_anchor"


def _record_ladder_halt(
    db: Session, *, scope: Scope, case_row: _CaseRow, at: datetime
) -> None:
    """Record, once per case, that the policy ladder stopped for want of an anchor.

    The transition table has no unique key beyond the ordinal, so idempotency is
    the existence check: the halt is a property of the case's published ladder (a
    skipped step followed by a step counted from a receipt that cannot exist), so
    one row per case covers resume, hold-lift re-arm and redelivery alike. The
    caller holds the case lock, which serialises the check against a concurrent
    writer.
    """

    if case_row.lifecycle != "active":
        return
    model = _models(scope).case_transition
    already = db.scalar(
        _where_scope(
            select(model.id)
            .where(
                model.case_id == case_row.id,
                model.reason_code == _LADDER_HALT_REASON,
            )
            .limit(1),
            scope,
            model,
        )
    )
    if already is not None:
        return
    _append_transition(
        db,
        scope=scope,
        row=case_row,
        from_state="active",
        to_state="active",
        reason_code=_LADDER_HALT_REASON,
        actor_ref="system:collections.policy_ladder",
        transitioned_at=at,
    )
    db.flush()


def _schedule_next_policy_step(
    db: Session,
    *,
    timer: CollectionsTimer,
    scope: Scope,
    case_row: _CaseRow,
    recorded_at: datetime,
    skip_past_due: bool = False,
) -> None:
    if skip_past_due:
        _skip_past_due_steps(db, scope=scope, case_row=case_row, now=recorded_at)
    progress = _step_progress(db, scope, case_row)
    if progress.step is None or progress.request_pending:
        return
    if progress.skipped and _step_anchor(case_row, progress) is None:
        # The successor counts from evidence a skipped step cannot supply: the
        # ladder stops here rather than raising and redelivering for ever. The
        # stop is recorded once on the case so it is not silent.
        _record_ladder_halt(db, scope=scope, case_row=case_row, at=recorded_at)
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
    request_kind: Literal["notice", "action", "skipped"],
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


class CollectionHoldService:
    """Sole writer for per-account collection holds (R10).

    A hold is an insert-only fact; it is live until a lift row names it. Placing
    and lifting take the hold-reference lock and then the subject lock, and
    ``lift`` takes the locks of the subject's active cases before the subject
    lock, so the order is always case, hold reference, subject.
    """

    @staticmethod
    def place(db: Session, command: PlaceCollectionHoldV1) -> HoldPlaced:
        plane = _models(command.scope)
        fingerprint = fingerprint_of(asdict(command))

        def operation(session: Session) -> Mapping[str, object]:
            _serialize_identity(session, command.scope, "hold", command.external_ref)
            live = _live_hold_by_ref(session, command.scope, command.external_ref)
            if live is not None:
                if live.subject_ref != command.subject_ref:
                    raise CollectionsConflict(
                        "hold_subject_conflict",
                        "The external reference is live on another subject.",
                    )
                return {
                    "kind": "placed",
                    "hold_id": str(live.id),
                    "existing": True,
                }
            highest = session.scalar(
                _where_scope(
                    select(func.max(plane.hold.placement_ordinal)).where(
                        plane.hold.external_ref == command.external_ref
                    ),
                    command.scope,
                    plane.hold,
                )
            )
            session.add(
                plane.hold(
                    **_values(
                        command.scope,
                        {
                            "id": command.command_id,
                            "subject_ref": command.subject_ref,
                            "external_ref": command.external_ref,
                            "placement_ordinal": int(highest or 0) + 1,
                            "reason_code": command.reason_code,
                            "evidence_ref": command.evidence_ref,
                            "actor_ref": command.actor_ref,
                            "approval_ref": command.approval_ref,
                            "placed_at": command.placed_at,
                        },
                    )
                )
            )
            session.flush()
            _recompute_subject(
                session,
                scope=command.scope,
                subject_ref=command.subject_ref,
                at=command.placed_at,
                reason_code="collection_hold_placed",
            )
            return {
                "kind": "placed",
                "hold_id": str(command.command_id),
                "existing": False,
            }

        outcome = _idempotent(
            db,
            scope=command.scope,
            operation_scope="collections.place_hold",
            key=command.idempotency_key,
            fingerprint=fingerprint,
            operation=operation,
        )
        return HoldPlaced(
            hold_id=UUID(str(outcome.result["hold_id"])),
            subject_ref=command.subject_ref,
            external_ref=command.external_ref,
            replayed=outcome.replayed or bool(outcome.result["existing"]),
        )

    @staticmethod
    def lift(
        db: Session,
        command: LiftCollectionHoldV1,
        timer: CollectionsTimer,
    ) -> HoldLifted | HoldNotLive:
        """Lift one hold; the last lift re-arms the ladder of every active case.

        Order: hold reference, then the subject's active cases by id, then the
        subject. Whether this is the last live hold is decided only after the
        subject lock, and a case that became active meanwhile is refused so the
        event is redelivered, so two simultaneous last lifts cannot both skip the
        re-arm.
        """

        plane = _models(command.scope)
        fingerprint = fingerprint_of(asdict(command))

        def active_case_ids(session: Session) -> tuple[UUID, ...]:
            return tuple(
                session.scalars(
                    _where_scope(
                        select(plane.case.id)
                        .where(
                            plane.case.subject_ref == command.subject_ref,
                            plane.case.lifecycle == "active",
                        )
                        .order_by(plane.case.id),
                        command.scope,
                        plane.case,
                    )
                ).all()
            )

        def operation(session: Session) -> Mapping[str, object]:
            _serialize_identity(session, command.scope, "hold", command.external_ref)
            live = _live_hold_by_ref(session, command.scope, command.external_ref)
            if live is None:
                return {"kind": "not_live"}
            if live.subject_ref != command.subject_ref:
                raise CollectionsConflict(
                    "hold_subject_conflict",
                    "The external reference is live on another subject.",
                )
            locked = active_case_ids(session)
            for case_id in locked:
                # Case locks come before the subject lock.
                _serialize_identity(session, command.scope, "case", case_id)
                _case(session, command.scope, case_id, lock=True)
            _serialize_identity(session, command.scope, "subject", command.subject_ref)
            if active_case_ids(session) != locked:
                raise CollectionsConflict(
                    "subject_cases_changed",
                    "An active case appeared while lifting; the lift is retried.",
                )
            others = [
                ref
                for ref in _live_hold_refs(session, command.scope, command.subject_ref)
                if ref != command.external_ref
            ]
            session.add(
                plane.hold_lift(
                    **_values(
                        command.scope,
                        {
                            "id": command.command_id,
                            "hold_id": live.id,
                            "reason_code": command.reason_code,
                            "evidence_ref": command.evidence_ref,
                            "actor_ref": command.actor_ref,
                            "approval_ref": command.approval_ref,
                            "lifted_at": command.lifted_at,
                        },
                    )
                )
            )
            session.flush()
            if not others:
                for case_id in locked:
                    _schedule_next_policy_step(
                        session,
                        timer=timer,
                        scope=command.scope,
                        case_row=_case(session, command.scope, case_id),
                        recorded_at=command.lifted_at,
                        skip_past_due=True,
                    )
            _recompute_subject(
                session,
                scope=command.scope,
                subject_ref=command.subject_ref,
                at=command.lifted_at,
                reason_code="collection_hold_lifted",
            )
            return {"kind": "lifted", "hold_id": str(live.id)}

        outcome = _idempotent(
            db,
            scope=command.scope,
            operation_scope="collections.lift_hold",
            key=command.idempotency_key,
            fingerprint=fingerprint,
            operation=operation,
        )
        if outcome.result["kind"] == "not_live":
            return HoldNotLive(command.external_ref, outcome.replayed)
        return HoldLifted(
            hold_id=UUID(str(outcome.result["hold_id"])),
            subject_ref=command.subject_ref,
            external_ref=command.external_ref,
            replayed=outcome.replayed,
        )


__all__ = [
    "AccessHoldBlocked",
    "AccessHoldDueResult",
    "AccessHoldEvaluated",
    "AccessHoldIgnored",
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
    "CollectionHoldService",
    "CollectionReconciliationService",
    "CollectionsConflict",
    "CollectionsNotFound",
    "CollectionsServiceError",
    "CreateCollectionPolicyV1",
    "GraceRecordKind",
    "GraceWriteResult",
    "PaymentArrangementService",
    "PersistGraceV1",
    "ProcessCollectionAccessHoldDueV1",
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
    "get_collection_case",
    "get_collection_subject_state",
    "republish_subject_state",
]
