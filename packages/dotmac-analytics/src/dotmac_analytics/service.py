"""Transactional analytics ingestion, reads and deterministic repair.

Every mutation participates in the caller's transaction. This module never
opens a session, commits or rolls back. Expected uniqueness races are isolated
with the kernel conflict savepoint.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from dotmac_analytics.contracts import (
    MAX_HISTORY_POINTS,
    IngestResult,
    InvalidAnalyticsContract,
    MetricComparison,
    MetricDeclaration,
    MetricDeclarationRegistry,
    MetricGranularity,
    MetricIdentityConflict,
    MetricSelector,
    MetricValue,
    ProjectionRebuildResult,
    RecordMetricBatchCommand,
    selector_digest,
)
from dotmac_analytics.models import (
    MetricCatalogEntry,
    MetricIngestReceipt,
    MetricObservation,
    MetricPoint,
    MetricProjectionRebuild,
)

ANALYTICS_INGEST_SCOPE = "analytics.record_metric_batch"
_WRITE_LOCK_DOMAIN = b"dotmac-analytics.tenant-writes.v1:"


def _serialize_tenant_writes(db: Session, tenant_id: UUID) -> None:
    """Serialize ingestion and full repair without locking a product row."""
    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(_WRITE_LOCK_DOMAIN + tenant_id.bytes).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    db.scalar(select(func.pg_advisory_xact_lock(lock_key)))


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _batch_document(
    command: RecordMetricBatchCommand,
    validated: tuple[
        tuple[MetricDeclaration, tuple[tuple[str, str], ...]], ...
    ],
) -> dict[str, object]:
    points: list[dict[str, object]] = []
    for point, (_, dimensions) in zip(command.points, validated, strict=True):
        points.append(
            {
                "metric_code": point.metric_code,
                "schema_version": point.schema_version,
                "period_start": _utc_iso(point.period_start),
                "period_end": _utc_iso(point.period_end),
                "granularity": point.granularity.value,
                "dimensions": dimensions,
                "value": _canonical_decimal(point.value),
                "currency_code": point.currency_code,
            }
        )
    points.sort(
        key=lambda item: (
            str(item["metric_code"]),
            cast(int, item["schema_version"]),
            str(item["period_start"]),
            str(item["period_end"]),
            str(item["granularity"]),
            repr(item["dimensions"]),
            str(item["currency_code"]),
        )
    )
    return {
        "domain": "dotmac-analytics.metric-batch.v1",
        "tenant_id": str(command.tenant_id),
        "source_owner": command.provenance.source_owner,
        "source_event_id": command.provenance.source_event_id,
        "source_schema_version": command.provenance.source_schema_version,
        "source_reference": command.provenance.source_reference,
        "observed_at": _utc_iso(command.observed_at),
        "points": points,
    }


def _ensure_catalog_entry(
    db: Session, *, tenant_id: UUID, declaration: MetricDeclaration
) -> MetricCatalogEntry:
    existing = db.scalar(
        select(MetricCatalogEntry).where(
            MetricCatalogEntry.tenant_id == tenant_id,
            MetricCatalogEntry.metric_code == declaration.metric_code,
            MetricCatalogEntry.schema_version == declaration.schema_version,
        )
    )
    if existing is not None:
        if existing.declaration_fingerprint != declaration.fingerprint:
            raise InvalidAnalyticsContract(
                f"activated metric {declaration.metric_code!r} v"
                f"{declaration.schema_version} cannot change declaration"
            )
        return existing

    row = MetricCatalogEntry(
        tenant_id=tenant_id,
        metric_code=declaration.metric_code,
        schema_version=declaration.schema_version,
        owner_code=declaration.owner_code,
        declaration_fingerprint=declaration.fingerprint,
        display_name=declaration.display_name,
        value_kind=declaration.value_kind.value,
        unit_code=declaration.unit_code,
        granularities_json=sorted(item.value for item in declaration.granularities),
        dimensions_json=declaration.serialized_dimensions(),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        winner = db.scalar(
            select(MetricCatalogEntry).where(
                MetricCatalogEntry.tenant_id == tenant_id,
                MetricCatalogEntry.metric_code == declaration.metric_code,
                MetricCatalogEntry.schema_version == declaration.schema_version,
            )
        )
        if winner is None:
            raise
        if winner.declaration_fingerprint != declaration.fingerprint:
            raise InvalidAnalyticsContract(
                f"activated metric {declaration.metric_code!r} v"
                f"{declaration.schema_version} cannot change declaration"
            )
        return winner


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _observation_rank(
    observation: MetricObservation,
) -> tuple[datetime, datetime, str]:
    return (
        _as_utc(observation.observed_at),
        _as_utc(observation.received_at),
        str(observation.id),
    )


def _point_rank(point: MetricPoint) -> tuple[datetime, datetime, str]:
    return (
        _as_utc(point.observed_at),
        _as_utc(point.received_at),
        str(point.observation_id),
    )


def _copy_observation_to_point(
    point: MetricPoint, observation: MetricObservation
) -> None:
    point.dimensions_json = observation.dimensions_json
    point.value_numeric = observation.value_numeric
    point.currency_code = observation.currency_code
    point.observation_id = observation.id
    point.observed_at = observation.observed_at
    point.received_at = observation.received_at


def _new_point(observation: MetricObservation) -> MetricPoint:
    return MetricPoint(
        tenant_id=observation.tenant_id,
        metric_code=observation.metric_code,
        schema_version=observation.schema_version,
        period_start=observation.period_start,
        period_end=observation.period_end,
        granularity=observation.granularity,
        selector_digest=observation.selector_digest,
        dimensions_json=observation.dimensions_json,
        value_numeric=observation.value_numeric,
        currency_code=observation.currency_code,
        observation_id=observation.id,
        observed_at=observation.observed_at,
        received_at=observation.received_at,
    )


def _point_query(observation: MetricObservation) -> Select[tuple[MetricPoint]]:
    return select(MetricPoint).where(
        MetricPoint.tenant_id == observation.tenant_id,
        MetricPoint.metric_code == observation.metric_code,
        MetricPoint.schema_version == observation.schema_version,
        MetricPoint.period_start == observation.period_start,
        MetricPoint.period_end == observation.period_end,
        MetricPoint.granularity == observation.granularity,
        MetricPoint.selector_digest == observation.selector_digest,
    )


def _promote_observation(db: Session, observation: MetricObservation) -> None:
    current = db.scalar(_point_query(observation).with_for_update())
    if current is not None:
        if _observation_rank(observation) > _point_rank(current):
            _copy_observation_to_point(current, observation)
            db.flush()
        return

    point = _new_point(observation)
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(point)
            db.flush()
    except IntegrityError:
        current = db.scalar(_point_query(observation).with_for_update())
        if current is None:
            raise
        if _observation_rank(observation) > _point_rank(current):
            _copy_observation_to_point(current, observation)
            db.flush()


def _record_batch_effect(
    db: Session,
    *,
    command: RecordMetricBatchCommand,
    validated: tuple[
        tuple[MetricDeclaration, tuple[tuple[str, str], ...]], ...
    ],
) -> dict[str, object]:
    ensured: set[tuple[str, int]] = set()
    for declaration, _ in validated:
        if declaration.identity not in ensured:
            _ensure_catalog_entry(
                db, tenant_id=command.tenant_id, declaration=declaration
            )
            ensured.add(declaration.identity)

    receipt = MetricIngestReceipt(
        tenant_id=command.tenant_id,
        source_owner=command.provenance.source_owner,
        source_event_id=command.provenance.source_event_id,
        source_schema_version=command.provenance.source_schema_version,
        source_reference=command.provenance.source_reference,
        adapter_code=command.provenance.adapter_code,
        delivery_id=command.provenance.delivery_id,
        observed_at=command.observed_at,
        received_at=command.received_at,
        point_count=len(command.points),
    )
    observations: list[MetricObservation] = []
    db.add(receipt)
    db.flush()
    for point, (_, dimensions) in zip(command.points, validated, strict=True):
        observation = MetricObservation(
            tenant_id=command.tenant_id,
            receipt_id=receipt.id,
            metric_code=point.metric_code,
            schema_version=point.schema_version,
            period_start=point.period_start,
            period_end=point.period_end,
            granularity=point.granularity.value,
            selector_digest=selector_digest(dimensions, point.currency_code),
            dimensions_json=[list(item) for item in dimensions],
            value_numeric=point.value,
            currency_code=point.currency_code,
            observed_at=command.observed_at,
            received_at=command.received_at,
        )
        db.add(observation)
        observations.append(observation)
    db.flush()

    for observation in observations:
        _promote_observation(db, observation)
    return {
        "receipt_id": str(receipt.id),
        "accepted_points": len(observations),
    }


def record_batch(
    db: Session,
    *,
    command: RecordMetricBatchCommand,
    declarations: MetricDeclarationRegistry,
) -> IngestResult:
    """Accept one aggregate batch through the kernel at-most-once owner."""
    validated = declarations.validate_batch(command)
    _serialize_tenant_writes(db, command.tenant_id)

    # Local imports preserve package importability before an adopter configures
    # its database engine, matching the other installable module adapters.
    from dotmac_kernel.idempotency import (
        IdempotencyConflict,
        execute_once,
        fingerprint_of,
    )

    identity_key = fingerprint_of(
        {
            "source_owner": command.provenance.source_owner,
            "source_event_id": command.provenance.source_event_id,
        }
    )
    fingerprint = fingerprint_of(_batch_document(command, validated))

    def operation(session: Session) -> dict[str, object]:
        return _record_batch_effect(
            session,
            command=command,
            validated=validated,
        )

    try:
        outcome = execute_once(
            db,
            tenant_id=command.tenant_id,
            scope=ANALYTICS_INGEST_SCOPE,
            key=identity_key,
            operation=operation,
            operation_name=ANALYTICS_INGEST_SCOPE,
            fingerprint=fingerprint,
        )
    except IdempotencyConflict as exc:
        raise MetricIdentityConflict(
            f"source event {command.provenance.source_owner!r}/"
            f"{command.provenance.source_event_id!r} was reused with different content"
        ) from exc

    result = outcome.result
    return IngestResult(
        receipt_id=UUID(str(result["receipt_id"])),
        accepted_points=int(str(result["accepted_points"])),
        replayed=outcome.replayed,
    )


def _dimension_pairs(value: list[list[str]]) -> tuple[tuple[str, str], ...]:
    return tuple((str(item[0]), str(item[1])) for item in value)


def _as_value(point: MetricPoint) -> MetricValue:
    return MetricValue(
        metric_code=point.metric_code,
        schema_version=point.schema_version,
        period_start=point.period_start,
        period_end=point.period_end,
        granularity=MetricGranularity(point.granularity),
        dimensions=_dimension_pairs(point.dimensions_json),
        value=Decimal(str(point.value_numeric)),
        currency_code=point.currency_code,
        observed_at=point.observed_at,
        received_at=point.received_at,
        observation_id=point.observation_id,
    )


def _selector_predicates(
    selector: MetricSelector,
    declarations: MetricDeclarationRegistry,
) -> tuple[ColumnElement[bool], ...]:
    dimensions = declarations.validate_selector(selector)
    return (
        MetricPoint.metric_code == selector.metric_code,
        MetricPoint.schema_version == selector.schema_version,
        MetricPoint.granularity == selector.granularity.value,
        MetricPoint.selector_digest
        == selector_digest(dimensions, selector.currency_code),
    )


def get_latest(
    db: Session,
    *,
    tenant_id: UUID,
    selectors: Sequence[MetricSelector],
    declarations: MetricDeclarationRegistry,
) -> tuple[MetricValue, ...]:
    """Return the latest present point for each selector; omit missing series."""
    if len(selectors) > 250:
        raise InvalidAnalyticsContract("latest read accepts at most 250 selectors")
    values: list[MetricValue] = []
    for selector in selectors:
        point = db.scalar(
            select(MetricPoint)
            .where(
                MetricPoint.tenant_id == tenant_id,
                *_selector_predicates(selector, declarations),
            )
            .order_by(MetricPoint.period_start.desc(), MetricPoint.observed_at.desc())
            .limit(1)
        )
        if point is not None:
            values.append(_as_value(point))
    return tuple(values)


def get_history(
    db: Session,
    *,
    tenant_id: UUID,
    selector: MetricSelector,
    declarations: MetricDeclarationRegistry,
    start: datetime,
    end: datetime,
    limit: int = 500,
) -> tuple[MetricValue, ...]:
    _require_read_range(start=start, end=end, limit=limit)
    points = db.scalars(
        select(MetricPoint)
        .where(
            MetricPoint.tenant_id == tenant_id,
            *_selector_predicates(selector, declarations),
            MetricPoint.period_start >= start,
            MetricPoint.period_start <= end,
        )
        .order_by(MetricPoint.period_start)
        .limit(limit)
    ).all()
    return tuple(_as_value(point) for point in points)


def _require_read_range(*, start: datetime, end: datetime, limit: int) -> None:
    for field_name, value in (("start", start), ("end", end)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidAnalyticsContract(f"{field_name} must be timezone-aware")
    if end < start:
        raise InvalidAnalyticsContract("history end must not precede start")
    if not 1 <= limit <= MAX_HISTORY_POINTS:
        raise InvalidAnalyticsContract(
            f"history limit must be between 1 and {MAX_HISTORY_POINTS}"
        )


def _point_at(
    db: Session,
    *,
    tenant_id: UUID,
    selector: MetricSelector,
    declarations: MetricDeclarationRegistry,
    period_start: datetime,
    period_end: datetime,
) -> MetricPoint | None:
    return db.scalar(
        select(MetricPoint).where(
            MetricPoint.tenant_id == tenant_id,
            *_selector_predicates(selector, declarations),
            MetricPoint.period_start == period_start,
            MetricPoint.period_end == period_end,
        )
    )


def compare_periods(
    db: Session,
    *,
    tenant_id: UUID,
    selector: MetricSelector,
    declarations: MetricDeclarationRegistry,
    current_period_start: datetime,
    current_period_end: datetime,
    prior_period_start: datetime,
    prior_period_end: datetime,
) -> MetricComparison:
    for field_name, value in (
        ("current_period_start", current_period_start),
        ("current_period_end", current_period_end),
        ("prior_period_start", prior_period_start),
        ("prior_period_end", prior_period_end),
    ):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidAnalyticsContract(f"{field_name} must be timezone-aware")
    if current_period_end <= current_period_start:
        raise InvalidAnalyticsContract(
            "current_period_end must be after current_period_start"
        )
    if prior_period_end <= prior_period_start:
        raise InvalidAnalyticsContract(
            "prior_period_end must be after prior_period_start"
        )
    current = _point_at(
        db,
        tenant_id=tenant_id,
        selector=selector,
        declarations=declarations,
        period_start=current_period_start,
        period_end=current_period_end,
    )
    prior = _point_at(
        db,
        tenant_id=tenant_id,
        selector=selector,
        declarations=declarations,
        period_start=prior_period_start,
        period_end=prior_period_end,
    )
    current_value = Decimal(str(current.value_numeric)) if current is not None else None
    prior_value = Decimal(str(prior.value_numeric)) if prior is not None else None
    delta = None
    percentage = None
    if current_value is not None and prior_value is not None:
        delta = current_value - prior_value
        if prior_value != 0:
            percentage = delta / prior_value * Decimal("100")
    return MetricComparison(
        metric_code=selector.metric_code,
        currency_code=selector.currency_code,
        current_value=current_value,
        prior_value=prior_value,
        delta=delta,
        percentage_change=percentage,
    )


def _projection_document(points: Sequence[MetricPoint]) -> list[dict[str, object]]:
    return [
        {
            "metric_code": point.metric_code,
            "schema_version": point.schema_version,
            "period_start": _utc_iso(point.period_start),
            "period_end": _utc_iso(point.period_end),
            "granularity": point.granularity,
            "selector_digest": point.selector_digest,
            "dimensions": point.dimensions_json,
            "value": format(Decimal(str(point.value_numeric)), "f"),
            "currency_code": point.currency_code,
            "observation_id": str(point.observation_id),
            "observed_at": _utc_iso(point.observed_at),
            "received_at": _utc_iso(point.received_at),
        }
        for point in sorted(
            points,
            key=lambda item: (
                item.metric_code,
                item.schema_version,
                _utc_iso(item.period_start),
                _utc_iso(item.period_end),
                item.granularity,
                item.selector_digest,
            ),
        )
    ]


def projection_digest(db: Session, *, tenant_id: UUID) -> str:
    points = db.scalars(
        select(MetricPoint).where(MetricPoint.tenant_id == tenant_id)
    ).all()
    encoded = json.dumps(
        _projection_document(points), sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _coordinate(observation: MetricObservation) -> tuple[object, ...]:
    return (
        observation.metric_code,
        observation.schema_version,
        _utc_iso(observation.period_start),
        _utc_iso(observation.period_end),
        observation.granularity,
        observation.selector_digest,
    )


def rebuild_projection(
    db: Session,
    *,
    tenant_id: UUID,
    rebuilt_by: str,
    rebuilt_at: datetime,
) -> ProjectionRebuildResult:
    """Rebuild one tenant's points from retained immutable observations."""
    if not rebuilt_by or len(rebuilt_by) > 255:
        raise InvalidAnalyticsContract("rebuilt_by must contain 1..255 characters")
    if rebuilt_at.tzinfo is None or rebuilt_at.utcoffset() is None:
        raise InvalidAnalyticsContract("rebuilt_at must be timezone-aware")
    _serialize_tenant_writes(db, tenant_id)
    before = projection_digest(db, tenant_id=tenant_id)
    observations = db.scalars(
        select(MetricObservation).where(MetricObservation.tenant_id == tenant_id)
    ).all()
    winners: dict[tuple[object, ...], MetricObservation] = {}
    for observation in observations:
        coordinate = _coordinate(observation)
        winner = winners.get(coordinate)
        if winner is None or _observation_rank(observation) > _observation_rank(winner):
            winners[coordinate] = observation

    db.execute(delete(MetricPoint).where(MetricPoint.tenant_id == tenant_id))
    db.flush()
    for observation in winners.values():
        db.add(_new_point(observation))
    db.flush()
    after = projection_digest(db, tenant_id=tenant_id)
    rebuild = MetricProjectionRebuild(
        tenant_id=tenant_id,
        before_digest=before,
        after_digest=after,
        point_count=len(winners),
        rebuilt_by=rebuilt_by,
        rebuilt_at=rebuilt_at,
    )
    db.add(rebuild)
    db.flush()
    return ProjectionRebuildResult(
        before_digest=before,
        after_digest=after,
        point_count=len(winners),
        rebuild_id=rebuild.id,
    )


__all__ = [
    "compare_periods",
    "get_history",
    "get_latest",
    "projection_digest",
    "rebuild_projection",
    "record_batch",
]
