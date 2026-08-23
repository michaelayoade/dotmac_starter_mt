"""Live PostgreSQL proofs for the tenant-only hosting-service owner."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, local

import pytest
from dotmac_hosting.contracts import (
    Actor,
    ApprovalObservationState,
    ChangeHostingPackage,
    ClearRetentionHold,
    ConsequenceDisposition,
    HostingLifecycleState,
    HostingAccountIdentityV1,
    HostingAllowance,
    HostingChangeRules,
    HostingObservationV1,
    HostingOutcomeEvidenceV1,
    HostingResourceFactV1,
    ProvisionHostingService,
    ProvisionHostingAccountV1,
    PublishHostingSpecificationVersion,
    RecordHostingOutcome,
    RequestTermination,
    OutcomeClass,
    OutcomeKind,
    RestoreSuspensionRequest,
    RetentionHoldRequest,
    SuspensionRequest,
    TerminationApprovalObservationV1,
    termination_content_digest,
)
from dotmac_hosting.manifest import module
from dotmac_hosting.models import (
    ALL_MODELS,
    HostingAttentionCondition,
    HostingCommandOutcome,
    HostingCommand,
    HostingDesiredRevision,
    HostingObservation,
    HostingObservationResource,
    HostingRetentionHold,
    HostingService,
    HostingSpecificationVersion,
    HostingSuspensionLock,
    HostingTerminationApprovalEvidence,
)
from dotmac_hosting.service import (
    ApprovalRequired,
    HostingError,
    apply_suspension_request,
    clear_retention_hold,
    place_retention_hold,
    publish_specification_version,
    receive_hosting_observation,
    receive_termination_approval,
    reconcile_hosting_service,
    record_hosting_outcome,
    request_package_change,
    request_provisioning,
    request_termination,
    restore_suspension,
)
from dotmac_kernel.audit_actions import (
    AuditActionRegistry,
    AuditActionsNotInstalledError,
    active_audit_actions,
    install_audit_actions,
)
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.messaging.envelope import CommandEnvelope
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import (
    CheckConstraint,
    create_engine,
    func,
    inspect as sa_inspect,
    select,
    text,
)
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from dotmac_hosting.vocabulary import (
    active_hosting_vocabulary,
    install_hosting_vocabulary,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
HOSTING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-hosting/src/dotmac_hosting/migrations/versions"
)
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
IDEMPOTENCY_EXPIRY = NOW + timedelta(days=30)
MUTATE_SERVICE_SQL = (
    "SELECT mod_hosting.mutate_hosting_service("
    "CAST(:tenant_id AS uuid), CAST(:service_id AS uuid), "
    "CAST(:expected_version AS integer), CAST(:mutation_kind AS text), "
    "CAST(:updated_at AS timestamp with time zone), "
    "CAST(:specification_code AS text), CAST(:specification_version AS integer), "
    "CAST(:lifecycle_state AS text), "
    "CAST(:state_effective_at AS timestamp with time zone), "
    "CAST(:observation_id AS uuid))"
)


@pytest.fixture(autouse=True)
def _hosting_audit_actions() -> Iterator[None]:
    try:
        previous = active_audit_actions()
    except AuditActionsNotInstalledError:
        previous = AuditActionRegistry(())
    install_audit_actions(AuditActionRegistry.from_manifests([module]))
    try:
        yield
    finally:
        install_audit_actions(previous)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — hosting needs PostgreSQL")
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
    name = f"hosting_{uuid.uuid4().hex[:12]}"
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
    previous_migration_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {HOSTING_VERSIONS}",
        )
        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="hosting", planes=(ModulePlane.TENANT,)),
        )
        admin_url = _url_for(superuser, name, user="app_admin")
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield {
            "admin": admin_url,
            "tenant": _url_for(superuser, name, user="app_user"),
        }
    finally:
        if previous_migration_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous_migration_url
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


def _actor() -> Actor:
    party_id = uuid.uuid4()
    return Actor(actor_type="user", actor_id=str(party_id), actor_party_id=party_id)


def _publish(db: Session, tenant_id: uuid.UUID, version: int = 1):
    return publish_specification_version(
        db,
        tenant_id=tenant_id,
        command=PublishHostingSpecificationVersion(
            specification_code="business-hosting",
            package_ref=f"business-hosting:v{version}",
            package_rank=1,
            allowances=(
                HostingAllowance(
                    resource_kind="disk_bytes",
                    quantity=Decimal(20 * version * 1024**3),
                    unit="bytes",
                ),
                HostingAllowance(
                    resource_kind="bandwidth_bytes",
                    quantity=Decimal(200 * 1024**3),
                    unit="bytes",
                ),
            ),
            included_artifacts=("tls", "backup"),
            capability_codes=("php", "database"),
            change_rules=HostingChangeRules(
                upgrade_allowed=True,
                downgrade_allowed=True,
                downgrade_requires_review=True,
                same_level_allowed=True,
            ),
            published_at=NOW,
        ),
        idempotency_key=f"spec:{version}",
        idempotency_expires_at=IDEMPOTENCY_EXPIRY,
    )


def _provision(db: Session, tenant_id: uuid.UUID):
    _publish(db, tenant_id)
    return request_provisioning(
        db,
        tenant_id=tenant_id,
        command=ProvisionHostingService(
            customer_ref="customer:1",
            order_line_ref="order-line:1",
            offer_version_ref="offer:v1",
            specification_code="business-hosting",
            specification_version=1,
            primary_domain="customer.ng",
            account_identity=HostingAccountIdentityV1(
                account_label="Customer Limited",
                administrative_email="admin@customer.ng",
                country_code="NG",
            ),
            requested_at=NOW,
        ),
        idempotency_key="provision:1",
        idempotency_expires_at=IDEMPOTENCY_EXPIRY,
    )


def _envelope(tenant_id: uuid.UUID, suffix: str) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=f"command:{suffix}",
        command_type="hosting.account.observation.v1",
        tenant_id=tenant_id,
        correlation_id=f"correlation:{suffix}",
        issued_at=NOW,
    )


def _approve_termination(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    service_id: uuid.UUID,
    expected_version: int,
    requested_at: datetime,
    request_id: str,
) -> RequestTermination:
    approval_request_id = uuid.uuid5(uuid.NAMESPACE_URL, request_id)
    command = RequestTermination(
        hosting_service_id=service_id,
        expected_version=expected_version,
        requested_at=requested_at,
        approval_request_id=approval_request_id,
    )
    receive_termination_approval(
        db,
        envelope=CommandEnvelope(
            command_id=f"approval-event:{request_id}",
            command_type="approval.approved",
            tenant_id=tenant_id,
            correlation_id=request_id,
            issued_at=NOW,
        ),
        source_event_id=uuid.uuid5(
            uuid.NAMESPACE_URL, f"approval-source:{request_id}"
        ),
        observation=TerminationApprovalObservationV1(
            event_type="approval.approved",
            request_id=approval_request_id,
            subject_type="hosting_service",
            subject_id=str(service_id),
            policy_code="hosting.termination.v1",
            policy_version=1,
            content_digest=termination_content_digest(
                tenant_id, service_id, expected_version, requested_at
            ),
            state=ApprovalObservationState.APPROVED,
        ),
        received_at=NOW,
    )
    return command


def _observation(
    *,
    event: str = "provider-event-1",
    kind: str = "active",
    observed_at: datetime = NOW + timedelta(seconds=1),
    account_ref: str = "account-1",
    operation_reference: str | None = None,
    source_mode: str = "ingress",
    capability_binding_ref: str = "hosting-binding-1",
    package_ref: str = "business-hosting:v1",
) -> HostingObservationV1:
    return HostingObservationV1(
        provider_account_ref=account_ref,
        provider_event_id=event,
        capability_binding_ref=capability_binding_ref,
        observation_kind=kind,
        provider_statuses=(kind,),
        observed_at=observed_at,
        operation_reference=operation_reference,
        observed_package_ref=package_ref,
        resources=(
            HostingResourceFactV1(
                resource_kind="mailbox_count",
                quantity=3,
                unit="count",
                period_start=NOW,
                period_end=NOW,
            ),
        ),
        source_mode=source_mode,
    )


def _activate(db: Session, tenant_id: uuid.UUID):
    receipt = _provision(db, tenant_id)
    receive_hosting_observation(
        db,
        envelope=_envelope(tenant_id, "active"),
        hosting_service_id=receipt.hosting_service_id,
        observation=_observation(operation_reference=str(receipt.command_id)),
        received_at=NOW + timedelta(seconds=2),
    )
    reconciled = reconcile_hosting_service(
        db,
        tenant_id=tenant_id,
        hosting_service_id=receipt.hosting_service_id,
        reconciled_at=NOW + timedelta(seconds=3),
    )
    assert reconciled.current_state is HostingLifecycleState.ACTIVE
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
                    "WHERE n.nspname='mod_hosting' AND c.relkind='r'"
                )
            )
        )
    engine.dispose()
    assert {row[0] for row in rows} == set(module.tables)
    assert all(row[1] and row[2] for row in rows)


def test_live_catalog_columns_match_every_hosting_model(scratch: dict[str, str]) -> None:
    engine = create_engine(scratch["admin"])
    inspector = sa_inspect(engine)
    for model in ALL_MODELS:
        live = {
            column["name"]: column
            for column in inspector.get_columns(model.__tablename__, schema="mod_hosting")
        }
        declared = {column.name: column for column in model.__table__.columns}
        assert set(live) == set(declared), model.__tablename__
        for name, column in declared.items():
            assert live[name]["nullable"] is column.nullable, (
                model.__tablename__,
                name,
            )
            assert live[name]["type"]._type_affinity is column.type._type_affinity, (
                model.__tablename__,
                name,
            )
        live_checks = {
            check["name"]
            for check in inspector.get_check_constraints(
                model.__tablename__, schema="mod_hosting"
            )
        }
        declared_checks = {
            constraint.name
            for constraint in model.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert live_checks == declared_checks, model.__tablename__
    engine.dispose()


def test_online_role_cannot_read_or_write_another_tenant(
    scratch: dict[str, str],
) -> None:
    tenant_a, tenant_b = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant_a) as db:
        _activate(db, tenant_a)
        db.commit()
    with _session(scratch["tenant"], tenant_b) as db:
        for model in ALL_MODELS:
            assert db.scalar(select(func.count()).select_from(model)) == 0
        with pytest.raises(DBAPIError):
            _publish(db, tenant_a)


def test_observation_is_evidence_until_reconciliation_and_dedupes(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _provision(db, tenant)
        first = receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "first"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(operation_reference=str(receipt.command_id)),
            received_at=NOW + timedelta(seconds=2),
        )
        second = receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "duplicate"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(operation_reference=str(receipt.command_id)),
            received_at=NOW + timedelta(seconds=3),
        )
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        assert service.lifecycle_state == HostingLifecycleState.PROVISIONING
        assert first.observation_id == second.observation_id
        assert second.duplicate
        assert db.scalar(select(func.count()).select_from(HostingObservation)) == 1
        assert (
            db.scalar(select(func.count()).select_from(HostingObservationResource))
            == 1
        )
        reconciled = reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(seconds=4),
        )
        assert reconciled.current_state is HostingLifecycleState.ACTIVE


def test_uncorrelated_poll_repairs_lost_callback_without_rewriting_evidence(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _provision(db, tenant)
        observation = _observation(
            event="provider-poll-after-lost-callback",
            operation_reference=str(receipt.command_id),
            source_mode="poll",
        )
        first = receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "uncorrelated-poll"),
            hosting_service_id=None,
            observation=observation,
            received_at=NOW + timedelta(seconds=2),
        )
        stored = db.get(HostingObservation, first.observation_id)
        assert stored is not None
        assert stored.hosting_service_id is None
        assert stored.operation_reference == str(receipt.command_id)

        repaired = reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(seconds=3),
        )
        assert repaired.current_state is HostingLifecycleState.ACTIVE
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        assert service.provider_account_ref == observation.provider_account_ref

        late_callback = receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "late-callback"),
            hosting_service_id=receipt.hosting_service_id,
            observation=observation,
            received_at=NOW + timedelta(seconds=4),
        )
        assert late_callback.duplicate
        assert late_callback.observation_id == first.observation_id
        db.expire(stored)
        assert stored.hosting_service_id is None
        assert (
            db.scalar(select(func.count()).select_from(HostingObservation)) == 1
        )


def test_provider_account_fallback_is_scoped_to_the_opaque_binding_pair(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        assert (
            service.capability_binding_ref,
            service.provider_account_ref,
        ) == ("hosting-binding-1", "account-1")

        collision = receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "cross-binding-account-collision"),
            hosting_service_id=None,
            observation=_observation(
                event="cross-binding-account-collision",
                kind="suspended",
                observed_at=NOW + timedelta(hours=1),
                account_ref="account-1",
                capability_binding_ref="hosting-binding-2",
                source_mode="poll",
            ),
            received_at=NOW + timedelta(hours=1, seconds=1),
        )
        collision_row = db.get(HostingObservation, collision.observation_id)
        assert collision_row is not None
        assert collision_row.hosting_service_id is None

        reconciled = reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(hours=1, seconds=2),
        )
        assert reconciled.drift.observed_account_state == "active"
        assert reconciled.drift.reasons == ()


def test_provider_binding_account_pair_is_unique_but_account_string_is_not(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])

    def provision(db: Session, suffix: str):
        return request_provisioning(
            db,
            tenant_id=tenant,
            command=ProvisionHostingService(
                customer_ref=f"customer:{suffix}",
                order_line_ref=f"order-line:{suffix}",
                offer_version_ref="offer:v1",
                specification_code="business-hosting",
                specification_version=1,
                primary_domain=f"{suffix}.ng",
                account_identity=HostingAccountIdentityV1(
                    account_label=f"Customer {suffix}",
                    administrative_email=f"admin@{suffix}.ng",
                    country_code="NG",
                ),
                requested_at=NOW,
            ),
            idempotency_key=f"provision:{suffix}",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )

    with _session(scratch["tenant"], tenant) as db:
        _publish(db, tenant)
        first = provision(db, "one")
        second = provision(db, "two")
        for receipt, binding, event in (
            (first, "binding-a", "event-a"),
            (second, "binding-b", "event-b"),
        ):
            receive_hosting_observation(
                db,
                envelope=_envelope(tenant, event),
                hosting_service_id=receipt.hosting_service_id,
                observation=_observation(
                    event=event,
                    account_ref="shared-account-string",
                    capability_binding_ref=binding,
                    operation_reference=str(receipt.command_id),
                ),
                received_at=NOW + timedelta(seconds=2),
            )
            reconcile_hosting_service(
                db,
                tenant_id=tenant,
                hosting_service_id=receipt.hosting_service_id,
                reconciled_at=NOW + timedelta(seconds=3),
            )
        assert db.scalar(select(func.count()).select_from(HostingService)) == 2

        third = provision(db, "three")
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "event-c"),
            hosting_service_id=third.hosting_service_id,
            observation=_observation(
                event="event-c",
                account_ref="shared-account-string",
                capability_binding_ref="binding-a",
                operation_reference=str(third.command_id),
            ),
            received_at=NOW + timedelta(seconds=4),
        )
        with pytest.raises(IntegrityError) as duplicate:
            reconcile_hosting_service(
                db,
                tenant_id=tenant,
                hosting_service_id=third.hosting_service_id,
                reconciled_at=NOW + timedelta(seconds=5),
            )
        assert duplicate.value.orig.diag.constraint_name == (
            "uq_hosting_services_binding_account"
        )


def test_observation_hint_must_prove_first_operation_then_frozen_pair(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _provision(db, tenant)
        with pytest.raises(HostingError, match="operation_reference does not belong"):
            receive_hosting_observation(
                db,
                envelope=_envelope(tenant, "wrong-operation"),
                hosting_service_id=receipt.hosting_service_id,
                observation=_observation(operation_reference=str(uuid.uuid4())),
                received_at=NOW + timedelta(seconds=2),
            )
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "right-operation"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(operation_reference=str(receipt.command_id)),
            received_at=NOW + timedelta(seconds=2),
        )
        reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(seconds=3),
        )
        with pytest.raises(HostingError, match="frozen binding/account pair"):
            receive_hosting_observation(
                db,
                envelope=_envelope(tenant, "wrong-pair"),
                hosting_service_id=receipt.hosting_service_id,
                observation=_observation(
                    event="wrong-pair",
                    capability_binding_ref="other-binding",
                    operation_reference=str(receipt.command_id),
                ),
                received_at=NOW + timedelta(seconds=4),
            )


def test_provision_outbox_is_the_exact_self_contained_v1_snapshot(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _provision(db, tenant)
        command = db.get(HostingCommand, receipt.command_id)
        event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant,
                OutboxEvent.event_type == "hosting.account.provision.requested.v1",
            )
        )
        assert command is not None and event is not None
        assert command.payload == event.payload
        payload = dict(event.payload)
        identity = HostingAccountIdentityV1(**dict(payload.pop("account_identity")))
        provider_request = ProvisionHostingAccountV1(
            account_identity=identity,
            **payload,
        )
        assert provider_request.package_ref == "business-hosting:v1"
        assert all(
            key not in event.payload
            for key in (
                "hosting_service_id",
                "desired_revision_id",
                "order_line_ref",
                "customer_ref",
                "owner_contact_ref",
            )
        )


def test_outcome_evidence_conflicts_on_changed_content_and_confirmation_closes_attention(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _provision(db, tenant)
        outcome = RecordHostingOutcome(
            hosting_command_id=receipt.command_id,
            evidence_key="provider-ack:1",
            outcome_kind=OutcomeKind.ACKNOWLEDGED,
            outcome_class=OutcomeClass.RECONCILIATION_REQUIRED,
            occurred_at=NOW + timedelta(seconds=1),
            provider_reference="ack:1",
            reason_code="callback_pending",
            evidence=HostingOutcomeEvidenceV1(
                provider_statuses=("accepted",),
                diagnostic_codes=("callback.pending",),
            ),
        )
        stored_id = record_hosting_outcome(
            db,
            envelope=_envelope(tenant, "outcome-one"),
            outcome=outcome,
            received_at=NOW + timedelta(seconds=2),
        )
        assert stored_id
        with pytest.raises(HostingError, match="different immutable content"):
            record_hosting_outcome(
                db,
                envelope=_envelope(tenant, "outcome-conflict"),
                outcome=RecordHostingOutcome(
                    hosting_command_id=receipt.command_id,
                    evidence_key="provider-ack:1",
                    outcome_kind=OutcomeKind.ACKNOWLEDGED,
                    outcome_class=OutcomeClass.RECONCILIATION_REQUIRED,
                    occurred_at=NOW + timedelta(seconds=1),
                    provider_reference="ack:1",
                    reason_code="callback_pending",
                    evidence=HostingOutcomeEvidenceV1(
                        provider_statuses=("accepted",),
                        diagnostic_codes=("changed",),
                    ),
                ),
                received_at=NOW + timedelta(seconds=3),
            )
        attention = db.scalar(
            select(HostingAttentionCondition).where(
                HostingAttentionCondition.source_command_id == receipt.command_id
            )
        )
        assert attention is not None and attention.resolved_at is None
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "outcome-confirmed"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(operation_reference=str(receipt.command_id)),
            received_at=NOW + timedelta(seconds=4),
        )
        reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(seconds=5),
        )
        db.refresh(attention)
        assert attention.resolution_code == "provider_observation_confirmed"


def test_retention_hold_refuses_delinquency_and_receipts_the_refusal(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        place_retention_hold(
            db,
            tenant_id=tenant,
            command=RetentionHoldRequest(
                hosting_service_id=receipt.hosting_service_id,
                hold_code="legal_preservation",
                source_owner="support",
                source_reference="case:1",
                reason_code="legal_hold",
                requested_at=NOW + timedelta(minutes=1),
            ),
            actor=_actor(),
            idempotency_key="hold:case:1",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        outcome = apply_suspension_request(
            db,
            tenant_id=tenant,
            command=SuspensionRequest(
                hosting_service_id=receipt.hosting_service_id,
                reason_code="delinquency",
                source_owner="collections",
                source_reference="case:2",
                requested_at=NOW + timedelta(minutes=2),
            ),
            actor=_actor(),
            idempotency_key="suspend:refused",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert outcome.disposition is ConsequenceDisposition.REFUSED
        assert outcome.reason_code == "retention_hold_active"
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        assert service.lifecycle_state == HostingLifecycleState.ACTIVE
        stored = db.get(HostingCommandOutcome, outcome.outcome_id)
        assert stored is not None and stored.outcome_kind == "refused"
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "hosting.suspension.decided"
            )
        )
        assert audit is not None
        assert audit.details["disposition"] == "refused"


def test_reason_scoped_restoration_names_remaining_blockers(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        for reason, owner in (("delinquency", "collections"), ("abuse", "abuse")):
            result = apply_suspension_request(
                db,
                tenant_id=tenant,
                command=SuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code=reason,
                    source_owner=owner,
                    source_reference=f"case:{reason}",
                    requested_at=NOW + timedelta(minutes=1),
                ),
                actor=_actor(),
                idempotency_key=f"suspend:{reason}",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            assert result.disposition is ConsequenceDisposition.DEFERRED
        suspended_fact = _observation(
            event="provider-event-suspended",
            kind="suspended",
            observed_at=NOW + timedelta(minutes=1, seconds=30),
        )
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "suspension-confirmed"),
            hosting_service_id=receipt.hosting_service_id,
            observation=suspended_fact,
            received_at=NOW + timedelta(minutes=1, seconds=31),
        )
        confirmed = reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(minutes=1, seconds=32),
        )
        assert confirmed.current_state is HostingLifecycleState.SUSPENDED
        suspension_commands = list(
            db.scalars(
                select(HostingCommand).where(
                    HostingCommand.idempotency_scope == "hosting.suspension"
                )
            )
        )
        assert len(suspension_commands) == 2
        for pending_command in suspension_commands:
            assert sorted(
                db.scalars(
                    select(HostingCommandOutcome.outcome_kind)
                    .where(
                        HostingCommandOutcome.hosting_command_id
                        == pending_command.id
                    )
                )
            ) == ["applied", "deferred"]
        outcome_count = db.scalar(
            select(func.count()).select_from(HostingCommandOutcome)
        )
        late_duplicate = receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "suspension-confirmed-late-duplicate"),
            hosting_service_id=receipt.hosting_service_id,
            observation=suspended_fact,
            received_at=NOW + timedelta(minutes=1, seconds=33),
        )
        assert late_duplicate.duplicate
        duplicate_reconcile = reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(minutes=1, seconds=34),
        )
        assert not duplicate_reconcile.changed
        assert (
            db.scalar(select(func.count()).select_from(HostingCommandOutcome))
            == outcome_count
        )
        one = restore_suspension(
            db,
            tenant_id=tenant,
            command=RestoreSuspensionRequest(
                hosting_service_id=receipt.hosting_service_id,
                reason_code="delinquency",
                restorer_code="collections.payment_satisfied",
                requested_at=NOW + timedelta(minutes=2),
            ),
            actor=_actor(),
            idempotency_key="restore:delinquency",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert one.disposition is ConsequenceDisposition.APPLIED
        assert one.remaining_blockers == ("abuse",)
        refused = restore_suspension(
                db,
                tenant_id=tenant,
                command=RestoreSuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code="abuse",
                    restorer_code="collections.payment_satisfied",
                    requested_at=NOW + timedelta(minutes=3),
                ),
                actor=_actor(),
                idempotency_key="restore:wrong-owner",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
        assert refused.disposition is ConsequenceDisposition.REFUSED
        assert refused.reason_code == "restorer_not_permitted"
        two = restore_suspension(
            db,
            tenant_id=tenant,
            command=RestoreSuspensionRequest(
                hosting_service_id=receipt.hosting_service_id,
                reason_code="abuse",
                restorer_code="abuse.cleared",
                requested_at=NOW + timedelta(minutes=4),
            ),
            actor=_actor(),
            idempotency_key="restore:abuse",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert two.disposition is ConsequenceDisposition.DEFERRED
        assert two.remaining_blockers == ()
        assert two.lifecycle_state is HostingLifecycleState.RESTORATION_REQUESTED
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "late-suspended"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(
                event="provider-event-late-suspended",
                kind="suspended",
                observed_at=NOW + timedelta(minutes=4, seconds=30),
            ),
            received_at=NOW + timedelta(minutes=4, seconds=31),
        )
        late = reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(minutes=4, seconds=32),
        )
        assert late.current_state is HostingLifecycleState.RESTORATION_REQUESTED
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "restoration-confirmed"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(
                event="provider-event-active-again",
                kind="active",
                observed_at=NOW + timedelta(minutes=5),
            ),
            received_at=NOW + timedelta(minutes=5, seconds=1),
        )
        restored = reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(minutes=5, seconds=2),
        )
        assert restored.current_state is HostingLifecycleState.ACTIVE
        restoration_command = db.scalar(
            select(HostingCommand).where(
                HostingCommand.idempotency_scope == "hosting.restoration",
                HostingCommand.id == two.command_id,
            )
        )
        assert restoration_command is not None
        assert sorted(
            db.scalars(
                select(HostingCommandOutcome.outcome_kind).where(
                    HostingCommandOutcome.hosting_command_id
                    == restoration_command.id
                )
            )
        ) == ["applied", "deferred"]


def test_inverse_suspension_commands_finalize_the_prior_deferred_consequence(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        actor = _actor()
        suspended = apply_suspension_request(
            db,
            tenant_id=tenant,
            command=SuspensionRequest(
                hosting_service_id=receipt.hosting_service_id,
                reason_code="abuse",
                source_owner="abuse",
                source_reference="case:inverse",
                requested_at=NOW + timedelta(minutes=1),
            ),
            actor=actor,
            idempotency_key="suspend:inverse:first",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        restored = restore_suspension(
            db,
            tenant_id=tenant,
            command=RestoreSuspensionRequest(
                hosting_service_id=receipt.hosting_service_id,
                reason_code="abuse",
                restorer_code="abuse.cleared",
                requested_at=NOW + timedelta(minutes=2),
            ),
            actor=actor,
            idempotency_key="restore:inverse",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert restored.disposition is ConsequenceDisposition.DEFERRED
        assert set(
            db.scalars(
                select(HostingCommandOutcome.outcome_kind).where(
                    HostingCommandOutcome.hosting_command_id == suspended.command_id
                )
            )
        ) == {"deferred", "superseded"}

        suspended_again = apply_suspension_request(
            db,
            tenant_id=tenant,
            command=SuspensionRequest(
                hosting_service_id=receipt.hosting_service_id,
                reason_code="abuse",
                source_owner="abuse",
                source_reference="case:inverse-again",
                requested_at=NOW + timedelta(minutes=3),
            ),
            actor=actor,
            idempotency_key="suspend:inverse:second",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert suspended_again.disposition is ConsequenceDisposition.DEFERRED
        assert set(
            db.scalars(
                select(HostingCommandOutcome.outcome_kind).where(
                    HostingCommandOutcome.hosting_command_id == restored.command_id
                )
            )
        ) == {"deferred", "superseded"}


def test_suspension_lock_freezes_allowed_restorers_at_open_time(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    previous = active_hosting_vocabulary()
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        pending = apply_suspension_request(
            db,
            tenant_id=tenant,
            command=SuspensionRequest(
                hosting_service_id=receipt.hosting_service_id,
                reason_code="abuse",
                source_owner="abuse",
                source_reference="case:snapshot",
                requested_at=NOW + timedelta(minutes=1),
            ),
            actor=_actor(),
            idempotency_key="suspend:snapshot",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert pending.disposition is ConsequenceDisposition.DEFERRED
        lock = db.scalar(select(HostingSuspensionLock))
        assert lock is not None
        assert lock.allowed_restorer_codes == ["abuse.cleared"]
        install_hosting_vocabulary(
            previous.extended(
                suspension_restorers={"abuse": ("abuse.new_clearer",)}
            )
        )
        try:
            restored = restore_suspension(
                db,
                tenant_id=tenant,
                command=RestoreSuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code="abuse",
                    restorer_code="abuse.cleared",
                    requested_at=NOW + timedelta(minutes=2),
                ),
                actor=_actor(),
                idempotency_key="restore:snapshot",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            assert restored.disposition is ConsequenceDisposition.DEFERRED
        finally:
            install_hosting_vocabulary(previous)


def test_approved_termination_revalidates_a_later_retention_hold(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        approved = _approve_termination(
            db,
            tenant,
            service_id=receipt.hosting_service_id,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(hours=1),
            request_id="approval:termination:1",
        )
        place_retention_hold(
            db,
            tenant_id=tenant,
            command=RetentionHoldRequest(
                hosting_service_id=receipt.hosting_service_id,
                hold_code="legal_preservation",
                source_owner="support",
                source_reference="case:late-hold",
                reason_code="legal_hold",
                requested_at=NOW + timedelta(minutes=45),
            ),
            actor=_actor(),
            idempotency_key="hold:late",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        refused = request_termination(
            db,
            tenant_id=tenant,
            command=approved,
            actor=_actor(),
            idempotency_key="terminate:blocked",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        replay = request_termination(
            db,
            tenant_id=tenant,
            command=approved,
            actor=_actor(),
            idempotency_key="terminate:blocked",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert refused.disposition is ConsequenceDisposition.REFUSED
        assert refused.reason_code == "retention_hold_active"
        assert replay.replayed and replay.outcome_id == refused.outcome_id
        stored = db.get(HostingCommandOutcome, refused.outcome_id)
        assert stored is not None
        assert (stored.outcome_kind, stored.outcome_class) == (
            OutcomeKind.REFUSED.value,
            OutcomeClass.TERMINAL.value,
        )


def test_specification_and_observation_evidence_is_structurally_immutable(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        observation = db.scalar(select(HostingObservation))
        specification = db.scalar(select(HostingSpecificationVersion))
        assert observation is not None and specification is not None
        db.commit()
        for statement in (
            "UPDATE mod_hosting.hosting_specification_versions SET package_ref='changed'",
            "DELETE FROM mod_hosting.hosting_observations",
        ):
            with pytest.raises(DBAPIError):
                db.execute(text(statement))
                db.flush()
            db.rollback()
            db.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant)},
            )
        assert db.get(HostingService, receipt.hosting_service_id) is not None
    with _session(scratch["admin"], tenant) as db:
        for statement in (
            "UPDATE mod_hosting.hosting_specification_versions SET package_ref='admin-changed'",
            "DELETE FROM mod_hosting.hosting_observations",
        ):
            with pytest.raises(DBAPIError) as mutation:
                db.execute(text(statement))
                db.flush()
            assert getattr(mutation.value.orig, "sqlstate", None) == "23001"
            db.rollback()
            db.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant)},
            )


def test_raw_sql_cannot_forge_specification_chain_or_owner_references(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _provision(db, tenant)
        specification = db.scalar(select(HostingSpecificationVersion).limit(1))
        assert specification is not None
        specification_id = specification.specification_id
        previous_digest = specification.content_digest
        db.commit()

        with pytest.raises(IntegrityError) as rules_error:
            db.execute(
                text(
                    "INSERT INTO mod_hosting.hosting_specification_versions "
                    "(id, tenant_id, specification_id, specification_code, version, "
                    "package_ref, package_rank, allowances, included_artifacts, capability_codes, "
                    "change_rules, content_digest, previous_version, "
                    "previous_content_digest, published_at) VALUES "
                    "(:id, :tenant, :specification, 'business-hosting', 2, "
                    "'business-hosting:v2', 1, '[]'::jsonb, '[]'::jsonb, "
                    "'[]'::jsonb, '{}'::jsonb, :digest, 1, :previous, :now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "specification": specification_id,
                    "digest": "2" * 64,
                    "previous": previous_digest,
                    "now": NOW,
                },
            )
        assert rules_error.value.orig.diag.constraint_name == (
            "ck_hosting_specification_versions_change_rules_shape"
        )

        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )

        with pytest.raises(IntegrityError) as chain_error:
            db.execute(
                text(
                    "INSERT INTO mod_hosting.hosting_specification_versions "
                    "(id, tenant_id, specification_id, specification_code, version, "
                    "package_ref, package_rank, allowances, included_artifacts, capability_codes, "
                    "change_rules, content_digest, previous_version, "
                    "previous_content_digest, published_at) VALUES "
                    "(:id, :tenant, :specification, 'business-hosting', 3, "
                    "'business-hosting:v3', 1, '[]'::jsonb, '[]'::jsonb, "
                    "'[]'::jsonb, "
                    "'{\"upgrade_allowed\":true,\"downgrade_allowed\":false,"
                    "\"downgrade_requires_review\":true,\"same_level_allowed\":true}'::jsonb, "
                    ":digest, 2, :previous, :now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "specification": specification_id,
                    "digest": "3" * 64,
                    "previous": "2" * 64,
                    "now": NOW,
                },
            )
        assert chain_error.value.orig.diag.constraint_name == (
            "fk_hosting_specification_versions_previous"
        )

        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(IntegrityError) as service_error:
            db.execute(
                text(
                    "INSERT INTO mod_hosting.hosting_services "
                    "(id, tenant_id, customer_ref, order_line_ref, offer_version_ref, "
                    "specification_code, specification_version, primary_domain, "
                    "account_label, administrative_email, country_code, "
                    "lifecycle_state, state_effective_at, row_version, created_at, "
                    "updated_at) VALUES (:id, :tenant, 'customer:raw', "
                    "'order-line:raw', 'offer:raw', 'missing', 999, 'raw.ng', "
                    "'Raw', 'raw@raw.ng', 'NG', 'provisioning', :now, 0, :now, :now)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant, "now": NOW},
            )
        assert service_error.value.orig.diag.constraint_name == (
            "fk_hosting_services_specification_version"
        )

        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(IntegrityError) as desired_error:
            db.execute(
                text(
                    "INSERT INTO mod_hosting.hosting_desired_revisions "
                    "(id, tenant_id, hosting_service_id, version, desired_account_state, "
                    "specification_code, specification_version, package_ref, "
                    "content_digest, requested_at) VALUES "
                    "(:id, :tenant, :service, 99, 'active', 'missing', 999, "
                    "'missing:v999', :digest, :now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "service": receipt.hosting_service_id,
                    "digest": "9" * 64,
                    "now": NOW,
                },
            )
        assert desired_error.value.orig.diag.constraint_name == (
            "fk_hosting_desired_revisions_specification_version"
        )


def test_online_role_cannot_delete_hosting_aggregate(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _provision(db, tenant)
        db.commit()
        with pytest.raises(DBAPIError) as deletion:
            db.execute(
                text("DELETE FROM mod_hosting.hosting_services WHERE id = :id"),
                {"id": receipt.hosting_service_id},
            )
        assert getattr(deletion.value.orig, "sqlstate", None) == "42501"


def test_raw_sql_cannot_forge_hosting_service_identity_or_transition(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        _publish(db, tenant, version=2)
        db.commit()
        with pytest.raises(DBAPIError) as identity:
            db.execute(
                text(
                    "UPDATE mod_hosting.hosting_services "
                    "SET customer_ref='forged' WHERE id=:service"
                ),
                {"service": receipt.hosting_service_id},
            )
        assert getattr(identity.value.orig, "sqlstate", None) == "42501"
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError) as lifecycle:
            db.execute(
                text(
                    "UPDATE mod_hosting.hosting_services SET "
                    "lifecycle_state='suspension_requested', "
                    "state_effective_at=:now, row_version=row_version+1, updated_at=:now "
                    "WHERE id=:service"
                ),
                {"service": receipt.hosting_service_id, "now": NOW + timedelta(hours=1)},
            )
        assert getattr(lifecycle.value.orig, "sqlstate", None) == "42501"

    with _session(scratch["admin"], tenant) as db:
        with pytest.raises(DBAPIError) as identity:
            db.execute(
                text(
                    "UPDATE mod_hosting.hosting_services "
                    "SET customer_ref='admin-forged', row_version=row_version+1 "
                    "WHERE id=:service"
                ),
                {"service": receipt.hosting_service_id},
            )
        assert getattr(identity.value.orig, "sqlstate", None) == "23001"
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError) as rebind:
            db.execute(
                text(
                    "UPDATE mod_hosting.hosting_services SET "
                    "capability_binding_ref='forged-binding', provider_account_ref='forged-account', "
                    "row_version=row_version+1, updated_at=:now WHERE id=:service"
                ),
                {"service": receipt.hosting_service_id, "now": NOW + timedelta(hours=1)},
            )
        assert getattr(rebind.value.orig, "sqlstate", None) == "23001"
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError) as version:
            db.execute(
                text(
                    "UPDATE mod_hosting.hosting_services SET lifecycle_state='suspension_requested', "
                    "state_effective_at=:now, updated_at=:now WHERE id=:service"
                ),
                {"service": receipt.hosting_service_id, "now": NOW + timedelta(hours=1)},
            )
        assert getattr(version.value.orig, "sqlstate", None) == "23001"
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError) as combined:
            db.execute(
                text(
                    "UPDATE mod_hosting.hosting_services SET "
                    "specification_version=2, lifecycle_state='suspension_requested', "
                    "state_effective_at=:now, row_version=row_version+1, updated_at=:now "
                    "WHERE id=:service"
                ),
                {
                    "service": receipt.hosting_service_id,
                    "now": NOW + timedelta(hours=1),
                },
            )
        assert getattr(combined.value.orig, "sqlstate", None) == "23001"
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError) as deletion:
            db.execute(
                text("DELETE FROM mod_hosting.hosting_services WHERE id=:service"),
                {"service": receipt.hosting_service_id},
            )
        assert getattr(deletion.value.orig, "sqlstate", None) == "23001"


def test_database_mutation_seam_enforces_version_and_tenant_authority(
    scratch: dict[str, str],
) -> None:
    tenant, other = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _provision(db, tenant)
        observation_receipt = receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "mutation-seam"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(
                event="mutation-seam",
                operation_reference=str(receipt.command_id),
            ),
            received_at=NOW + timedelta(seconds=2),
        )
        parameters = {
            "tenant_id": tenant,
            "service_id": receipt.hosting_service_id,
            "expected_version": 0,
            "mutation_kind": "provider_correlation",
            "updated_at": NOW + timedelta(seconds=3),
            "specification_code": None,
            "specification_version": None,
            "lifecycle_state": None,
            "state_effective_at": None,
            "observation_id": observation_receipt.observation_id,
        }
        assert db.scalar(text(MUTATE_SERVICE_SQL), parameters) == 1
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        db.refresh(service)
        assert (service.row_version, service.capability_binding_ref) == (
            1,
            "hosting-binding-1",
        )
        db.commit()
        with pytest.raises(DBAPIError) as stale:
            db.scalar(text(MUTATE_SERVICE_SQL), parameters)
        assert getattr(stale.value.orig, "sqlstate", None) == "40001"
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant)},
        )

    with _session(scratch["tenant"], other) as db:
        with pytest.raises(DBAPIError) as crossed:
            db.scalar(
                text(MUTATE_SERVICE_SQL),
                {
                    **parameters,
                    "expected_version": 1,
                    "updated_at": NOW + timedelta(seconds=4),
                },
            )
        assert getattr(crossed.value.orig, "sqlstate", None) == "42501"


def test_termination_approval_ingress_accepts_only_final_exact_evidence(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        request_id = uuid.uuid4()
        pending = TerminationApprovalObservationV1(
            event_type="approval.requested",
            request_id=request_id,
            subject_type="hosting_service",
            subject_id=str(service.id),
            policy_code="hosting.termination.v1",
            policy_version=1,
            content_digest=termination_content_digest(
                tenant,
                service.id,
                service.row_version,
                NOW + timedelta(hours=1),
            ),
            state=ApprovalObservationState.PENDING,
        )
        with pytest.raises(ApprovalRequired, match="only final"):
            receive_termination_approval(
                db,
                envelope=CommandEnvelope(
                    command_id="approval-event:pending",
                    command_type="approval.requested",
                    tenant_id=tenant,
                    correlation_id="approval:pending",
                    issued_at=NOW,
                ),
                source_event_id=uuid.uuid4(),
                observation=pending,
                received_at=NOW,
            )
        assert db.scalar(
            select(func.count()).select_from(
                HostingTerminationApprovalEvidence
            )
        ) == 0
        with pytest.raises(ApprovalRequired, match="was not received"):
            request_termination(
                db,
                tenant_id=tenant,
                command=RequestTermination(
                    hosting_service_id=service.id,
                    expected_version=service.row_version,
                    requested_at=NOW + timedelta(hours=1),
                    approval_request_id=uuid.uuid4(),
                ),
                actor=_actor(),
                idempotency_key="termination:missing-approval-evidence",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )


def test_termination_approval_source_identity_conflicts_on_changed_evidence(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        requested_at = NOW + timedelta(hours=1)
        request_id = uuid.uuid4()
        source_event_id = uuid.uuid4()
        observation = TerminationApprovalObservationV1(
            event_type="approval.approved",
            request_id=request_id,
            subject_type="hosting_service",
            subject_id=str(service.id),
            policy_code="hosting.termination.v1",
            policy_version=1,
            content_digest=termination_content_digest(
                tenant, service.id, service.row_version, requested_at
            ),
            state=ApprovalObservationState.APPROVED,
        )
        receive_termination_approval(
            db,
            envelope=CommandEnvelope(
                command_id="approval-event:source-identity:first",
                command_type="approval.approved",
                tenant_id=tenant,
                correlation_id="approval-source-identity",
                issued_at=NOW,
            ),
            source_event_id=source_event_id,
            observation=observation,
            received_at=NOW,
        )
        with pytest.raises(HostingError, match="source event identity was reused"):
            receive_termination_approval(
                db,
                envelope=CommandEnvelope(
                    command_id="approval-event:source-identity:changed",
                    command_type="approval.approved",
                    tenant_id=tenant,
                    correlation_id="approval-source-identity",
                    issued_at=NOW,
                ),
                source_event_id=source_event_id,
                observation=TerminationApprovalObservationV1(
                    event_type="approval.approved",
                    request_id=uuid.uuid4(),
                    subject_type="hosting_service",
                    subject_id=str(service.id),
                    policy_code="hosting.termination.v1",
                    policy_version=1,
                    content_digest=observation.content_digest,
                    state=ApprovalObservationState.APPROVED,
                ),
                received_at=NOW,
            )
        assert db.scalar(
            select(func.count()).select_from(HostingTerminationApprovalEvidence)
        ) == 1


def test_idempotent_package_change_replays_and_conflicts(scratch: dict[str, str]) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        _publish(db, tenant, version=2)
        command = ChangeHostingPackage(
            hosting_service_id=receipt.hosting_service_id,
            specification_code="business-hosting",
            specification_version=2,
            requested_at=NOW + timedelta(hours=1),
        )
        actor = _actor()
        first = request_package_change(
            db,
            tenant_id=tenant,
            command=command,
            actor=actor,
            idempotency_key="package-change",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        second = request_package_change(
            db,
            tenant_id=tenant,
            command=command,
            actor=actor,
            idempotency_key="package-change",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert first.command_id == second.command_id
        assert second.replayed
        with pytest.raises(IdempotencyConflict):
            request_package_change(
                db,
                tenant_id=tenant,
                command=ChangeHostingPackage(
                    hosting_service_id=receipt.hosting_service_id,
                    specification_code="business-hosting",
                    specification_version=1,
                    requested_at=NOW + timedelta(hours=1),
                ),
                actor=actor,
                idempotency_key="package-change",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
        assert (
            db.scalar(select(func.count()).select_from(HostingDesiredRevision)) == 2
        )


def test_package_direction_is_derived_from_rank_and_current_rules(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        premium = publish_specification_version(
            db,
            tenant_id=tenant,
            command=PublishHostingSpecificationVersion(
                specification_code="premium-hosting",
                package_ref="premium-hosting:v1",
                package_rank=2,
                allowances=(
                    HostingAllowance(
                        resource_kind="disk_bytes",
                        quantity=Decimal(100 * 1024**3),
                        unit="bytes",
                    ),
                ),
                included_artifacts=("tls", "backup"),
                capability_codes=("php", "database"),
                change_rules=HostingChangeRules(
                    upgrade_allowed=True,
                    downgrade_allowed=True,
                    downgrade_requires_review=True,
                    same_level_allowed=True,
                ),
                published_at=NOW,
            ),
            idempotency_key="spec:premium:1",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        upgraded = request_package_change(
            db,
            tenant_id=tenant,
            command=ChangeHostingPackage(
                hosting_service_id=receipt.hosting_service_id,
                specification_code="premium-hosting",
                specification_version=premium.assigned_version,
                requested_at=NOW + timedelta(hours=1),
            ),
            actor=_actor(),
            idempotency_key="package:upgrade",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert upgraded.direction == "upgrade"
        assert upgraded.disposition is ConsequenceDisposition.DEFERRED
        desired_count = db.scalar(
            select(func.count()).select_from(HostingDesiredRevision)
        )
        refused = request_package_change(
            db,
            tenant_id=tenant,
            command=ChangeHostingPackage(
                hosting_service_id=receipt.hosting_service_id,
                specification_code="business-hosting",
                specification_version=1,
                requested_at=NOW + timedelta(hours=2),
            ),
            actor=_actor(),
            idempotency_key="package:downgrade",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert refused.direction == "downgrade"
        assert refused.disposition is ConsequenceDisposition.REFUSED
        assert refused.reason_code == "manual_required"
        assert (
            db.scalar(select(func.count()).select_from(HostingDesiredRevision))
            == desired_count
        )


def test_package_observation_applies_latest_and_supersedes_older_deferred_change(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        actor = _actor()
        _publish(db, tenant, version=2)
        first = request_package_change(
            db,
            tenant_id=tenant,
            command=ChangeHostingPackage(
                hosting_service_id=receipt.hosting_service_id,
                specification_code="business-hosting",
                specification_version=2,
                requested_at=NOW + timedelta(minutes=10),
            ),
            actor=actor,
            idempotency_key="package:confirmation:v2",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        _publish(db, tenant, version=3)
        second = request_package_change(
            db,
            tenant_id=tenant,
            command=ChangeHostingPackage(
                hosting_service_id=receipt.hosting_service_id,
                specification_code="business-hosting",
                specification_version=3,
                requested_at=NOW + timedelta(minutes=20),
            ),
            actor=actor,
            idempotency_key="package:confirmation:v3",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "package-v3"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(
                event="package-v3",
                operation_reference=str(second.command_id),
                observed_at=NOW + timedelta(minutes=21),
                package_ref="business-hosting:v3",
            ),
            received_at=NOW + timedelta(minutes=22),
        )
        reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(minutes=23),
        )
        outcomes = {
            command_id: set(
                db.scalars(
                    select(HostingCommandOutcome.outcome_kind).where(
                        HostingCommandOutcome.hosting_command_id == command_id
                    )
                )
            )
            for command_id in (first.command_id, second.command_id)
        }
        assert outcomes[first.command_id] == {"deferred", "superseded"}
        assert outcomes[second.command_id] == {"deferred", "applied"}
        counts = {
            command_id: len(kinds) for command_id, kinds in outcomes.items()
        }
        reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(minutes=24),
        )
        assert {
            command_id: db.scalar(
                select(func.count())
                .select_from(HostingCommandOutcome)
                .where(HostingCommandOutcome.hosting_command_id == command_id)
            )
            for command_id in (first.command_id, second.command_id)
        } == counts

        _publish(db, tenant, version=4)
        failed = request_package_change(
            db,
            tenant_id=tenant,
            command=ChangeHostingPackage(
                hosting_service_id=receipt.hosting_service_id,
                specification_code="business-hosting",
                specification_version=4,
                requested_at=NOW + timedelta(minutes=25),
            ),
            actor=actor,
            idempotency_key="package:confirmation:v4",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        record_hosting_outcome(
            db,
            envelope=_envelope(tenant, "package-v4-failed"),
            outcome=RecordHostingOutcome(
                hosting_command_id=failed.command_id,
                evidence_key="provider-failure:v4",
                outcome_kind=OutcomeKind.FAILED,
                outcome_class=OutcomeClass.TERMINAL,
                occurred_at=NOW + timedelta(minutes=26),
                reason_code="package_not_available",
            ),
            received_at=NOW + timedelta(minutes=27),
        )
        assert set(
            db.scalars(
                select(HostingCommandOutcome.outcome_kind).where(
                    HostingCommandOutcome.hosting_command_id == failed.command_id
                )
            )
        ) == {"deferred", "failed"}


def test_retention_hold_place_and_clear_replay_then_conflict(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        actor = _actor()
        place = RetentionHoldRequest(
            hosting_service_id=receipt.hosting_service_id,
            hold_code="legal_preservation",
            source_owner="support",
            source_reference="case:retention",
            reason_code="legal_hold",
            requested_at=NOW + timedelta(minutes=10),
        )
        first = place_retention_hold(
            db,
            tenant_id=tenant,
            command=place,
            actor=actor,
            idempotency_key="hold:replay",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        second = place_retention_hold(
            db,
            tenant_id=tenant,
            command=place,
            actor=actor,
            idempotency_key="hold:replay",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert second.retention_hold_id == first.retention_hold_id
        assert second.replayed
        with pytest.raises(IdempotencyConflict):
            place_retention_hold(
                db,
                tenant_id=tenant,
                command=RetentionHoldRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    hold_code="legal_preservation",
                    source_owner="support",
                    source_reference="case:retention",
                    reason_code="different_reason",
                    requested_at=NOW + timedelta(minutes=10),
                ),
                actor=actor,
                idempotency_key="hold:replay",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )

        clear = ClearRetentionHold(
            hosting_service_id=receipt.hosting_service_id,
            hold_code="legal_preservation",
            source_owner="support",
            source_reference="case:retention",
            reason_code="case_closed",
            requested_at=NOW + timedelta(minutes=20),
        )
        wrong_source = clear_retention_hold(
            db,
            tenant_id=tenant,
            command=ClearRetentionHold(
                hosting_service_id=receipt.hosting_service_id,
                hold_code="legal_preservation",
                source_owner="sales",
                source_reference="case:retention",
                reason_code="case_closed",
                requested_at=NOW + timedelta(minutes=19),
            ),
            actor=actor,
            idempotency_key="hold-clear:wrong-source",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert wrong_source.disposition is ConsequenceDisposition.REFUSED
        assert wrong_source.reason_code == "retention_hold_source_not_authorized"
        cleared = clear_retention_hold(
            db,
            tenant_id=tenant,
            command=clear,
            actor=actor,
            idempotency_key="hold-clear:replay",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        replayed = clear_retention_hold(
            db,
            tenant_id=tenant,
            command=clear,
            actor=actor,
            idempotency_key="hold-clear:replay",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert replayed.retention_hold_id == cleared.retention_hold_id
        assert replayed.replayed
        missing = clear_retention_hold(
            db,
            tenant_id=tenant,
            command=ClearRetentionHold(
                hosting_service_id=receipt.hosting_service_id,
                hold_code="legal_preservation",
                source_owner="support",
                source_reference="case:retention",
                reason_code="already_closed",
                requested_at=NOW + timedelta(minutes=21),
            ),
            actor=actor,
            idempotency_key="hold-clear:not-active",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert missing.disposition is ConsequenceDisposition.REFUSED
        assert missing.reason_code == "retention_hold_not_active"
        refusal_audits = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "hosting.retention_hold.changed"
                )
            )
        )
        assert {
            row.details.get("reason_code")
            for row in refusal_audits
            if row.details.get("transition") == "clear_refused"
        } == {
            "retention_hold_source_not_authorized",
            "retention_hold_not_active",
        }
        with pytest.raises(IdempotencyConflict):
            clear_retention_hold(
                db,
                tenant_id=tenant,
                command=ClearRetentionHold(
                    hosting_service_id=receipt.hosting_service_id,
                    hold_code="legal_preservation",
                    source_owner="support",
                    source_reference="case:retention",
                    reason_code="different_clear_reason",
                    requested_at=NOW + timedelta(minutes=20),
                ),
                actor=actor,
                idempotency_key="hold-clear:replay",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )


def test_termination_replays_after_mutation_and_changed_command_conflicts(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        provisioned = _activate(db, tenant)
        service = db.get(HostingService, provisioned.hosting_service_id)
        assert service is not None
        command = _approve_termination(
            db,
            tenant,
            service_id=service.id,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(hours=1),
            request_id="approval:termination:replay",
        )
        actor = _actor()
        first = request_termination(
            db,
            tenant_id=tenant,
            command=command,
            actor=actor,
            idempotency_key="termination:replay",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        second = request_termination(
            db,
            tenant_id=tenant,
            command=command,
            actor=actor,
            idempotency_key="termination:replay",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert second.command_id == first.command_id
        assert second.replayed

        changed = _approve_termination(
            db,
            tenant,
            service_id=service.id,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(hours=2),
            request_id="approval:termination:changed",
        )
        with pytest.raises(IdempotencyConflict):
            request_termination(
                db,
                tenant_id=tenant,
                command=changed,
                actor=actor,
                idempotency_key="termination:replay",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )


def test_termination_is_deferred_until_independent_observation_and_replay_is_stable(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        provisioned = _activate(db, tenant)
        service = db.get(HostingService, provisioned.hosting_service_id)
        assert service is not None
        command = _approve_termination(
            db,
            tenant,
            service_id=service.id,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(hours=1),
            request_id="approval:termination:confirmation",
        )
        actor = _actor()
        pending = request_termination(
            db,
            tenant_id=tenant,
            command=command,
            actor=actor,
            idempotency_key="termination:confirmation",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert pending.disposition is ConsequenceDisposition.DEFERRED
        assert pending.lifecycle_state is HostingLifecycleState.TERMINATING
        outbound = db.get(HostingCommand, pending.command_id)
        assert outbound is not None
        assert set(outbound.payload) == {"operation_reference", "account_ref"}

        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "terminated"),
            hosting_service_id=None,
            observation=_observation(
                event="terminated",
                kind="terminated",
                observed_at=NOW + timedelta(hours=1, minutes=1),
                source_mode="poll",
            ),
            received_at=NOW + timedelta(hours=1, minutes=2),
        )
        reconciled = reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=service.id,
            reconciled_at=NOW + timedelta(hours=1, minutes=3),
        )
        assert reconciled.current_state is HostingLifecycleState.TERMINATED
        assert db.scalar(
            select(func.count())
            .select_from(HostingCommandOutcome)
            .where(
                HostingCommandOutcome.hosting_command_id == pending.command_id,
                HostingCommandOutcome.outcome_kind == "applied",
            )
        ) == 1
        replay = request_termination(
            db,
            tenant_id=tenant,
            command=command,
            actor=actor,
            idempotency_key="termination:confirmation",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert replay.replayed
        assert replay.lifecycle_state is HostingLifecycleState.TERMINATING
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "hosting.termination.requested"
            )
        )
        assert audit is not None
        assert audit.details["disposition"] == "deferred"


def test_termination_first_refuses_late_hold_with_durable_attention(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        provisioned = _activate(db, tenant)
        service = db.get(HostingService, provisioned.hosting_service_id)
        assert service is not None
        termination = _approve_termination(
            db,
            tenant,
            service_id=service.id,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(hours=1),
            request_id="approval:termination:hold-race",
        )
        request_termination(
            db,
            tenant_id=tenant,
            command=termination,
            actor=_actor(),
            idempotency_key="termination:hold-race",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        hold = place_retention_hold(
            db,
            tenant_id=tenant,
            command=RetentionHoldRequest(
                hosting_service_id=service.id,
                hold_code="legal_preservation",
                source_owner="support",
                source_reference="case:too-late",
                reason_code="legal_hold",
                requested_at=NOW + timedelta(hours=1, minutes=1),
            ),
            actor=_actor(),
            idempotency_key="hold:after-termination",
            idempotency_expires_at=IDEMPOTENCY_EXPIRY,
        )
        assert hold.disposition is ConsequenceDisposition.REFUSED
        assert hold.reason_code == "termination_in_flight_manual_required"
        assert hold.retention_hold_id is None
        assert db.scalar(
            select(func.count())
            .select_from(HostingRetentionHold)
            .where(HostingRetentionHold.cleared_at.is_(None))
        ) == 0
        attention = db.scalar(
            select(HostingAttentionCondition).where(
                HostingAttentionCondition.condition_code
                == "retention_hold_after_termination"
            )
        )
        assert attention is not None
        assert attention.classification == "urgent_manual_required"


def test_retention_hold_and_termination_race_has_only_a_serial_result(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        provisioned = _activate(db, tenant)
        service = db.get(HostingService, provisioned.hosting_service_id)
        assert service is not None
        expected_version = service.row_version
        termination = _approve_termination(
            db,
            tenant,
            service_id=service.id,
            expected_version=expected_version,
            requested_at=NOW + timedelta(hours=1),
            request_id="approval:termination:concurrent-hold",
        )
        db.commit()

    barrier = Barrier(2)

    def terminate() -> str:
        with _session(scratch["tenant"], tenant) as db:
            barrier.wait(timeout=5)
            result = request_termination(
                db,
                tenant_id=tenant,
                command=termination,
                actor=_actor(),
                idempotency_key="termination:concurrent-hold",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            db.commit()
            return result.disposition.value

    def hold() -> str:
        with _session(scratch["tenant"], tenant) as db:
            barrier.wait(timeout=5)
            result = place_retention_hold(
                db,
                tenant_id=tenant,
                command=RetentionHoldRequest(
                    hosting_service_id=provisioned.hosting_service_id,
                    hold_code="legal_preservation",
                    source_owner="support",
                    source_reference="case:concurrent",
                    reason_code="legal_hold",
                    requested_at=NOW + timedelta(hours=1),
                ),
                actor=_actor(),
                idempotency_key="hold:concurrent-termination",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            db.commit()
            return result.disposition.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        termination_result = executor.submit(terminate)
        hold_result = executor.submit(hold)
        results = {termination_result.result(), hold_result.result()}
    assert results in ({"deferred", "refused"}, {"refused", "applied"})
    with _session(scratch["tenant"], tenant) as db:
        service = db.get(HostingService, provisioned.hosting_service_id)
        assert service is not None
        active_holds = db.scalar(
            select(func.count())
            .select_from(HostingRetentionHold)
            .where(HostingRetentionHold.cleared_at.is_(None))
        )
        assert (service.lifecycle_state, active_holds) in {
            (HostingLifecycleState.TERMINATING.value, 0),
            (HostingLifecycleState.ACTIVE.value, 1),
        }


def test_concurrent_publication_assigns_one_monotonic_chain(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])

    def publish(index: int) -> tuple[int, int | None, str | None]:
        with _session(scratch["tenant"], tenant) as db:
            receipt = publish_specification_version(
                db,
                tenant_id=tenant,
                command=PublishHostingSpecificationVersion(
                    specification_code="concurrent-hosting",
                    package_ref=f"concurrent-hosting:v{index}",
                    package_rank=1,
                    allowances=(
                        HostingAllowance(
                            resource_kind="disk_bytes",
                            quantity=Decimal(index * 1024),
                            unit="bytes",
                        ),
                    ),
                    included_artifacts=("tls",),
                    capability_codes=("php",),
                    change_rules=HostingChangeRules(
                        upgrade_allowed=True,
                        downgrade_allowed=False,
                        downgrade_requires_review=True,
                        same_level_allowed=True,
                    ),
                    published_at=NOW + timedelta(seconds=index),
                ),
                idempotency_key=f"concurrent-spec:{index}",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            db.commit()
            return (
                receipt.assigned_version,
                receipt.previous_version,
                receipt.previous_content_digest,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(publish, (1, 2)))
    assert [result[0] for result in results] == [1, 2]
    assert results[0][1:] == (None, None)
    assert results[1][1] == 1
    assert results[1][2] is not None


def test_publication_lock_sensitivity_fails_when_guard_is_removed(
    scratch: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import dotmac_hosting.service as hosting_service

    tenant, _ = _seed_tenants(scratch["admin"])
    command = PublishHostingSpecificationVersion(
        specification_code="guarded-hosting",
        package_ref="guarded-hosting:v1",
        package_rank=1,
        allowances=(
            HostingAllowance(
                resource_kind="disk_bytes", quantity=Decimal(1), unit="bytes"
            ),
        ),
        included_artifacts=("tls",),
        capability_codes=("php",),
        change_rules=HostingChangeRules(
            upgrade_allowed=True,
            downgrade_allowed=False,
            downgrade_requires_review=True,
            same_level_allowed=True,
        ),
        published_at=NOW,
    )
    holder = _session(scratch["tenant"], tenant)
    holder.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:identity, 0))"
        ),
        {"identity": f"hosting-specification:{tenant}:guarded-hosting"},
    )
    try:
        with _session(scratch["tenant"], tenant) as contender:
            contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError) as blocked:
                publish_specification_version(
                    contender,
                    tenant_id=tenant,
                    command=command,
                    idempotency_key="guarded:blocked",
                    idempotency_expires_at=IDEMPOTENCY_EXPIRY,
                )
            assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"

        def removed_guard(db: Session, tenant_id: uuid.UUID, code: str) -> None:
            del db, tenant_id, code

        monkeypatch.setattr(
            hosting_service, "_lock_specification_identity", removed_guard
        )
        with _session(scratch["tenant"], tenant) as unguarded:
            receipt = publish_specification_version(
                unguarded,
                tenant_id=tenant,
                command=command,
                idempotency_key="guarded:removed",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            assert receipt.assigned_version == 1
            unguarded.commit()
    finally:
        holder.rollback()
        holder.close()


def test_concurrent_same_reason_suspension_has_one_active_lock(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        db.commit()

    def suspend(index: int) -> bool:
        with _session(scratch["tenant"], tenant) as db:
            result = apply_suspension_request(
                db,
                tenant_id=tenant,
                command=SuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code="abuse",
                    source_owner="abuse",
                    source_reference=f"case:{index}",
                    requested_at=NOW + timedelta(minutes=index + 1),
                ),
                actor=_actor(),
                idempotency_key=f"suspend:race:{index}",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            db.commit()
            return result.disposition is ConsequenceDisposition.DEFERRED

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(suspend, range(2)))
    assert results.count(True) == 1
    with _session(scratch["tenant"], tenant) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(HostingSuspensionLock)
                .where(HostingSuspensionLock.cleared_at.is_(None))
            )
            == 1
        )
        assert db.scalar(select(func.count()).select_from(HostingRetentionHold)) == 0
        assert db.scalar(select(func.count()).select_from(HostingAttentionCondition)) >= 0


def test_concurrent_restorations_clear_each_lock_and_request_provider_once(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        for reason, owner in (("delinquency", "collections"), ("abuse", "abuse")):
            apply_suspension_request(
                db,
                tenant_id=tenant,
                command=SuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code=reason,
                    source_owner=owner,
                    source_reference=f"case:restore-race:{reason}",
                    requested_at=NOW + timedelta(minutes=1),
                ),
                actor=_actor(),
                idempotency_key=f"suspend:restore-race:{reason}",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "restore-race-suspended"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(
                event="restore-race-suspended",
                kind="suspended",
                observed_at=NOW + timedelta(minutes=2),
            ),
            received_at=NOW + timedelta(minutes=2, seconds=1),
        )
        reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(minutes=2, seconds=2),
        )
        db.commit()

    barrier = Barrier(2)

    def restore(reason: str, restorer: str) -> str:
        with _session(scratch["tenant"], tenant) as db:
            barrier.wait(timeout=5)
            result = restore_suspension(
                db,
                tenant_id=tenant,
                command=RestoreSuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code=reason,
                    restorer_code=restorer,
                    requested_at=NOW + timedelta(minutes=3),
                ),
                actor=_actor(),
                idempotency_key=f"restore:race:{reason}",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            db.commit()
            return result.disposition.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            restore, "delinquency", "collections.payment_satisfied"
        )
        second = executor.submit(restore, "abuse", "abuse.cleared")
        assert sorted((first.result(), second.result())) == ["applied", "deferred"]
    with _session(scratch["tenant"], tenant) as db:
        assert db.scalar(
            select(func.count())
            .select_from(HostingSuspensionLock)
            .where(HostingSuspensionLock.cleared_at.is_(None))
        ) == 0
        provider_restores = [
            row
            for row in db.scalars(
                select(HostingCommand).where(
                    HostingCommand.idempotency_scope == "hosting.restoration"
                )
            )
            if row.payload.get("action") == "restore"
        ]
        assert len(provider_restores) == 1


def test_restoration_row_lock_sensitivity_reproduces_missed_provider_restore(
    scratch: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import dotmac_hosting.service as hosting_service

    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        for reason, owner in (("delinquency", "collections"), ("abuse", "abuse")):
            apply_suspension_request(
                db,
                tenant_id=tenant,
                command=SuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code=reason,
                    source_owner=owner,
                    source_reference=f"case:no-lock-restore:{reason}",
                    requested_at=NOW + timedelta(minutes=1),
                ),
                actor=_actor(),
                idempotency_key=f"suspend:no-lock-restore:{reason}",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
        receive_hosting_observation(
            db,
            envelope=_envelope(tenant, "no-lock-restore-suspended"),
            hosting_service_id=receipt.hosting_service_id,
            observation=_observation(
                event="no-lock-restore-suspended",
                kind="suspended",
                observed_at=NOW + timedelta(minutes=2),
            ),
            received_at=NOW + timedelta(minutes=2, seconds=1),
        )
        reconcile_hosting_service(
            db,
            tenant_id=tenant,
            hosting_service_id=receipt.hosting_service_id,
            reconciled_at=NOW + timedelta(minutes=2, seconds=2),
        )
        db.commit()

    original_service = hosting_service._service
    original_active_locks = hosting_service._active_locks
    after_clear = Barrier(2)
    calls = local()

    def removed_service_lock(
        db: Session,
        tenant_id: uuid.UUID,
        service_id: uuid.UUID,
        *,
        lock: bool = False,
    ):
        del lock
        return original_service(db, tenant_id, service_id, lock=False)

    def synchronized_active_locks(
        db: Session, tenant_id: uuid.UUID, service_id: uuid.UUID
    ):
        calls.count = getattr(calls, "count", 0) + 1
        if calls.count == 2:
            after_clear.wait(timeout=5)
        return original_active_locks(db, tenant_id, service_id)

    monkeypatch.setattr(hosting_service, "_service", removed_service_lock)
    monkeypatch.setattr(hosting_service, "_active_locks", synchronized_active_locks)

    def restore(reason: str, restorer: str) -> str:
        with _session(scratch["tenant"], tenant) as db:
            result = restore_suspension(
                db,
                tenant_id=tenant,
                command=RestoreSuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code=reason,
                    restorer_code=restorer,
                    requested_at=NOW + timedelta(minutes=3),
                ),
                actor=_actor(),
                idempotency_key=f"restore:no-lock:{reason}",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            db.commit()
            return result.disposition.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            restore, "delinquency", "collections.payment_satisfied"
        )
        second = executor.submit(restore, "abuse", "abuse.cleared")
        assert sorted((first.result(), second.result())) == ["applied", "applied"]
    with _session(scratch["tenant"], tenant) as db:
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        assert service.lifecycle_state == HostingLifecycleState.SUSPENDED
        assert db.scalar(
            select(func.count())
            .select_from(HostingSuspensionLock)
            .where(HostingSuspensionLock.cleared_at.is_(None))
        ) == 0


def test_suspension_and_package_change_serialize_on_the_service_row(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        _publish(db, tenant, version=2)
        db.commit()
    barrier = Barrier(2)

    def suspend() -> str:
        with _session(scratch["tenant"], tenant) as db:
            barrier.wait(timeout=5)
            result = apply_suspension_request(
                db,
                tenant_id=tenant,
                command=SuspensionRequest(
                    hosting_service_id=receipt.hosting_service_id,
                    reason_code="abuse",
                    source_owner="abuse",
                    source_reference="case:package-race",
                    requested_at=NOW + timedelta(hours=1),
                ),
                actor=_actor(),
                idempotency_key="suspend:package-race",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            db.commit()
            return result.disposition.value

    def change_package() -> str:
        with _session(scratch["tenant"], tenant) as db:
            barrier.wait(timeout=5)
            result = request_package_change(
                db,
                tenant_id=tenant,
                command=ChangeHostingPackage(
                    hosting_service_id=receipt.hosting_service_id,
                    specification_code="business-hosting",
                    specification_version=2,
                    requested_at=NOW + timedelta(hours=1),
                ),
                actor=_actor(),
                idempotency_key="package:suspension-race",
                idempotency_expires_at=IDEMPOTENCY_EXPIRY,
            )
            db.commit()
            return result.disposition.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        suspend_result = executor.submit(suspend)
        package_result = executor.submit(change_package)
        assert suspend_result.result() == "deferred"
        assert package_result.result() in {"deferred", "refused"}
    with _session(scratch["tenant"], tenant) as db:
        service = db.get(HostingService, receipt.hosting_service_id)
        assert service is not None
        assert service.lifecycle_state == HostingLifecycleState.SUSPENSION_REQUESTED
        package_outcome = db.scalar(
            select(HostingCommandOutcome)
            .join(HostingCommand)
            .where(HostingCommand.idempotency_scope == "hosting.package")
        )
        assert package_outcome is not None
        assert (service.specification_version == 2) is (
            package_outcome.outcome_kind == "deferred"
        )


def test_service_row_lock_sensitivity_reproduces_exact_desired_revision_fork(
    scratch: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import dotmac_hosting.service as hosting_service

    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _activate(db, tenant)
        _publish(db, tenant, version=2)
        db.commit()
    barrier = Barrier(2)
    desired_barrier = Barrier(2)
    original_service = hosting_service._service

    def removed_lock(
        db: Session,
        tenant_id: uuid.UUID,
        service_id: uuid.UUID,
        *,
        lock: bool = False,
    ):
        row = original_service(db, tenant_id, service_id, lock=False)
        if lock:
            barrier.wait(timeout=5)
        return row

    monkeypatch.setattr(hosting_service, "_service", removed_lock)

    def synchronized_desired_append(
        db: Session,
        *,
        tenant_id: uuid.UUID,
        service: HostingService,
        desired_account_state: str,
        specification: HostingSpecificationVersion,
        requested_at: datetime,
    ) -> HostingDesiredRevision:
        previous = db.scalar(
            select(func.max(HostingDesiredRevision.version)).where(
                HostingDesiredRevision.tenant_id == tenant_id,
                HostingDesiredRevision.hosting_service_id == service.id,
            )
        )
        version = int(previous or 0) + 1
        desired_barrier.wait(timeout=5)
        content = {
            "desired_account_state": desired_account_state,
            "specification_code": specification.specification_code,
            "specification_version": specification.version,
            "package_ref": specification.package_ref,
        }
        row = HostingDesiredRevision(
            tenant_id=tenant_id,
            hosting_service_id=service.id,
            version=version,
            desired_account_state=desired_account_state,
            specification_code=specification.specification_code,
            specification_version=specification.version,
            package_ref=specification.package_ref,
            content_digest=hosting_service.fingerprint(content),
            requested_at=requested_at,
        )
        db.add(row)
        db.flush()
        return row

    monkeypatch.setattr(
        hosting_service, "_append_desired", synchronized_desired_append
    )

    def run_suspend() -> str:
        with _session(scratch["tenant"], tenant) as db:
            try:
                result = apply_suspension_request(
                    db,
                    tenant_id=tenant,
                    command=SuspensionRequest(
                        hosting_service_id=receipt.hosting_service_id,
                        reason_code="abuse",
                        source_owner="abuse",
                        source_reference="case:no-lock",
                        requested_at=NOW + timedelta(hours=1),
                    ),
                    actor=_actor(),
                    idempotency_key="suspend:no-lock",
                    idempotency_expires_at=IDEMPOTENCY_EXPIRY,
                )
                db.commit()
                return result.disposition.value
            except DBAPIError as exc:
                db.rollback()
                if (
                    getattr(exc.orig, "sqlstate", None) != "23505"
                    or getattr(exc.orig.diag, "constraint_name", None)
                    != "uq_hosting_desired_revisions_service_version"
                ):
                    raise
                return "desired_revision_fork_refused"

    def run_package() -> str:
        with _session(scratch["tenant"], tenant) as db:
            try:
                result = request_package_change(
                    db,
                    tenant_id=tenant,
                    command=ChangeHostingPackage(
                        hosting_service_id=receipt.hosting_service_id,
                        specification_code="business-hosting",
                        specification_version=2,
                        requested_at=NOW + timedelta(hours=1),
                    ),
                    actor=_actor(),
                    idempotency_key="package:no-lock",
                    idempotency_expires_at=IDEMPOTENCY_EXPIRY,
                )
                db.commit()
                return result.disposition.value
            except DBAPIError as exc:
                db.rollback()
                if (
                    getattr(exc.orig, "sqlstate", None) != "23505"
                    or getattr(exc.orig.diag, "constraint_name", None)
                    != "uq_hosting_desired_revisions_service_version"
                ):
                    raise
                return "desired_revision_fork_refused"

    with ThreadPoolExecutor(max_workers=2) as executor:
        suspended = executor.submit(run_suspend)
        packaged = executor.submit(run_package)
        results = {suspended.result(), packaged.result()}
    assert "desired_revision_fork_refused" in results
