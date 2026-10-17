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

    def require_ready(self):
        if not self.available:
            raise HealthAdmissionError("authority unavailable")

    def enrollment(self, enrollment_ref):
        self.require_ready()
        assert enrollment_ref == self.projection.enrollment_ref
        return self.projection


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
