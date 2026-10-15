"""Deterministic, rebuildable analytics projections and drift repair."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from dotmac_web_analytics import _native
from dotmac_web_analytics.contracts import (
    AggregateMetricQuery,
    AggregateMetricRow,
    EventDeclarationRegistry,
    FunnelDefinition,
    FunnelResult,
    MetricDimension,
    ProjectionDriftReport,
    ProjectionRepairResult,
    RebuildProjectionsCommand,
    SessionProjection,
    VisitorProjection,
)
from dotmac_web_analytics.models import (
    AggregateMetric,
    AnalyticsProperty,
    EventClassificationEvidence,
    EventObservation,
    FunnelDefinitionRow,
    FunnelResultRow,
    ProjectionDriftEvidence,
    ProjectionGeneration,
    SessionEventLink,
    SessionizationRuleRow,
    SessionProjectionRow,
    VisitorProjectionRow,
)


def _hash(document: object) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Normalize driver-returned timestamps to an aware UTC instant.

    PostgreSQL returns aware values for ``TIMESTAMPTZ`` while SQLite drops the
    offset despite SQLAlchemy's ``timezone=True`` declaration. Public
    projections and their repair digest must describe the same instant on both
    drivers; otherwise a clean rebuild is immediately reported as drift.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _instant(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def _observations(
    db: Session, tenant_id: UUID, property_id: UUID
) -> tuple[EventObservation, ...]:
    return tuple(
        db.scalars(
            select(EventObservation)
            .where(
                EventObservation.tenant_id == tenant_id,
                EventObservation.property_id == property_id,
            )
            .order_by(
                EventObservation.occurred_at,
                EventObservation.received_at,
                EventObservation.id,
            )
        )
    )


def _effective_classifications(
    db: Session, tenant_id: UUID, observations: tuple[EventObservation, ...]
) -> dict[UUID, EventClassificationEvidence]:
    if not observations:
        return {}
    rows = db.scalars(
        select(EventClassificationEvidence).where(
            EventClassificationEvidence.tenant_id == tenant_id,
            EventClassificationEvidence.observation_id.in_(
                [observation.id for observation in observations]
            ),
        )
    )
    effective: dict[UUID, EventClassificationEvidence] = {}
    for row in rows:
        current = effective.get(row.observation_id)
        if current is None or (
            row.classifier_version,
            row.classifier_code,
            row.classified_at,
            str(row.id),
        ) > (
            current.classifier_version,
            current.classifier_code,
            current.classified_at,
            str(current.id),
        ):
            effective[row.observation_id] = row
    return effective


def _authoritative_digest(
    observations: tuple[EventObservation, ...],
    classifications: dict[UUID, EventClassificationEvidence],
) -> str:
    document = []
    for observation in observations:
        classification = classifications.get(observation.id)
        document.append(
            (
                str(observation.id),
                observation.content_fingerprint,
                (
                    classification.classifier_code,
                    classification.classifier_version,
                    classification.analytically_included,
                    classification.is_bot,
                    tuple(classification.reasons_json),
                )
                if classification
                else None,
            )
        )
    return _hash(("web-analytics-authority-v1", document))


def _included(
    observations: tuple[EventObservation, ...],
    classifications: dict[UUID, EventClassificationEvidence],
) -> tuple[EventObservation, ...]:
    return tuple(
        observation
        for observation in observations
        if classifications.get(observation.id) is None
        or classifications[observation.id].analytically_included
    )


def _session_rows(
    observations: tuple[EventObservation, ...],
    groups: list[list[int]],
    command: RebuildProjectionsCommand,
) -> tuple[list[SessionProjectionRow], list[SessionEventLink]]:
    sessions: list[SessionProjectionRow] = []
    links: list[SessionEventLink] = []
    for indices in groups:
        group = [observations[index] for index in indices]
        visitor_digest = group[0].visitor_digest
        key = _hash(
            (
                "web-session-v1",
                str(command.property_id),
                visitor_digest,
                command.session_rule.code,
                command.session_rule.version,
                str(group[0].id),
            )
        )
        sessions.append(
            SessionProjectionRow(
                tenant_id=command.tenant_id,
                generation_id=UUID(int=0),
                property_id=command.property_id,
                session_key=key,
                visitor_digest=visitor_digest,
                rule_code=command.session_rule.code,
                rule_version=command.session_rule.version,
                started_at=group[0].occurred_at,
                ended_at=group[-1].occurred_at,
                event_count=len(group),
            )
        )
        links.extend(
            SessionEventLink(
                tenant_id=command.tenant_id,
                generation_id=UUID(int=0),
                property_id=command.property_id,
                observation_id=observation.id,
                session_key=key,
            )
            for observation in group
        )
    sessions.sort(key=lambda row: (row.started_at, row.session_key))
    links.sort(key=lambda row: str(row.observation_id))
    return sessions, links


def _dimensions(
    observation: EventObservation,
) -> tuple[tuple[MetricDimension, str], ...]:
    return (
        (MetricDimension.ROUTE, observation.canonical_path or "(none)"),
        (MetricDimension.SOURCE, observation.acquisition_source or "(direct)"),
        (
            MetricDimension.CAMPAIGN_MARKER,
            observation.acquisition_campaign or "(none)",
        ),
        (MetricDimension.DEVICE, observation.device_class),
        (MetricDimension.EVENT, observation.event_code),
    )


def _microseconds(value: datetime) -> int:
    delta = _as_utc(value) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _native_snapshot(
    observations: tuple[EventObservation, ...], timezone_name: str
) -> tuple[list[_native.Event], dict[int, datetime]]:
    try:
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        from dotmac_web_analytics.contracts import InvalidContract

        raise InvalidContract(f"unknown IANA timezone {timezone_name!r}") from exc
    buckets: dict[int, datetime] = {}
    events: list[_native.Event] = []
    for observation in observations:
        occurred = _as_utc(observation.occurred_at)
        local_day = occurred.astimezone(zone).date()
        bucket = datetime.combine(local_day, time.min, tzinfo=zone).astimezone(UTC)
        bucket_us = _microseconds(bucket)
        buckets[bucket_us] = bucket
        events.append(
            (
                observation.visitor_digest,
                _microseconds(occurred),
                _microseconds(observation.received_at),
                str(observation.id),
                observation.event_code,
                observation.event_schema_version,
                observation.pseudonym_key_version,
                bucket_us,
                [(str(dimension), key) for dimension, key in _dimensions(observation)],
            )
        )
    return events, buckets


def _projection_document(
    visitors: list[VisitorProjectionRow],
    sessions: list[SessionProjectionRow],
    metrics: list[AggregateMetric],
    funnels: list[FunnelResultRow],
) -> object:
    return (
        "web-analytics-projection-v1",
        [
            (
                row.visitor_digest,
                row.pseudonym_key_version,
                _instant(row.first_seen_at),
                _instant(row.last_seen_at),
            )
            for row in sorted(visitors, key=lambda item: item.visitor_digest)
        ],
        [
            (
                row.session_key,
                row.visitor_digest,
                row.rule_code,
                row.rule_version,
                _instant(row.started_at),
                _instant(row.ended_at),
                row.event_count,
            )
            for row in sorted(sessions, key=lambda item: item.session_key)
        ],
        [
            (
                _instant(row.bucket_start),
                row.dimension,
                row.dimension_key,
                row.event_count,
                row.visitor_count,
                row.session_count,
            )
            for row in sorted(
                metrics,
                key=lambda item: (
                    item.bucket_start,
                    item.dimension,
                    item.dimension_key,
                ),
            )
        ],
        [
            (
                row.definition_code,
                row.definition_version,
                row.entrants,
                tuple(row.completed_by_step_json),
            )
            for row in sorted(
                funnels,
                key=lambda item: (item.definition_code, item.definition_version),
            )
        ],
    )


def _delete_projection_rows(db: Session, tenant_id: UUID, property_id: UUID) -> None:
    for model in (
        FunnelResultRow,
        AggregateMetric,
        SessionEventLink,
        SessionProjectionRow,
        VisitorProjectionRow,
        ProjectionGeneration,
    ):
        db.execute(
            delete(model).where(
                model.tenant_id == tenant_id,
                model.property_id == property_id,
            )
        )


def rebuild_projections(
    db: Session,
    *,
    command: RebuildProjectionsCommand,
    registry: EventDeclarationRegistry,
    funnels: tuple[FunnelDefinition, ...],
) -> ProjectionRepairResult:
    prop = _property(db, command.tenant_id, command.property_id)
    if command.timezone_name != prop.timezone_name:
        from dotmac_web_analytics.contracts import InvalidContract

        raise InvalidContract("rebuild timezone must match the registered property")
    previous = prop.active_generation_id
    observations = _observations(db, command.tenant_id, command.property_id)
    for observation in observations:
        registry.require(observation.event_code, observation.event_schema_version)
    classifications = _effective_classifications(db, command.tenant_id, observations)
    included = _included(observations, classifications)
    authoritative = _authoritative_digest(observations, classifications)

    events, buckets = _native_snapshot(included, command.timezone_name)
    visitor_values, session_groups, metric_values, funnel_values = (
        _native.project_batch(
            events=events,
            inactivity_seconds=command.session_rule.inactivity_seconds,
            funnels=[
                (
                    funnel.within_seconds,
                    [
                        (step.event_code, step.event_schema_version)
                        for step in funnel.steps
                    ],
                )
                for funnel in funnels
            ],
        )
    )
    visitors = [
        VisitorProjectionRow(
            tenant_id=command.tenant_id,
            generation_id=UUID(int=0),
            property_id=command.property_id,
            visitor_digest=digest,
            pseudonym_key_version=key_version,
            first_seen_at=included[first].occurred_at,
            last_seen_at=included[last].occurred_at,
        )
        for digest, key_version, first, last in visitor_values
    ]
    sessions, links = _session_rows(included, session_groups, command)
    metrics = [
        AggregateMetric(
            tenant_id=command.tenant_id,
            generation_id=UUID(int=0),
            property_id=command.property_id,
            bucket_start=buckets[bucket],
            dimension=dimension,
            dimension_key=key,
            event_count=event_count,
            visitor_count=visitor_count,
            session_count=session_count,
        )
        for bucket, dimension, key, event_count, visitor_count, session_count in metric_values
    ]
    funnel_rows: list[FunnelResultRow] = []
    for funnel, (entrants, completed) in zip(funnels, funnel_values, strict=True):
        for step in funnel.steps:
            registry.require(step.event_code, step.event_schema_version)
        existing = db.scalar(
            select(FunnelDefinitionRow).where(
                FunnelDefinitionRow.tenant_id == command.tenant_id,
                FunnelDefinitionRow.property_id == command.property_id,
                FunnelDefinitionRow.code == funnel.code,
                FunnelDefinitionRow.version == funnel.version,
            )
        )
        steps = [[step.event_code, step.event_schema_version] for step in funnel.steps]
        if existing is None:
            db.add(
                FunnelDefinitionRow(
                    tenant_id=command.tenant_id,
                    property_id=command.property_id,
                    code=funnel.code,
                    version=funnel.version,
                    steps_json=steps,
                    within_seconds=funnel.within_seconds,
                )
            )
        elif (
            existing.steps_json != steps
            or existing.within_seconds != funnel.within_seconds
        ):
            from dotmac_web_analytics.contracts import InvalidContract

            raise InvalidContract(
                f"funnel {funnel.code!r} v{funnel.version} was redefined"
            )
        funnel_rows.append(
            FunnelResultRow(
                tenant_id=command.tenant_id,
                generation_id=UUID(int=0),
                property_id=command.property_id,
                definition_code=funnel.code,
                definition_version=funnel.version,
                entrants=entrants,
                completed_by_step_json=list(completed),
            )
        )

    projection_digest = _hash(
        _projection_document(visitors, sessions, metrics, funnel_rows)
    )
    generation_id = uuid4()
    for visitor_row in visitors:
        visitor_row.generation_id = generation_id
    for session_row in sessions:
        session_row.generation_id = generation_id
    for link_row in links:
        link_row.generation_id = generation_id
    for metric_row in metrics:
        metric_row.generation_id = generation_id
    for funnel_row in funnel_rows:
        funnel_row.generation_id = generation_id

    _delete_projection_rows(db, command.tenant_id, command.property_id)
    rule = db.scalar(
        select(SessionizationRuleRow).where(
            SessionizationRuleRow.tenant_id == command.tenant_id,
            SessionizationRuleRow.property_id == command.property_id,
            SessionizationRuleRow.code == command.session_rule.code,
            SessionizationRuleRow.version == command.session_rule.version,
        )
    )
    if rule is None:
        db.add(
            SessionizationRuleRow(
                tenant_id=command.tenant_id,
                property_id=command.property_id,
                code=command.session_rule.code,
                version=command.session_rule.version,
                inactivity_seconds=command.session_rule.inactivity_seconds,
            )
        )
    elif rule.inactivity_seconds != command.session_rule.inactivity_seconds:
        from dotmac_web_analytics.contracts import InvalidContract

        raise InvalidContract("sessionization rule version was redefined")
    generation = ProjectionGeneration(
        id=generation_id,
        tenant_id=command.tenant_id,
        property_id=command.property_id,
        projection_version=command.projection_version,
        session_rule_code=command.session_rule.code,
        session_rule_version=command.session_rule.version,
        timezone_name=command.timezone_name,
        authoritative_digest=authoritative,
        projection_digest=projection_digest,
        observation_count=len(observations),
        created_at=command.requested_at,
    )
    db.add_all([generation, *visitors, *sessions, *links, *metrics, *funnel_rows])
    prop.active_generation_id = generation_id
    db.flush()
    return ProjectionRepairResult(previous, generation_id, projection_digest)


def read_sessions(
    db: Session, *, tenant_id: UUID, property_id: UUID
) -> tuple[SessionProjection, ...]:
    prop = _property(db, tenant_id, property_id)
    if prop.active_generation_id is None:
        return ()
    rows = db.scalars(
        select(SessionProjectionRow)
        .where(
            SessionProjectionRow.tenant_id == tenant_id,
            SessionProjectionRow.property_id == property_id,
            SessionProjectionRow.generation_id == prop.active_generation_id,
        )
        .order_by(SessionProjectionRow.started_at, SessionProjectionRow.session_key)
    )
    return tuple(
        SessionProjection(
            row.tenant_id,
            row.property_id,
            row.session_key,
            row.visitor_digest,
            row.rule_code,
            row.rule_version,
            _as_utc(row.started_at),
            _as_utc(row.ended_at),
            row.event_count,
        )
        for row in rows
    )


def read_visitors(
    db: Session, *, tenant_id: UUID, property_id: UUID
) -> tuple[VisitorProjection, ...]:
    prop = _property(db, tenant_id, property_id)
    if prop.active_generation_id is None:
        return ()
    rows = db.scalars(
        select(VisitorProjectionRow)
        .where(
            VisitorProjectionRow.tenant_id == tenant_id,
            VisitorProjectionRow.property_id == property_id,
            VisitorProjectionRow.generation_id == prop.active_generation_id,
        )
        .order_by(VisitorProjectionRow.visitor_digest)
    )
    return tuple(
        VisitorProjection(
            row.tenant_id,
            row.property_id,
            row.visitor_digest,
            row.pseudonym_key_version,
            _as_utc(row.first_seen_at),
            _as_utc(row.last_seen_at),
        )
        for row in rows
    )


def read_aggregate_metrics(
    db: Session, query: AggregateMetricQuery
) -> tuple[AggregateMetricRow, ...]:
    prop = _property(db, query.tenant_id, query.property_id)
    if prop.active_generation_id is None:
        return ()
    generation = db.scalar(
        select(ProjectionGeneration).where(
            ProjectionGeneration.tenant_id == query.tenant_id,
            ProjectionGeneration.property_id == query.property_id,
            ProjectionGeneration.id == prop.active_generation_id,
        )
    )
    if generation is None or generation.timezone_name != query.timezone_name:
        from dotmac_web_analytics.contracts import InvalidContract

        raise InvalidContract("metric timezone does not match the active generation")
    rows = db.scalars(
        select(AggregateMetric)
        .where(
            AggregateMetric.tenant_id == query.tenant_id,
            AggregateMetric.property_id == query.property_id,
            AggregateMetric.generation_id == prop.active_generation_id,
            AggregateMetric.bucket_start >= query.starts_at,
            AggregateMetric.bucket_start < query.ends_at,
            AggregateMetric.dimension.in_([str(value) for value in query.dimensions]),
        )
        .order_by(
            AggregateMetric.bucket_start,
            AggregateMetric.dimension,
            AggregateMetric.dimension_key,
        )
    )
    return tuple(
        AggregateMetricRow(
            _as_utc(row.bucket_start),
            ((MetricDimension(row.dimension), row.dimension_key),),
            row.event_count,
            row.visitor_count,
            row.session_count,
        )
        for row in rows
    )


def read_funnel_result(
    db: Session,
    *,
    tenant_id: UUID,
    property_id: UUID,
    definition_code: str,
    definition_version: int,
) -> FunnelResult:
    prop = _property(db, tenant_id, property_id)
    row = db.scalar(
        select(FunnelResultRow).where(
            FunnelResultRow.tenant_id == tenant_id,
            FunnelResultRow.property_id == property_id,
            FunnelResultRow.generation_id == prop.active_generation_id,
            FunnelResultRow.definition_code == definition_code,
            FunnelResultRow.definition_version == definition_version,
        )
    )
    if row is None:
        from dotmac_web_analytics.contracts import InvalidContract

        raise InvalidContract("funnel result is not present in the active generation")
    return FunnelResult(
        row.definition_code,
        row.definition_version,
        row.generation_id,
        row.entrants,
        tuple(row.completed_by_step_json),
    )


def _stored_projection_digest(
    db: Session, tenant_id: UUID, property_id: UUID, generation_id: UUID
) -> tuple[str, int]:
    visitors = list(
        db.scalars(
            select(VisitorProjectionRow).where(
                VisitorProjectionRow.tenant_id == tenant_id,
                VisitorProjectionRow.property_id == property_id,
                VisitorProjectionRow.generation_id == generation_id,
            )
        )
    )
    sessions = list(
        db.scalars(
            select(SessionProjectionRow).where(
                SessionProjectionRow.tenant_id == tenant_id,
                SessionProjectionRow.property_id == property_id,
                SessionProjectionRow.generation_id == generation_id,
            )
        )
    )
    metrics = list(
        db.scalars(
            select(AggregateMetric).where(
                AggregateMetric.tenant_id == tenant_id,
                AggregateMetric.property_id == property_id,
                AggregateMetric.generation_id == generation_id,
            )
        )
    )
    funnels = list(
        db.scalars(
            select(FunnelResultRow).where(
                FunnelResultRow.tenant_id == tenant_id,
                FunnelResultRow.property_id == property_id,
                FunnelResultRow.generation_id == generation_id,
            )
        )
    )
    projected_events = db.scalar(
        select(func.coalesce(func.sum(AggregateMetric.event_count), 0)).where(
            AggregateMetric.tenant_id == tenant_id,
            AggregateMetric.property_id == property_id,
            AggregateMetric.generation_id == generation_id,
            AggregateMetric.dimension == str(MetricDimension.EVENT),
        )
    )
    return _hash(_projection_document(visitors, sessions, metrics, funnels)), int(
        projected_events or 0
    )


def detect_projection_drift(
    db: Session,
    *,
    tenant_id: UUID,
    property_id: UUID,
    registry: EventDeclarationRegistry,
) -> ProjectionDriftReport:
    prop = _property(db, tenant_id, property_id)
    observations = _observations(db, tenant_id, property_id)
    for observation in observations:
        registry.require(observation.event_code, observation.event_schema_version)
    classifications = _effective_classifications(db, tenant_id, observations)
    authoritative = _authoritative_digest(observations, classifications)
    projection_digest = None
    projected_events = 0
    generation = None
    if prop.active_generation_id is not None:
        generation = db.get(ProjectionGeneration, prop.active_generation_id)
        projection_digest, projected_events = _stored_projection_digest(
            db, tenant_id, property_id, prop.active_generation_id
        )
    drifted = (
        generation is None
        or generation.authoritative_digest != authoritative
        or generation.projection_digest != projection_digest
    )
    if drifted:
        db.add(
            ProjectionDriftEvidence(
                tenant_id=tenant_id,
                property_id=property_id,
                generation_id=prop.active_generation_id,
                authoritative_digest=authoritative,
                projection_digest=projection_digest or "sha256:" + "0" * 64,
                detected_at=datetime.now(UTC),
                repaired_generation_id=None,
            )
        )
        db.flush()
    return ProjectionDriftReport(
        tenant_id,
        property_id,
        prop.active_generation_id,
        authoritative,
        projection_digest,
        len(observations),
        projected_events,
        drifted,
    )


def repair_projection_drift(
    db: Session,
    *,
    command: RebuildProjectionsCommand,
    registry: EventDeclarationRegistry,
    funnels: tuple[FunnelDefinition, ...],
) -> ProjectionRepairResult:
    report = detect_projection_drift(
        db,
        tenant_id=command.tenant_id,
        property_id=command.property_id,
        registry=registry,
    )
    result = rebuild_projections(
        db, command=command, registry=registry, funnels=funnels
    )
    if report.drifted:
        db.add(
            ProjectionDriftEvidence(
                tenant_id=command.tenant_id,
                property_id=command.property_id,
                generation_id=report.active_generation_id,
                authoritative_digest=report.authoritative_digest,
                projection_digest=report.projection_digest or "sha256:" + "0" * 64,
                detected_at=command.requested_at,
                repaired_generation_id=result.active_generation_id,
            )
        )
        db.flush()
    return result


__all__ = [
    "detect_projection_drift",
    "read_aggregate_metrics",
    "read_funnel_result",
    "read_sessions",
    "read_visitors",
    "rebuild_projections",
    "repair_projection_drift",
]
