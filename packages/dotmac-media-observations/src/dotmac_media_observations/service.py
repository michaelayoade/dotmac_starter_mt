"""Append immutable media facts and rebuild their disposable projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_media_observations.contracts import (
    ClaimStatus,
    CountValue,
    CurrentEntityState,
    DecimalValue,
    DriftItem,
    DriftReport,
    DurationValue,
    EntityDisposition,
    EntityObservation,
    ExactMoney,
    HierarchyObservation,
    InvalidObservation,
    JsonValue,
    MetricDefinitionDeclaration,
    MetricObservation,
    MetricSemantic,
    MetricValue,
    MetricValueType,
    NodeTypeDeclaration,
    NormalizedMediaFact,
    ObservationCommand,
    ObservationConflict,
    ObservationKind,
    ObservationRejection,
    ObservationSource,
    ObservedHierarchyEdge,
    PeriodMetric,
    ProviderRestatement,
    RatioValue,
    ReconciliationResult,
    RecordOutcome,
    RecordStatus,
    UnsupportedObservation,
)
from dotmac_media_observations.models import (
    CurrentEntity,
    CurrentHierarchy,
    CurrentMetric,
    EntityFact,
    HierarchyFact,
    MetricDefinition,
    MetricFact,
    MetricPeriod,
    NodeDefinition,
    ObservationEnvelope,
    ObservationReceipt,
    ReconciliationEvidence,
)

_EntityKey = tuple[str, str, str, str]
_HierarchyKey = tuple[str, str, str, str]


def declare_node_type(db: Session, command: NodeTypeDeclaration) -> NodeDefinition:
    content = {
        "code": command.code,
        "version": command.version,
        "label": command.label,
        "traits": command.traits,
        "declared_by": command.declared_by,
        "declared_at": command.declared_at,
    }
    fingerprint = _fingerprint(content)
    existing = db.scalar(
        select(NodeDefinition).where(
            NodeDefinition.tenant_id == command.tenant_id,
            NodeDefinition.code == command.code,
            NodeDefinition.version == command.version,
        )
    )
    if existing is not None:
        _require_same_definition(
            existing.definition_fingerprint,
            fingerprint,
            code=command.code,
            version=command.version,
        )
        return existing
    definition = NodeDefinition(
        tenant_id=command.tenant_id,
        code=command.code,
        version=command.version,
        label=command.label,
        traits=cast(dict[str, object], _json_storage(command.traits)),
        definition_fingerprint=fingerprint,
        declared_by=command.declared_by,
        declared_at=_utc(command.declared_at),
    )
    db.add(definition)
    try:
        db.flush()
    except IntegrityError as exc:
        raise ObservationConflict(
            ObservationRejection(
                code="declaration_identity_conflict",
                message=(
                    f"node declaration {command.code!r} v{command.version} "
                    "conflicts with concurrent stored evidence"
                ),
            )
        ) from exc
    return definition


def declare_metric(
    db: Session, command: MetricDefinitionDeclaration
) -> MetricDefinition:
    content = {
        "code": command.code,
        "version": command.version,
        "label": command.label,
        "value_type": command.value_type,
        "unit": command.unit,
        "semantic": command.semantic,
        "observation_origin": ClaimStatus.PROVIDER_REPORTED,
        "declared_by": command.declared_by,
        "declared_at": command.declared_at,
    }
    fingerprint = _fingerprint(content)
    existing = db.scalar(
        select(MetricDefinition).where(
            MetricDefinition.tenant_id == command.tenant_id,
            MetricDefinition.code == command.code,
            MetricDefinition.version == command.version,
        )
    )
    if existing is not None:
        _require_same_definition(
            existing.definition_fingerprint,
            fingerprint,
            code=command.code,
            version=command.version,
        )
        return existing
    definition = MetricDefinition(
        tenant_id=command.tenant_id,
        code=command.code,
        version=command.version,
        label=command.label,
        value_type=command.value_type.value,
        unit=command.unit,
        semantic=command.semantic.value,
        observation_origin=ClaimStatus.PROVIDER_REPORTED.value,
        definition_fingerprint=fingerprint,
        declared_by=command.declared_by,
        declared_at=_utc(command.declared_at),
    )
    db.add(definition)
    try:
        db.flush()
    except IntegrityError as exc:
        raise ObservationConflict(
            ObservationRejection(
                code="declaration_identity_conflict",
                message=(
                    f"metric declaration {command.code!r} v{command.version} "
                    "conflicts with concurrent stored evidence"
                ),
            )
        ) from exc
    return definition


def record_entity(db: Session, command: EntityObservation) -> RecordOutcome:
    _require_node_definition(
        db,
        tenant_id=command.source.tenant_id,
        code=command.node_code,
        version=command.node_version,
    )
    content = {
        "external_account_ref": command.external_account_ref,
        "entity_ref": command.entity_ref,
        "node_code": command.node_code,
        "node_version": command.node_version,
        "name": command.name,
        "state": command.state,
        "disposition": command.disposition,
        "properties": command.properties,
    }
    envelope, status, created = _record_envelope(
        db,
        source=command.source,
        kind=ObservationKind.ENTITY,
        fact_content=content,
        restates_observation_id=command.restates_observation_id,
    )
    if created:
        db.add(
            EntityFact(
                tenant_id=command.source.tenant_id,
                observation_id=envelope.id,
                external_account_ref=command.external_account_ref,
                entity_ref=command.entity_ref,
                node_code=command.node_code,
                node_version=command.node_version,
                name=command.name,
                state=command.state,
                disposition=command.disposition.value,
                properties=cast(dict[str, object], _json_storage(command.properties)),
            )
        )
        db.flush()
        _refresh_projections(db, command.source.tenant_id)
    return RecordOutcome(envelope.id, envelope.content_fingerprint, status)


def record_hierarchy(db: Session, command: HierarchyObservation) -> RecordOutcome:
    content: dict[str, object] = {
        "external_account_ref": command.external_account_ref,
        "child_entity_ref": command.child_entity_ref,
        "parent_entity_ref": command.parent_entity_ref,
    }
    envelope, status, created = _record_envelope(
        db,
        source=command.source,
        kind=ObservationKind.HIERARCHY,
        fact_content=content,
        restates_observation_id=command.restates_observation_id,
    )
    if created:
        db.add(
            HierarchyFact(
                tenant_id=command.source.tenant_id,
                observation_id=envelope.id,
                external_account_ref=command.external_account_ref,
                child_entity_ref=command.child_entity_ref,
                parent_entity_ref=command.parent_entity_ref,
            )
        )
        db.flush()
        _refresh_projections(db, command.source.tenant_id)
    return RecordOutcome(envelope.id, envelope.content_fingerprint, status)


def record_metric(db: Session, command: MetricObservation) -> RecordOutcome:
    definition = _require_metric_definition(
        db,
        tenant_id=command.source.tenant_id,
        code=command.metric_code,
        version=command.metric_version,
    )
    if definition.value_type != command.value.value_type.value:
        raise InvalidObservation(
            f"metric {command.metric_code!r} v{command.metric_version} expects "
            f"{definition.value_type}, received {command.value.value_type.value}"
        )
    content = {
        "external_account_ref": command.external_account_ref,
        "entity_ref": command.entity_ref,
        "metric_code": command.metric_code,
        "metric_version": command.metric_version,
        "period_start": command.period_start,
        "period_end": command.period_end,
        "value": _value_content(command.value),
        "claim_status": ClaimStatus.PROVIDER_REPORTED,
    }
    # An already-stored identity is checked before period work. For a genuinely
    # new metric, one savepoint covers period + envelope + fact so a changed
    # identity racing on another metric series cannot strand a canonical period.
    if _source_identity(db, command.source) is not None:
        envelope, status, _created = _record_envelope(
            db,
            source=command.source,
            kind=ObservationKind.METRIC,
            fact_content=content,
            restates_observation_id=command.restates_observation_id,
        )
        return RecordOutcome(envelope.id, envelope.content_fingerprint, status)

    with db.begin_nested():
        period = _get_or_create_period(db, command)
        envelope, status, created = _record_envelope(
            db,
            source=command.source,
            kind=ObservationKind.METRIC,
            fact_content=content,
            restates_observation_id=command.restates_observation_id,
        )
        if created:
            values = _value_columns(command.value)
            db.add(
                MetricFact(
                    tenant_id=command.source.tenant_id,
                    observation_id=envelope.id,
                    period_id=period.id,
                    value_type=command.value.value_type.value,
                    claim_status=ClaimStatus.PROVIDER_REPORTED.value,
                    **values,
                )
            )
            db.flush()
    if created:
        _refresh_projections(db, command.source.tenant_id)
    return RecordOutcome(envelope.id, envelope.content_fingerprint, status)


def record_restatement(db: Session, command: ProviderRestatement) -> RecordOutcome:
    replacement = command.replacement
    source = replacement.source
    original = db.scalar(
        select(ObservationEnvelope).where(
            ObservationEnvelope.tenant_id == source.tenant_id,
            ObservationEnvelope.id == command.replaces_observation_id,
        )
    )
    if original is None:
        raise InvalidObservation(
            f"restated observation {command.replaces_observation_id} was not found"
        )
    expected_kind = _kind_of(replacement)
    if original.kind != expected_kind.value:
        raise InvalidObservation(
            f"cannot restate {original.kind} evidence with "
            f"{expected_kind.value} evidence"
        )
    if (
        original.installation_ref != source.installation_ref
        or original.source_system != source.source_system
    ):
        raise InvalidObservation(
            "a restatement must retain installation_ref and source_system"
        )
    _require_same_restatement_subject(db, original, replacement)
    replacement = replace(
        replacement, restates_observation_id=command.replaces_observation_id
    )
    if isinstance(replacement, EntityObservation):
        return record_entity(db, replacement)
    if isinstance(replacement, HierarchyObservation):
        return record_hierarchy(db, replacement)
    return record_metric(db, replacement)


def read_current_entity(
    db: Session,
    *,
    tenant_id: UUID,
    installation_ref: str,
    source_system: str,
    external_account_ref: str,
    entity_ref: str,
) -> CurrentEntityState | None:
    row = db.scalar(
        select(CurrentEntity).where(
            CurrentEntity.tenant_id == tenant_id,
            CurrentEntity.installation_ref == installation_ref,
            CurrentEntity.source_system == source_system,
            CurrentEntity.external_account_ref == external_account_ref,
            CurrentEntity.entity_ref == entity_ref,
        )
    )
    if row is None:
        return None
    return CurrentEntityState(
        observation_id=row.observation_id,
        installation_ref=row.installation_ref,
        source_system=row.source_system,
        external_account_ref=row.external_account_ref,
        entity_ref=row.entity_ref,
        node_code=row.node_code,
        node_version=row.node_version,
        name=row.name,
        state=row.state,
        disposition=EntityDisposition(row.disposition),
        properties=cast(dict[str, JsonValue], row.properties),
        source_observed_at=_aware(row.source_observed_at),
    )


def read_hierarchy(
    db: Session,
    *,
    tenant_id: UUID,
    installation_ref: str,
    source_system: str,
    external_account_ref: str,
) -> tuple[ObservedHierarchyEdge, ...]:
    rows = db.scalars(
        select(CurrentHierarchy)
        .where(
            CurrentHierarchy.tenant_id == tenant_id,
            CurrentHierarchy.installation_ref == installation_ref,
            CurrentHierarchy.source_system == source_system,
            CurrentHierarchy.external_account_ref == external_account_ref,
        )
        .order_by(CurrentHierarchy.child_entity_ref)
    ).all()
    return tuple(
        ObservedHierarchyEdge(
            observation_id=row.observation_id,
            installation_ref=row.installation_ref,
            source_system=row.source_system,
            external_account_ref=row.external_account_ref,
            child_entity_ref=row.child_entity_ref,
            parent_entity_ref=row.parent_entity_ref,
            drift_code=row.drift_code,
            source_observed_at=_aware(row.source_observed_at),
        )
        for row in rows
    )


def read_period_metrics(
    db: Session,
    *,
    tenant_id: UUID,
    installation_ref: str,
    source_system: str,
    external_account_ref: str,
    entity_ref: str,
    period_start: datetime,
    period_end: datetime,
) -> tuple[PeriodMetric, ...]:
    query = (
        select(
            CurrentMetric,
            MetricFact,
            MetricPeriod,
            MetricDefinition,
            ObservationEnvelope,
        )
        .join(
            MetricFact,
            (MetricFact.tenant_id == CurrentMetric.tenant_id)
            & (MetricFact.observation_id == CurrentMetric.observation_id),
        )
        .join(
            MetricPeriod,
            (MetricPeriod.tenant_id == CurrentMetric.tenant_id)
            & (MetricPeriod.id == CurrentMetric.period_id),
        )
        .join(
            MetricDefinition,
            (MetricDefinition.tenant_id == MetricPeriod.tenant_id)
            & (MetricDefinition.code == MetricPeriod.metric_code)
            & (MetricDefinition.version == MetricPeriod.metric_version),
        )
        .join(
            ObservationEnvelope,
            (ObservationEnvelope.tenant_id == CurrentMetric.tenant_id)
            & (ObservationEnvelope.id == CurrentMetric.observation_id),
        )
        .where(
            CurrentMetric.tenant_id == tenant_id,
            MetricPeriod.installation_ref == installation_ref,
            MetricPeriod.source_system == source_system,
            MetricPeriod.external_account_ref == external_account_ref,
            MetricPeriod.entity_ref == entity_ref,
            MetricPeriod.period_start < _utc(period_end),
            MetricPeriod.period_end > _utc(period_start),
        )
        .order_by(MetricPeriod.period_start, MetricPeriod.metric_code)
    )
    out: list[PeriodMetric] = []
    for _current, fact, period, definition, envelope in db.execute(query).all():
        receipts = tuple(
            db.scalars(
                select(ObservationReceipt.transport_receipt_ref)
                .where(
                    ObservationReceipt.tenant_id == tenant_id,
                    ObservationReceipt.observation_id == envelope.id,
                )
                .order_by(ObservationReceipt.transport_receipt_ref)
            ).all()
        )
        out.append(
            PeriodMetric(
                observation_id=envelope.id,
                installation_ref=envelope.installation_ref,
                source_system=envelope.source_system,
                source_observation_id=envelope.source_observation_id,
                external_account_ref=period.external_account_ref,
                entity_ref=period.entity_ref,
                metric_code=period.metric_code,
                metric_version=period.metric_version,
                semantic=MetricSemantic(definition.semantic),
                unit=definition.unit,
                period_start=_aware(period.period_start),
                period_end=_aware(period.period_end),
                value=_value_from_fact(fact),
                claim_status=ClaimStatus(fact.claim_status),
                source_observed_at=_aware(envelope.source_observed_at),
                received_at=_aware(envelope.received_at),
                transport_receipt_refs=receipts,
                normalization_version=envelope.normalization_version,
            )
        )
    return tuple(out)


def report_hierarchy_drift(db: Session, *, tenant_id: UUID) -> DriftReport:
    expected = _expected_projections(db, tenant_id)
    items: list[DriftItem] = []
    for key, row in sorted(expected["hierarchy"].items()):
        if row["drift_code"]:
            items.append(
                DriftItem(
                    projection="hierarchy",
                    identity="/".join(key),
                    code=str(row["drift_code"]),
                )
            )
    items.extend(
        _compare_projection(
            "hierarchy",
            expected["hierarchy"],
            _actual_hierarchy(db, tenant_id),
        )
    )
    return DriftReport(tuple(items))


def report_metric_drift(db: Session, *, tenant_id: UUID) -> DriftReport:
    expected = _expected_projections(db, tenant_id)["metrics"]
    return DriftReport(
        tuple(_compare_projection("metric", expected, _actual_metrics(db, tenant_id)))
    )


def reconcile_projections(
    db: Session,
    *,
    tenant_id: UUID,
    actor_ref: str,
    reason: str,
    apply: bool,
) -> ReconciliationResult:
    if not actor_ref.strip() or not reason.strip():
        raise InvalidObservation("reconciliation requires actor_ref and reason")
    expected = _expected_projections(db, tenant_id)
    actual = {
        "entities": _actual_entities(db, tenant_id),
        "hierarchy": _actual_hierarchy(db, tenant_id),
        "metrics": _actual_metrics(db, tenant_id),
    }
    drift = [
        *_compare_projection("entity", expected["entities"], actual["entities"]),
        *_compare_projection("hierarchy", expected["hierarchy"], actual["hierarchy"]),
        *_compare_projection("metric", expected["metrics"], actual["metrics"]),
    ]
    before_digest = _fingerprint(actual)
    expected_digest = _fingerprint(expected)
    if apply:
        _replace_projections(db, tenant_id, expected)
    evidence = ReconciliationEvidence(
        tenant_id=tenant_id,
        actor_ref=actor_ref,
        reason=reason,
        drift_count=len(drift),
        before_digest=before_digest,
        expected_digest=expected_digest,
        applied=apply,
    )
    db.add(evidence)
    db.flush()
    return ReconciliationResult(
        evidence_id=evidence.id,
        drift_count=len(drift),
        before_digest=before_digest,
        expected_digest=expected_digest,
        applied=apply,
    )


def emit_analytics_fact(
    db: Session, *, tenant_id: UUID, observation_id: UUID
) -> NormalizedMediaFact:
    envelope = db.scalar(
        select(ObservationEnvelope).where(
            ObservationEnvelope.tenant_id == tenant_id,
            ObservationEnvelope.id == observation_id,
        )
    )
    if envelope is None:
        raise InvalidObservation(f"observation {observation_id} was not found")
    kind = ObservationKind(envelope.kind)
    account_ref: str
    entity_ref: str
    metric_code: str | None = None
    metric_version: int | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    value: MetricValue | None = None
    claim_status: ClaimStatus | None = None
    if kind is ObservationKind.ENTITY:
        entity_fact = _entity_fact(db, tenant_id, observation_id)
        account_ref = entity_fact.external_account_ref
        entity_ref = entity_fact.entity_ref
    elif kind is ObservationKind.HIERARCHY:
        hierarchy_fact = _hierarchy_fact(db, tenant_id, observation_id)
        account_ref = hierarchy_fact.external_account_ref
        entity_ref = hierarchy_fact.child_entity_ref
    else:
        metric_fact = _metric_fact(db, tenant_id, observation_id)
        period = db.scalar(
            select(MetricPeriod).where(
                MetricPeriod.tenant_id == tenant_id,
                MetricPeriod.id == metric_fact.period_id,
            )
        )
        if period is None:  # pragma: no cover - protected by composite FK
            raise InvalidObservation("metric period is missing")
        account_ref, entity_ref = period.external_account_ref, period.entity_ref
        metric_code, metric_version = period.metric_code, period.metric_version
        period_start, period_end = (
            _aware(period.period_start),
            _aware(period.period_end),
        )
        value = _value_from_fact(metric_fact)
        claim_status = ClaimStatus(metric_fact.claim_status)
    return NormalizedMediaFact(
        observation_id=envelope.id,
        kind=kind,
        installation_ref=envelope.installation_ref,
        source_system=envelope.source_system,
        source_observation_id=envelope.source_observation_id,
        source_observed_at=_aware(envelope.source_observed_at),
        received_at=_aware(envelope.received_at),
        normalization_version=envelope.normalization_version,
        external_account_ref=account_ref,
        entity_ref=entity_ref,
        metric_code=metric_code,
        metric_version=metric_version,
        period_start=period_start,
        period_end=period_end,
        value=value,
        claim_status=claim_status,
    )


def _record_envelope(
    db: Session,
    *,
    source: ObservationSource,
    kind: ObservationKind,
    fact_content: dict[str, object],
    restates_observation_id: UUID | None,
) -> tuple[ObservationEnvelope, RecordStatus, bool]:
    fingerprint = _fingerprint(
        {
            "installation_ref": source.installation_ref,
            "source_system": source.source_system,
            "source_observation_id": source.source_observation_id,
            "kind": kind,
            "source_observed_at": source.observed_at,
            "normalization_version": source.normalization_version,
            "restates_observation_id": restates_observation_id,
            "fact": fact_content,
        }
    )
    existing = _source_identity(db, source)
    if existing is not None:
        _require_same_observation(existing, fingerprint)
        _attach_receipt(db, existing, source)
        return existing, RecordStatus.REPLAYED, False

    depth = 0
    if restates_observation_id is not None:
        previous = db.scalar(
            select(ObservationEnvelope).where(
                ObservationEnvelope.tenant_id == source.tenant_id,
                ObservationEnvelope.id == restates_observation_id,
            )
        )
        if previous is None:
            raise InvalidObservation(
                f"restated observation {restates_observation_id} was not found"
            )
        if previous.kind != kind.value:
            raise InvalidObservation(
                f"cannot restate {previous.kind} evidence with {kind.value} evidence"
            )
        depth = previous.restatement_depth + 1

    envelope = ObservationEnvelope(
        tenant_id=source.tenant_id,
        installation_ref=source.installation_ref,
        source_system=source.source_system,
        source_observation_id=source.source_observation_id,
        kind=kind.value,
        content_fingerprint=fingerprint,
        source_observed_at=_utc(source.observed_at),
        received_at=_utc(source.received_at),
        normalization_version=source.normalization_version,
        restates_observation_id=restates_observation_id,
        restatement_depth=depth,
    )
    created = True
    try:
        with db.begin_nested():
            db.add(envelope)
            db.flush()
    except IntegrityError:
        created = False
        concurrent_envelope = _source_identity(db, source)
        if concurrent_envelope is None:  # pragma: no cover - defensive race branch
            raise
        envelope = concurrent_envelope
        _require_same_observation(envelope, fingerprint)
    _attach_receipt(db, envelope, source)
    status = (
        RecordStatus.REPLAYED
        if not created
        else RecordStatus.RESTATED
        if restates_observation_id is not None
        else RecordStatus.RECORDED
    )
    return envelope, status, created


def _attach_receipt(
    db: Session, envelope: ObservationEnvelope, source: ObservationSource
) -> None:
    receipt = db.scalar(
        select(ObservationReceipt).where(
            ObservationReceipt.tenant_id == source.tenant_id,
            ObservationReceipt.installation_ref == source.installation_ref,
            ObservationReceipt.transport_receipt_ref == source.transport_receipt_ref,
        )
    )
    if receipt is not None:
        if receipt.observation_id != envelope.id:
            raise ObservationConflict(
                ObservationRejection(
                    code="transport_receipt_conflict",
                    message=(
                        f"transport receipt {source.transport_receipt_ref!r} is "
                        "already attached to a different observation"
                    ),
                    source_observation_id=source.source_observation_id,
                    observation_id=receipt.observation_id,
                )
            )
        return
    candidate = ObservationReceipt(
        tenant_id=source.tenant_id,
        observation_id=envelope.id,
        installation_ref=source.installation_ref,
        transport_receipt_ref=source.transport_receipt_ref,
        received_at=_utc(source.received_at),
    )
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
    except IntegrityError as exc:
        receipt = db.scalar(
            select(ObservationReceipt).where(
                ObservationReceipt.tenant_id == source.tenant_id,
                ObservationReceipt.installation_ref == source.installation_ref,
                ObservationReceipt.transport_receipt_ref
                == source.transport_receipt_ref,
            )
        )
        if receipt is None or receipt.observation_id != envelope.id:
            raise ObservationConflict(
                ObservationRejection(
                    code="transport_receipt_conflict",
                    message=(
                        f"transport receipt {source.transport_receipt_ref!r} "
                        "conflicts with concurrent evidence"
                    ),
                    source_observation_id=source.source_observation_id,
                )
            ) from exc


def _source_identity(
    db: Session, source: ObservationSource
) -> ObservationEnvelope | None:
    return db.scalar(
        select(ObservationEnvelope).where(
            ObservationEnvelope.tenant_id == source.tenant_id,
            ObservationEnvelope.installation_ref == source.installation_ref,
            ObservationEnvelope.source_system == source.source_system,
            ObservationEnvelope.source_observation_id == source.source_observation_id,
        )
    )


def _require_same_observation(existing: ObservationEnvelope, fingerprint: str) -> None:
    if existing.content_fingerprint != fingerprint:
        raise ObservationConflict(
            ObservationRejection(
                code="observation_identity_conflict",
                message=(
                    f"observation identity {existing.source_observation_id!r} was "
                    "reused with different normalized content"
                ),
                source_observation_id=existing.source_observation_id,
                observation_id=existing.id,
            )
        )


def _get_or_create_period(db: Session, command: MetricObservation) -> MetricPeriod:
    identity = (
        str(command.source.tenant_id),
        command.source.installation_ref,
        command.source.source_system,
        command.external_account_ref,
        command.entity_ref,
        command.metric_code,
        str(command.metric_version),
    )
    if db.get_bind().dialect.name == "postgresql":
        lock = int.from_bytes(
            hashlib.sha256("\x1f".join(identity).encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )
        db.execute(select(func.pg_advisory_xact_lock(lock)))

    exact = db.scalar(
        select(MetricPeriod).where(
            MetricPeriod.tenant_id == command.source.tenant_id,
            MetricPeriod.installation_ref == command.source.installation_ref,
            MetricPeriod.source_system == command.source.source_system,
            MetricPeriod.external_account_ref == command.external_account_ref,
            MetricPeriod.entity_ref == command.entity_ref,
            MetricPeriod.metric_code == command.metric_code,
            MetricPeriod.metric_version == command.metric_version,
            MetricPeriod.period_start == _utc(command.period_start),
            MetricPeriod.period_end == _utc(command.period_end),
        )
    )
    if exact is not None:
        return exact
    overlap = db.scalar(
        select(MetricPeriod).where(
            MetricPeriod.tenant_id == command.source.tenant_id,
            MetricPeriod.installation_ref == command.source.installation_ref,
            MetricPeriod.source_system == command.source.source_system,
            MetricPeriod.external_account_ref == command.external_account_ref,
            MetricPeriod.entity_ref == command.entity_ref,
            MetricPeriod.metric_code == command.metric_code,
            MetricPeriod.metric_version == command.metric_version,
            MetricPeriod.period_start < _utc(command.period_end),
            MetricPeriod.period_end > _utc(command.period_start),
        )
    )
    if overlap is not None:
        raise ObservationConflict(
            ObservationRejection(
                code="metric_period_overlap",
                message=(
                    "metric period overlap is refused; periods use half-open "
                    "[start,end) semantics"
                ),
                source_observation_id=command.source.source_observation_id,
            )
        )
    period = MetricPeriod(
        tenant_id=command.source.tenant_id,
        installation_ref=command.source.installation_ref,
        source_system=command.source.source_system,
        external_account_ref=command.external_account_ref,
        entity_ref=command.entity_ref,
        metric_code=command.metric_code,
        metric_version=command.metric_version,
        period_start=_utc(command.period_start),
        period_end=_utc(command.period_end),
    )
    db.add(period)
    try:
        db.flush()
    except IntegrityError as exc:
        raise ObservationConflict(
            ObservationRejection(
                code="metric_period_conflict",
                message="metric period conflicts with concurrent stored evidence",
                source_observation_id=command.source.source_observation_id,
            )
        ) from exc
    return period


def _refresh_projections(db: Session, tenant_id: UUID) -> None:
    _replace_projections(db, tenant_id, _expected_projections(db, tenant_id))


def _expected_projections(
    db: Session, tenant_id: UUID
) -> dict[str, dict[tuple[str, ...], dict[str, object]]]:
    entity_candidates: dict[_EntityKey, tuple[EntityFact, ObservationEnvelope]] = {}
    entity_rows = db.execute(
        select(EntityFact, ObservationEnvelope)
        .join(
            ObservationEnvelope,
            (ObservationEnvelope.tenant_id == EntityFact.tenant_id)
            & (ObservationEnvelope.id == EntityFact.observation_id),
        )
        .where(EntityFact.tenant_id == tenant_id)
    ).all()
    for entity_fact, entity_envelope in entity_rows:
        entity_key = (
            entity_envelope.installation_ref,
            entity_envelope.source_system,
            entity_fact.external_account_ref,
            entity_fact.entity_ref,
        )
        entity_previous = entity_candidates.get(entity_key)
        if entity_previous is None or _effective_key(entity_envelope) > _effective_key(
            entity_previous[1]
        ):
            entity_candidates[entity_key] = (entity_fact, entity_envelope)

    entities: dict[tuple[str, ...], dict[str, object]] = {}
    for entity_key, (entity_fact, entity_envelope) in entity_candidates.items():
        entities[entity_key] = {
            "observation_id": entity_envelope.id,
            "installation_ref": entity_key[0],
            "source_system": entity_key[1],
            "external_account_ref": entity_key[2],
            "entity_ref": entity_key[3],
            "node_code": entity_fact.node_code,
            "node_version": entity_fact.node_version,
            "name": entity_fact.name,
            "state": entity_fact.state,
            "disposition": entity_fact.disposition,
            "properties": entity_fact.properties,
            "source_observed_at": _aware(entity_envelope.source_observed_at),
            "projection_fingerprint": entity_envelope.content_fingerprint,
        }

    hierarchy_candidates: dict[
        _HierarchyKey, tuple[HierarchyFact, ObservationEnvelope]
    ] = {}
    hierarchy_rows = db.execute(
        select(HierarchyFact, ObservationEnvelope)
        .join(
            ObservationEnvelope,
            (ObservationEnvelope.tenant_id == HierarchyFact.tenant_id)
            & (ObservationEnvelope.id == HierarchyFact.observation_id),
        )
        .where(HierarchyFact.tenant_id == tenant_id)
    ).all()
    for hierarchy_fact, hierarchy_envelope in hierarchy_rows:
        hierarchy_key = (
            hierarchy_envelope.installation_ref,
            hierarchy_envelope.source_system,
            hierarchy_fact.external_account_ref,
            hierarchy_fact.child_entity_ref,
        )
        hierarchy_previous = hierarchy_candidates.get(hierarchy_key)
        if hierarchy_previous is None or _effective_key(
            hierarchy_envelope
        ) > _effective_key(hierarchy_previous[1]):
            hierarchy_candidates[hierarchy_key] = (
                hierarchy_fact,
                hierarchy_envelope,
            )

    active = {
        key
        for key, row in entities.items()
        if row["disposition"] != EntityDisposition.DELETED.value
    }
    cycle_children = _cycle_children(hierarchy_candidates, active)
    hierarchy: dict[tuple[str, ...], dict[str, object]] = {}
    for hierarchy_key, (
        hierarchy_fact,
        hierarchy_envelope,
    ) in hierarchy_candidates.items():
        parent_key = (
            hierarchy_key[0],
            hierarchy_key[1],
            hierarchy_key[2],
            hierarchy_fact.parent_entity_ref,
        )
        if hierarchy_key not in active:
            drift_code: str | None = "missing_child"
        elif parent_key not in active:
            drift_code = "missing_parent"
        elif hierarchy_key in cycle_children:
            drift_code = "cycle"
        else:
            drift_code = None
        hierarchy[hierarchy_key] = {
            "observation_id": hierarchy_envelope.id,
            "installation_ref": hierarchy_key[0],
            "source_system": hierarchy_key[1],
            "external_account_ref": hierarchy_key[2],
            "child_entity_ref": hierarchy_key[3],
            "parent_entity_ref": hierarchy_fact.parent_entity_ref,
            "drift_code": drift_code,
            "source_observed_at": _aware(hierarchy_envelope.source_observed_at),
            "projection_fingerprint": _fingerprint(
                {
                    "fact": hierarchy_envelope.content_fingerprint,
                    "drift_code": drift_code,
                }
            ),
        }

    metric_candidates: dict[
        tuple[str, ...], tuple[MetricFact, ObservationEnvelope]
    ] = {}
    metric_rows = db.execute(
        select(MetricFact, ObservationEnvelope)
        .join(
            ObservationEnvelope,
            (ObservationEnvelope.tenant_id == MetricFact.tenant_id)
            & (ObservationEnvelope.id == MetricFact.observation_id),
        )
        .where(MetricFact.tenant_id == tenant_id)
    ).all()
    for metric_fact, metric_envelope in metric_rows:
        metric_key = (str(metric_fact.period_id),)
        metric_previous = metric_candidates.get(metric_key)
        if metric_previous is None or _effective_key(metric_envelope) > _effective_key(
            metric_previous[1]
        ):
            metric_candidates[metric_key] = (metric_fact, metric_envelope)
    metrics: dict[tuple[str, ...], dict[str, object]] = {}
    for projected_metric_key, (
        projected_metric_fact,
        projected_metric_envelope,
    ) in metric_candidates.items():
        metrics[projected_metric_key] = {
            "observation_id": projected_metric_envelope.id,
            "period_id": projected_metric_fact.period_id,
            "source_observed_at": _aware(projected_metric_envelope.source_observed_at),
            "projection_fingerprint": (projected_metric_envelope.content_fingerprint),
        }
    return {"entities": entities, "hierarchy": hierarchy, "metrics": metrics}


def _cycle_children(
    candidates: dict[_HierarchyKey, tuple[HierarchyFact, ObservationEnvelope]],
    active: set[tuple[str, ...]],
) -> set[_HierarchyKey]:
    cycle: set[_HierarchyKey] = set()
    by_scope: dict[tuple[str, str, str], dict[str, str]] = {}
    for key, (fact, _envelope) in candidates.items():
        parent_key = (key[0], key[1], key[2], fact.parent_entity_ref)
        if key in active and parent_key in active:
            by_scope.setdefault(key[:3], {})[key[3]] = fact.parent_entity_ref
    for scope, edges in by_scope.items():
        for start in edges:
            traversal: list[str] = []
            positions: dict[str, int] = {}
            current = start
            while current in edges:
                if current in positions:
                    for child in traversal[positions[current] :]:
                        cycle.add((*scope, child))
                    break
                positions[current] = len(traversal)
                traversal.append(current)
                current = edges[current]
    return cycle


def _replace_projections(
    db: Session,
    tenant_id: UUID,
    expected: dict[str, dict[tuple[str, ...], dict[str, object]]],
) -> None:
    db.execute(delete(CurrentMetric).where(CurrentMetric.tenant_id == tenant_id))
    db.execute(delete(CurrentHierarchy).where(CurrentHierarchy.tenant_id == tenant_id))
    db.execute(delete(CurrentEntity).where(CurrentEntity.tenant_id == tenant_id))
    for row in expected["entities"].values():
        db.add(CurrentEntity(tenant_id=tenant_id, **row))
    for row in expected["hierarchy"].values():
        db.add(CurrentHierarchy(tenant_id=tenant_id, **row))
    for row in expected["metrics"].values():
        db.add(CurrentMetric(tenant_id=tenant_id, **row))
    db.flush()


def _actual_entities(
    db: Session, tenant_id: UUID
) -> dict[tuple[str, ...], dict[str, object]]:
    out: dict[tuple[str, ...], dict[str, object]] = {}
    for row in db.scalars(
        select(CurrentEntity).where(CurrentEntity.tenant_id == tenant_id)
    ):
        key = (
            row.installation_ref,
            row.source_system,
            row.external_account_ref,
            row.entity_ref,
        )
        out[key] = {
            "observation_id": row.observation_id,
            "installation_ref": row.installation_ref,
            "source_system": row.source_system,
            "external_account_ref": row.external_account_ref,
            "entity_ref": row.entity_ref,
            "node_code": row.node_code,
            "node_version": row.node_version,
            "name": row.name,
            "state": row.state,
            "disposition": row.disposition,
            "properties": row.properties,
            "source_observed_at": _aware(row.source_observed_at),
            "projection_fingerprint": row.projection_fingerprint,
        }
    return out


def _actual_hierarchy(
    db: Session, tenant_id: UUID
) -> dict[tuple[str, ...], dict[str, object]]:
    out: dict[tuple[str, ...], dict[str, object]] = {}
    for row in db.scalars(
        select(CurrentHierarchy).where(CurrentHierarchy.tenant_id == tenant_id)
    ):
        key = (
            row.installation_ref,
            row.source_system,
            row.external_account_ref,
            row.child_entity_ref,
        )
        out[key] = {
            "observation_id": row.observation_id,
            "installation_ref": row.installation_ref,
            "source_system": row.source_system,
            "external_account_ref": row.external_account_ref,
            "child_entity_ref": row.child_entity_ref,
            "parent_entity_ref": row.parent_entity_ref,
            "drift_code": row.drift_code,
            "source_observed_at": _aware(row.source_observed_at),
            "projection_fingerprint": row.projection_fingerprint,
        }
    return out


def _actual_metrics(
    db: Session, tenant_id: UUID
) -> dict[tuple[str, ...], dict[str, object]]:
    out: dict[tuple[str, ...], dict[str, object]] = {}
    for row in db.scalars(
        select(CurrentMetric).where(CurrentMetric.tenant_id == tenant_id)
    ):
        key = (str(row.period_id),)
        out[key] = {
            "observation_id": row.observation_id,
            "period_id": row.period_id,
            "source_observed_at": _aware(row.source_observed_at),
            "projection_fingerprint": row.projection_fingerprint,
        }
    return out


def _compare_projection(
    name: str,
    expected: dict[tuple[str, ...], dict[str, object]],
    actual: dict[tuple[str, ...], dict[str, object]],
) -> list[DriftItem]:
    items: list[DriftItem] = []
    for key in sorted(set(expected) | set(actual)):
        if key not in actual:
            code = "missing_projection"
        elif key not in expected:
            code = "unexpected_projection"
        elif _fingerprint(expected[key]) != _fingerprint(actual[key]):
            code = "projection_mismatch"
        else:
            continue
        items.append(DriftItem(name, "/".join(key), code))
    return items


def _effective_key(envelope: ObservationEnvelope) -> tuple[datetime, int, str]:
    return (
        _aware(envelope.source_observed_at),
        envelope.restatement_depth,
        envelope.source_observation_id,
    )


def _require_node_definition(
    db: Session, *, tenant_id: UUID, code: str, version: int
) -> NodeDefinition:
    definition = db.scalar(
        select(NodeDefinition).where(
            NodeDefinition.tenant_id == tenant_id,
            NodeDefinition.code == code,
            NodeDefinition.version == version,
        )
    )
    if definition is None:
        raise UnsupportedObservation(
            f"node declaration {code!r} v{version} is not registered for this tenant"
        )
    return definition


def _require_metric_definition(
    db: Session, *, tenant_id: UUID, code: str, version: int
) -> MetricDefinition:
    definition = db.scalar(
        select(MetricDefinition).where(
            MetricDefinition.tenant_id == tenant_id,
            MetricDefinition.code == code,
            MetricDefinition.version == version,
        )
    )
    if definition is None:
        raise UnsupportedObservation(
            f"metric declaration {code!r} v{version} is not registered for this tenant"
        )
    return definition


def _require_same_definition(
    stored: str, incoming: str, *, code: str, version: int
) -> None:
    if stored != incoming:
        raise ObservationConflict(
            ObservationRejection(
                code="declaration_identity_conflict",
                message=(
                    f"declaration {code!r} v{version} was reused with different content"
                ),
            )
        )


def _require_same_restatement_subject(
    db: Session,
    original: ObservationEnvelope,
    replacement: ObservationCommand,
) -> None:
    tenant_id = replacement.source.tenant_id
    if isinstance(replacement, EntityObservation):
        entity_fact = _entity_fact(db, tenant_id, original.id)
        same = (
            entity_fact.external_account_ref == replacement.external_account_ref
            and entity_fact.entity_ref == replacement.entity_ref
        )
    elif isinstance(replacement, HierarchyObservation):
        hierarchy_fact = _hierarchy_fact(db, tenant_id, original.id)
        same = (
            hierarchy_fact.external_account_ref == replacement.external_account_ref
            and hierarchy_fact.child_entity_ref == replacement.child_entity_ref
        )
    else:
        metric_fact = _metric_fact(db, tenant_id, original.id)
        period = db.scalar(
            select(MetricPeriod).where(
                MetricPeriod.tenant_id == tenant_id,
                MetricPeriod.id == metric_fact.period_id,
            )
        )
        same = period is not None and (
            period.external_account_ref == replacement.external_account_ref
            and period.entity_ref == replacement.entity_ref
            and period.metric_code == replacement.metric_code
            and period.metric_version == replacement.metric_version
            and _aware(period.period_start) == _aware(replacement.period_start)
            and _aware(period.period_end) == _aware(replacement.period_end)
        )
    if not same:
        raise InvalidObservation(
            "a restatement must correct the same entity, hierarchy child or "
            "metric period"
        )


def _entity_fact(db: Session, tenant_id: UUID, observation_id: UUID) -> EntityFact:
    fact = db.scalar(
        select(EntityFact).where(
            EntityFact.tenant_id == tenant_id,
            EntityFact.observation_id == observation_id,
        )
    )
    if fact is None:
        raise InvalidObservation("entity observation has no immutable fact")
    return fact


def _hierarchy_fact(
    db: Session, tenant_id: UUID, observation_id: UUID
) -> HierarchyFact:
    fact = db.scalar(
        select(HierarchyFact).where(
            HierarchyFact.tenant_id == tenant_id,
            HierarchyFact.observation_id == observation_id,
        )
    )
    if fact is None:
        raise InvalidObservation("hierarchy observation has no immutable fact")
    return fact


def _metric_fact(db: Session, tenant_id: UUID, observation_id: UUID) -> MetricFact:
    fact = db.scalar(
        select(MetricFact).where(
            MetricFact.tenant_id == tenant_id,
            MetricFact.observation_id == observation_id,
        )
    )
    if fact is None:
        raise InvalidObservation("metric observation has no immutable fact")
    return fact


def _kind_of(command: ObservationCommand) -> ObservationKind:
    if isinstance(command, EntityObservation):
        return ObservationKind.ENTITY
    if isinstance(command, HierarchyObservation):
        return ObservationKind.HIERARCHY
    return ObservationKind.METRIC


def _value_content(value: MetricValue) -> dict[str, object]:
    if isinstance(value, ExactMoney):
        return {
            "value_type": value.value_type,
            "amount": value.amount,
            "currency": value.currency,
            "minor_unit": value.minor_unit,
            "minor_units": value.minor_units,
        }
    return {"value_type": value.value_type, "value": value.value}


def _value_columns(value: MetricValue) -> dict[str, object | None]:
    columns: dict[str, object | None] = {
        "count_value": None,
        "decimal_value": None,
        "money_amount": None,
        "money_minor_units": None,
        "money_currency": None,
        "money_minor_unit": None,
        "duration_value": None,
        "ratio_value": None,
    }
    if isinstance(value, CountValue):
        columns["count_value"] = value.value
    elif isinstance(value, DecimalValue):
        columns["decimal_value"] = value.value
    elif isinstance(value, ExactMoney):
        columns.update(
            money_amount=value.amount,
            money_minor_units=value.minor_units,
            money_currency=value.currency,
            money_minor_unit=value.minor_unit,
        )
    elif isinstance(value, DurationValue):
        columns["duration_value"] = value.value
    else:
        columns["ratio_value"] = value.value
    return columns


def _value_from_fact(fact: MetricFact) -> MetricValue:
    value_type = MetricValueType(fact.value_type)
    if value_type is MetricValueType.COUNT:
        return CountValue(cast(int, fact.count_value))
    if value_type is MetricValueType.DECIMAL:
        return DecimalValue(cast(Decimal, fact.decimal_value))
    if value_type is MetricValueType.MONEY:
        return ExactMoney(
            amount=cast(Decimal, fact.money_amount),
            currency=cast(str, fact.money_currency),
            minor_unit=cast(int, fact.money_minor_unit),
        )
    if value_type is MetricValueType.DURATION:
        return DurationValue(cast(int, fact.duration_value))
    return RatioValue(cast(Decimal, fact.ratio_value))


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _aware(value).astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    return value


def _json_storage(value: object) -> object:
    return _canonical(value)


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


__all__ = [
    "declare_metric",
    "declare_node_type",
    "emit_analytics_fact",
    "read_current_entity",
    "read_hierarchy",
    "read_period_metrics",
    "reconcile_projections",
    "record_entity",
    "record_hierarchy",
    "record_metric",
    "record_restatement",
    "report_hierarchy_drift",
    "report_metric_drift",
]
