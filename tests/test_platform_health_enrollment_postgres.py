"""Hosted, migrated PostgreSQL admission races, ACLs and retention canaries."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from time import monotonic, sleep

import pytest
from dotmac_platform_health import (
    AuthenticatedHealthSource,
    EnrollmentProjection,
    HealthAdmission,
    HealthAdmissionError,
    HealthEligibilityAssessment,
    HealthObservationInput,
    HealthState,
    record_observation,
    register_component,
)
from dotmac_platform_health.models import HealthGrant, HealthObservation
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_platform_health_released_migration_upgrade import (
    _config,
    _scratch_database,
)

AT = datetime(2026, 10, 17, tzinfo=UTC)


class TestAuthority:
    def require_ready(self):
        pass

    def enrollment(self, ref):
        return EnrollmentProjection(
            ref,
            "installation:1",
            "config:1",
            "candidate:1",
            "source:1",
            "account:1",
            "transport:1",
            "credential:1",
            ("epoch-1-api",),
            "runtime-proof:1",
            "runtime-authority:1",
            "credential-proof:1",
            "credential-authority:1",
            AT,
            AT + timedelta(hours=1),
            "eligible",
        )

    def health_eligibility(self, ref):
        projection = self.enrollment(ref)
        return HealthEligibilityAssessment(
            "assessment:1",
            ref,
            projection.identity_fingerprint,
            AT,
            AT,
            AT + timedelta(hours=1),
            "eligible",
            "runtime:current",
            "runtime:authority",
            "credential:current",
            "credential:authority",
        )


@pytest.fixture
def migrated(monkeypatch):
    from alembic import command

    with _scratch_database(monkeypatch) as url:
        command.upgrade(_config(), "ph_0003_enrollment_grants")
        engine = create_engine(url)
        with Session(engine) as db:
            register_component(
                db, code="epoch-1-api", display_name="API", freshness_seconds=60
            )
            db.commit()
        yield engine
        engine.dispose()


def _command(key="tick:1"):
    return HealthObservationInput(
        "source:1", key, "epoch-1-api", HealthState.HEALTHY, AT, AT, "ok", {}
    )


def _source():
    return AuthenticatedHealthSource(
        "installation:1",
        "source:1",
        "account:1",
        "transport:1",
        "credential:1",
        "config:1",
    )


def test_concurrent_grants_and_duplicate_arrivals_share_one_durable_identity(migrated):
    admission = HealthAdmission(TestAuthority())
    start = Barrier(2)

    def grant():
        with Session(migrated) as db:
            start.wait(timeout=10)
            result = admission.create_grant(db, enrollment_ref="enrollment:1", at=AT)
            identity = result.id
            db.commit()
            return identity

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(grant) for _ in range(2)]
        identities = [future.result(timeout=30) for future in futures]
    assert identities[0] == identities[1]
    start = Barrier(2)

    def receive():
        with Session(migrated) as db:
            start.wait(timeout=10)
            result = admission.record(
                db, grant_id=identities[0], source=_source(), command=_command(), at=AT
            )
            identity, replayed = result.observation.id, result.replayed
            db.commit()
            return identity, replayed

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(receive) for _ in range(2)]
        receipts = [future.result(timeout=30) for future in futures]
    assert receipts[0][0] == receipts[1][0]
    assert sorted(receipt[1] for receipt in receipts) == [False, True]
    with Session(migrated) as db:
        assert len(db.scalars(select(HealthGrant)).all()) == 1
        assert len(db.scalars(select(HealthObservation)).all()) == 1


def test_legacy_grant_race_serializes_and_never_upgrades_history(migrated):
    admission = HealthAdmission(TestAuthority())
    start = Barrier(2)

    def grant():
        with Session(migrated) as db:
            start.wait(timeout=10)
            row = admission.create_grant(db, enrollment_ref="enrollment:1", at=AT)
            identity = row.id
            db.commit()
            return identity

    def legacy():
        with Session(migrated) as db:
            start.wait(timeout=10)
            try:
                result = record_observation(db, _command())
            except HealthAdmissionError:
                return None
            identity = result.observation.id
            db.commit()
            return identity

    with ThreadPoolExecutor(max_workers=2) as executor:
        grant_future = executor.submit(grant)
        legacy_future = executor.submit(legacy)
        grant_id = grant_future.result(timeout=30)
        legacy_id = legacy_future.result(timeout=30)
    with Session(migrated) as db:
        with pytest.raises(HealthAdmissionError, match="reserved"):
            record_observation(db, _command("new-legacy"))
        if legacy_id is not None:
            from dotmac_platform_health import HealthConflict

            assert db.get(HealthObservation, legacy_id).grant_id is None
            with pytest.raises(HealthConflict):
                admission.record(
                    db, grant_id=grant_id, source=_source(), command=_command(), at=AT
                )
            assert record_observation(db, _command()).replayed


def test_immutable_evidence_sql_and_platform_only_acl(migrated):
    admission = HealthAdmission(TestAuthority())
    with Session(migrated) as db:
        grant = admission.create_grant(db, enrollment_ref="enrollment:1", at=AT)
        admission.record(
            db, grant_id=grant.id, source=_source(), command=_command(), at=AT
        )
        admission.close_grant(db, grant_id=grant.id, closed_at=AT, reason="retired")
        db.commit()
    for statement in (
        "UPDATE mod_health.health_grants SET installation_ref='replacement'",
        "DELETE FROM mod_health.health_grant_components",
        "UPDATE mod_health.health_grant_closures SET reason='rewritten'",
        "UPDATE mod_health.health_observations SET grant_id=NULL",
        "UPDATE mod_health.health_eligibility_assessments "
        "SET assessment_ref='replacement'",
        "DELETE FROM mod_health.health_eligibility_assessments",
        "TRUNCATE mod_health.health_eligibility_assessments CASCADE",
        "DELETE FROM mod_health.health_observations",
        "TRUNCATE mod_health.health_observations",
    ):
        with migrated.begin() as conn:
            with pytest.raises(DBAPIError):
                with conn.begin_nested():
                    conn.execute(text(statement))
    # The narrow id UPDATE privilege exists only for PostgreSQL FK KEY SHARE.
    # Even a no-op id write must hit the immutable trigger for both service roles.
    # app_admin cannot assume platform_api. Preserve the configured migration
    # owner's connection details and replace only its scratch database target.
    owner_url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    assert owner_url is not None  # The migrated fixture requires this source.
    database = migrated.url.database
    assert database is not None
    migration_owner = create_engine(make_url(owner_url).set(database=database))
    try:
        for role in ("app_admin", "platform_api"):
            for statement in (
                "UPDATE mod_health.health_grants SET id=id",
                "UPDATE mod_health.health_grant_components SET id=id",
                "UPDATE mod_health.health_eligibility_assessments SET id=id",
            ):
                with migration_owner.connect() as conn, conn.begin() as transaction:
                    conn.execute(text(f"SET LOCAL ROLE {role}"))
                    assert conn.scalar(text("SELECT current_user")) == role
                    with pytest.raises(
                        DBAPIError, match="health evidence is immutable"
                    ):
                        with conn.begin_nested():
                            conn.execute(text(statement))
                    transaction.rollback()
    finally:
        migration_owner.dispose()
    with migrated.begin() as conn:
        conn.execute(text("SET LOCAL ROLE app_user"))
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("SELECT * FROM mod_health.health_grants"))


def test_upgrade_preserves_old_null_and_refuses_populated_downgrade(monkeypatch):
    from alembic import command

    with _scratch_database(monkeypatch) as url:
        command.upgrade(_config(), "ph_0002_freshness_snapshot")
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mod_health.health_components "
                    "(id, code, display_name, freshness_seconds, active) VALUES "
                    "('00000000-0000-0000-0000-000000000001', "
                    "'legacy', 'Legacy', 60, true)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO mod_health.health_observations "
                    "(id, component_id, source_ref, observation_key, "
                    "request_fingerprint, state, observed_at, received_at, "
                    "summary, labels) VALUES "
                    "('00000000-0000-0000-0000-000000000002', "
                    "'00000000-0000-0000-0000-000000000001', "
                    "'legacy', 'old', repeat('a',64), 'healthy', "
                    "now(), now(), 'old', '{}')"
                )
            )
        command.upgrade(_config(), "ph_0003_enrollment_grants")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT grant_id FROM mod_health.health_observations")
                ).scalar_one()
                is None
            )
        with pytest.raises(
            DBAPIError, match="populated health evidence downgrade refused"
        ):
            command.downgrade(_config(), "ph_0002_freshness_snapshot")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM mod_health.health_observations")
                ).scalar_one()
                == 1
            )
        engine.dispose()


def test_downgrade_waits_for_racing_writer_before_testing_emptiness(migrated):
    """A pre-guard invisible write must become retained evidence, not lost DDL.

    Without the early owner locks the empty guard passes while the writer is
    uncommitted, then later DDL waits. Committing that writer lets the unsafe
    downgrade succeed. Early locks instead wait before the guard, which sees
    the committed row and refuses the downgrade.
    """
    from alembic import command

    with Session(migrated) as writer:
        receipt = record_observation(writer, _command("racing-downgrade"))
        observation_id = receipt.observation.id
        with ThreadPoolExecutor(max_workers=1) as executor:
            downgrade = executor.submit(
                command.downgrade, _config(), "ph_0002_freshness_snapshot"
            )
            deadline = monotonic() + 15
            blocked = False
            try:
                while monotonic() < deadline:
                    with migrated.connect() as reader:
                        blocked = bool(
                            reader.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname = current_database() "
                                    "AND pid <> pg_backend_pid() "
                                    "AND wait_event_type = 'Lock')"
                                )
                            )
                        )
                    if blocked or downgrade.done():
                        break
                    sleep(0.05)
            finally:
                # Always release the writer so a failing canary cannot strand
                # a blocked migration thread or its scratch database cleanup.
                writer.commit()
            assert blocked, "downgrade did not contend with the evidence writer"
            with pytest.raises(
                DBAPIError, match="populated health evidence downgrade refused"
            ):
                downgrade.result(timeout=30)
    with Session(migrated) as reader:
        retained = reader.get(HealthObservation, observation_id)
        assert retained is not None and retained.grant_id is None
