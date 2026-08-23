"""Live PostgreSQL proofs for the greenfield domain-service owner."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from dotmac_domains.contracts import (
    Actor,
    ApplyDNSRecordSetsV1,
    ApprovalDecision,
    ApprovalReceipt,
    ClearDomainHold,
    ConfigureDNSZoneV1,
    ConsequenceRequest,
    DNSObservationV1,
    DNSRecordSetV1,
    DomainContactSetV1,
    DomainContactV1,
    DomainContactsIntent,
    DomainDNSRecordsetsIntent,
    DomainDNSZoneIntent,
    DomainLifecycleState,
    DomainNameserversIntent,
    DomainObservationV1,
    DomainPostalAddressV1,
    OutcomeClass,
    OutcomeKind,
    RecordRegistrarOutcome,
    RegisterDomain,
    RegisterDomainV1,
    RenewDomain,
    RenewDomainV1,
    RequestTransferDomain,
    SetDomainIntent,
    TransferDirection,
    TransferDomainV1,
    UpdateDomainContactsV1,
    UpdateDomainNameserversV1,
    transfer_out_content_digest,
)
from dotmac_domains.manifest import module
from dotmac_domains.models import (
    ALL_MODELS,
    DomainAttentionCondition,
    DomainCommand,
    DomainCommandOutcome,
    DNSObservation,
    DomainHold,
    DomainIntent,
    DomainObservation,
    DomainService,
)
from dotmac_domains.service import (
    DomainError,
    InvalidDomainTransition,
    ReleaseNotPermitted,
    StaleDomainVersion,
    StaleRegistrarObservation,
    apply_consequence_request,
    clear_domain_hold,
    receive_dns_observation,
    receive_registrar_observation,
    reconcile_domain,
    record_registrar_outcome,
    request_registration,
    request_renewal,
    request_transfer,
    set_domain_intent,
)
from dotmac_kernel.audit_actions import (
    AuditActionRegistry,
    AuditActionsNotInstalledError,
    active_audit_actions,
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
def _domain_audit_actions() -> Iterator[None]:
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

    previous_migration_url = os.environ.get("MIGRATION_DATABASE_URL")
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


def _registration(
    name: str = "customer.ng",
    *,
    requested_at: datetime = NOW,
    contact_set: DomainContactSetV1 | None = None,
) -> RegisterDomain:
    return RegisterDomain(
        name=name,
        order_line_ref=f"order-line:{name}",
        offer_version_ref="domain-offer:v1",
        term_months=12,
        contact_set=contact_set or _contact_set(),
        nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        privacy_requested=True,
        commercial_renewal_at=EXPIRES - timedelta(days=30),
        requested_at=requested_at,
    )


def _contact_set(*, email: str = "owner@example.ng") -> DomainContactSetV1:
    address = DomainPostalAddressV1(
        line_one="12 Domain Street",
        city="Abuja",
        region="FCT",
        postal_code="900001",
        country_code="NG",
    )
    registrant = DomainContactV1(
        full_name="Example Owner",
        organization="Example Limited",
        email=email,
        phone="+2348090000000",
        address=address,
    )
    return DomainContactSetV1(
        source_authority="cloud.customer.contacts",
        source_reference="contact-set:1",
        source_version="7",
        registrant=registrant,
        administrative=registrant,
        technical=registrant,
        billing=registrant,
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


def _renewal_poll(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    name: str = "customer.ng",
    at: datetime,
    binding: str = "registrar-binding-1",
) -> uuid.UUID:
    observation = _observation(
        name,
        event=f"renewal-poll:{name}:{at.isoformat()}",
        kind="expiry_observed",
        observed_at=at - timedelta(seconds=2),
        source_mode="poll",
    )
    if binding != observation.capability_binding_ref:
        observation = DomainObservationV1(
            name=observation.name,
            observation_kind=observation.observation_kind,
            provider_statuses=observation.provider_statuses,
            observed_at=observation.observed_at,
            provider_event_id=observation.provider_event_id,
            capability_binding_ref=binding,
            expires_at=observation.expires_at,
            nameservers=observation.nameservers,
            source_mode="poll",
        )
    receipt = receive_registrar_observation(
        db,
        envelope=_envelope(tenant_id, f"renewal-poll:{name}:{at.isoformat()}"),
        observation=observation,
        received_at=at - timedelta(seconds=1),
    )
    return receipt.observation_id


def _dns_observation(
    *,
    event: str = "dns-event-1",
    binding: str = "dns-binding-1",
    observed_at: datetime = NOW + timedelta(seconds=1),
    address: str = "192.0.2.10",
) -> DNSObservationV1:
    return DNSObservationV1(
        zone_name="customer.ng",
        provider_event_id=event,
        capability_binding_ref=binding,
        observed_at=observed_at,
        nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        recordsets=(DNSRecordSetV1("@", "A", 300, (address,)),),
        source_mode="poll",
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


def test_dns_observation_is_deduplicated_by_binding_and_rejects_content_conflict(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    observation = _dns_observation()
    with _session(scratch["tenant"], tenant) as db:
        first = receive_dns_observation(
            db,
            envelope=_envelope(tenant, "dns-1"),
            observation=observation,
            received_at=NOW + timedelta(seconds=2),
        )
        duplicate = receive_dns_observation(
            db,
            envelope=_envelope(tenant, "dns-2"),
            observation=observation,
            received_at=NOW + timedelta(seconds=3),
        )
        assert duplicate.observation_id == first.observation_id
        assert duplicate.duplicate
        with pytest.raises(DomainError, match="reused with different data"):
            receive_dns_observation(
                db,
                envelope=_envelope(tenant, "dns-3"),
                observation=_dns_observation(address="192.0.2.11"),
                received_at=NOW + timedelta(seconds=4),
            )
        assert db.scalar(select(func.count()).select_from(DNSObservation)) == 1


def test_future_provider_facts_are_refused_before_persistence(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        with pytest.raises(DomainError, match="future-dated"):
            receive_registrar_observation(
                db,
                envelope=_envelope(tenant, "future-registrar"),
                observation=_observation(observed_at=NOW + timedelta(minutes=2)),
                received_at=NOW + timedelta(minutes=1),
            )
        with pytest.raises(DomainError, match="future-dated"):
            receive_dns_observation(
                db,
                envelope=_envelope(tenant, "future-dns"),
                observation=_dns_observation(
                    observed_at=NOW + timedelta(minutes=2)
                ),
                received_at=NOW + timedelta(minutes=1),
            )
        assert db.scalar(select(func.count()).select_from(DomainObservation)) == 0
        assert db.scalar(select(func.count()).select_from(DNSObservation)) == 0


def test_nullable_observation_correlation_is_still_same_tenant_fk_when_present(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        db.add(
            DNSObservation(
                tenant_id=tenant,
                domain_service_id=uuid.uuid4(),
                zone_name="customer.ng",
                capability_binding_ref="dns-binding-1",
                provider_event_id="invalid-correlation",
                observed_nameservers=[],
                observed_recordsets=[],
                observed_recordsets_digest="0" * 64,
                source_mode="poll",
                payload_digest="1" * 64,
                observed_at=NOW,
                received_at=NOW,
            )
        )
        with pytest.raises(DBAPIError):
            db.flush()


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


def test_first_acquisition_confirmation_fixes_the_registrar_binding(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(),
            idempotency_key="binding-registration",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "binding-first"),
            observation=_observation(event="binding-first"),
            received_at=NOW + timedelta(seconds=2),
        )
        foreign = _observation(
            event="binding-foreign",
            observed_at=NOW + timedelta(seconds=3),
        )
        foreign = DomainObservationV1(
            name=foreign.name,
            observation_kind=foreign.observation_kind,
            provider_statuses=foreign.provider_statuses,
            observed_at=foreign.observed_at,
            provider_event_id=foreign.provider_event_id,
            capability_binding_ref="registrar-binding-foreign",
            expires_at=foreign.expires_at,
            nameservers=foreign.nameservers,
        )
        receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "binding-foreign"),
            observation=foreign,
            received_at=NOW + timedelta(seconds=4),
        )
        reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(seconds=5),
        )
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        assert service.registrar_binding_ref == "registrar-binding-1"


def test_dns_reconciliation_uses_canonical_state_from_the_active_binding(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        set_domain_intent(
            db,
            tenant_id=tenant,
            command=SetDomainIntent(
                domain_service_id=receipt.domain_service_id,
                intent=DomainDNSRecordsetsIntent(
                    recordsets=(
                        DNSRecordSetV1("@", "A", 300, ("192.0.2.10",)),
                    )
                ),
                requested_at=NOW + timedelta(minutes=1),
            ),
            idempotency_key="dns-recordsets",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        receive_dns_observation(
            db,
            envelope=_envelope(tenant, "dns-active"),
            observation=_dns_observation(
                event="dns-active", observed_at=NOW + timedelta(minutes=2)
            ),
            received_at=NOW + timedelta(minutes=2, seconds=1),
        )
        first = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(minutes=3),
        )
        assert not first.drift.dns_recordsets_disagree
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None and service.dns_binding_ref == "dns-binding-1"

        receive_dns_observation(
            db,
            envelope=_envelope(tenant, "dns-foreign"),
            observation=_dns_observation(
                event="dns-foreign",
                binding="dns-binding-foreign",
                observed_at=NOW + timedelta(minutes=4),
                address="192.0.2.99",
            ),
            received_at=NOW + timedelta(minutes=4, seconds=1),
        )
        second = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(minutes=5),
        )
        assert not second.drift.dns_recordsets_disagree
        receive_dns_observation(
            db,
            envelope=_envelope(tenant, "dns-active-drift"),
            observation=_dns_observation(
                event="dns-active-drift",
                observed_at=NOW + timedelta(minutes=6),
                address="192.0.2.99",
            ),
            received_at=NOW + timedelta(minutes=6, seconds=1),
        )
        drifted = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(minutes=7),
        )
        assert drifted.drift.dns_recordsets_disagree


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


def test_renewal_refuses_ingress_stale_and_inactive_binding_facts(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        requested_at = NOW + timedelta(days=10)
        ingress = receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "renewal-ingress"),
            observation=_observation(
                event="renewal-ingress",
                kind="expiry_observed",
                observed_at=requested_at - timedelta(minutes=2),
                source_mode="ingress",
            ),
            received_at=requested_at - timedelta(minutes=1),
        )
        stale = receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "renewal-stale-observed"),
            observation=_observation(
                event="renewal-stale-observed",
                kind="expiry_observed",
                observed_at=requested_at - timedelta(days=2),
                source_mode="poll",
            ),
            # Recent delivery does not make an old provider observation safe.
            received_at=requested_at - timedelta(minutes=1),
        ).observation_id
        future = receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "renewal-future-at-decision"),
            observation=_observation(
                event="renewal-future-at-decision",
                kind="expiry_observed",
                observed_at=requested_at + timedelta(minutes=1),
                source_mode="poll",
            ),
            received_at=requested_at + timedelta(minutes=2),
        )
        inactive = _renewal_poll(
            db,
            tenant,
            at=requested_at,
            binding="registrar-binding-foreign",
        )
        superseded = _renewal_poll(
            db,
            tenant,
            at=requested_at - timedelta(minutes=10),
        )
        _renewal_poll(
            db,
            tenant,
            at=requested_at - timedelta(minutes=5),
        )
        _register_and_activate(db, tenant, "other-customer.ng")
        unmatched = _renewal_poll(
            db,
            tenant,
            name="other-customer.ng",
            at=requested_at,
        )
        for index, (observation_id, message) in enumerate(
            (
                (ingress.observation_id, "POLL"),
                (stale, "older"),
                (future.observation_id, "future-dated"),
                (inactive, "active registrar binding"),
                (superseded, "latest relevant"),
                (unmatched, "another domain"),
            )
        ):
            with pytest.raises(StaleRegistrarObservation, match=message):
                request_renewal(
                    db,
                    tenant_id=tenant,
                    command=RenewDomain(
                        domain_service_id=receipt.domain_service_id,
                        term_months=12,
                        coverage_reference=f"coverage:refusal:{index}",
                        registrar_observation_id=observation_id,
                        commercial_renewal_at=EXPIRES + timedelta(days=365),
                        requested_at=requested_at,
                    ),
                    idempotency_key=f"renewal-refusal:{index}",
                    idempotency_expires_at=requested_at + timedelta(days=30),
                )


def test_paid_renewal_failure_stays_open_until_confirmed_observation(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        renewal_at = NOW + timedelta(days=300)
        registrar_observation_id = _renewal_poll(
            db, tenant, at=renewal_at
        )
        renewal = request_renewal(
            db,
            tenant_id=tenant,
            command=RenewDomain(
                domain_service_id=receipt.domain_service_id,
                term_months=12,
                coverage_reference="coverage:paid-renewal",
                commercial_renewal_at=EXPIRES + timedelta(days=335),
                registrar_observation_id=registrar_observation_id,
                requested_at=renewal_at,
            ),
            idempotency_key="renewal",
            idempotency_expires_at=NOW + timedelta(days=700),
        )
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        assert service.lifecycle_state == DomainLifecycleState.RENEWAL_REQUESTED
        renewal_event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "domains.registrar.renewal.requested.v1"
            )
        )
        assert renewal_event is not None
        renewal_request = RenewDomainV1.from_payload(renewal_event.payload)
        assert renewal_request.observed_expires_at == EXPIRES
        assert "coverage_reference" not in renewal_event.payload
        assert "domain_service_id" not in renewal_event.payload
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


def test_terminal_registration_failure_releases_name_for_a_new_aggregate(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        failed = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(),
            idempotency_key="failed-registration",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        record_registrar_outcome(
            db,
            envelope=_envelope(tenant, "terminal-registration"),
            outcome=RecordRegistrarOutcome(
                domain_command_id=failed.command_id,
                evidence_key="terminal-registration",
                outcome_kind=OutcomeKind.FAILED,
                outcome_class=OutcomeClass.TERMINAL,
                occurred_at=NOW + timedelta(seconds=1),
                reason_code="registry_policy_refused",
            ),
            received_at=NOW + timedelta(seconds=2),
        )
        failed_service = db.get(DomainService, failed.domain_service_id)
        assert failed_service is not None
        assert (
            failed_service.lifecycle_state
            == DomainLifecycleState.REGISTRATION_FAILED.value
        )
        failure_event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "domains.provider_command.failed.v1"
            )
        )
        assert failure_event is not None
        assert failure_event.payload["command_kind"] == "registration"
        assert failure_event.payload["repair_state"] == "registration_failed"
        with pytest.raises(InvalidDomainTransition, match="terminal domain"):
            set_domain_intent(
                db,
                tenant_id=tenant,
                command=SetDomainIntent(
                    domain_service_id=failed.domain_service_id,
                    intent=DomainNameserversIntent(
                        nameservers=("ns3.dotmac.ng", "ns4.dotmac.ng")
                    ),
                    requested_at=NOW + timedelta(seconds=2, microseconds=1),
                ),
                idempotency_key="failed-domain-intent",
                idempotency_expires_at=NOW + timedelta(days=30),
            )
        replacement = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(requested_at=NOW + timedelta(seconds=3)),
            idempotency_key="replacement-registration",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        assert replacement.domain_service_id != failed.domain_service_id


def test_outcome_evidence_key_refuses_changed_content(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = request_registration(
            db,
            tenant_id=tenant,
            command=_registration(),
            idempotency_key="outcome-conflict-registration",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        record_registrar_outcome(
            db,
            envelope=_envelope(tenant, "outcome-1"),
            outcome=RecordRegistrarOutcome(
                domain_command_id=receipt.command_id,
                evidence_key="provider-attempt-1",
                outcome_kind=OutcomeKind.ACKNOWLEDGED,
                outcome_class=OutcomeClass.SUCCEEDED,
                occurred_at=NOW + timedelta(seconds=1),
                provider_reference="provider-order-1",
            ),
            received_at=NOW + timedelta(seconds=2),
        )
        with pytest.raises(DomainError, match="reused with different data"):
            record_registrar_outcome(
                db,
                envelope=_envelope(tenant, "outcome-2"),
                outcome=RecordRegistrarOutcome(
                    domain_command_id=receipt.command_id,
                    evidence_key="provider-attempt-1",
                    outcome_kind=OutcomeKind.FAILED,
                    outcome_class=OutcomeClass.RETRYABLE,
                    occurred_at=NOW + timedelta(seconds=1),
                    reason_code="timeout",
                ),
                received_at=NOW + timedelta(seconds=3),
            )


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


def test_typed_desired_state_emits_only_declared_provider_operations(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        initial_version = service.row_version
        intents = (
            DomainContactsIntent(contact_set=_contact_set(email="ops@example.ng")),
            DomainNameserversIntent(nameservers=("ns3.dotmac.ng", "ns4.dotmac.ng")),
            DomainDNSZoneIntent(nameservers=("ns3.dotmac.ng", "ns4.dotmac.ng")),
            DomainDNSRecordsetsIntent(
                recordsets=(
                    DNSRecordSetV1(
                        owner="www",
                        record_type="A",
                        ttl=300,
                        values=("192.0.2.20",),
                    ),
                )
            ),
        )
        for index, intent in enumerate(intents):
            set_domain_intent(
                db,
                tenant_id=tenant,
                command=SetDomainIntent(
                    domain_service_id=receipt.domain_service_id,
                    intent=intent,
                    requested_at=NOW + timedelta(minutes=index + 1),
                ),
                idempotency_key=f"intent-{index}",
                idempotency_expires_at=NOW + timedelta(days=30),
            )
        event_types = set(
            db.scalars(
                select(OutboxEvent.event_type).where(
                    OutboxEvent.event_type.like("%.requested.v1")
                )
            )
        )
        assert {
            "domains.registrar.contacts.requested.v1",
            "domains.registrar.nameservers.requested.v1",
            "dns.authoritative.zone.requested.v1",
            "dns.authoritative.recordset.requested.v1",
        }.issubset(event_types)
        assert "dns.authoritative.intent.requested.v1" not in event_types
        assert service.row_version == initial_version + len(intents)

        contacts_event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type
                == "domains.registrar.contacts.requested.v1"
            )
        )
        assert contacts_event is not None
        contacts_request = UpdateDomainContactsV1.from_payload(
            contacts_event.payload
        )
        assert contacts_request.contact_set == _contact_set(email="ops@example.ng")

        nameservers_event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type
                == "domains.registrar.nameservers.requested.v1"
            )
        )
        zone_event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "dns.authoritative.zone.requested.v1"
            )
        )
        recordsets_event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "dns.authoritative.recordset.requested.v1"
            )
        )
        assert nameservers_event is not None
        assert zone_event is not None
        assert recordsets_event is not None
        nameservers_request = UpdateDomainNameserversV1.from_payload(
            nameservers_event.payload
        )
        zone_request = ConfigureDNSZoneV1.from_payload(zone_event.payload)
        recordsets_request = ApplyDNSRecordSetsV1.from_payload(
            recordsets_event.payload
        )
        assert nameservers_request.nameservers == (
            "ns3.dotmac.ng",
            "ns4.dotmac.ng",
        )
        assert zone_request.nameservers == ("ns3.dotmac.ng", "ns4.dotmac.ng")
        assert recordsets_request.recordsets[0].values == ("192.0.2.20",)

        owner_local_fields = {
            "contact_set_ref",
            "nameserver_set_ref",
            "domain_service_id",
            "intent_id",
            "intent_version",
        }
        for event in (
            contacts_event,
            nameservers_event,
            zone_event,
            recordsets_event,
        ):
            assert not owner_local_fields.intersection(event.payload)


def test_intent_change_advances_version_and_stales_prior_transfer_approval(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    actor = Actor(actor_type="service", actor_id="cloud-assembly")
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        unsigned = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(days=2),
        )
        approved = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=unsigned.requested_at,
            approval=ApprovalReceipt(
                policy_code="domains.transfer_out",
                policy_version=1,
                content_digest=transfer_out_content_digest(
                    service.registered_name, unsigned
                ),
                decision=ApprovalDecision.APPROVED,
                decided_at=NOW + timedelta(days=1),
                decision_reference="approval-before-intent-change",
            ),
        )
        set_domain_intent(
            db,
            tenant_id=tenant,
            command=SetDomainIntent(
                domain_service_id=service.id,
                intent=DomainNameserversIntent(
                    nameservers=("ns3.dotmac.ng", "ns4.dotmac.ng")
                ),
                requested_at=NOW + timedelta(days=1, hours=1),
            ),
            idempotency_key="intent-stales-approval",
            idempotency_expires_at=NOW + timedelta(days=30),
        )
        with pytest.raises(StaleDomainVersion):
            request_transfer(
                db,
                tenant_id=tenant,
                command=approved,
                idempotency_key="stale-after-intent",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )


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
        registration_event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type
                == "domains.registrar.registration.requested.v1"
            )
        )
        assert registration_event is not None
        original_payload = deepcopy(registration_event.payload)
        provider_request = RegisterDomainV1.from_payload(original_payload)
        assert provider_request.contact_set == _contact_set()
        assert provider_request.nameservers == (
            "ns1.dotmac.ng",
            "ns2.dotmac.ng",
        )
        assert not {
            "contact_set_ref",
            "nameserver_set_ref",
            "contact_intent_ref",
        }.intersection(original_payload)
        with pytest.raises(IdempotencyConflict):
            request_registration(
                db,
                tenant_id=tenant,
                command=_registration(
                    contact_set=_contact_set(email="changed@example.ng")
                ),
                idempotency_key="registration-replay",
                idempotency_expires_at=NOW + timedelta(days=30),
            )
        assert registration_event.payload == original_payload


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
        renewal_at = NOW + timedelta(days=300)
        registrar_observation_id = _renewal_poll(
            db, tenant, at=renewal_at
        )
        db.commit()

    renewal_command = RenewDomain(
        domain_service_id=registration.domain_service_id,
        term_months=12,
        coverage_reference="coverage:concurrent-renewal",
        commercial_renewal_at=EXPIRES + timedelta(days=335),
        registrar_observation_id=registrar_observation_id,
        requested_at=renewal_at,
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


def test_transfer_out_refuses_missing_or_changed_approval_then_emits_exact_operation(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    operator_id = uuid.uuid4()
    actor = Actor(
        actor_type="user",
        actor_id=str(operator_id),
        actor_party_id=operator_id,
    )
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        unapproved = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(days=2),
            approval=None,
        )
        with pytest.raises(ReleaseNotPermitted):
            request_transfer(
                db,
                tenant_id=tenant,
                command=unapproved,
                idempotency_key="transfer-without-approval",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        digest = transfer_out_content_digest(service.registered_name, unapproved)
        approved = ApprovalReceipt(
            policy_code="domains.transfer_out",
            policy_version=1,
            content_digest=digest,
            decision=ApprovalDecision.APPROVED,
            decided_at=NOW + timedelta(days=1),
            decision_reference="approval-1",
        )
        exact = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(days=2),
            approval=approved,
        )
        wrong_policy = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(days=2),
            approval=ApprovalReceipt(
                policy_code="domains.release",
                policy_version=1,
                content_digest=digest,
                decision=ApprovalDecision.APPROVED,
                decided_at=NOW + timedelta(days=1),
                decision_reference="wrong-policy-approval",
            ),
        )
        with pytest.raises(ReleaseNotPermitted, match="exact content"):
            request_transfer(
                db,
                tenant_id=tenant,
                command=wrong_policy,
                idempotency_key="transfer-wrong-policy",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        changed_time = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(days=2, seconds=1),
            approval=approved,
        )
        with pytest.raises(ReleaseNotPermitted, match="exact content"):
            request_transfer(
                db,
                tenant_id=tenant,
                command=changed_time,
                idempotency_key="transfer-changed-time",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        stale_unapproved = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version + 1,
            requested_at=NOW + timedelta(days=2),
        )
        stale = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version + 1,
            requested_at=NOW + timedelta(days=2),
            approval=ApprovalReceipt(
                policy_code="domains.transfer_out",
                policy_version=1,
                content_digest=transfer_out_content_digest(
                    service.registered_name, stale_unapproved
                ),
                decision=ApprovalDecision.APPROVED,
                decided_at=NOW + timedelta(days=1),
                decision_reference="stale-approval",
            ),
        )
        with pytest.raises(StaleDomainVersion):
            request_transfer(
                db,
                tenant_id=tenant,
                command=stale,
                idempotency_key="transfer-stale-version",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        transfer = request_transfer(
            db,
            tenant_id=tenant,
            command=exact,
            idempotency_key="transfer-approved",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        assert transfer.lifecycle_state is DomainLifecycleState.TRANSFER_OUT_REQUESTED
        event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "domains.registrar.transfer.requested.v1"
            )
        )
        assert event is not None
        transfer_request = TransferDomainV1.from_payload(event.payload)
        assert transfer_request.direction is TransferDirection.APPROVE_OUT
        assert "domain_service_id" not in event.payload
        assert (
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type.like("domains.registrar.release.%"))
            )
            == 0
        )
        previous_version = service.row_version
        record_registrar_outcome(
            db,
            envelope=_envelope(tenant, "terminal-transfer-out"),
            outcome=RecordRegistrarOutcome(
                domain_command_id=transfer.command_id,
                evidence_key="terminal-transfer-out",
                outcome_kind=OutcomeKind.FAILED,
                outcome_class=OutcomeClass.TERMINAL,
                occurred_at=NOW + timedelta(days=2, seconds=1),
                reason_code="transfer_policy_refused",
            ),
            received_at=NOW + timedelta(days=2, seconds=2),
        )
        assert service.lifecycle_state == DomainLifecycleState.ACTIVE.value
        assert service.row_version == previous_version + 1
        assert db.scalar(
            select(func.count())
            .select_from(DomainAttentionCondition)
            .where(
                DomainAttentionCondition.condition_code
                == "terminal_transfer_out_failure"
            )
        ) == 1
        retry_unsigned = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(days=3),
        )
        retry = request_transfer(
            db,
            tenant_id=tenant,
            command=RequestTransferDomain(
                domain_service_id=service.id,
                direction=TransferDirection.APPROVE_OUT,
                expected_version=service.row_version,
                requested_at=retry_unsigned.requested_at,
                approval=ApprovalReceipt(
                    policy_code="domains.transfer_out",
                    policy_version=1,
                    content_digest=transfer_out_content_digest(
                        service.registered_name, retry_unsigned
                    ),
                    decision=ApprovalDecision.APPROVED,
                    decided_at=NOW + timedelta(days=2, hours=1),
                    decision_reference="retry-transfer-approval",
                ),
            ),
            idempotency_key="transfer-retry",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        cancel = request_transfer(
            db,
            tenant_id=tenant,
            command=RequestTransferDomain(
                domain_service_id=service.id,
                direction=TransferDirection.CANCEL,
                requested_at=NOW + timedelta(days=3, seconds=1),
            ),
            idempotency_key="transfer-cancel",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        assert retry.lifecycle_state is DomainLifecycleState.TRANSFER_OUT_REQUESTED
        version_before_failed_cancel = service.row_version
        record_registrar_outcome(
            db,
            envelope=_envelope(tenant, "terminal-transfer-cancel"),
            outcome=RecordRegistrarOutcome(
                domain_command_id=cancel.command_id,
                evidence_key="terminal-transfer-cancel",
                outcome_kind=OutcomeKind.FAILED,
                outcome_class=OutcomeClass.TERMINAL,
                occurred_at=NOW + timedelta(days=3, seconds=2),
                reason_code="cancel_refused",
            ),
            received_at=NOW + timedelta(days=3, seconds=3),
        )
        assert service.lifecycle_state == DomainLifecycleState.TRANSFER_OUT_REQUESTED
        assert service.row_version == version_before_failed_cancel
        assert db.scalar(
            select(func.count())
            .select_from(DomainAttentionCondition)
            .where(
                DomainAttentionCondition.condition_code
                == "terminal_transfer_cancel_failure"
            )
        ) == 1


def test_unexpected_provider_deletion_cannot_release_an_active_domain_silently(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        receive_registrar_observation(
            db,
            envelope=_envelope(tenant, "unexpected-deletion"),
            observation=_observation(
                event="unexpected-deletion-event",
                kind="deleted",
                observed_at=NOW + timedelta(days=1),
                expires_at=None,
            ),
            received_at=NOW + timedelta(days=1, seconds=1),
        )
        result = reconcile_domain(
            db,
            tenant_id=tenant,
            domain_service_id=receipt.domain_service_id,
            reconciled_at=NOW + timedelta(days=1, seconds=2),
        )
        assert result.current_state is DomainLifecycleState.ACTIVE
        condition = db.scalar(
            select(DomainAttentionCondition).where(
                DomainAttentionCondition.condition_code
                == "unexpected_provider_deletion"
            )
        )
        assert condition is not None and condition.resolved_at is None


def test_concurrent_transfer_outs_cannot_both_pass_the_approval_guard(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        service = db.get(DomainService, receipt.domain_service_id)
        assert service is not None
        service_id = service.id
        registered_name = service.registered_name
        expected_version = service.row_version
        db.commit()

    def run(index: int) -> str:
        requested_at = NOW + timedelta(days=4, seconds=index)
        unsigned = RequestTransferDomain(
            domain_service_id=service_id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=expected_version,
            requested_at=requested_at,
        )
        command = RequestTransferDomain(
            domain_service_id=service_id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=expected_version,
            requested_at=requested_at,
            approval=ApprovalReceipt(
                policy_code="domains.transfer_out",
                policy_version=1,
                content_digest=transfer_out_content_digest(registered_name, unsigned),
                decision=ApprovalDecision.APPROVED,
                decided_at=NOW + timedelta(days=3),
                decision_reference=f"concurrent-approval-{index}",
            ),
        )
        try:
            with _session(scratch["tenant"], tenant) as db:
                request_transfer(
                    db,
                    tenant_id=tenant,
                    command=command,
                    idempotency_key=f"concurrent-transfer-{index}",
                    idempotency_expires_at=NOW + timedelta(days=30),
                    actor=Actor(actor_type="service", actor_id="cloud-assembly"),
                )
                db.commit()
            return "accepted"
        except StaleDomainVersion:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, range(2)))

    assert sorted(results) == ["accepted", "stale"]
    with _session(scratch["tenant"], tenant) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(DomainCommand)
                .where(DomainCommand.command_kind == "transfer_out")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "domains.registrar.transfer.requested.v1"
                )
            )
            == 1
        )


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


def test_source_owned_hold_blocks_transfer_out_until_its_owner_clears_it(
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
        unapproved = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=NOW + timedelta(days=4),
            approval=None,
        )
        digest = transfer_out_content_digest(service.registered_name, unapproved)
        transfer = RequestTransferDomain(
            domain_service_id=service.id,
            direction=TransferDirection.APPROVE_OUT,
            expected_version=service.row_version,
            requested_at=unapproved.requested_at,
            approval=ApprovalReceipt(
                policy_code="domains.transfer_out",
                policy_version=1,
                content_digest=digest,
                decision=ApprovalDecision.APPROVED,
                decided_at=NOW + timedelta(days=2, hours=1),
                decision_reference="approval-held-release",
            ),
        )
        with pytest.raises(ReleaseNotPermitted, match="active domain hold"):
            request_transfer(
                db,
                tenant_id=tenant,
                command=transfer,
                idempotency_key="held-transfer",
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
        accepted = request_transfer(
            db,
            tenant_id=tenant,
            command=transfer,
            idempotency_key="transfer-after-hold",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        assert accepted.lifecycle_state is DomainLifecycleState.TRANSFER_OUT_REQUESTED
        hold_id = db.scalar(select(DomainHold.id))
        assert hold_id is not None
        db.commit()

    admin = create_engine(scratch["admin"])
    with admin.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
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


def test_hold_identity_includes_owner_and_reference(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    actor = Actor(actor_type="service", actor_id="cloud-assembly")
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        for index in (1, 2):
            apply_consequence_request(
                db,
                tenant_id=tenant,
                command=ConsequenceRequest(
                    domain_service_id=receipt.domain_service_id,
                    consequence_kind="renewal_review",
                    source_owner="collections",
                    source_reference=f"case-{index}",
                    reason_code="review",
                    requested_at=NOW + timedelta(days=index),
                ),
                idempotency_key=f"hold-{index}",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        assert db.scalar(
            select(func.count())
            .select_from(DomainHold)
            .where(DomainHold.cleared_at.is_(None))
        ) == 2
        with pytest.raises(DomainError, match="owner and reference"):
            clear_domain_hold(
                db,
                tenant_id=tenant,
                command=ClearDomainHold(
                    domain_service_id=receipt.domain_service_id,
                    hold_code="renewal_review",
                    source_owner="collections",
                    source_reference="case-not-owned",
                    reason_code="wrong_identity",
                    requested_at=NOW + timedelta(days=3),
                ),
                idempotency_key="wrong-hold-identity",
                idempotency_expires_at=NOW + timedelta(days=30),
                actor=actor,
            )
        clear_domain_hold(
            db,
            tenant_id=tenant,
            command=ClearDomainHold(
                domain_service_id=receipt.domain_service_id,
                hold_code="renewal_review",
                source_owner="collections",
                source_reference="case-1",
                reason_code="cleared",
                requested_at=NOW + timedelta(days=3),
            ),
            idempotency_key="clear-case-1",
            idempotency_expires_at=NOW + timedelta(days=30),
            actor=actor,
        )
        assert db.scalar(
            select(func.count())
            .select_from(DomainHold)
            .where(DomainHold.cleared_at.is_(None))
        ) == 1


@pytest.mark.parametrize(
    ("model", "update_statement", "delete_statement"),
    [
        (
            DomainCommand,
            text("UPDATE mod_domains.domain_commands SET id = id WHERE id = :id"),
            text("DELETE FROM mod_domains.domain_commands WHERE id = :id"),
        ),
        (
            DomainCommandOutcome,
            text(
                "UPDATE mod_domains.domain_command_outcomes "
                "SET id = id WHERE id = :id"
            ),
            text("DELETE FROM mod_domains.domain_command_outcomes WHERE id = :id"),
        ),
        (
            DomainObservation,
            text("UPDATE mod_domains.domain_observations SET id = id WHERE id = :id"),
            text("DELETE FROM mod_domains.domain_observations WHERE id = :id"),
        ),
        (
            DNSObservation,
            text("UPDATE mod_domains.dns_observations SET id = id WHERE id = :id"),
            text("DELETE FROM mod_domains.dns_observations WHERE id = :id"),
        ),
        (
            DomainIntent,
            text("UPDATE mod_domains.domain_intents SET id = id WHERE id = :id"),
            text("DELETE FROM mod_domains.domain_intents WHERE id = :id"),
        ),
    ],
)
def test_online_and_admin_paths_cannot_rewrite_or_delete_evidence(
    scratch: dict[str, str],
    model: Any,
    update_statement: Any,
    delete_statement: Any,
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
        receive_dns_observation(
            db,
            envelope=_envelope(tenant, f"evidence-dns:{model.__name__}"),
            observation=_dns_observation(event=f"evidence:{model.__name__}"),
            received_at=NOW + timedelta(seconds=2),
        )
        row = db.scalar(select(model))
        assert row is not None
        row_id = row.id
        decision = consequence.decision
        db.commit()
        with pytest.raises(DBAPIError):
            db.execute(update_statement, {"id": row_id})
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(delete_statement, {"id": row_id})
        db.rollback()

    admin = create_engine(scratch["admin"])
    with admin.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError):
            connection.execute(update_statement, {"id": row_id})
        connection.rollback()
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError):
            connection.execute(delete_statement, {"id": row_id})
        connection.rollback()
    admin.dispose()
    assert decision == "refused"


def test_domain_service_identity_cannot_be_rewritten_or_deleted_by_raw_sql(
    scratch: dict[str, str],
) -> None:
    tenant, _ = _seed_tenants(scratch["admin"])
    with _session(scratch["tenant"], tenant) as db:
        receipt = _register_and_activate(db, tenant)
        db.commit()
        for statement in (
            text(
                "UPDATE mod_domains.domain_services "
                "SET registered_name = 'stolen.ng' WHERE id = :id"
            ),
            text("DELETE FROM mod_domains.domain_services WHERE id = :id"),
        ):
            with pytest.raises(DBAPIError):
                db.execute(statement, {"id": receipt.domain_service_id})
            db.rollback()

    admin = create_engine(scratch["admin"])
    with admin.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        for statement in (
            text(
                "UPDATE mod_domains.domain_services "
                "SET registered_name = 'stolen.ng', row_version = row_version + 1 "
                "WHERE id = :id"
            ),
            text("DELETE FROM mod_domains.domain_services WHERE id = :id"),
        ):
            with pytest.raises(DBAPIError):
                connection.execute(statement, {"id": receipt.domain_service_id})
            connection.rollback()
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant)},
            )
    admin.dispose()


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
