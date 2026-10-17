"""Hosted admission canaries; test authorities are never production verification."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base
from dotmac_platform_health import HealthConflict, HealthObservationInput, HealthState
from dotmac_platform_health.admission import (
    AuthenticatedHealthSource,
    EnrollmentProjection,
    HealthAdmission,
    HealthAdmissionError,
    HealthEligibilityAssessment,
)
from dotmac_platform_health.models import PLATFORM_MODELS, HealthProjection
from dotmac_platform_health.service import (
    produce_signed_health_evidence,
    rebuild_projections,
    record_observation,
    register_component,
    summarize_health,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

AT = datetime(2026, 10, 17, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_health": None}},
    )
    Base.metadata.create_all(
        engine, tables=[model.__table__ for model in PLATFORM_MODELS]
    )
    with Session(engine) as session:
        yield session
    engine.dispose()


class Authority:
    def __init__(self, projection):
        self.projection = projection
        self.available = True
        self.assessment_override = None

    def require_ready(self):
        if not self.available:
            raise HealthAdmissionError("authority unavailable")

    def enrollment(self, enrollment_ref):
        self.require_ready()
        assert enrollment_ref == self.projection.enrollment_ref
        return self.projection

    def health_eligibility(self, enrollment_ref):
        self.require_ready()
        if self.assessment_override is not None:
            return self.assessment_override
        p = self.projection
        return HealthEligibilityAssessment(
            "assessment:" + p.fingerprint,
            enrollment_ref,
            p.identity_fingerprint,
            p.eligible_from,
            p.eligible_from,
            p.eligible_until,
            "eligible",
            "current-runtime:1",
            "current-runtime-owner:1",
            "current-credential:1",
            "current-credential-owner:1",
        )


@pytest.fixture
def setup(db):
    projection = EnrollmentProjection(
        "enrollment:1",
        "installation:1",
        "config:1",
        "candidate:1",
        "source:1",
        "account:1",
        "transport:1",
        "credential:1",
        ("deployment-epoch-1-api",),
        "runtime-proof:1",
        "runtime-owner:1",
        "credential-proof:1",
        "credential-owner:1",
        AT,
        AT + timedelta(hours=1),
        "eligible",
    )
    authority = Authority(projection)
    admission = HealthAdmission(authority)
    register_component(
        db, code=projection.component_codes[0], display_name="API", freshness_seconds=60
    )
    grant = admission.create_grant(db, enrollment_ref="enrollment:1", at=AT)
    source = AuthenticatedHealthSource(
        "installation:1",
        "source:1",
        "account:1",
        "transport:1",
        "credential:1",
        "config:1",
    )
    command = HealthObservationInput(
        "source:1",
        "tick:1",
        projection.component_codes[0],
        HealthState.HEALTHY,
        AT,
        AT,
        "ok",
        {},
    )
    return admission, authority, grant, source, command


@pytest.mark.parametrize(
    "field",
    [
        "installation_ref",
        "source_ref",
        "account_ref",
        "transport_ref",
        "credential_ref",
        "configuration_ref",
    ],
)
def test_authenticated_identity_mismatch_never_records(db, setup, field):
    admission, _, grant, source, command = setup
    with pytest.raises(HealthAdmissionError, match="binding"):
        admission.record(
            db,
            grant_id=grant.id,
            source=replace(source, **{field: "wrong"}),
            command=command,
            at=AT,
        )


def test_missing_authority_and_incomplete_independent_evidence_refuse(setup):
    _, authority, _, _, _ = setup
    with pytest.raises(HealthAdmissionError):
        HealthAdmission(None)
    for field in (
        "runtime_evidence_ref",
        "runtime_authority_ref",
        "credential_evidence_ref",
        "credential_authority_ref",
    ):
        with pytest.raises(HealthAdmissionError):
            replace(authority.projection, **{field: ""})


def test_exact_replay_survives_closure_but_never_binding_or_content_change(db, setup):
    admission, authority, grant, source, command = setup
    first = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    admission.close_grant(db, grant_id=grant.id, closed_at=AT, reason="retired")
    authority.available = False
    from dotmac_platform_health.models import HealthComponent

    db.get(HealthComponent, first.observation.component_id).active = False
    db.flush()
    replay = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    assert replay.replayed and replay.observation.id == first.observation.id
    assert replay.observation.received_at == first.observation.received_at
    with pytest.raises(HealthConflict):
        admission.record(
            db,
            grant_id=grant.id,
            source=source,
            command=replace(command, summary="changed"),
            at=AT,
        )
    with pytest.raises(HealthAdmissionError):
        admission.record(
            db,
            grant_id=grant.id,
            source=source,
            command=replace(command, observation_key="new"),
            at=AT,
        )


def test_reserved_legacy_bypass_and_unbound_contamination(db, setup):
    admission, _, grant, source, command = setup
    with pytest.raises(HealthAdmissionError, match="reserved"):
        record_observation(db, command)
    first = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    # Simulate historical unbound evidence and a corrupted rebuildable projection.
    from dotmac_platform_health.models import HealthObservation

    unbound = HealthObservation(
        id=uuid4(),
        component_id=first.observation.component_id,
        source_ref="other",
        observation_key="legacy",
        request_fingerprint="a" * 64,
        state="unhealthy",
        freshness_seconds=60,
        observed_at=AT + timedelta(seconds=1),
        received_at=AT + timedelta(seconds=1),
        summary="wrong epoch",
        labels={},
    )
    db.add(unbound)
    db.flush()
    projection = db.scalar(select(HealthProjection))
    projection.observation_id = unbound.id
    db.flush()
    rebuild_projections(db, rebuilt_at=AT, admission=admission)
    summary = summarize_health(db, as_of=AT, admission=admission)[0]
    assert summary.observation_id == first.observation.id
    assert summary.state == "healthy"


def test_authority_outage_preserves_projection_and_never_calls_signer(db, setup):
    admission, authority, grant, source, command = setup
    admission.record(db, grant_id=grant.id, source=source, command=command, at=AT)
    original = db.scalar(select(HealthProjection)).id
    authority.available = False
    with pytest.raises(HealthAdmissionError):
        rebuild_projections(db, rebuilt_at=AT, admission=admission)
    assert db.scalar(select(HealthProjection)).id == original

    class Signer:
        def sign_health_evidence(self, *args):
            pytest.fail("signer ran without authority")

    with pytest.raises(HealthAdmissionError):
        produce_signed_health_evidence(
            db,
            requested_components=[command.component_code],
            evaluated_at=AT,
            signer=Signer(),
            admission=admission,
        )


def test_current_projection_change_refuses_new_writes_but_exact_replay_survives(
    db, setup
):
    admission, authority, grant, source, command = setup
    first = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    authority.projection = replace(authority.projection, configuration_ref="new-config")
    with pytest.raises(HealthAdmissionError, match="projection changed"):
        admission.record(
            db,
            grant_id=grant.id,
            source=source,
            command=replace(command, observation_key="new"),
            at=AT,
        )
    assert (
        admission.record(
            db, grant_id=grant.id, source=source, command=command, at=AT
        ).observation.id
        == first.observation.id
    )
    with pytest.raises(HealthAdmissionError, match="binding"):
        admission.record(
            db,
            grant_id=grant.id,
            source=replace(source, credential_ref="new-credential"),
            command=command,
            at=AT,
        )


def test_old_unbound_identity_cannot_upgrade_to_a_grant(db, setup):
    admission, _, grant, source, command = setup
    receipt = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    # SQLite fixture simulates a pre-migration row; PostgreSQL forbids mutation.
    receipt.observation.grant_id = None
    db.flush()
    with pytest.raises(HealthConflict, match="grant"):
        admission.record(db, grant_id=grant.id, source=source, command=command, at=AT)
    assert receipt.observation.grant_id is None


def test_permanent_installation_and_component_identity_cannot_be_reassigned(db, setup):
    admission, authority, grant, _, _ = setup
    admission.close_grant(db, grant_id=grant.id, closed_at=AT, reason="retired")
    authority.projection = replace(
        authority.projection,
        enrollment_ref="enrollment:2",
        installation_ref="installation:2",
    )
    with pytest.raises(HealthAdmissionError, match="reserved"):
        admission.create_grant(db, enrollment_ref="enrollment:2", at=AT)


def test_deployment_signing_requires_exact_roster_and_unbound_is_missing(db, setup):
    from dotmac_platform_health import build_health_evidence

    admission, _, grant, source, command = setup
    admission.record(db, grant_id=grant.id, source=source, command=command, at=AT)
    register_component(db, code="unbound", display_name="Unbound", freshness_seconds=60)
    record_observation(
        db, replace(command, component_code="unbound", observation_key="unbound")
    )
    evidence = build_health_evidence(
        db, requested_components=["unbound"], evaluated_at=AT
    )
    assert evidence.components[0].state == "unknown"
    assert evidence.components[0].observation_id is None

    class Signer:
        def sign_health_evidence(self, *args):
            pytest.fail("mixed roster reached signer")

    with pytest.raises(HealthAdmissionError, match="roster"):
        produce_signed_health_evidence(
            db,
            requested_components=[command.component_code, "unbound"],
            evaluated_at=AT,
            signer=Signer(),
            admission=admission,
        )


def test_frozen_received_at_and_legacy_fingerprint_encoding_are_preserved(db, setup):
    from dotmac_platform_health.service import _fingerprint

    admission, _, grant, source, command = setup
    expected = "be70a73706985af6766ac484738c47a1cdf3b6ad5f3b451ed99ea211790728e0"
    assert _fingerprint(command) == expected
    receipt = admission.record(
        db,
        grant_id=grant.id,
        source=source,
        command=command,
        at=AT + timedelta(seconds=20),
    )
    assert receipt.observation.request_fingerprint == expected
    assert receipt.observation.received_at == command.received_at
    assert (
        _fingerprint(replace(command, received_at=AT + timedelta(seconds=1)))
        != expected
    )


def test_installation_cannot_move_to_a_new_enrollment_even_with_disjoint_roster(
    db, setup
):
    admission, authority, _, _, _ = setup
    register_component(
        db, code="epoch-2-api", display_name="API 2", freshness_seconds=60
    )
    authority.projection = replace(
        authority.projection,
        enrollment_ref="enrollment:2",
        component_codes=("epoch-2-api",),
    )
    with pytest.raises(HealthAdmissionError, match="installation conflict"):
        admission.create_grant(db, enrollment_ref="enrollment:2", at=AT)
    authority.projection = replace(
        authority.projection, installation_ref="installation:2"
    )
    assert (
        admission.create_grant(
            db, enrollment_ref="enrollment:2", at=AT
        ).installation_ref
        == "installation:2"
    )


def test_pre_reservation_legacy_replay_remains_but_cannot_gain_provenance(db, setup):
    admission, authority, _, source, command = setup
    register_component(
        db, code="legacy-first", display_name="Legacy", freshness_seconds=60
    )
    old_command = replace(
        command, component_code="legacy-first", observation_key="legacy-first"
    )
    old = record_observation(db, old_command)
    authority.projection = replace(
        authority.projection,
        enrollment_ref="enrollment:legacy",
        installation_ref="installation:legacy",
        component_codes=("legacy-first",),
    )
    grant = admission.create_grant(db, enrollment_ref="enrollment:legacy", at=AT)
    assert record_observation(db, old_command).observation.id == old.observation.id
    with pytest.raises(HealthConflict):
        admission.record(
            db,
            grant_id=grant.id,
            source=replace(source, installation_ref="installation:legacy"),
            command=old_command,
            at=AT,
        )


def test_legacy_cannot_replay_a_bound_receipt_by_matching_all_command_fields(db, setup):
    admission, _, grant, source, command = setup
    bound = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    projection = db.scalar(select(HealthProjection))
    before = (projection.id, projection.observation_id, projection.state)
    with pytest.raises(HealthConflict):
        record_observation(db, command)
    assert (projection.id, projection.observation_id, projection.state) == before
    assert bound.observation.grant_id == grant.id


def test_health_renewal_keeps_initial_admission_and_links_exact_assessment(db, setup):
    from dotmac_platform_health.models import HealthEligibilitySnapshot
    from dotmac_platform_health.service import build_health_evidence

    admission, authority, grant, source, command = setup
    original_document, original_fingerprint = (
        dict(grant.projection),
        grant.projection_fingerprint,
    )
    first = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    old_assessment_id = first.observation.assessment_id
    renewed_at = AT + timedelta(hours=2)
    authority.assessment_override = HealthEligibilityAssessment(
        "assessment:renewed",
        grant.enrollment_ref,
        authority.projection.identity_fingerprint,
        renewed_at,
        renewed_at,
        renewed_at + timedelta(seconds=20),
        "eligible",
        "current-runtime:2",
        "current-runtime-owner:2",
        "current-credential:2",
        "current-credential-owner:2",
    )
    # Original execution/admission interval has expired; unchanged runtime health
    # has independent fresh owner evidence. No token is executed or reconsumed.
    renewed = admission.create_grant(
        db, enrollment_ref=grant.enrollment_ref, at=renewed_at
    )
    receipt = admission.record(
        db,
        grant_id=grant.id,
        source=source,
        command=replace(
            command,
            observation_key="renewed",
            observed_at=renewed_at,
            received_at=renewed_at,
        ),
        at=renewed_at,
    )
    assert renewed.id == grant.id
    assert grant.projection == original_document
    assert grant.projection_fingerprint == original_fingerprint
    assert receipt.observation.assessment_id != old_assessment_id
    snapshot = db.get(HealthEligibilitySnapshot, receipt.observation.assessment_id)
    assert snapshot.assessment == authority.assessment_override.document()
    evidence = build_health_evidence(
        db,
        requested_components=[command.component_code],
        evaluated_at=renewed_at,
        admission=admission,
    )
    assert evidence.valid_until == renewed_at + timedelta(seconds=20)
    assert (
        admission.record(
            db, grant_id=grant.id, source=source, command=command, at=renewed_at
        ).observation.id
        == first.observation.id
    )


@pytest.mark.parametrize(
    "field",
    [
        "enrollment_ref",
        "installation_ref",
        "configuration_ref",
        "candidate_ref",
        "source_ref",
        "account_ref",
        "transport_ref",
        "credential_ref",
        "component_codes",
        "runtime_evidence_ref",
        "runtime_authority_ref",
        "credential_evidence_ref",
        "credential_authority_ref",
    ],
)
def test_every_immutable_identity_field_conflicts(db, setup, field):
    admission, authority, grant, source, command = setup
    value = ("other-component",) if field == "component_codes" else "changed"
    authority.projection = replace(authority.projection, **{field: value})
    with pytest.raises(HealthAdmissionError):
        admission.record(db, grant_id=grant.id, source=source, command=command, at=AT)


@pytest.mark.parametrize("standing", ["revoked", "ineligible", "closed"])
def test_noneligible_assessment_refuses_fresh_health(db, setup, standing):
    admission, authority, grant, source, command = setup
    authority.assessment_override = replace(
        authority.health_eligibility(grant.enrollment_ref), standing=standing
    )
    with pytest.raises(HealthAdmissionError, match="currently eligible"):
        admission.record(db, grant_id=grant.id, source=source, command=command, at=AT)


def test_expired_assessment_and_closed_grant_cannot_use_cached_snapshot(db, setup):
    admission, authority, grant, source, command = setup
    authority.assessment_override = authority.health_eligibility(grant.enrollment_ref)
    with pytest.raises(HealthAdmissionError, match="currently eligible"):
        admission.record(
            db,
            grant_id=grant.id,
            source=source,
            command=command,
            at=AT + timedelta(hours=1),
        )
    admission.close_grant(db, grant_id=grant.id, closed_at=AT, reason="closed")
    with pytest.raises(HealthAdmissionError, match="closed"):
        admission.record(db, grant_id=grant.id, source=source, command=command, at=AT)


def test_assessment_reference_cannot_rewrite_provenance(db, setup):
    admission, authority, grant, source, command = setup
    authority.assessment_override = replace(
        authority.health_eligibility(grant.enrollment_ref),
        runtime_evidence_ref="replacement",
    )
    with pytest.raises(HealthAdmissionError, match="reference reused"):
        admission.record(db, grant_id=grant.id, source=source, command=command, at=AT)


def test_renewed_projection_dates_do_not_rewrite_permanent_identity(db, setup):
    admission, authority, grant, source, command = setup
    original = dict(grant.projection), grant.projection_fingerprint
    authority.projection = replace(
        authority.projection,
        eligible_from=AT - timedelta(seconds=1),
        eligible_until=AT + timedelta(hours=2),
    )
    receipt = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    assert receipt.observation.assessment_id is not None
    assert (grant.projection, grant.projection_fingerprint) == original


@pytest.mark.parametrize(
    "field",
    [
        "assessment_ref",
        "runtime_evidence_ref",
        "runtime_authority_ref",
        "credential_evidence_ref",
        "credential_authority_ref",
    ],
)
def test_assessment_requires_durable_current_provenance(db, setup, field):
    _, authority, grant, _, _ = setup
    with pytest.raises(HealthAdmissionError):
        replace(authority.health_eligibility(grant.enrollment_ref), **{field: ""})


def test_projection_only_authority_is_not_an_accepting_fallback():
    class ProjectionOnly:
        def require_ready(self):
            pass

    with pytest.raises(HealthAdmissionError, match="current health eligibility"):
        HealthAdmission(ProjectionOnly())


def test_renewed_assessment_keeps_still_fresh_observation_without_new_receipt(
    db, setup
):
    from dotmac_platform_health.models import (
        HealthEligibilitySnapshot,
        HealthObservation,
    )
    from dotmac_platform_health.service import build_health_evidence

    admission, authority, grant, source, command = setup
    authority.assessment_override = replace(
        authority.health_eligibility(grant.enrollment_ref),
        assessment_ref="assessment:short-admission",
        valid_until=AT + timedelta(seconds=20),
    )
    receipt = admission.record(
        db, grant_id=grant.id, source=source, command=command, at=AT
    )
    original_assessment_id = receipt.observation.assessment_id
    original_fingerprint = receipt.observation.request_fingerprint
    original_snapshot = dict(
        db.get(HealthEligibilitySnapshot, original_assessment_id).assessment
    )
    renewed_at = AT + timedelta(seconds=30)
    authority.assessment_override = replace(
        authority.assessment_override,
        assessment_ref="assessment:current-renewal",
        assessed_at=renewed_at,
        valid_from=renewed_at,
        valid_until=AT + timedelta(seconds=50),
        runtime_evidence_ref="runtime:renewed-current",
        credential_evidence_ref="credential:renewed-current",
    )
    evidence = build_health_evidence(
        db,
        requested_components=[command.component_code],
        evaluated_at=renewed_at,
        admission=admission,
    )
    assert evidence.components[0].observation_id == receipt.observation.id
    assert evidence.components[0].freshness == "fresh"
    assert evidence.valid_until == AT + timedelta(seconds=50)
    assert evidence.valid_until > renewed_at
    assert len(db.scalars(select(HealthObservation)).all()) == 1
    assert receipt.observation.assessment_id == original_assessment_id
    assert receipt.observation.request_fingerprint == original_fingerprint
    assert (
        db.get(HealthEligibilitySnapshot, original_assessment_id).assessment
        == original_snapshot
    )


def test_first_grant_uses_fresh_health_assessment_after_admission_interval_expired(db):
    projection = EnrollmentProjection(
        "enrollment:first",
        "installation:first",
        "config:first",
        "candidate:first",
        "source:first",
        "account:first",
        "transport:first",
        "credential:first",
        ("first-api",),
        "baseline-runtime:first",
        "baseline-runtime-owner:first",
        "baseline-credential:first",
        "baseline-credential-owner:first",
        AT,
        AT + timedelta(hours=1),
        "eligible",
    )
    authority = Authority(projection)
    current_at = AT + timedelta(hours=2)
    authority.assessment_override = HealthEligibilityAssessment(
        "assessment:first-current",
        projection.enrollment_ref,
        projection.identity_fingerprint,
        current_at,
        current_at,
        current_at + timedelta(seconds=30),
        "eligible",
        "runtime:first-current",
        "runtime:first-owner",
        "credential:first-current",
        "credential:first-owner",
    )
    admission = HealthAdmission(authority)
    register_component(db, code="first-api", display_name="API", freshness_seconds=60)
    grant = admission.create_grant(
        db, enrollment_ref=projection.enrollment_ref, at=current_at
    )
    assert grant.projection == projection.document()
    assert grant.projection_fingerprint == projection.fingerprint
    assert projection.eligible_until < current_at
    from dotmac_platform_health.models import HealthEligibilitySnapshot

    snapshot = db.scalar(
        select(HealthEligibilitySnapshot).where(
            HealthEligibilitySnapshot.grant_id == grant.id
        )
    )
    assert snapshot.assessment == authority.assessment_override.document()
