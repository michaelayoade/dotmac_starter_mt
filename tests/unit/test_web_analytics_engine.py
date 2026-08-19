"""RED-first behavioural canaries for the web-analytics owner.

SQLite proves deterministic service behaviour. PostgreSQL RLS, grants and
append-only enforcement have their own integration canary.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_web_analytics import (
    CORE_EVENT_DECLARATIONS,
    PAGE_VIEW_EVENT_CODE,
    AcquisitionEvidence,
    AggregateMetricQuery,
    AttributeKind,
    ClassificationEvidenceCommand,
    CollectionAdmissionEvidence,
    CollectionDecision,
    CollectionRefused,
    ConsentState,
    DeviceClass,
    EventAttributeSpec,
    EventDeclaration,
    EventDeclarationRegistry,
    ExpireObservationsCommand,
    FunnelDefinition,
    FunnelStep,
    IngestStatus,
    InvalidContract,
    MetricDimension,
    OpaqueVisitorToken,
    PageEvidence,
    PrivacyDeletionCommand,
    PrivacyPolicyEvidence,
    PropertyRegistration,
    RebuildProjectionsCommand,
    RecordEventBatchCommand,
    RecordEventCommand,
    SessionizationRule,
    StreamRegistration,
    TransportKind,
    TransportProvenance,
)
from dotmac_web_analytics.models import (
    TENANT_MODELS,
    AggregateMetric,
    EventClassificationEvidence,
    EventObservation,
    ProjectionDriftEvidence,
)
from dotmac_web_analytics.projections import (
    detect_projection_drift,
    read_aggregate_metrics,
    read_funnel_result,
    read_sessions,
    rebuild_projections,
    repair_projection_drift,
)
from dotmac_web_analytics.retention import expire_observations, privacy_delete
from dotmac_web_analytics.service import (
    list_observations,
    record_classification,
    record_event,
    record_event_batch,
    record_page_view,
    register_property,
    register_stream,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)
TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
RULE = SessionizationRule("standard", 1, 30 * 60)
FORM_EVENT = EventDeclaration(
    code="forms.completed",
    schema_version=1,
    attributes=(
        EventAttributeSpec(
            "form_kind",
            AttributeKind.ENUM,
            required=True,
            max_length=24,
            allowed_values=("contact", "coverage"),
        ),
    ),
)
REGISTRY = EventDeclarationRegistry((*CORE_EVENT_DECLARATIONS, FORM_EVENT))


class DeterministicPseudonymizer:
    key_version = 7

    def digest(
        self,
        *,
        tenant_id: uuid.UUID,
        property_id: uuid.UUID,
        token: OpaqueVisitorToken,
    ) -> str:
        envelope = (
            f"web-visitor-v1:{tenant_id}:{property_id}:"
            f"{token.reveal_for_pseudonymization()}"
        )
        return "sha256:" + hashlib.sha256(envelope.encode()).hexdigest()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_webanalytics": None}},
    )
    for model in TENANT_MODELS:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _register(
    db: Session,
    *,
    tenant_id: uuid.UUID = TENANT_A,
    property_code: str = "site.one",
    stream_code: str = "browser",
    retention_days: int = 30,
) -> tuple[object, object]:
    prop = register_property(
        db,
        PropertyRegistration(
            tenant_id=tenant_id,
            property_code=property_code,
            display_name=property_code,
            allowed_origins=(f"https://{property_code}.invalid",),
            timezone_name="Africa/Lagos",
            raw_retention_days=retention_days,
            replay_evidence_days=retention_days + 30,
        ),
    )
    stream = register_stream(
        db,
        StreamRegistration(
            tenant_id=tenant_id,
            property_code=property_code,
            stream_code=stream_code,
            accepted_protocol_versions=(1,),
        ),
    )
    return prop, stream


def _command(
    *,
    tenant_id: uuid.UUID = TENANT_A,
    property_code: str = "site.one",
    stream_code: str = "browser",
    event_id: str = "event-1",
    event_code: str = PAGE_VIEW_EVENT_CODE,
    occurred_at: datetime = NOW,
    visitor_value: str | None = None,
    attributes: tuple[tuple[str, str | int | bool], ...] = (),
    page_url: str | None = None,
    referrer_url: str | None = "https://referrer.invalid/article?ignored=value",
    decision: CollectionDecision = CollectionDecision.ALLOW,
    policy_version: str = "privacy-1",
    provenance: TransportProvenance | None = None,
) -> RecordEventCommand:
    return RecordEventCommand(
        tenant_id=tenant_id,
        property_code=property_code,
        stream_code=stream_code,
        protocol_version=1,
        event_id=event_id,
        event_code=event_code,
        event_schema_version=1,
        occurred_at=occurred_at,
        visitor_token=OpaqueVisitorToken(visitor_value or "opaque-visitor-value-0001"),
        privacy=PrivacyPolicyEvidence(
            policy_version=policy_version,
            consent_state=(
                ConsentState.GRANTED
                if decision is CollectionDecision.ALLOW
                else ConsentState.DENIED
            ),
            decision=decision,
            global_privacy_control=False,
            do_not_track=False,
            evaluated_at=occurred_at,
        ),
        admission=CollectionAdmissionEvidence(
            adapter_code="web.collect",
            origin=f"https://{property_code}.invalid",
            checked_at=occurred_at,
            origin_verified=True,
            rate_limit_permitted=True,
        ),
        provenance=provenance
        or TransportProvenance(
            TransportKind.LOCAL,
            source_system="local.website",
            source_reference="request-1",
        ),
        attributes=attributes,
        page=PageEvidence(
            page_url
            if page_url is not None
            else f"https://{property_code}.invalid/pricing?utm_source=search",
            referrer_url,
        ),
        acquisition=AcquisitionEvidence(source="search"),
        device_class=DeviceClass.DESKTOP,
    )


def _record(
    db: Session,
    command: RecordEventCommand,
    *,
    received_at: datetime | None = None,
):  # type: ignore[no-untyped-def]
    return record_event(
        db,
        registry=REGISTRY,
        pseudonymizer=DeterministicPseudonymizer(),
        command=command,
        received_at=received_at or command.occurred_at,
    )


def _rebuild(
    db: Session,
    property_id: uuid.UUID,
    *,
    funnels: tuple[FunnelDefinition, ...] = (),
):  # type: ignore[no-untyped-def]
    return rebuild_projections(
        db,
        command=RebuildProjectionsCommand(
            tenant_id=TENANT_A,
            property_id=property_id,
            session_rule=RULE,
            projection_version=1,
            timezone_name="Africa/Lagos",
            requested_at=NOW + timedelta(days=1),
        ),
        registry=REGISTRY,
        funnels=funnels,
    )


def test_same_identity_and_fingerprint_replays_changed_content_conflicts(
    db: Session,
) -> None:
    prop, _stream = _register(db)
    first = _record(db, _command())
    replay = _record(db, _command())
    conflict = _record(
        db,
        _command(page_url="https://site.one.invalid/different"),
    )

    assert first.status is IngestStatus.ACCEPTED
    assert replay.status is IngestStatus.REPLAYED
    assert replay.observation_id == first.observation_id
    assert conflict.status is IngestStatus.CONFLICT
    observations = list_observations(db, tenant_id=TENANT_A, property_id=prop.id)
    assert len(observations) == 1
    assert observations[0].canonical_path == "/pricing"

    repeated_conflict = _record(
        db,
        _command(page_url="https://site.one.invalid/different"),
    )
    assert repeated_conflict.status is IngestStatus.CONFLICT


def test_a_bounded_batch_reports_partial_expected_failures_in_order(
    db: Session,
) -> None:
    _register(db)
    batch = RecordEventBatchCommand(
        (
            _command(event_id="ok-1"),
            _command(event_id="bad", event_code="undeclared.event"),
            _command(event_id="ok-2"),
        )
    )

    result = record_event_batch(
        db,
        registry=REGISTRY,
        pseudonymizer=DeterministicPseudonymizer(),
        command=batch,
        received_at=NOW,
    )

    assert [item.event_id for item in result.results] == ["ok-1", "bad", "ok-2"]
    assert [item.status for item in result.results] == [
        IngestStatus.ACCEPTED,
        IngestStatus.REJECTED,
        IngestStatus.ACCEPTED,
    ]


def test_same_event_identity_is_scoped_to_property_and_stream(db: Session) -> None:
    prop_one, _ = _register(db, property_code="site.one")
    prop_two, _ = _register(db, property_code="site.two")
    one = _record(db, _command(property_code="site.one"))
    two = _record(db, _command(property_code="site.two"))

    assert one.status is IngestStatus.ACCEPTED
    assert two.status is IngestStatus.ACCEPTED
    observations = db.scalars(select(EventObservation)).all()
    by_property = {row.property_id: row for row in observations}
    assert set(by_property) == {prop_one.id, prop_two.id}
    assert (
        by_property[prop_one.id].visitor_digest
        != by_property[prop_two.id].visitor_digest
    )


def test_explicit_tenant_and_property_filters_do_not_leak_rows(db: Session) -> None:
    prop_a, _ = _register(db, tenant_id=TENANT_A, property_code="site.a")
    prop_b, _ = _register(db, tenant_id=TENANT_B, property_code="site.b")
    _record(db, _command(tenant_id=TENANT_A, property_code="site.a"))
    _record(db, _command(tenant_id=TENANT_B, property_code="site.b"))

    assert len(list_observations(db, tenant_id=TENANT_A, property_id=prop_a.id)) == 1
    assert len(list_observations(db, tenant_id=TENANT_B, property_id=prop_b.id)) == 1
    assert list_observations(db, tenant_id=TENANT_A, property_id=prop_b.id) == ()


def test_late_event_rebuilds_sessions_from_event_time_not_arrival_order(
    db: Session,
) -> None:
    prop, _ = _register(db)
    _record(db, _command(event_id="first", occurred_at=NOW))
    _record(
        db,
        _command(event_id="third", occurred_at=NOW + timedelta(minutes=40)),
        received_at=NOW + timedelta(minutes=40),
    )
    _rebuild(db, prop.id)
    assert len(read_sessions(db, tenant_id=TENANT_A, property_id=prop.id)) == 2

    _record(
        db,
        _command(event_id="late", occurred_at=NOW + timedelta(minutes=20)),
        received_at=NOW + timedelta(hours=4),
    )
    _rebuild(db, prop.id)
    sessions = read_sessions(db, tenant_id=TENANT_A, property_id=prop.id)
    assert len(sessions) == 1
    assert sessions[0].started_at == NOW
    assert sessions[0].ended_at == NOW + timedelta(minutes=40)
    assert sessions[0].event_count == 3


def test_session_boundary_is_exact_and_timezone_does_not_change_it(db: Session) -> None:
    prop, _ = _register(db)
    _record(db, _command(event_id="one", occurred_at=NOW))
    _record(db, _command(event_id="boundary", occurred_at=NOW + timedelta(minutes=30)))
    _record(
        db,
        _command(
            event_id="outside", occurred_at=NOW + timedelta(minutes=60, seconds=1)
        ),
    )
    _rebuild(db, prop.id)
    sessions = read_sessions(db, tenant_id=TENANT_A, property_id=prop.id)
    assert [session.event_count for session in sessions] == [2, 1]

    metrics = read_aggregate_metrics(
        db,
        AggregateMetricQuery(
            tenant_id=TENANT_A,
            property_id=prop.id,
            starts_at=NOW - timedelta(days=1),
            ends_at=NOW + timedelta(days=1),
            timezone_name="Africa/Lagos",
            dimensions=(MetricDimension.EVENT,),
        ),
    )
    assert sum(row.events for row in metrics) == 3

    with pytest.raises(InvalidContract, match="timezone"):
        read_aggregate_metrics(
            db,
            AggregateMetricQuery(
                tenant_id=TENANT_A,
                property_id=prop.id,
                starts_at=NOW - timedelta(days=1),
                ends_at=NOW + timedelta(days=1),
                timezone_name="UTC",
                dimensions=(MetricDimension.EVENT,),
            ),
        )


def test_bot_reclassification_adds_evidence_and_rebuild_changes_inclusion(
    db: Session,
) -> None:
    prop, _ = _register(db)
    accepted = _record(db, _command())
    original = db.get(EventObservation, accepted.observation_id)
    assert original is not None
    original_fingerprint = original.content_fingerprint

    record_classification(
        db,
        command=ClassificationEvidenceCommand(
            tenant_id=TENANT_A,
            observation_id=original.id,
            classifier_code="standard.bot",
            classifier_version=1,
            classified_at=NOW,
            is_bot=True,
            analytically_included=False,
            reasons=("known.bot",),
        ),
    )
    _rebuild(db, prop.id)
    assert (
        read_aggregate_metrics(
            db,
            AggregateMetricQuery(
                TENANT_A,
                prop.id,
                NOW - timedelta(days=1),
                NOW + timedelta(days=1),
                "Africa/Lagos",
                (MetricDimension.EVENT,),
            ),
        )
        == ()
    )

    record_classification(
        db,
        command=ClassificationEvidenceCommand(
            tenant_id=TENANT_A,
            observation_id=original.id,
            classifier_code="standard.bot",
            classifier_version=2,
            classified_at=NOW + timedelta(minutes=1),
            is_bot=False,
            analytically_included=True,
            reasons=("verified.browser",),
        ),
    )
    _rebuild(db, prop.id)
    assert (
        sum(
            row.events
            for row in read_aggregate_metrics(
                db,
                AggregateMetricQuery(
                    TENANT_A,
                    prop.id,
                    NOW - timedelta(days=1),
                    NOW + timedelta(days=1),
                    "Africa/Lagos",
                    (MetricDimension.EVENT,),
                ),
            )
        )
        == 1
    )
    assert (
        db.get(EventObservation, original.id).content_fingerprint
        == original_fingerprint
    )


def test_classifier_replay_is_idempotent_and_changed_evidence_is_refused(
    db: Session,
) -> None:
    _register(db)
    accepted = _record(db, _command())
    assert accepted.observation_id is not None
    first = record_classification(
        db,
        command=ClassificationEvidenceCommand(
            tenant_id=TENANT_A,
            observation_id=accepted.observation_id,
            classifier_code="standard.bot",
            classifier_version=1,
            classified_at=NOW,
            is_bot=False,
            analytically_included=True,
            reasons=("verified.browser",),
        ),
    )
    replay = record_classification(
        db,
        command=ClassificationEvidenceCommand(
            tenant_id=TENANT_A,
            observation_id=accepted.observation_id,
            classifier_code="standard.bot",
            classifier_version=1,
            classified_at=NOW,
            is_bot=False,
            analytically_included=True,
            reasons=("verified.browser",),
        ),
    )
    assert replay.id == first.id
    assert db.scalar(select(func.count()).select_from(EventClassificationEvidence)) == 1

    with pytest.raises(InvalidContract, match="redefined"):
        record_classification(
            db,
            command=ClassificationEvidenceCommand(
                tenant_id=TENANT_A,
                observation_id=accepted.observation_id,
                classifier_code="standard.bot",
                classifier_version=1,
                classified_at=NOW,
                is_bot=True,
                analytically_included=False,
                reasons=("known.bot",),
            ),
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://site.one.invalid/path?token=secret",
        "https://site.one.invalid/path?email=person%40example.invalid",
        "https://user:password@site.one.invalid/path",
    ],
)
def test_sensitive_url_inputs_are_refused_before_persistence(
    db: Session, url: str
) -> None:
    _register(db)
    with pytest.raises(CollectionRefused, match="sensitive"):
        _record(db, _command(page_url=url))
    assert db.scalar(select(func.count()).select_from(EventObservation)) == 0


def test_raw_ip_pii_and_free_metadata_are_not_command_fields() -> None:
    values = _command().__dict__ if hasattr(_command(), "__dict__") else {}
    assert not {"raw_ip", "email", "phone", "metadata", "user_agent"} & set(values)
    with pytest.raises(TypeError):
        replace(_command(), raw_ip="192.0.2.1")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "command",
    (
        _command(event_id="person@example.invalid"),
        _command(
            provenance=TransportProvenance(
                TransportKind.LOCAL,
                source_system="local.website",
                source_reference="person@example.invalid",
            )
        ),
    ),
)
def test_obvious_pii_is_rejected_from_persisted_identifiers(
    db: Session, command: RecordEventCommand
) -> None:
    _register(db)
    with pytest.raises(CollectionRefused, match="sensitive"):
        _record(db, command)
    assert db.scalar(select(func.count()).select_from(EventObservation)) == 0


def test_a_later_denied_consent_decision_cannot_be_persisted(db: Session) -> None:
    _register(db)
    with pytest.raises(CollectionRefused, match="privacy policy"):
        _command(decision=CollectionDecision.DENY, policy_version="privacy-2")
    assert db.scalar(select(func.count()).select_from(EventObservation)) == 0


def test_remote_outbox_redelivery_replays_the_first_party_event(db: Session) -> None:
    _register(db)
    first_provenance = TransportProvenance(
        TransportKind.INTEGRATOR,
        source_system="website.connector",
        source_reference="site-event-1",
        delivery_id="delivery-1",
    )
    retry_provenance = replace(first_provenance, delivery_id="delivery-2")
    first = _record(db, _command(provenance=first_provenance))
    retry = _record(db, _command(provenance=retry_provenance))

    assert first.status is IngestStatus.ACCEPTED
    assert retry.status is IngestStatus.REPLAYED
    assert retry.observation_id == first.observation_id


def test_retention_expiry_rebuilds_aggregates_without_deleted_counts(
    db: Session,
) -> None:
    prop, _ = _register(db, retention_days=1)
    _record(db, _command(event_id="expired"), received_at=NOW)
    _record(
        db,
        _command(event_id="retained", occurred_at=NOW + timedelta(days=2)),
        received_at=NOW + timedelta(days=2),
    )
    _rebuild(db, prop.id)

    result = expire_observations(
        db,
        command=ExpireObservationsCommand(
            TENANT_A,
            prop.id,
            NOW + timedelta(days=1, seconds=1),
            NOW + timedelta(days=3),
        ),
        rebuild=RebuildProjectionsCommand(
            TENANT_A,
            prop.id,
            RULE,
            1,
            "Africa/Lagos",
            NOW + timedelta(days=3),
        ),
        registry=REGISTRY,
        funnels=(),
    )
    assert result.deleted_observations == 1
    assert len(list_observations(db, tenant_id=TENANT_A, property_id=prop.id)) == 1
    assert (
        sum(
            row.events
            for row in read_aggregate_metrics(
                db,
                AggregateMetricQuery(
                    TENANT_A,
                    prop.id,
                    NOW - timedelta(days=1),
                    NOW + timedelta(days=4),
                    "Africa/Lagos",
                    (MetricDimension.EVENT,),
                ),
            )
        )
        == 1
    )


def test_privacy_deletion_is_property_scoped_and_rebuilds(db: Session) -> None:
    prop_one, _ = _register(db, property_code="site.one")
    prop_two, _ = _register(db, property_code="site.two")
    first = _record(
        db,
        _command(property_code="site.one", visitor_value="shared-browser-value-1"),
    )
    _record(
        db,
        _command(property_code="site.two", visitor_value="shared-browser-value-1"),
    )
    observation = db.get(EventObservation, first.observation_id)
    assert observation is not None

    result = privacy_delete(
        db,
        command=PrivacyDeletionCommand(
            TENANT_A,
            prop_one.id,
            "privacy-request-1",
            observation.visitor_digest,
            NOW + timedelta(days=1),
        ),
        rebuild=RebuildProjectionsCommand(
            TENANT_A,
            prop_one.id,
            RULE,
            1,
            "Africa/Lagos",
            NOW + timedelta(days=1),
        ),
        registry=REGISTRY,
        funnels=(),
    )
    assert result.deleted_observations == 1
    assert list_observations(db, tenant_id=TENANT_A, property_id=prop_one.id) == ()
    assert len(list_observations(db, tenant_id=TENANT_A, property_id=prop_two.id)) == 1
    replay = privacy_delete(
        db,
        command=PrivacyDeletionCommand(
            TENANT_A,
            prop_one.id,
            "privacy-request-1",
            observation.visitor_digest,
            NOW + timedelta(days=1),
        ),
        rebuild=RebuildProjectionsCommand(
            TENANT_A,
            prop_one.id,
            RULE,
            1,
            "Africa/Lagos",
            NOW + timedelta(days=1),
        ),
        registry=REGISTRY,
        funnels=(),
    )
    assert replay.active_generation_id == result.active_generation_id
    assert replay.verified_digest == result.verified_digest


def test_funnel_is_deterministic_over_declared_observational_events(
    db: Session,
) -> None:
    prop, _ = _register(db)
    _record(db, _command(event_id="view", occurred_at=NOW))
    _record(
        db,
        _command(
            event_id="form",
            event_code="forms.completed",
            occurred_at=NOW + timedelta(minutes=5),
            attributes=(("form_kind", "contact"),),
        ),
    )
    funnel = FunnelDefinition(
        "pricing.to.form",
        1,
        (FunnelStep(PAGE_VIEW_EVENT_CODE, 1), FunnelStep("forms.completed", 1)),
        within_seconds=3600,
    )
    first = _rebuild(db, prop.id, funnels=(funnel,))
    result_one = read_funnel_result(
        db,
        tenant_id=TENANT_A,
        property_id=prop.id,
        definition_code=funnel.code,
        definition_version=funnel.version,
    )
    second = _rebuild(db, prop.id, funnels=(funnel,))
    result_two = read_funnel_result(
        db,
        tenant_id=TENANT_A,
        property_id=prop.id,
        definition_code=funnel.code,
        definition_version=funnel.version,
    )
    assert first.verified_digest == second.verified_digest
    assert result_one.completed_by_step == result_two.completed_by_step == (1, 1)


def test_projection_drift_is_detected_and_repaired_from_observations(
    db: Session,
) -> None:
    prop, _ = _register(db)
    _record(db, _command())
    _rebuild(db, prop.id)
    metric = db.scalar(select(AggregateMetric))
    assert metric is not None
    metric.event_count = 99
    db.flush()

    drift = detect_projection_drift(
        db,
        tenant_id=TENANT_A,
        property_id=prop.id,
        registry=REGISTRY,
    )
    assert drift.drifted
    repaired = repair_projection_drift(
        db,
        command=RebuildProjectionsCommand(
            TENANT_A,
            prop.id,
            RULE,
            1,
            "Africa/Lagos",
            NOW + timedelta(days=1),
        ),
        registry=REGISTRY,
        funnels=(),
    )
    assert repaired.active_generation_id != repaired.previous_generation_id
    repair_evidence = db.scalar(
        select(ProjectionDriftEvidence).where(
            ProjectionDriftEvidence.repaired_generation_id
            == repaired.active_generation_id
        )
    )
    assert repair_evidence is not None
    assert not detect_projection_drift(
        db,
        tenant_id=TENANT_A,
        property_id=prop.id,
        registry=REGISTRY,
    ).drifted


def test_page_view_has_an_explicit_typed_entry_point(db: Session) -> None:
    from dotmac_web_analytics import RecordPageViewCommand

    _register(db)
    result = record_page_view(
        db,
        registry=REGISTRY,
        pseudonymizer=DeterministicPseudonymizer(),
        command=RecordPageViewCommand(_command()),
        received_at=NOW,
    )
    assert result.status is IngestStatus.ACCEPTED
