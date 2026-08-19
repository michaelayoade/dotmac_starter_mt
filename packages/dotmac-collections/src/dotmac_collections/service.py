"""Canonical flush-only writers for tenant Collections facts.

Every command receives the caller's :class:`~sqlalchemy.orm.Session`, mutates
only ``mod_coll`` rows (plus the kernel idempotency ledger), and flushes.  The
kernel boundary remains the only transaction authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, TypeAlias, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import execute_once, fingerprint_of
from dotmac_kernel.money import Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_collections._validation import require_aware, require_text
from dotmac_collections.actions import (
    ActionApplied,
    ActionReceipt,
    ActionReceiptConflict,
    CollectionActionRequestedV1,
)
from dotmac_collections.arrangements import PaymentArrangementDraftV1
from dotmac_collections.contracts import AssessCollectionExposureV1
from dotmac_collections.grace import GraceGrantV1
from dotmac_collections.models import (
    CollectionActionReceipt,
    CollectionActionRequest,
    CollectionCase,
    CollectionCaseExposure,
    CollectionCaseTransition,
    CollectionGraceGrant,
    CollectionNoticeReceipt,
    CollectionNoticeRequest,
    CollectionPolicy,
    CollectionPolicyStep,
    CollectionPolicyVersion,
    CollectionReconciliation,
    CollectionStepAttempt,
    PaymentArrangement,
    PaymentArrangementExposure,
    PaymentArrangementInstallment,
    PaymentArrangementSettlementReceipt,
)
from dotmac_collections.notices import (
    CollectionNoticeRequestedV1,
    NoticeAccepted,
    NoticeReceipt,
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
    ReceivablePositionV1,
    ReceivablesReader,
)

CaseLifecycle = Literal["active", "paused", "resolved", "cancelled"]


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
    scope: TenantScope
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


CaseAssessmentResult: TypeAlias = AssessmentBlocked | AssessmentNoCase | CaseAssessed


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
class CaseLifecycleCommandV1:
    command_id: UUID
    idempotency_key: str
    scope: TenantScope
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
    scope: TenantScope
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
    scope: TenantScope
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
    scope: TenantScope
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


def _serialize_identity(db: Session, scope: TenantScope, *parts: object) -> None:
    """Serialize even first-row decisions without inventing a lock table."""
    if db.get_bind().dialect.name != "postgresql":
        return
    key = _lock_key("collections", scope.tenant_id, *parts)
    db.scalar(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


def _policy(
    db: Session,
    scope: TenantScope,
    *,
    policy_id: UUID | None = None,
    code: str | None = None,
) -> CollectionPolicy | None:
    statement = select(CollectionPolicy).where(
        CollectionPolicy.tenant_id == scope.tenant_id
    )
    if policy_id is not None:
        statement = statement.where(CollectionPolicy.id == policy_id)
    if code is not None:
        statement = statement.where(CollectionPolicy.policy_code == code)
    return db.execute(statement).scalar_one_or_none()


def _policy_version(
    db: Session,
    scope: TenantScope,
    policy_version_id: UUID,
    *,
    lock: bool = False,
) -> CollectionPolicyVersion:
    statement = select(CollectionPolicyVersion).where(
        CollectionPolicyVersion.tenant_id == scope.tenant_id,
        CollectionPolicyVersion.id == policy_version_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise CollectionsNotFound(
            "policy_version_not_found", "The policy version was not found."
        )
    return row


def _live_case(
    db: Session,
    scope: TenantScope,
    *,
    source_owner: str,
    exposure_ref: str,
    lock: bool = False,
) -> CollectionCase | None:
    statement = select(CollectionCase).where(
        CollectionCase.tenant_id == scope.tenant_id,
        CollectionCase.source_owner == source_owner,
        CollectionCase.exposure_ref == exposure_ref,
        CollectionCase.lifecycle.in_(("active", "paused")),
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()


def _case(
    db: Session, scope: TenantScope, case_id: UUID, *, lock: bool = False
) -> CollectionCase:
    statement = select(CollectionCase).where(
        CollectionCase.tenant_id == scope.tenant_id,
        CollectionCase.id == case_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise CollectionsNotFound(
            "case_not_found", "The collection case was not found."
        )
    return row


def _position_document(position: ReceivablePositionV1) -> dict[str, object]:
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
        "available_credit": money(position.available_credit),
        "funding_available": money(position.funding_available),
        "due_at": position.due_at.isoformat() if position.due_at else None,
        "coverage_start_at": (
            position.coverage_start_at.isoformat()
            if position.coverage_start_at
            else None
        ),
        "resolution": position.resolution,
        "authority": position.authority,
        "completeness": position.completeness,
        "observed_at": position.observed_at.isoformat(),
    }


def _append_transition(
    db: Session,
    *,
    scope: TenantScope,
    row: CollectionCase,
    from_state: str | None,
    to_state: CaseLifecycle,
    reason_code: str,
    actor_ref: str,
    transitioned_at: datetime,
) -> None:
    ordinal = db.scalar(
        select(
            func.coalesce(func.max(CollectionCaseTransition.transition_ordinal), 0)
        ).where(
            CollectionCaseTransition.tenant_id == scope.tenant_id,
            CollectionCaseTransition.case_id == row.id,
        )
    )
    db.add(
        CollectionCaseTransition(
            id=uuid4(),
            tenant_id=scope.tenant_id,
            case_id=row.id,
            transition_ordinal=int(ordinal or 0) + 1,
            from_state=from_state,
            to_state=to_state,
            reason_code=reason_code,
            actor_ref=actor_ref,
            transitioned_at=transitioned_at,
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
        db.add(
            CollectionPolicy(
                id=command.policy_id,
                tenant_id=command.scope.tenant_id,
                policy_code=command.policy_code,
                description=command.description,
            )
        )
        db.flush()
        return PolicyWriteResult(command.policy_id, False)

    @staticmethod
    def publish(
        db: Session,
        *,
        scope: TenantScope,
        draft: PolicyVersionDraftV1,
        publication: PolicyPublicationV1,
    ) -> PolicyVersionWriteResult:
        published = publish_policy_version(draft, publication)
        _serialize_identity(db, scope, "policy", draft.policy_code)
        policy = _policy(db, scope, code=draft.policy_code)
        if policy is None:
            raise CollectionsNotFound("policy_not_found", "The policy was not found.")
        existing = db.execute(
            select(CollectionPolicyVersion).where(
                CollectionPolicyVersion.tenant_id == scope.tenant_id,
                (
                    (CollectionPolicyVersion.id == publication.policy_version_id)
                    | (
                        (CollectionPolicyVersion.policy_id == policy.id)
                        & (CollectionPolicyVersion.version == publication.version)
                    )
                ),
            )
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
        db.add(
            CollectionPolicyVersion(
                id=published.policy_version_id,
                tenant_id=scope.tenant_id,
                policy_id=policy.id,
                version=published.version,
                reason_code=published.reason_code,
                collection_timing=published.collection_timing,
                grace=grace,
                effective_from=published.effective_from,
                actor_ref=published.actor_ref,
                publication_reason=published.reason,
                published_at=published.published_at,
                version_fingerprint=published.version_fingerprint,
            )
        )
        for step in published.steps:
            db.add(
                CollectionPolicyStep(
                    id=uuid4(),
                    tenant_id=scope.tenant_id,
                    policy_version_id=published.policy_version_id,
                    step_code=step.code,
                    ordinal=step.ordinal,
                    offset_seconds=int(step.offset.total_seconds()),
                    offset_anchor=step.offset_anchor,
                    request_kind=step.request_kind,
                    action_code=step.action_code,
                    receipt_required=step.receipt_required,
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
    command: AssessCollectionExposureV1, position: ReceivablePositionV1
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


class CollectionCaseService:
    """Sole writer for case membership and lifecycle transitions."""

    @staticmethod
    def assess(
        db: Session,
        *,
        command: AssessCollectionExposureV1,
        policy_version_id: UUID,
        reader: ReceivablesReader,
        assessed_at: datetime,
    ) -> CaseAssessmentResult:
        require_aware("assessed_at", assessed_at)
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
            if position.authority != "authoritative":
                return {
                    "kind": "blocked",
                    "reason_code": "position_not_authoritative",
                    "retry_after": None,
                }
            if position.completeness != "complete":
                return {
                    "kind": "blocked",
                    "reason_code": "position_incomplete",
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
            closed = position.resolution in {"resolved", "cancelled", "reversed"}
            closed = closed or position.collectible_receivable.is_zero
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
            opened = row is None
            if row is None:
                row = CollectionCase(
                    id=command.command_id,
                    tenant_id=command.scope.tenant_id,
                    policy_version_id=policy_version_id,
                    source_owner=command.source_owner,
                    exposure_ref=command.exposure_ref,
                    subject_ref=command.subject_ref,
                    service_ref=command.service_ref,
                    collection_timing=command.collection_timing,
                    reason_code=command.reason_code,
                    lifecycle="active",
                    source_version=position.source_version,
                    position_fingerprint=position.state_fingerprint,
                    opened_at=assessed_at,
                    resolved_at=None,
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
                    CollectionCaseExposure(
                        id=uuid4(),
                        tenant_id=command.scope.tenant_id,
                        case_id=row.id,
                        source_owner=position.source_owner,
                        exposure_ref=position.exposure_ref,
                        source_version=position.source_version,
                        position_fingerprint=position.state_fingerprint,
                        position_snapshot=_position_document(position),
                        observed_at=position.observed_at,
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
            return {
                "kind": "case",
                "case_id": str(row.id),
                "lifecycle": row.lifecycle,
                "source_version": row.source_version,
                "position_fingerprint": row.position_fingerprint,
                "opened": opened,
            }

        outcome = execute_once(
            db,
            tenant_id=command.scope.tenant_id,
            scope="collections.assess_exposure",
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
            return {
                "case_id": str(row.id),
                "lifecycle": row.lifecycle,
                "source_version": row.source_version,
                "position_fingerprint": row.position_fingerprint,
            }

        outcome = execute_once(
            db,
            tenant_id=command.scope.tenant_id,
            scope="collections.transition_case",
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


def _step(
    db: Session,
    scope: TenantScope,
    *,
    policy_version_id: UUID,
    step_code: str,
) -> CollectionPolicyStep:
    row = db.execute(
        select(CollectionPolicyStep).where(
            CollectionPolicyStep.tenant_id == scope.tenant_id,
            CollectionPolicyStep.policy_version_id == policy_version_id,
            CollectionPolicyStep.step_code == step_code,
        )
    ).scalar_one_or_none()
    if row is None:
        raise CollectionsNotFound(
            "policy_step_not_found", "The policy step was not found."
        )
    return row


def _validate_request_case(
    db: Session,
    scope: TenantScope,
    request: CollectionActionRequestedV1 | CollectionNoticeRequestedV1,
) -> tuple[CollectionCase, CollectionPolicyStep]:
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
    return row, _step(
        db,
        scope,
        policy_version_id=request.policy_version_id,
        step_code=request.policy_step_code,
    )


def _request_replay(
    existing: CollectionActionRequest | CollectionNoticeRequest,
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
    scope: TenantScope,
    case_id: UUID,
    policy_step_code: str,
    attempt_ordinal: int,
    request_kind: Literal["notice", "action"],
    request_id: UUID,
    fingerprint: str,
    attempted_at: datetime,
) -> None:
    existing = db.execute(
        select(CollectionStepAttempt).where(
            CollectionStepAttempt.tenant_id == scope.tenant_id,
            CollectionStepAttempt.case_id == case_id,
            CollectionStepAttempt.policy_step_code == policy_step_code,
            CollectionStepAttempt.attempt_ordinal == attempt_ordinal,
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
        CollectionStepAttempt(
            id=uuid4(),
            tenant_id=scope.tenant_id,
            case_id=case_id,
            policy_step_code=policy_step_code,
            attempt_ordinal=attempt_ordinal,
            request_kind=request_kind,
            request_id=request_id,
            decision_fingerprint=fingerprint,
            attempted_at=attempted_at,
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


class CollectionActionService:
    """Sole writer for product-action requests and owner receipts."""

    @staticmethod
    def request(
        db: Session,
        *,
        scope: TenantScope,
        request: CollectionActionRequestedV1,
    ) -> RequestWriteResult:
        fingerprint = fingerprint_of(asdict(request))
        _serialize_identity(db, scope, "case", request.case_id)
        existing = db.execute(
            select(CollectionActionRequest).where(
                CollectionActionRequest.tenant_id == scope.tenant_id,
                (
                    (CollectionActionRequest.id == request.request_id)
                    | (
                        CollectionActionRequest.idempotency_key
                        == request.idempotency_key
                    )
                ),
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
            CollectionActionRequest(
                id=request.request_id,
                tenant_id=scope.tenant_id,
                case_id=request.case_id,
                idempotency_key=request.idempotency_key,
                request_fingerprint=fingerprint,
                policy_version_id=request.policy_version_id,
                policy_step_code=request.policy_step_code,
                attempt_ordinal=request.step_attempt_ordinal,
                action_code=request.action_code,
                effect_scope=request.effect_scope,
                decision_evidence=_position_document(request.decision_evidence),
                requested_at=request.requested_at,
            )
        )
        db.flush()
        return RequestWriteResult(request.request_id, False)

    @staticmethod
    def record_receipt(
        db: Session, *, scope: TenantScope, receipt: ActionReceipt
    ) -> ReceiptWriteResult:
        _serialize_identity(db, scope, "action_receipt", receipt.request_id)
        request = db.execute(
            select(CollectionActionRequest).where(
                CollectionActionRequest.tenant_id == scope.tenant_id,
                CollectionActionRequest.id == receipt.request_id,
            )
        ).scalar_one_or_none()
        if request is None:
            raise CollectionsNotFound(
                "action_request_not_found", "The action request was not found."
            )
        fingerprint = fingerprint_of(asdict(receipt))
        existing = db.execute(
            select(CollectionActionReceipt).where(
                CollectionActionReceipt.tenant_id == scope.tenant_id,
                CollectionActionReceipt.request_id == receipt.request_id,
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
            CollectionActionReceipt(
                id=uuid4(),
                tenant_id=scope.tenant_id,
                request_id=receipt.request_id,
                receipt_kind=kind,
                owner_code=owner,
                owner_receipt_id=owner_receipt,
                receipt_fingerprint=fingerprint,
                receipt_evidence=evidence,
                observed_at=observed_at,
            )
        )
        db.flush()
        return ReceiptWriteResult(receipt.request_id, fingerprint, False)


class CollectionNoticeService:
    """Sole writer for notice requests and delivery-owner receipts."""

    @staticmethod
    def request(
        db: Session,
        *,
        scope: TenantScope,
        request: CollectionNoticeRequestedV1,
    ) -> RequestWriteResult:
        fingerprint = fingerprint_of(asdict(request))
        _serialize_identity(db, scope, "case", request.case_id)
        existing = db.execute(
            select(CollectionNoticeRequest).where(
                CollectionNoticeRequest.tenant_id == scope.tenant_id,
                (
                    (CollectionNoticeRequest.id == request.request_id)
                    | (
                        CollectionNoticeRequest.idempotency_key
                        == request.idempotency_key
                    )
                ),
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
            CollectionNoticeRequest(
                id=request.request_id,
                tenant_id=scope.tenant_id,
                case_id=request.case_id,
                idempotency_key=request.idempotency_key,
                request_fingerprint=fingerprint,
                policy_version_id=request.policy_version_id,
                policy_step_code=request.policy_step_code,
                attempt_ordinal=request.step_attempt_ordinal,
                purpose_code=request.purpose_code,
                decision_evidence=_position_document(request.decision_evidence),
                requested_at=request.requested_at,
            )
        )
        db.flush()
        return RequestWriteResult(request.request_id, False)

    @staticmethod
    def record_receipt(
        db: Session, *, scope: TenantScope, receipt: NoticeReceipt
    ) -> ReceiptWriteResult:
        _serialize_identity(db, scope, "notice_receipt", receipt.request_id)
        request = db.execute(
            select(CollectionNoticeRequest).where(
                CollectionNoticeRequest.tenant_id == scope.tenant_id,
                CollectionNoticeRequest.id == receipt.request_id,
            )
        ).scalar_one_or_none()
        if request is None:
            raise CollectionsNotFound(
                "notice_request_not_found", "The notice request was not found."
            )
        fingerprint = fingerprint_of(asdict(receipt))
        existing = db.execute(
            select(CollectionNoticeReceipt).where(
                CollectionNoticeReceipt.tenant_id == scope.tenant_id,
                CollectionNoticeReceipt.request_id == receipt.request_id,
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
            CollectionNoticeReceipt(
                id=uuid4(),
                tenant_id=scope.tenant_id,
                request_id=receipt.request_id,
                receipt_kind=kind,
                owner_code=owner,
                owner_receipt_id=owner_receipt,
                receipt_fingerprint=fingerprint,
                receipt_evidence=evidence,
                observed_at=observed_at,
            )
        )
        db.flush()
        return ReceiptWriteResult(receipt.request_id, fingerprint, False)


def _arrangement(
    db: Session,
    scope: TenantScope,
    arrangement_id: UUID,
    *,
    lock: bool = False,
) -> PaymentArrangement:
    statement = select(PaymentArrangement).where(
        PaymentArrangement.tenant_id == scope.tenant_id,
        PaymentArrangement.id == arrangement_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise CollectionsNotFound(
            "arrangement_not_found", "The payment arrangement was not found."
        )
    return row


class PaymentArrangementService:
    """Sole writer for exact arrangement membership and settlement evidence."""

    @staticmethod
    def propose(
        db: Session, draft: PaymentArrangementDraftV1
    ) -> ArrangementWriteResult:
        fingerprint = fingerprint_of(asdict(draft))
        _serialize_identity(db, draft.scope, "arrangement", draft.arrangement_id)
        existing = db.execute(
            select(PaymentArrangement).where(
                PaymentArrangement.tenant_id == draft.scope.tenant_id,
                PaymentArrangement.id == draft.arrangement_id,
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
        db.add(
            PaymentArrangement(
                id=draft.arrangement_id,
                tenant_id=draft.scope.tenant_id,
                arrangement_ref=str(draft.arrangement_id),
                subject_ref=draft.subject_ref,
                currency=currency.code,
                minor_units=currency.minor_units,
                arrangement_fingerprint=fingerprint,
                lifecycle="proposed",
                proposed_at=draft.proposed_at,
                accepted_at=None,
            )
        )
        for exposure in draft.exposures:
            db.add(
                PaymentArrangementExposure(
                    id=uuid4(),
                    tenant_id=draft.scope.tenant_id,
                    arrangement_id=draft.arrangement_id,
                    source_owner=exposure.source_owner,
                    exposure_ref=exposure.exposure_ref,
                    source_version=exposure.source_version,
                    position_fingerprint=exposure.position_fingerprint,
                    subject_ref=exposure.subject_ref,
                    service_ref=exposure.service_ref,
                    admitted_amount=exposure.admitted_amount.amount,
                )
            )
        for installment in draft.installments:
            db.add(
                PaymentArrangementInstallment(
                    id=uuid4(),
                    tenant_id=draft.scope.tenant_id,
                    arrangement_id=draft.arrangement_id,
                    ordinal=installment.ordinal,
                    amount=installment.amount.amount,
                    due_at=installment.due_at,
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

        outcome = execute_once(
            db,
            tenant_id=command.scope.tenant_id,
            scope="collections.transition_arrangement",
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
        _serialize_identity(db, receipt.scope, "arrangement", receipt.arrangement_id)
        row = _arrangement(db, receipt.scope, receipt.arrangement_id, lock=True)
        existing = db.execute(
            select(PaymentArrangementSettlementReceipt).where(
                PaymentArrangementSettlementReceipt.tenant_id
                == receipt.scope.tenant_id,
                PaymentArrangementSettlementReceipt.arrangement_id
                == receipt.arrangement_id,
                PaymentArrangementSettlementReceipt.source_owner
                == receipt.source_owner,
                PaymentArrangementSettlementReceipt.settlement_ref
                == receipt.settlement_ref,
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
            select(
                func.coalesce(func.sum(PaymentArrangementInstallment.amount), 0)
            ).where(
                PaymentArrangementInstallment.tenant_id == receipt.scope.tenant_id,
                PaymentArrangementInstallment.arrangement_id == receipt.arrangement_id,
            )
        )
        total_settled = db.scalar(
            select(
                func.coalesce(
                    func.sum(PaymentArrangementSettlementReceipt.settled_amount), 0
                )
            ).where(
                PaymentArrangementSettlementReceipt.tenant_id
                == receipt.scope.tenant_id,
                PaymentArrangementSettlementReceipt.arrangement_id
                == receipt.arrangement_id,
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
            PaymentArrangementSettlementReceipt(
                id=receipt.receipt_id,
                tenant_id=receipt.scope.tenant_id,
                arrangement_id=receipt.arrangement_id,
                source_owner=receipt.source_owner,
                settlement_ref=receipt.settlement_ref,
                source_version=receipt.source_version,
                receipt_fingerprint=receipt.receipt_fingerprint,
                settled_amount=receipt.settled_amount.amount,
                settled_at=receipt.settled_at,
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
        fingerprint = fingerprint_of(asdict(command))
        _serialize_identity(db, grant.scope, "case", grant.case_id, "grace")
        existing = db.execute(
            select(CollectionGraceGrant).where(
                CollectionGraceGrant.tenant_id == grant.scope.tenant_id,
                CollectionGraceGrant.id == grant.grant_id,
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
                select(CollectionGraceGrant).where(
                    CollectionGraceGrant.tenant_id == grant.scope.tenant_id,
                    CollectionGraceGrant.id == command.supersedes_grant_id,
                )
            ).scalar_one_or_none()
            if prior is None or prior.case_id != grant.case_id:
                raise CollectionsConflict(
                    "grace_supersession_conflict",
                    "The superseded grace does not belong to this case.",
                )
            successor = db.execute(
                select(CollectionGraceGrant).where(
                    CollectionGraceGrant.tenant_id == grant.scope.tenant_id,
                    CollectionGraceGrant.supersedes_grant_id
                    == command.supersedes_grant_id,
                )
            ).scalar_one_or_none()
            if successor is not None:
                raise CollectionsConflict(
                    "grace_already_superseded",
                    "The prior grace already has a superseding record.",
                )
        db.add(
            CollectionGraceGrant(
                id=grant.grant_id,
                tenant_id=grant.scope.tenant_id,
                case_id=grant.case_id,
                grant_kind=command.kind,
                supersedes_grant_id=command.supersedes_grant_id,
                grant_fingerprint=fingerprint,
                anchor_kind=grant.anchor_kind,
                anchor_at=grant.anchor_at,
                duration_seconds=int(grant.duration.total_seconds()),
                actor_ref=grant.actor_ref,
                reason_code=grant.reason_code,
                granted_at=grant.granted_at,
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
            select(CollectionReconciliation).where(
                CollectionReconciliation.tenant_id == command.scope.tenant_id,
                CollectionReconciliation.case_id == command.case_id,
                CollectionReconciliation.source_version == command.source_version,
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
            CollectionReconciliation(
                id=command.reconciliation_id,
                tenant_id=command.scope.tenant_id,
                case_id=command.case_id,
                source_owner=command.source_owner,
                exposure_ref=command.exposure_ref,
                source_version=command.source_version,
                source_fingerprint=command.source_fingerprint,
                rebuilt_fingerprint=command.rebuilt_fingerprint,
                outcome=outcome,
                reconciled_at=command.reconciled_at,
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
    "PolicyVersionWriteResult",
    "PolicyWriteResult",
    "ReceiptWriteResult",
    "ReconcileCollectionCaseV1",
    "ReconciliationWriteResult",
    "RequestWriteResult",
]
