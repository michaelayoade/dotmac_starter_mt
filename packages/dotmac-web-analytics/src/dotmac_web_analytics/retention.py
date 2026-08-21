"""Explicit retention/privacy deletion with mandatory projection rebuild."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dotmac_web_analytics.contracts import (
    EventDeclarationRegistry,
    ExpireObservationsCommand,
    FunnelDefinition,
    PrivacyDeletionCommand,
    ProjectionRepairResult,
    RebuildProjectionsCommand,
)
from dotmac_web_analytics.models import (
    AnalyticsProperty,
    EventObservation,
    EventReplayTombstone,
    PrivacyDeletionEvidence,
    ProjectionGeneration,
)
from dotmac_web_analytics.projections import rebuild_projections


def _property(db: Session, tenant_id: UUID, property_id: UUID) -> AnalyticsProperty:
    prop = db.scalar(
        select(AnalyticsProperty).where(
            AnalyticsProperty.tenant_id == tenant_id,
            AnalyticsProperty.id == property_id,
        )
    )
    if prop is None:
        from dotmac_web_analytics.contracts import InvalidContract

        raise InvalidContract("analytics property does not exist in tenant")
    return prop


def _tombstone_and_delete(
    db: Session,
    *,
    prop: AnalyticsProperty,
    observations: tuple[EventObservation, ...],
    deleted_at: datetime,
) -> int:
    for observation in observations:
        db.add(
            EventReplayTombstone(
                tenant_id=observation.tenant_id,
                property_id=observation.property_id,
                stream_id=observation.stream_id,
                event_id=observation.event_id,
                content_fingerprint=observation.content_fingerprint,
                deleted_at=deleted_at,
                expires_at=observation.received_at
                + timedelta(days=prop.replay_evidence_days),
            )
        )
    db.flush()
    for observation in observations:
        db.delete(observation)
    db.flush()
    return len(observations)


def expire_observations(
    db: Session,
    *,
    command: ExpireObservationsCommand,
    rebuild: RebuildProjectionsCommand,
    registry: EventDeclarationRegistry,
    funnels: tuple[FunnelDefinition, ...],
) -> ProjectionRepairResult:
    if (
        rebuild.tenant_id != command.tenant_id
        or rebuild.property_id != command.property_id
    ):
        from dotmac_web_analytics.contracts import InvalidContract

        raise InvalidContract("retention rebuild must target the deleted property")
    prop = _property(db, command.tenant_id, command.property_id)
    observations = tuple(
        db.scalars(
            select(EventObservation).where(
                EventObservation.tenant_id == command.tenant_id,
                EventObservation.property_id == command.property_id,
                EventObservation.expires_at <= command.cutoff,
            )
        )
    )
    deleted = _tombstone_and_delete(
        db,
        prop=prop,
        observations=observations,
        deleted_at=command.requested_at,
    )
    result = rebuild_projections(
        db, command=rebuild, registry=registry, funnels=funnels
    )
    return replace(result, deleted_observations=deleted)


def privacy_delete(
    db: Session,
    *,
    command: PrivacyDeletionCommand,
    rebuild: RebuildProjectionsCommand,
    registry: EventDeclarationRegistry,
    funnels: tuple[FunnelDefinition, ...],
) -> ProjectionRepairResult:
    if (
        rebuild.tenant_id != command.tenant_id
        or rebuild.property_id != command.property_id
    ):
        from dotmac_web_analytics.contracts import InvalidContract

        raise InvalidContract("privacy rebuild must target the deleted property")
    existing = db.scalar(
        select(PrivacyDeletionEvidence).where(
            PrivacyDeletionEvidence.tenant_id == command.tenant_id,
            PrivacyDeletionEvidence.property_id == command.property_id,
            PrivacyDeletionEvidence.request_id == command.request_id,
        )
    )
    if existing is not None:
        generation = db.scalar(
            select(ProjectionGeneration).where(
                ProjectionGeneration.tenant_id == command.tenant_id,
                ProjectionGeneration.property_id == command.property_id,
                ProjectionGeneration.id == existing.generation_id,
            )
        )
        if generation is None:
            from dotmac_web_analytics.contracts import InvalidContract

            raise InvalidContract("privacy deletion evidence lost its generation")
        return ProjectionRepairResult(
            existing.generation_id,
            existing.generation_id,
            generation.projection_digest,
            existing.deleted_observations,
        )
    prop = _property(db, command.tenant_id, command.property_id)
    observations = tuple(
        db.scalars(
            select(EventObservation).where(
                EventObservation.tenant_id == command.tenant_id,
                EventObservation.property_id == command.property_id,
                EventObservation.visitor_digest == command.visitor_digest,
            )
        )
    )
    deleted = _tombstone_and_delete(
        db,
        prop=prop,
        observations=observations,
        deleted_at=command.requested_at,
    )
    result = rebuild_projections(
        db, command=rebuild, registry=registry, funnels=funnels
    )
    db.add(
        PrivacyDeletionEvidence(
            tenant_id=command.tenant_id,
            property_id=command.property_id,
            request_id=command.request_id,
            deleted_observations=deleted,
            requested_at=command.requested_at,
            completed_at=rebuild.requested_at,
            generation_id=result.active_generation_id,
        )
    )
    db.flush()
    return replace(result, deleted_observations=deleted)


def expire_replay_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    property_id: UUID,
    cutoff: datetime,
) -> int:
    result = db.execute(
        delete(EventReplayTombstone).where(
            EventReplayTombstone.tenant_id == tenant_id,
            EventReplayTombstone.property_id == property_id,
            EventReplayTombstone.expires_at <= cutoff,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


__all__ = ["expire_observations", "expire_replay_evidence", "privacy_delete"]
