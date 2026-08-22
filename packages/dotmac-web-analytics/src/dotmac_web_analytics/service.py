"""Registration and append-only ingestion services.

Callers own the transaction. Expected uniqueness races are isolated by the
kernel savepoint; this module never commits, rolls back or opens a session.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_web_analytics.contracts import (
    BatchIngestResult,
    ClassificationEvidenceCommand,
    EventDeclarationRegistry,
    IngestResult,
    IngestStatus,
    InvalidContract,
    PropertyRegistration,
    RecordEventBatchCommand,
    RecordEventCommand,
    RecordPageViewCommand,
    StreamRegistration,
    UnknownEventDeclaration,
    VisitorPseudonymizer,
    WebAnalyticsError,
)
from dotmac_web_analytics.models import (
    AnalyticsProperty,
    AnalyticsStream,
    EventClassificationEvidence,
    EventConflictEvidence,
    EventObservation,
    EventReplayTombstone,
)
from dotmac_web_analytics.privacy import (
    CanonicalLocation,
    canonicalize_url,
    validate_safe_scalar,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidContract(f"{field_name} must be timezone-aware")
    return value


def register_property(db: Session, command: PropertyRegistration) -> AnalyticsProperty:
    existing = db.scalar(
        select(AnalyticsProperty).where(
            AnalyticsProperty.tenant_id == command.tenant_id,
            AnalyticsProperty.code == command.property_code,
        )
    )
    values = (
        command.display_name,
        list(command.allowed_origins),
        command.timezone_name,
        command.raw_retention_days,
        command.replay_evidence_days,
    )
    if existing is not None:
        current = (
            existing.display_name,
            existing.allowed_origins,
            existing.timezone_name,
            existing.raw_retention_days,
            existing.replay_evidence_days,
        )
        if current != values:
            raise InvalidContract(
                f"property {command.property_code!r} already has different "
                "configuration"
            )
        return existing
    row = AnalyticsProperty(
        tenant_id=command.tenant_id,
        code=command.property_code,
        display_name=command.display_name,
        allowed_origins=list(command.allowed_origins),
        timezone_name=command.timezone_name,
        raw_retention_days=command.raw_retention_days,
        replay_evidence_days=command.replay_evidence_days,
    )
    db.add(row)
    db.flush()
    return row


def register_stream(db: Session, command: StreamRegistration) -> AnalyticsStream:
    prop = _property_by_code(db, command.tenant_id, command.property_code)
    existing = db.scalar(
        select(AnalyticsStream).where(
            AnalyticsStream.tenant_id == command.tenant_id,
            AnalyticsStream.property_id == prop.id,
            AnalyticsStream.code == command.stream_code,
        )
    )
    versions = list(command.accepted_protocol_versions)
    if existing is not None:
        if existing.accepted_protocol_versions != versions:
            raise InvalidContract(
                f"stream {command.stream_code!r} already has different versions"
            )
        return existing
    row = AnalyticsStream(
        tenant_id=command.tenant_id,
        property_id=prop.id,
        code=command.stream_code,
        accepted_protocol_versions=versions,
    )
    db.add(row)
    db.flush()
    return row


def _property_by_code(db: Session, tenant_id: UUID, code: str) -> AnalyticsProperty:
    row = db.scalar(
        select(AnalyticsProperty).where(
            AnalyticsProperty.tenant_id == tenant_id,
            AnalyticsProperty.code == code,
        )
    )
    if row is None:
        raise InvalidContract(f"analytics property {code!r} is not registered")
    return row


def _stream_by_code(
    db: Session, tenant_id: UUID, property_id: UUID, code: str
) -> AnalyticsStream:
    row = db.scalar(
        select(AnalyticsStream).where(
            AnalyticsStream.tenant_id == tenant_id,
            AnalyticsStream.property_id == property_id,
            AnalyticsStream.code == code,
        )
    )
    if row is None:
        raise InvalidContract(f"analytics stream {code!r} is not registered")
    return row


def _canonical_page(
    command: RecordEventCommand, prop: AnalyticsProperty
) -> tuple[CanonicalLocation | None, CanonicalLocation | None]:
    if command.admission.origin.rstrip("/").lower() not in {
        item.rstrip("/").lower() for item in prop.allowed_origins
    }:
        from dotmac_web_analytics.contracts import CollectionRefused

        raise CollectionRefused("admitted origin is not registered for this property")
    if command.page is None:
        return None, None
    page = canonicalize_url(
        command.page.page_url, allowed_origins=tuple(prop.allowed_origins)
    )
    referrer = None
    if command.page.referrer_url:
        parsed = command.page.referrer_url
        # A referrer can be external; canonicalise it against its own parsed
        # origin while applying the same privacy rejection/stripping rules.
        from urllib.parse import urlsplit

        split = urlsplit(parsed)
        if not split.hostname:
            from dotmac_web_analytics.contracts import CollectionRefused

            raise CollectionRefused("referrer URL is malformed")
        port = f":{split.port}" if split.port else ""
        origin = f"{split.scheme.lower()}://{split.hostname.lower()}{port}"
        referrer = canonicalize_url(parsed, allowed_origins=(origin,))
    return page, referrer


def _fingerprint(
    command: RecordEventCommand,
    *,
    property_id: UUID,
    stream_id: UUID,
    visitor_digest: str,
    attributes: tuple[tuple[str, str | int | bool], ...],
    page: CanonicalLocation | None,
    referrer: CanonicalLocation | None,
) -> str:
    document = {
        "domain": "dotmac-web-analytics.event.v1",
        "tenant_id": str(command.tenant_id),
        "property_id": str(property_id),
        "stream_id": str(stream_id),
        "protocol_version": command.protocol_version,
        "event_id": command.event_id,
        "event_code": command.event_code,
        "event_schema_version": command.event_schema_version,
        "occurred_at": command.occurred_at.astimezone(UTC).isoformat(),
        "visitor_digest": visitor_digest,
        "attributes": attributes,
        "page": (page.origin, page.path) if page else None,
        "referrer": (referrer.origin, referrer.path) if referrer else None,
        "acquisition": (
            command.acquisition.source,
            command.acquisition.medium,
            command.acquisition.campaign,
            command.acquisition.term,
            command.acquisition.content,
        ),
        "device_class": str(command.device_class),
        "privacy": (
            command.privacy.policy_version,
            str(command.privacy.consent_state),
            command.privacy.global_privacy_control,
            command.privacy.do_not_track,
            command.privacy.evaluated_at.astimezone(UTC).isoformat(),
        ),
        "admission": (
            command.admission.adapter_code,
            command.admission.origin,
        ),
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _existing_identity(
    db: Session,
    *,
    tenant_id: UUID,
    property_id: UUID,
    stream_id: UUID,
    event_id: str,
) -> EventObservation | EventReplayTombstone | None:
    observation = db.scalar(
        select(EventObservation).where(
            EventObservation.tenant_id == tenant_id,
            EventObservation.property_id == property_id,
            EventObservation.stream_id == stream_id,
            EventObservation.event_id == event_id,
        )
    )
    if observation is not None:
        return observation
    return db.scalar(
        select(EventReplayTombstone).where(
            EventReplayTombstone.tenant_id == tenant_id,
            EventReplayTombstone.property_id == property_id,
            EventReplayTombstone.stream_id == stream_id,
            EventReplayTombstone.event_id == event_id,
        )
    )


def _replay_or_conflict(
    db: Session,
    *,
    existing: EventObservation | EventReplayTombstone,
    command: RecordEventCommand,
    fingerprint: str,
    property_id: UUID,
    stream_id: UUID,
    detected_at: datetime,
) -> IngestResult:
    if existing.content_fingerprint == fingerprint:
        observation_id = existing.id if isinstance(existing, EventObservation) else None
        return IngestResult(command.event_id, IngestStatus.REPLAYED, observation_id)
    recorded = db.scalar(
        select(EventConflictEvidence).where(
            EventConflictEvidence.tenant_id == command.tenant_id,
            EventConflictEvidence.property_id == property_id,
            EventConflictEvidence.stream_id == stream_id,
            EventConflictEvidence.event_id == command.event_id,
            EventConflictEvidence.presented_fingerprint == fingerprint,
        )
    )
    if recorded is not None:
        return IngestResult(
            command.event_id,
            IngestStatus.CONFLICT,
            existing.id if isinstance(existing, EventObservation) else None,
            "event_identity_conflict",
        )
    conflict = EventConflictEvidence(
        tenant_id=command.tenant_id,
        property_id=property_id,
        stream_id=stream_id,
        event_id=command.event_id,
        existing_fingerprint=existing.content_fingerprint,
        presented_fingerprint=fingerprint,
        detected_at=detected_at,
        source_system=command.provenance.source_system,
        source_reference=command.provenance.source_reference,
        delivery_id=command.provenance.delivery_id,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(conflict)
            db.flush()
    except IntegrityError:
        recorded_conflict_id = db.scalar(
            select(EventConflictEvidence.id).where(
                EventConflictEvidence.tenant_id == command.tenant_id,
                EventConflictEvidence.property_id == property_id,
                EventConflictEvidence.stream_id == stream_id,
                EventConflictEvidence.event_id == command.event_id,
                EventConflictEvidence.presented_fingerprint == fingerprint,
            )
        )
        if recorded_conflict_id is None:
            raise
    return IngestResult(
        command.event_id,
        IngestStatus.CONFLICT,
        existing.id if isinstance(existing, EventObservation) else None,
        "event_identity_conflict",
    )


def record_event(
    db: Session,
    *,
    registry: EventDeclarationRegistry,
    pseudonymizer: VisitorPseudonymizer,
    command: RecordEventCommand,
    received_at: datetime,
) -> IngestResult:
    received_at = _aware(received_at, field_name="received_at")
    declaration = registry.require(command.event_code, command.event_schema_version)
    attributes = declaration.validate_attributes(command.attributes)
    validate_safe_scalar(command.event_id)
    validate_safe_scalar(command.privacy.policy_version)
    validate_safe_scalar(command.provenance.source_reference)
    if command.provenance.delivery_id is not None:
        validate_safe_scalar(command.provenance.delivery_id)
    for _name, attribute_value in attributes:
        if isinstance(attribute_value, str):
            validate_safe_scalar(attribute_value)
    for acquisition_value in (
        command.acquisition.source,
        command.acquisition.medium,
        command.acquisition.campaign,
        command.acquisition.term,
        command.acquisition.content,
    ):
        if acquisition_value is not None:
            validate_safe_scalar(acquisition_value)

    prop = _property_by_code(db, command.tenant_id, command.property_code)
    stream = _stream_by_code(db, command.tenant_id, prop.id, command.stream_code)
    if command.protocol_version not in stream.accepted_protocol_versions:
        raise InvalidContract(
            f"stream {stream.code!r} does not accept protocol "
            f"v{command.protocol_version}"
        )
    page, referrer = _canonical_page(command, prop)
    visitor_digest = pseudonymizer.digest(
        tenant_id=command.tenant_id,
        property_id=prop.id,
        token=command.visitor_token,
    )
    if not _DIGEST_RE.fullmatch(visitor_digest) or pseudonymizer.key_version < 1:
        raise InvalidContract("pseudonymizer returned an invalid digest or key version")
    fingerprint = _fingerprint(
        command,
        property_id=prop.id,
        stream_id=stream.id,
        visitor_digest=visitor_digest,
        attributes=attributes,
        page=page,
        referrer=referrer,
    )
    existing = _existing_identity(
        db,
        tenant_id=command.tenant_id,
        property_id=prop.id,
        stream_id=stream.id,
        event_id=command.event_id,
    )
    if existing is not None:
        return _replay_or_conflict(
            db,
            existing=existing,
            command=command,
            fingerprint=fingerprint,
            property_id=prop.id,
            stream_id=stream.id,
            detected_at=received_at,
        )

    row = EventObservation(
        tenant_id=command.tenant_id,
        property_id=prop.id,
        stream_id=stream.id,
        event_id=command.event_id,
        protocol_version=command.protocol_version,
        event_code=command.event_code,
        event_schema_version=command.event_schema_version,
        content_fingerprint=fingerprint,
        occurred_at=command.occurred_at,
        received_at=received_at,
        expires_at=received_at + timedelta(days=prop.raw_retention_days),
        visitor_digest=visitor_digest,
        pseudonym_key_version=pseudonymizer.key_version,
        canonical_origin=page.origin if page else None,
        canonical_path=page.path if page else None,
        referrer_origin=referrer.origin if referrer else None,
        referrer_path=referrer.path if referrer else None,
        acquisition_source=command.acquisition.source,
        acquisition_medium=command.acquisition.medium,
        acquisition_campaign=command.acquisition.campaign,
        acquisition_term=command.acquisition.term,
        acquisition_content=command.acquisition.content,
        device_class=str(command.device_class),
        attributes_json=[[name, value] for name, value in attributes],
        privacy_policy_version=command.privacy.policy_version,
        consent_state=str(command.privacy.consent_state),
        global_privacy_control=command.privacy.global_privacy_control,
        do_not_track=command.privacy.do_not_track,
        privacy_evaluated_at=command.privacy.evaluated_at,
        adapter_code=command.admission.adapter_code,
        admission_origin=command.admission.origin,
        admission_checked_at=command.admission.checked_at,
        transport_kind=str(command.provenance.kind),
        source_system=command.provenance.source_system,
        source_reference=command.provenance.source_reference,
        delivery_id=command.provenance.delivery_id,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError:
        winner = _existing_identity(
            db,
            tenant_id=command.tenant_id,
            property_id=prop.id,
            stream_id=stream.id,
            event_id=command.event_id,
        )
        if winner is None:
            raise
        return _replay_or_conflict(
            db,
            existing=winner,
            command=command,
            fingerprint=fingerprint,
            property_id=prop.id,
            stream_id=stream.id,
            detected_at=received_at,
        )
    return IngestResult(command.event_id, IngestStatus.ACCEPTED, row.id)


def record_page_view(
    db: Session,
    *,
    registry: EventDeclarationRegistry,
    pseudonymizer: VisitorPseudonymizer,
    command: RecordPageViewCommand,
    received_at: datetime,
) -> IngestResult:
    return record_event(
        db,
        registry=registry,
        pseudonymizer=pseudonymizer,
        command=command.event,
        received_at=received_at,
    )


def record_event_batch(
    db: Session,
    *,
    registry: EventDeclarationRegistry,
    pseudonymizer: VisitorPseudonymizer,
    command: RecordEventBatchCommand,
    received_at: datetime,
) -> BatchIngestResult:
    from dotmac_kernel.db import conflict_savepoint

    results: list[IngestResult] = []
    for event in command.events:
        try:
            with conflict_savepoint(db):
                result = record_event(
                    db,
                    registry=registry,
                    pseudonymizer=pseudonymizer,
                    command=event,
                    received_at=received_at,
                )
        except (WebAnalyticsError, UnknownEventDeclaration) as exc:
            results.append(
                IngestResult(
                    event.event_id,
                    IngestStatus.REJECTED,
                    error_code=exc.__class__.__name__,
                )
            )
        else:
            results.append(result)
    return BatchIngestResult(tuple(results))


def record_classification(
    db: Session,
    *,
    command: ClassificationEvidenceCommand,
) -> EventClassificationEvidence:
    observation = db.scalar(
        select(EventObservation.id).where(
            EventObservation.tenant_id == command.tenant_id,
            EventObservation.id == command.observation_id,
        )
    )
    if observation is None:
        raise InvalidContract("classification observation does not exist in tenant")
    existing = db.scalar(
        select(EventClassificationEvidence).where(
            EventClassificationEvidence.tenant_id == command.tenant_id,
            EventClassificationEvidence.observation_id == command.observation_id,
            EventClassificationEvidence.classifier_code == command.classifier_code,
            EventClassificationEvidence.classifier_version
            == command.classifier_version,
        )
    )
    if existing is not None:
        recorded = (
            existing.classified_at,
            existing.is_bot,
            existing.analytically_included,
            tuple(existing.reasons_json),
        )
        presented = (
            command.classified_at,
            command.is_bot,
            command.analytically_included,
            command.reasons,
        )
        if recorded != presented:
            raise InvalidContract("classifier code/version was redefined")
        return existing
    row = EventClassificationEvidence(
        tenant_id=command.tenant_id,
        observation_id=command.observation_id,
        classifier_code=command.classifier_code,
        classifier_version=command.classifier_version,
        classified_at=command.classified_at,
        is_bot=command.is_bot,
        analytically_included=command.analytically_included,
        reasons_json=list(command.reasons),
    )
    db.add(row)
    db.flush()
    return row


def list_observations(
    db: Session, *, tenant_id: UUID, property_id: UUID
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


__all__ = [
    "list_observations",
    "record_classification",
    "record_event",
    "record_event_batch",
    "record_page_view",
    "register_property",
    "register_stream",
]
