"""Live PostgreSQL proofs for the greenfield domain-service owner."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from dotmac_domains.contracts import (
    Actor,
    ApprovalDecision,
    ApprovalReceipt,
    ClearDomainHold,
    ConsequenceRequest,
    DomainLifecycleState,
    DomainObservationV1,
    OutcomeClass,
    OutcomeKind,
    RecordRegistrarOutcome,
    RegisterDomain,
    ReleaseDomain,
    RenewDomain,
    release_content_digest,
)
from dotmac_domains.manifest import module
from dotmac_domains.models import (
    ALL_MODELS,
    DomainAttentionCondition,
    DomainCommand,
    DomainCommandOutcome,
    DomainHold,
    DomainIntent,
    DomainObservation,
    DomainService,
)
from dotmac_domains.service import (
    DomainError,
    ReleaseNotPermitted,
    apply_consequence_request,
    clear_domain_hold,
    receive_registrar_observation,
    reconcile_domain,
    record_registrar_outcome,
    request_registration,
    request_release,
    request_renewal,
)
from dotmac_kernel.audit_actions import (
    AuditActionRegistry,
    install_audit_actions,
)
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.messaging.envelope import CommandEnvelope
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
DOMAIN_VERSIONS = (
    REPO_ROOT / "packages/dotmac-domains/src/dotmac_domains/migrations/versions"
)
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
EXPIRES = NOW + timedelta(days=365)


@pytest.fixture(autouse=True)
def _domain_audit_actions() -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — domains needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def scratch() -> Iterator[dict[str, str]]:
    superuser = _superuser_url()
    name = f"domains_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {DOMAIN_VERSIONS}",
        )
        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="domains", planes=(ModulePlane.TENANT,)),
        )
        admin_url = _url_for(superuser, name, user="app_admin")
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield {
            "admin": admin_url,
            "tenant": _url_for(superuser, name, user="app_user"),
        }
    finally:
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name, is_active) "
                    "VALUES (:id, :slug, :name, true)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
    engine.dispose()
    return tenant_a, tenant_b


def _session(url: str, tenant_id: uuid.UUID) -> Session:
    session = Session(create_engine(url))
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, false)"),
        {"tenant": str(tenant_id)},
    )
    return session


def _registration(
    name: str = "customer.ng", *, requested_at: datetime = NOW
) -> RegisterDomain:
    return RegisterDomain(
        name=name,
        order_line_ref=f"order-line:{name}",
        offer_version_ref="domain-offer:v1",
        term_months=12,
        contact_set_ref="contact-set:1",
        nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        privacy_requested=True,
        commercial_renewal_at=EXPIRES - timedelta(days=30),
        requested_at=requested_at,
    )


def _envelope(tenant_id: uuid.UUID, suffix: str) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=f"command:{suffix}",
        command_type="domains.registrar.observation.v1",
        tenant_id=tenant_id,
        correlation_id=f"correlation:{suffix}",
        issued_at=NOW,
    )


def _observation(
    name: str = "customer.ng",
    *,
    event: str = "event-registered",
    kind: str = "registered",
    observed_at: datetime = NOW + timedelta(seconds=1),
    expires_at: datetime | None = EXPIRES,
    source_mode: str = "ingress",
) -> DomainObservationV1:
    return DomainObservationV1(
        name=name,
        observation_kind=kind,
        provider_statuses=("ok",),
        expires_at=expires_at,
        nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        observed_at=observed_at,
        provider_event_id=event,
        capability_binding_ref="registrar-binding-1",
        source_mode=source_mode,
    )


def _register_and_activate(
    db: Session, tenant_id: uuid.UUID, name: str = "customer.ng"
):
    receipt = request_registration(
        db,
        tenant_id=tenant_id,
        command=_registration(name),
        idempotency_key=f"register:{name}",
        idempotency_expires_at=NOW + timedelta(days=30),
    )
    receive_registrar_observation(
        db,
        envelope=_envelope(tenant_id, f"registered:{name}"),
        observation=_observation(name, event=f"event:{name}"),
        received_at=NOW + timedelta(seconds=2),
    )
    result = reconcile_domain(
        db,
        tenant_id=tenant_id,
        domain_service_id=receipt.domain_service_id,
        reconciled_at=NOW + timedelta(seconds=3),
    )
    assert result.current_state is DomainLifecycleState.ACTIVE
    return receipt


def test_live_catalog_proves_tenant_only_forced_rls(scratch: dict[str, str]) -> None:
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(scratch["admin"])
    with engine.connect() as connection:
        assert audit_live_schemas(connection, registry) == ()
        rows = list(
            connection.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='mod_domains' AND c.relkind='r'"
                )
            )
        )
    engine.dispose()
    assert {row[0] for row in rows} == set(module.tables)
    assert all(row[1] and row[2] for row in rows)


def test_online_role_cannot_read_or_write_another_tenant(
    scratch: dict[str, str],
) -> None:
    tenant_a, tenant_b = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant_a) as db:
        _register_and_activate(db, tenant_a)
        db.commit()
    with _session(scratch["tenant"], tenant_b) as db:
        for model in ALL_MODELS:
            assert db.scalar(select(func.count()).select_from(model)) == 0
        with pytest.raises(DBAPIError):
            request_registration(
                db,
                tenant_id=tenant_a,
                command=_registration("forged.ng"),
                idempotency_key="forged",
                idempotency_expires_at=NOW + timedelta(days=30),
            )


def test_callback_records_evidence_but_cannot_assign_lifecycle_state(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(),
            idempotency_key="registration",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "callback"),
            observation=_observation(),
            received_at=NOW + timedelta(seconds=2),
        )
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        assert service.lifecycle_state == DomainLifecycleState.REGISTRATION_REQUESTED
        assert db.scalar(select(func.count()).select_from(DomainObservation)) == 1
        reconciled = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(seconds=3),
        )
        assert reconciled.current_state is DomainLifecycleState.ACTIVE


def test_observation_before_local_correlation_survives_and_reconciles(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        observation_receipt = receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "early-callback"),
            observation=_observation(
                event="early-event", observed_at=NOW + timedelta(seconds=2)
            ),
            received_at=NOW + timedelta(seconds=3),
        )
        row = db.get(DomainObservation, observation_receipt.observation_id)
        assert row is not None and row.domain_service_id is None
        receipt = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(requested_at=NOW),
            idempotency_key="registration-after-callback",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        result = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(seconds=4),
        )
        assert result.current_state is DomainLifecycleState.ACTIVE
        assert row.domain_service_id is None


def test_duplicate_provider_event_is_one_immutable_observation(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    observation = _observation(event="duplicate-event")
    with _session(scratch["tenant"], tenant) as db:
        first = receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "delivery-1"),
            observation=observation,
            received_at=NOW + timedelta(seconds=2),
        )
        second = receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "delivery-2"),
            observation=observation,
            received_at=NOW + timedelta(seconds=3),
        )
        assert first.observation_id == second.observation_id
        assert second.duplicate
        assert db.scalar(select(func.count()).select_from(DomainObservation)) == 1


def test_poll_repairs_a_lost_callback_to_the_same_terminal_state(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(),
            idempotency_key="lost-callback",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        polled = _observation(event="poll-event", source_mode="poll")
        receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "poll"),
            observation=polled,
            received_at=NOW + timedelta(minutes=10),
        )
        result = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(minutes=11),
        )
        assert result.current_state is DomainLifecycleState.ACTIVE


def test_out_of_order_expiry_does_not_regress_newer_registration(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "late-old-expiry"),
            observation=_observation(
                event="old-expiry",
                kind="expiry_observed",
                observed_at=NOW - timedelta(days=1),
                expires_at=NOW - timedelta(days=2),
            ),
            received_at=NOW + timedelta(days=1),
        )
        result = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(days=1),
        )
        assert result.current_state is DomainLifecycleState.ACTIVE
        assert not result.changed


def test_paid_renewal_failure_stays_open_until_confirmed_observation(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        renewal = request_renewal(
            db,
            tenant_id=tenant,
            command=RenewDomain(
                domain_service_id=receipt.domain_service_id,
                term_months=12,
                coverage_reference="coverage:paid-renewal",
                commercial_renewal_at=EXPIRES + timedelta(days=335),
                expected_registrar_expiry=EXPIRES,
                requested_at=NOW + timedelta(days=300),
            ),
            idempotency_key="renewal",
            idempotency_expires_at=NOW + timedelta(days=700),
        )
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        assert service.lifecycle_state == DomainLifecycleState.RENEWAL_REQUESTED
        record_registrar_outcome(
            db,
            envelope=_envelope(tenant, "renewal-failed"),
            outcome=RecordRegistrarOutcome(
                domain_command_id=renewal.command_id,
                evidence_key="registrar-failure-1",
                outcome_kind=OutcomeKind.FAILED,
                outcome_class=OutcomeClass.RETRYABLE,
                occurred_at=NOW + timedelta(days=300, seconds=1),
                reason_code="registrar_unreachable",
            ),
            received_at=NOW + timedelta(days=300, seconds=2),
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(DomainAttentionCondition)
                .where(DomainAttentionCondition.resolved_at.is_(None))
            )
            == 1
        )
        # An unrelated successful poll does not silently clear a paid failure.
        receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "unchanged-poll"),
            observation=_observation(
                event="unchanged-poll-event",
                kind="expiry_observed",
                observed_at=NOW + timedelta(days=300, seconds=3),
                expires_at=EXPIRES,
                source_mode="poll",
            ),
            received_at=NOW + timedelta(days=300, seconds=4),
        )
        reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(days=300, seconds=5),
        )
        condition = db.scalar(select(DomainAttentionCondition))
        assert condition is not None and condition.resolved_at is None

        receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "renewed"),
            observation=_observation(
                event="renewed-event",
                kind="renewed",
                observed_at=NOW + timedelta(days=300, seconds=6),
                expires_at=EXPIRES + timedelta(days=365),
            ),
            received_at=NOW + timedelta(days=300, seconds=7),
        )
        result = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(days=300, seconds=8),
        )
        assert result.current_state is DomainLifecycleState.ACTIVE
        assert condition.resolved_at is not None


def test_commercial_and_registrar_dates_survive_drift_detection(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        service = db.get(DomainService, receipt.domain_service_id)
        observation = db.scalar(select(DomainObservation))
        assert service is not None and observation is not None
        commercial_before = service.commercial_renewal_at
        registrar_before = observation.expires_at
        result = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(seconds=5),
        )
        assert result.drift.expiry_disagrees
        assert service.commercial_renewal_at == commercial_before
        assert observation.expires_at == registrar_before


def test_registration_replay_returns_original_and_changed_request_conflicts(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        first = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(),
            idempotency_key="registration-replay",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        second = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(),
            idempotency_key="registration-replay",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        assert first.command_id == second.command_id
        assert second.replayed
        assert db.scalar(select(func.count()).select_from(DomainService)) == 1
        with pytest.raises(IdempotencyConflict):
            request_registration(
                db,
                tenant_id=tenant,
                command=_registration("changed.ng"),
                idempotency_key="registration-replay",
                idempotency_expires_at=NOW + timedelta(days=30),
            )


def test_concurrent_same_key_registration_produces_one_service_and_outbox(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])

    def run() -> tuple[uuid.UUID, bool]:
        with _session(scratch["tenant"], tenant) as db:
            receipt = request_registration(
                db,
                tenant_id=tenant,
                command=_registration(),
                idempotency_key="concurrent-registration",
                idempotency_expires_at=NOW + timedelta(days=30),
            )
            db.commit()
            return receipt.command_id, receipt.replayed

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))
    assert len({command_id for command_id, _ in results}) == 1
    assert sorted(replayed for _, replayed in results) == [False, True]
    with _session(scratch["tenant"], tenant) as db:
        assert db.scalar(select(func.count()).select_from(DomainService)) == 1
        assert db.scalar(select(func.count()).select_from(DomainCommand)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 2


def test_concurrent_same_key_renewal_serializes_before_idempotency_lookup(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        registration = _register_and_activate(db, tenant)
        db.commit()

    renewal_command = RenewDomain(
        domain_service_id=registration.domain_service_id,
        term_months=12,
        coverage_reference="coverage:concurrent-renewal",
        commercial_renewal_at=EXPIRES + timedelta(days=335),
        expected_registrar_expiry=EXPIRES,
        requested_at=NOW + timedelta(days=300),
    )

    def run() -> tuple[uuid.UUID, bool]:
        with _session(scratch["tenant"], tenant) as db:
            receipt = request_renewal(
                db,
                tenant_id=tenant,
                command=renewal_command,
                idempotency_key="concurrent-renewal",
                idempotency_expires_at=NOW + timedelta(days=700),
            )
            db.commit()
            return receipt.command_id, receipt.replayed

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))

    # This fails if the aggregate lock moves back inside execute_once: the
    # losing request sees renewal_requested before it can replay the ledger.
    assert len({command_id for command_id, _ in results}) == 1
    assert sorted(replayed for _, replayed in results) == [False, True]
    with _session(scratch["tenant"], tenant) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(DomainCommand)
                .where(DomainCommand.command_kind == "renewal")
            )
            == 1
        )


def test_release_refuses_missing_stale_or_changed_approval_then_accepts_exact(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    actor = Actor(actor_type="user", actor_id="operator-1")
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        unapproved = ReleaseDomain(
            domain_service_id=service.id,
            expected_version=service.row_version,
            reason_code="customer_requested_transfer",
            requested_at=NOW + timedelta(days=2),
            approval=None,
        )
        with pytest.raises(ReleaseNotPermitted):
            request_release(
                db,
                tenant_id=tenant,
                command=unapproved,
                idempotency_key="release-without-approval",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        digest = release_content_digest(service.registered_name, unapproved)
        approved = ApprovalReceipt(
            policy_code="domains.release",
            policy_version=1,
            content_digest=digest,
            decision=ApprovalDecision.APPROVED,
            decided_at=NOW + timedelta(days=1),
            decision_reference="approval-1",
        )
        exact = ReleaseDomain(
            domain_service_id=service.id,
            expected_version=service.row_version,
            reason_code="customer_requested_transfer",
            requested_at=NOW + timedelta(days=2),
            approval=approved,
        )
        wrong_policy = ReleaseDomain(
            domain_service_id=service.id,
            expected_version=service.row_version,
            reason_code="customer_requested_transfer",
            requested_at=NOW + timedelta(days=2),
            approval=ApprovalReceipt(
                policy_code="domains.transfer_out",
                policy_version=1,
                content_digest=digest,
                decision=ApprovalDecision.APPROVED,
                decided_at=NOW + timedelta(days=1),
                decision_reference="wrong-policy-approval",
            ),
        )
        with pytest.raises(ReleaseNotPermitted, match="exact content"):
            request_release(
                db,
                tenant_id=tenant,
                command=wrong_policy,
                idempotency_key="release-wrong-policy",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        release = request_release(
            db,
            tenant_id=tenant,
            command=exact,
            idempotency_key="release-approved",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        assert release.lifecycle_state is DomainLifecycleState.RELEASE_REQUESTED


def test_collections_style_request_cannot_release_or_expire_a_domain(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        outcome = apply_consequence_request(
            db,
            tenant_id=tenant,
            command=ConsequenceRequest(
                domain_service_id=receipt.domain_service_id,
                consequence_kind="release",
                source_owner="collections",
                source_reference="case-1",
                reason_code="delinquent",
                requested_at=NOW + timedelta(days=2),
            ),
            idempotency_key="collections-release",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=Actor(actor_type="service", actor_id="cloud-assembly"),
        )
        service = db.get(DomainService, receipt.domain_service_id)
        assert outcome.decision == "refused"
        assert service is not None
        assert service.lifecycle_state == DomainLifecycleState.ACTIVE


def test_source_owned_hold_blocks_release_until_its_owner_clears_it(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    actor = Actor(actor_type="service", actor_id="cloud-assembly")
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        applied = apply_consequence_request(
            db,
            tenant_id=tenant,
            command=ConsequenceRequest(
                domain_service_id=receipt.domain_service_id,
                consequence_kind="renewal_review",
                source_owner="collections",
                source_reference="case-hold",
                reason_code="renewal_needs_review",
                requested_at=NOW + timedelta(days=2),
            ),
            idempotency_key="apply-renewal-hold",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        assert applied.decision == "applied"
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        unapproved = ReleaseDomain(
            domain_service_id=service.id,
            expected_version=service.row_version,
            reason_code="customer_requested_transfer",
            requested_at=NOW + timedelta(days=4),
            approval=None,
        )
        digest = release_content_digest(service.registered_name, unapproved)
        release = ReleaseDomain(
            domain_service_id=service.id,
            expected_version=service.row_version,
            reason_code=unapproved.reason_code,
            requested_at=unapproved.requested_at,
            approval=ApprovalReceipt(
                policy_code="domains.release",
                policy_version=1,
                content_digest=digest,
                decision=ApprovalDecision.APPROVED,
                decided_at=NOW + timedelta(days=2, hours=1),
                decision_reference="approval-held-release",
            ),
        )
        with pytest.raises(ReleaseNotPermitted, match="active domain hold"):
            request_release(
                db,
                tenant_id=tenant,
                command=release,
                idempotency_key="held-release",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        with pytest.raises(DomainError, match="source owner"):
            clear_domain_hold(
                db,
                tenant_id=tenant,
                command=ClearDomainHold(
                    domain_service_id=service.id,
                    hold_code="renewal_review",
                    source_owner="orders",
                    source_reference="case-hold",
                    reason_code="wrong_owner",
                    requested_at=NOW + timedelta(days=3, hours=1),
                ),
                idempotency_key="wrong-owner-clear",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        cleared = clear_domain_hold(
            db,
            tenant_id=tenant,
            command=ClearDomainHold(
                domain_service_id=service.id,
                hold_code="renewal_review",
                source_owner="collections",
                source_reference="case-hold",
                reason_code="review_complete",
                requested_at=NOW + timedelta(days=3, hours=2),
            ),
            idempotency_key="clear-renewal-hold",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        assert cleared.decision == "applied"
        accepted = request_release(
            db,
            tenant_id=tenant,
            command=release,
            idempotency_key="release-after-hold",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        assert accepted.lifecycle_state is DomainLifecycleState.RELEASE_REQUESTED
        hold_id = db.scalar(select(DomainHold.id))
        assert hold_id is not None
        db.commit()

    admin = create_engine(scratch["admin"])
    with admin.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE mod_domains.domain_holds "
                    "SET source_reference = 'rewritten' WHERE id = :id"
                ),
                {"id": hold_id},
            )
        connection.rollback()
    admin.dispose()


@pytest.mark.parametrize(
    ("model", "update_statement"),
    [
        (
            DomainCommand,
            text("UPDATE mod_domains.domain_commands SET id = id WHERE id = :id"),
        ),
        (
            DomainCommandOutcome,
            text(
                "UPDATE mod_domains.domain_command_outcomes "
                "SET id = id WHERE id = :id"
            ),
        ),
        (
            DomainObservation,
            text("UPDATE mod_domains.domain_observations SET id = id WHERE id = :id"),
        ),
        (
            DomainIntent,
            text("UPDATE mod_domains.domain_intents SET id = id WHERE id = :id"),
        ),
    ],
)
def test_online_and_admin_paths_cannot_rewrite_or_delete_evidence(
    scratch: dict[str, str], model: Any, update_statement: Any
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        consequence = apply_consequence_request(
            db,
            tenant_id=tenant,
            command=ConsequenceRequest(
                domain_service_id=receipt.domain_service_id,
                consequence_kind="release",
                source_owner="collections",
                source_reference="case-evidence",
                reason_code="delinquent",
                requested_at=NOW + timedelta(days=2),
            ),
            idempotency_key="evidence-outcome",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=Actor(actor_type="service", actor_id="cloud-assembly"),
        )
        row = db.scalar(select(model))
        assert row is not None
        row_id = row.id
        with pytest.raises(DBAPIError):
            db.execute(update_statement, {"id": row_id})
        db.rollback()

    admin = create_engine(scratch["admin"])
    with admin.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(update_statement, {"id": row_id})
        connection.rollback()
    admin.dispose()
    assert consequence.decision == "refused"


def test_app_user_cannot_turn_tenant_domain_routing_state_into_domain_state(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    routing_id = uuid.uuid4()
    admin = create_engine(scratch["admin"])
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.tenant_domains (id, tenant_id, domain) "
                "VALUES (:id, :tenant, 'portal.customer.ng')"
            ),
            {"id": routing_id, "tenant": tenant},
        )
    admin.dispose()
    with _session(scratch["tenant"], tenant) as db:
        with pytest.raises(DBAPIError):
            db.execute(
                text(
                    "UPDATE public.tenant_domains SET verified_at = now() "
                    "WHERE id = :id"
                ),
                {"id": routing_id},
            )


def test_caller_rollback_removes_state_idempotency_and_outbox_together(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        request_registration(
            db,
            tenant_id=tenant,
            command=_registration(),
            idempotency_key="rolled-back-registration",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        db.rollback()
    with _session(scratch["tenant"], tenant) as db:
        assert db.scalar(select(func.count()).select_from(DomainService)) == 0
        assert db.scalar(select(func.count()).select_from(DomainCommand)) == 0
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def _weak_lifecycle_writer(
    db: Session, tenant_id: uuid.UUID, service_id: uuid.UUID
) -> None:
    """Deliberate violation used only to prove the callback canary detects it."""

    service = db.scalar(
        select(DomainService).where(
            DomainService.tenant_id == tenant_id, DomainService.id == service_id
        )
    )
    assert service is not None
    service.lifecycle_state = DomainLifecycleState.EXPIRED.value
    db.flush()


def test_callback_owner_canary_fails_if_the_guard_is_removed(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        _weak_lifecycle_writer(db, tenant, receipt.domain_service_id)
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        with pytest.raises(AssertionError):
            assert service.lifecycle_state == DomainLifecycleState.ACTIVE
