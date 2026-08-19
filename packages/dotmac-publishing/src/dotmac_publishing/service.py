"""Tenant-scoped publication decisions extracted from Mkt.

Services receive the caller's session and explicit tenant scope. They mutate and
flush only. Provider transport happens later through the kernel outbox; timer
storage is supplied through the typed port.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TypeVar, cast
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict, execute_once, fingerprint_of
from dotmac_kernel.messaging.outbox import enqueue_event
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from dotmac_publishing.contracts import (
    Conflict,
    DeliveryObservationV1,
    DeliveryOutcome,
    DispatchPublicationV1,
    NotFound,
    ObservationResult,
    PublicationSnapshotV1,
    PublicationTimerPort,
    PublicationTimerTrigger,
    RequestPublication,
    StaleTimer,
)
from dotmac_publishing.lifecycle import (
    DeliveryState,
    PublicationState,
    check_delivery_transition,
    derive_publication_state,
)
from dotmac_publishing.models import (
    PublicationAttempt,
    PublicationDelivery,
    PublicationObservation,
    PublicationRelease,
)

_Model = TypeVar("_Model")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-publishing requires an explicit TenantScope")
    return scope.tenant_id


def _one(db: Session, statement: Select[tuple[_Model]], *, detail: str) -> _Model:
    result = db.scalar(statement)
    if result is None:
        raise NotFound(detail)
    return result


def _release(db: Session, tenant_id: UUID, release_id: UUID) -> PublicationRelease:
    return _one(
        db,
        select(PublicationRelease)
        .options(selectinload(PublicationRelease.deliveries))
        .where(
            PublicationRelease.tenant_id == tenant_id,
            PublicationRelease.id == release_id,
        ),
        detail=f"publication release {release_id} was not found",
    )


def _delivery(db: Session, tenant_id: UUID, delivery_id: UUID) -> PublicationDelivery:
    return _one(
        db,
        select(PublicationDelivery)
        .options(
            selectinload(PublicationDelivery.release).selectinload(
                PublicationRelease.deliveries
            )
        )
        .where(
            PublicationDelivery.tenant_id == tenant_id,
            PublicationDelivery.id == delivery_id,
        ),
        detail=f"publication delivery {delivery_id} was not found",
    )


def _attempt(db: Session, tenant_id: UUID, attempt_id: UUID) -> PublicationAttempt:
    return _one(
        db,
        select(PublicationAttempt)
        .options(
            selectinload(PublicationAttempt.delivery)
            .selectinload(PublicationDelivery.release)
            .selectinload(PublicationRelease.deliveries)
        )
        .where(
            PublicationAttempt.tenant_id == tenant_id,
            PublicationAttempt.id == attempt_id,
        ),
        detail=f"publication attempt {attempt_id} was not found",
    )


def _snapshot(release: PublicationRelease) -> PublicationSnapshotV1:
    payload = release.snapshot_payload
    schema_version = payload.get("schema_version")
    source_ref = payload.get("source_ref")
    title = payload.get("title")
    body = payload.get("body")
    variant_key = payload.get("variant_key")
    creative_refs = payload.get("creative_refs")
    if (
        not isinstance(schema_version, int)
        or not isinstance(source_ref, str)
        or not isinstance(title, str)
        or not isinstance(body, str)
        or (variant_key is not None and not isinstance(variant_key, str))
        or not isinstance(creative_refs, list)
        or not all(isinstance(value, str) for value in creative_refs)
    ):
        raise Conflict(
            f"publication release {release.id} has an invalid persisted snapshot"
        )
    return PublicationSnapshotV1(
        source_ref=source_ref,
        title=title,
        body=body,
        variant_key=cast(str | None, variant_key),
        creative_refs=tuple(cast(list[str], creative_refs)),
        schema_version=schema_version,
    )


def _reconcile(release: PublicationRelease) -> PublicationState:
    state = derive_publication_state(delivery.state for delivery in release.deliveries)
    release.state = state
    return state


def request_publication(
    db: Session,
    *,
    scope: TenantScope,
    command: RequestPublication,
    timers: PublicationTimerPort,
    recorded_at: datetime,
) -> PublicationRelease:
    tenant_id = _tenant(scope)

    def operation(session: Session) -> dict[str, object]:
        release = PublicationRelease(
            tenant_id=tenant_id,
            request_key=command.request_key,
            request_fingerprint=command.fingerprint,
            source_ref=command.snapshot.source_ref,
            actor_ref=command.actor_ref,
            requested_for=command.requested_for,
            snapshot_version=command.snapshot.schema_version,
            snapshot_payload=command.snapshot.as_dict(),
            snapshot_digest=command.snapshot.digest,
            state=PublicationState.SCHEDULED,
        )
        session.add(release)
        session.flush()
        for target in command.targets:
            session.add(
                PublicationDelivery(
                    tenant_id=tenant_id,
                    publication_release_id=release.id,
                    target_ref=target.target_ref,
                    variant_key=target.variant_key,
                    state=DeliveryState.PENDING,
                )
            )
        session.flush()
        scheduled = timers.schedule(
            session,
            tenant_id=tenant_id,
            publication_release_id=release.id,
            due_at=command.requested_for,
            recorded_at=recorded_at,
        )
        release.timer_generation = scheduled.generation
        session.flush()
        return {"publication_release_id": str(release.id)}

    try:
        outcome = execute_once(
            db,
            tenant_id=tenant_id,
            scope="publishing.request",
            key=command.request_key,
            operation=operation,
            operation_name="request publication",
            fingerprint=command.fingerprint,
            correlation_id=command.request_key,
        )
    except IdempotencyConflict as exc:
        raise Conflict(
            "publication key was already used with a different request"
        ) from exc
    release_ref = outcome.result.get("publication_release_id")
    if not isinstance(release_ref, str):
        raise Conflict("publication idempotency result is missing its release id")
    return _release(db, tenant_id, UUID(release_ref))


def get_publication(
    db: Session, *, scope: TenantScope, publication_release_id: UUID
) -> PublicationRelease:
    return _release(db, _tenant(scope), publication_release_id)


def list_publications(
    db: Session, *, scope: TenantScope
) -> tuple[PublicationRelease, ...]:
    tenant_id = _tenant(scope)
    return tuple(
        db.scalars(
            select(PublicationRelease)
            .options(selectinload(PublicationRelease.deliveries))
            .where(PublicationRelease.tenant_id == tenant_id)
            .order_by(PublicationRelease.requested_for, PublicationRelease.id)
        )
    )


def _next_attempt_number(db: Session, *, tenant_id: UUID, delivery_id: UUID) -> int:
    current = db.scalar(
        select(func.max(PublicationAttempt.attempt_number)).where(
            PublicationAttempt.tenant_id == tenant_id,
            PublicationAttempt.publication_delivery_id == delivery_id,
        )
    )
    return int(current or 0) + 1


def _create_attempt(
    db: Session,
    *,
    tenant_id: UUID,
    delivery: PublicationDelivery,
    release: PublicationRelease,
    recorded_at: datetime,
) -> PublicationAttempt:
    check_delivery_transition(delivery.state, DeliveryState.INTENT_PUBLISHED)
    attempt_id = uuid.uuid4()
    attempt_number = _next_attempt_number(
        db, tenant_id=tenant_id, delivery_id=delivery.id
    )
    command = DispatchPublicationV1(
        publication_release_id=release.id,
        publication_delivery_id=delivery.id,
        publication_attempt_id=attempt_id,
        attempt_number=attempt_number,
        target_ref=delivery.target_ref,
        requested_for=release.requested_for,
        snapshot=_snapshot(release),
    )
    event = enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type="publishing.dispatch.v1",
        payload=command.as_dict(),
        correlation_id=str(release.id),
    )
    attempt = PublicationAttempt(
        id=attempt_id,
        tenant_id=tenant_id,
        publication_delivery_id=delivery.id,
        attempt_number=attempt_number,
        state=DeliveryState.INTENT_PUBLISHED,
        outbox_event_ref=str(event.id),
        requested_at=recorded_at,
    )
    db.add(attempt)
    delivery.state = DeliveryState.INTENT_PUBLISHED
    delivery.error_detail = None
    db.flush()
    return attempt


def dispatch_due_publication(
    db: Session,
    *,
    scope: TenantScope,
    trigger: PublicationTimerTrigger,
    timers: PublicationTimerPort,
    recorded_at: datetime,
) -> PublicationRelease:
    tenant_id = _tenant(scope)
    release = _release(db, tenant_id, trigger.publication_release_id)
    if release.timer_generation != trigger.generation:
        raise StaleTimer("stale publication timer generation")
    acceptance = timers.accept(
        db,
        tenant_id=tenant_id,
        trigger=trigger,
        accepted_at=recorded_at,
    )
    if not acceptance.current:
        raise StaleTimer(f"stale publication timer: {acceptance.reason}")
    if release.state != PublicationState.SCHEDULED:
        return release
    for delivery in release.deliveries:
        if delivery.state == DeliveryState.PENDING:
            _create_attempt(
                db,
                tenant_id=tenant_id,
                delivery=delivery,
                release=release,
                recorded_at=recorded_at,
            )
    _reconcile(release)
    db.flush()
    return release


def retry_delivery(
    db: Session,
    *,
    scope: TenantScope,
    publication_delivery_id: UUID,
    request_key: str,
    recorded_at: datetime,
) -> PublicationAttempt:
    tenant_id = _tenant(scope)
    delivery = _delivery(db, tenant_id, publication_delivery_id)
    release = delivery.release
    retry_fingerprint = fingerprint_of(
        {"publication_delivery_id": str(publication_delivery_id)}
    )

    def operation(session: Session) -> dict[str, object]:
        if delivery.state != DeliveryState.FAILED:
            raise Conflict("only a failed publication delivery can be retried")
        attempt = _create_attempt(
            session,
            tenant_id=tenant_id,
            delivery=delivery,
            release=release,
            recorded_at=recorded_at,
        )
        _reconcile(release)
        session.flush()
        return {"publication_attempt_id": str(attempt.id)}

    try:
        outcome = execute_once(
            db,
            tenant_id=tenant_id,
            scope="publishing.retry",
            key=request_key,
            operation=operation,
            operation_name="retry publication delivery",
            fingerprint=retry_fingerprint,
            correlation_id=str(release.id),
        )
    except IdempotencyConflict as exc:
        raise Conflict("retry key was already used with a different request") from exc
    attempt_ref = outcome.result.get("publication_attempt_id")
    if not isinstance(attempt_ref, str):
        raise Conflict("retry idempotency result is missing its attempt id")
    return _attempt(db, tenant_id, UUID(attempt_ref))


def _observation_state(outcome: DeliveryOutcome) -> DeliveryState:
    return {
        DeliveryOutcome.ACCEPTED: DeliveryState.ACCEPTED,
        DeliveryOutcome.PUBLISHED: DeliveryState.PUBLISHED,
        DeliveryOutcome.FAILED: DeliveryState.FAILED,
        DeliveryOutcome.CANCELLED: DeliveryState.CANCELLED,
    }[outcome]


def _result_for(db: Session, observation: PublicationObservation) -> ObservationResult:
    attempt = _attempt(db, observation.tenant_id, observation.publication_attempt_id)
    return ObservationResult(
        observation_id=observation.id,
        publication_state=attempt.delivery.release.state,
    )


def record_delivery_observation(
    db: Session,
    *,
    scope: TenantScope,
    command: DeliveryObservationV1,
    recorded_at: datetime,
) -> ObservationResult:
    tenant_id = _tenant(scope)
    existing = db.scalar(
        select(PublicationObservation).where(
            PublicationObservation.tenant_id == tenant_id,
            PublicationObservation.receipt_ref == command.receipt_ref,
        )
    )
    if existing is not None:
        if existing.fingerprint != command.fingerprint:
            raise Conflict("receipt_ref was already used for a different observation")
        return _result_for(db, existing)

    try:
        attempt_id = UUID(command.attempt_ref)
    except ValueError as exc:
        raise NotFound(
            f"publication attempt {command.attempt_ref!r} was not found"
        ) from exc
    attempt = _attempt(db, tenant_id, attempt_id)
    delivery = attempt.delivery
    release = delivery.release
    desired = _observation_state(command.outcome)
    check_delivery_transition(attempt.state, desired)
    check_delivery_transition(delivery.state, desired)

    from dotmac_kernel.db import conflict_savepoint

    observation = PublicationObservation(
        tenant_id=tenant_id,
        publication_attempt_id=attempt.id,
        receipt_ref=command.receipt_ref,
        fingerprint=command.fingerprint,
        outcome=command.outcome,
        remote_ref=command.remote_ref,
        error_detail=command.error_detail,
        observed_at=command.observed_at,
        recorded_at=recorded_at,
    )
    try:
        with conflict_savepoint(db):
            db.add(observation)
            attempt.state = desired
            delivery.state = desired
            if desired == DeliveryState.ACCEPTED:
                attempt.completed_at = None
            else:
                attempt.completed_at = command.observed_at
            delivery.remote_ref = command.remote_ref
            delivery.error_detail = command.error_detail
            publication_state = _reconcile(release)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalar(
            select(PublicationObservation).where(
                PublicationObservation.tenant_id == tenant_id,
                PublicationObservation.receipt_ref == command.receipt_ref,
            )
        )
        if winner is None:
            raise
        if winner.fingerprint != command.fingerprint:
            raise Conflict(
                "receipt_ref was already used for a different observation"
            ) from exc
        return _result_for(db, winner)
    return ObservationResult(
        observation_id=observation.id,
        publication_state=publication_state,
    )


def cancel_publication(
    db: Session,
    *,
    scope: TenantScope,
    publication_release_id: UUID,
    timers: PublicationTimerPort,
    recorded_at: datetime,
) -> PublicationRelease:
    tenant_id = _tenant(scope)
    release = _release(db, tenant_id, publication_release_id)
    if release.state == PublicationState.CANCELLED:
        return release
    if release.state != PublicationState.SCHEDULED:
        raise Conflict(
            f"publication in {release.state.value!r} cannot be cancelled locally; "
            "a dispatched target requires a withdrawal intent"
        )
    timers.cancel(
        db,
        tenant_id=tenant_id,
        publication_release_id=release.id,
        recorded_at=recorded_at,
    )
    for delivery in release.deliveries:
        check_delivery_transition(delivery.state, DeliveryState.CANCELLED)
        delivery.state = DeliveryState.CANCELLED
    _reconcile(release)
    db.flush()
    return release


__all__ = [
    "cancel_publication",
    "dispatch_due_publication",
    "get_publication",
    "list_publications",
    "record_delivery_observation",
    "request_publication",
    "retry_delivery",
]
