"""PostgreSQL correctness proofs for the tenant-only Orders owner.

Sub supplies the product-first behavior, but no source supplies tenant RLS,
structurally immutable accepted lines, or a real concurrency proof.  Every test
here therefore uses a separately migrated disposable PostgreSQL database.
"""

from __future__ import annotations

import contextlib
import os
import threading
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.audit_actions import (
    AuditActionRegistry,
    AuditActionsNotInstalledError,
    active_audit_actions,
    install_audit_actions,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.messaging.models import OutboxEvent, OutboxStatus
from dotmac_kernel.money import Money, currency
from dotmac_orders import (
    AcceptOrderCommand,
    AcknowledgeFulfillmentCommand,
    ActorRef,
    CancelOrderCommand,
    FxSnapshotV1,
    LineInput,
    OrderCommandResult,
    OrderConflict,
    OrderError,
    RecordCoverageResolutionCommand,
    SubmitOrderCommand,
    TaxSnapshotV1,
    TermsSnapshotV1,
    TermValueV1,
    accept_order,
    acknowledge_fulfillment,
    cancel_order,
    get_order_timeline,
    reconcile_fulfillment_publications,
    record_coverage_resolution,
    submit_order,
)
from dotmac_orders.manifest import module
from dotmac_orders.models import (
    CoverageGate,
    CoverageResolutionReceipt,
    FulfillmentRequest,
    Order,
    OrderLineSnapshot,
)
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
ORDERS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-orders/src/dotmac_orders/migrations/versions"
)

NOW = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)
NGN = currency("NGN")


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — Orders proofs need PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def scratch() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"orders_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    try:
        previous_actions = active_audit_actions()
    except AuditActionsNotInstalledError:
        previous_actions = AuditActionRegistry(())
    install_audit_actions(AuditActionRegistry.from_manifests((module,)))
    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {ORDERS_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        install_audit_actions(previous_actions)
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _make_tenants(admin_url: str, count: int = 2) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in range(count)]
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for tenant_id in ids:
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, name, slug, is_active) "
                    "VALUES (:id, :name, :slug, true)"
                ),
                {
                    "id": tenant_id,
                    "name": f"tenant-{tenant_id.hex[:8]}",
                    "slug": tenant_id.hex[:8],
                },
            )
    engine.dispose()
    return ids


def _tenant_engine(url: str, tenant_id: uuid.UUID):
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _set_tenant(dbapi_connection, _record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_tenant', %s, false)",
                (str(tenant_id),),
            )

    return engine


def _session(url: str, tenant_id: uuid.UUID) -> Session:
    return Session(_tenant_engine(url, tenant_id))


def _line(line_key: str = "internet") -> LineInput:
    return LineInput(
        line_key=line_key,
        description="Managed internet service",
        quantity=Decimal("1"),
        unit_price=Money.of("1000", NGN),
        discount=Money.of("50", NGN),
        taxes=(
            TaxSnapshotV1(
                tax_code="vat",
                source_version="tax-policy-4",
                taxable_basis=Money.of("950", NGN),
                rate=Decimal("0.075"),
                amount=Money.of("71.25", NGN),
            ),
        ),
        price_version_ref="price-version-7",
        terms_ref="terms-version-3",
        terms_snapshot=TermsSnapshotV1(
            version_ref="terms-version-3",
            values=(TermValueV1(name="minimum_term_months", value="12"),),
        ),
        specification_ref="service-specification-42",
        source_ref="quote:42",
        source_version="accepted:7",
    )


def _submit_command(
    *,
    key: str = "checkout-1",
    reference: str = "ORD-000001",
    customer: str = "customer-1",
    lines: tuple[LineInput, ...] | None = None,
    obligations: tuple[str, ...] = ("invoice:1", "settlement:1"),
) -> SubmitOrderCommand:
    return SubmitOrderCommand(
        idempotency_key=key,
        order_reference=reference,
        customer_ref=customer,
        currency_code="NGN",
        currency_minor_units=2,
        lines=lines or (_line(),),
        coverage_obligation_refs=obligations,
        submitted_by=ActorRef(actor_type="user", actor_id="operator-1"),
        submitted_at=NOW,
        source_ref="checkout:1",
        source_version="v1",
    )


def _accept(session: Session, tenant_id: uuid.UUID, order_id: uuid.UUID) -> None:
    accept_order(
        session,
        scope=TenantScope(tenant_id=tenant_id),
        command=AcceptOrderCommand(
            idempotency_key=f"accept:{order_id}",
            order_id=order_id,
            accepted_by=ActorRef(actor_type="user", actor_id="approver-1"),
            accepted_at=NOW,
        ),
    )


def _resolve(
    session: Session,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID,
    obligation: str,
) -> OrderCommandResult:
    return record_coverage_resolution(
        session,
        scope=TenantScope(tenant_id=tenant_id),
        command=RecordCoverageResolutionCommand(
            idempotency_key=f"coverage:{order_id}:{obligation}",
            order_id=order_id,
            obligation_ref=obligation,
            resolution_ref=f"billing:{obligation}",
            resolution_kind="billing.coverage_satisfied.v1",
            resolved_at=NOW,
            source_ref="dotmac-billing",
            source_version="position:17",
        ),
    )


def test_tenant_rls_hides_orders_lines_and_receipts_from_another_tenant(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    left, right = _make_tenants(admin_url)
    with _session(user_url, left) as session:
        result = submit_order(
            session,
            scope=TenantScope(tenant_id=left),
            command=_submit_command(),
        )
        _resolve(session, left, result.order.order_id, "invoice:1")
        session.commit()

    with _session(user_url, right) as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert session.scalar(select(func.count()).select_from(OrderLineSnapshot)) == 0
        assert (
            session.scalar(select(func.count()).select_from(CoverageResolutionReceipt))
            == 0
        )
        with pytest.raises(DBAPIError):
            submit_order(
                session,
                scope=TenantScope(tenant_id=left),
                command=_submit_command(
                    key="cross-tenant-write",
                    reference="ORD-CROSS-TENANT",
                ),
            )
        session.rollback()


def test_rls_sensitivity_without_the_guard_the_other_tenant_sees_the_order(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    left, right = _make_tenants(admin_url)
    with _session(user_url, left) as session:
        submit_order(
            session,
            scope=TenantScope(tenant_id=left),
            command=_submit_command(),
        )
        session.commit()

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP POLICY orders_tenant_isolation ON mod_orders.orders"))
        conn.execute(text("ALTER TABLE mod_orders.orders NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE mod_orders.orders DISABLE ROW LEVEL SECURITY"))
    engine.dispose()

    with _session(user_url, right) as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1


def test_frozen_line_and_header_cannot_be_rewritten_deleted_or_reopened(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant_id) as session:
        result = submit_order(
            session,
            scope=TenantScope(tenant_id=tenant_id),
            command=_submit_command(),
        )
        original = result.order
        session.commit()

    engine = create_engine(admin_url)
    for statement in (
        "UPDATE mod_orders.order_line_snapshots SET unit_price = 1",
        "DELETE FROM mod_orders.order_line_snapshots",
        "UPDATE mod_orders.orders SET snapshot_frozen_at = NULL",
        "UPDATE mod_orders.coverage_gates SET state = 'binding'",
        "UPDATE mod_orders.coverage_gates SET obligation_count = obligation_count + 1",
        "INSERT INTO mod_orders.order_line_snapshots "
        "(id, tenant_id, order_id, line_key, description, quantity, currency_code, "
        "currency_minor_units, unit_price, extended_price, discount_amount, "
        "tax_amount, tax_snapshot, line_total, price_version_ref, terms_ref, "
        "terms_snapshot, specification_ref, source_ref, source_version, "
        "snapshot_fingerprint) SELECT gen_random_uuid(), tenant_id, order_id, "
        "'late-line', description, quantity, currency_code, currency_minor_units, "
        "unit_price, extended_price, discount_amount, tax_amount, tax_snapshot, "
        "line_total, price_version_ref, terms_ref, terms_snapshot, "
        "specification_ref, source_ref, source_version, snapshot_fingerprint "
        "FROM mod_orders.order_line_snapshots LIMIT 1",
        "INSERT INTO mod_orders.coverage_obligations "
        "(id, tenant_id, gate_id, obligation_ref) SELECT gen_random_uuid(), "
        "tenant_id, id, 'late-obligation' FROM mod_orders.coverage_gates LIMIT 1",
    ):
        with engine.connect() as conn, pytest.raises(DBAPIError):
            with conn.begin():
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant_id)},
                )
                conn.execute(text(statement))
    engine.dispose()

    with _session(user_url, tenant_id) as session:
        replay = session.get(Order, original.order_id)
        line = session.scalar(select(OrderLineSnapshot))
        assert replay is not None and line is not None
        assert replay.total_amount == original.totals.total.amount
        assert line.unit_price == original.lines[0].unit_price.amount


def test_online_roles_cannot_delete_any_order_owned_state(
    scratch: tuple[str, str],
) -> None:
    admin_url, _user_url = scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        for role in ("app_user", "platform_api"):
            for table in module.tables:
                assert not conn.scalar(
                    text("SELECT has_table_privilege(" ":role, :table, 'DELETE')"),
                    {"role": role, "table": f"mod_orders.{table}"},
                ), (role, table)
    engine.dispose()


def test_a_direct_half_snapshot_is_refused_at_transaction_commit(
    scratch: tuple[str, str],
) -> None:
    admin_url, _user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    engine = create_engine(admin_url)

    with engine.connect() as conn, pytest.raises(DBAPIError):
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_orders.orders ("
                    "id, tenant_id, order_reference, customer_ref, state, "
                    "currency_code, currency_minor_units, subtotal_amount, "
                    "discount_amount, tax_amount, total_amount, "
                    "snapshot_fingerprint, snapshot_frozen_at, "
                    "submitted_actor_type, submitted_actor_id, submitted_at"
                    ") VALUES ("
                    "gen_random_uuid(), CAST(:tenant AS uuid), 'DIRECT-HALF', "
                    "'customer-1', 'submitted', 'NGN', 2, 0, 0, 0, 0, "
                    "repeat('0', 64), now(), 'user', 'operator-1', now()"
                    ")"
                ),
                {"tenant": str(tenant_id)},
            )
    engine.dispose()


def test_submission_replay_conflict_and_rollback_share_the_kernel_ledger(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant_id)
    command = _submit_command()

    with _session(user_url, tenant_id) as session:
        with pytest.raises(OrderError) as fx_range:
            submit_order(
                session,
                scope=scope,
                command=replace(
                    command,
                    idempotency_key="invalid-fx",
                    order_reference="ORD-INVALID-FX",
                    fx_snapshot=FxSnapshotV1(
                        base_currency_code="USD",
                        quote_currency_code="NGN",
                        rate=Decimal("100000000000000000000"),
                        rate_ref="rate:1",
                        source="treasury",
                        as_of=NOW,
                    ),
                ),
            )
        assert fx_range.value.code == "fx_rate_out_of_range"
        first = submit_order(session, scope=scope, command=command)
        assert first.order.source_ref == "checkout:1"
        assert first.order.source_version == "v1"
        assert first.order.submitted_by.actor_id == "operator-1"
        assert first.order.submitted_at == NOW
        assert first.order.coverage.obligation_refs == (
            "invoice:1",
            "settlement:1",
        )
        assert first.order.coverage.resolutions == ()
        session.commit()
    with _session(user_url, tenant_id) as session:
        replay = submit_order(session, scope=scope, command=command)
        assert replay.replayed is True
        assert replay.order.order_id == first.order.order_id
        with pytest.raises(IdempotencyConflict):
            submit_order(
                session,
                scope=scope,
                command=_submit_command(customer="different-customer"),
            )
        with pytest.raises(IdempotencyConflict):
            submit_order(
                session,
                scope=scope,
                command=replace(
                    command,
                    submitted_by=ActorRef(
                        actor_type="user",
                        actor_id="different-operator",
                    ),
                ),
            )
        with pytest.raises(OrderError) as invalid_actor:
            submit_order(
                session,
                scope=scope,
                command=replace(
                    command,
                    submitted_by=ActorRef(actor_type="invented"),
                ),
            )
        assert invalid_actor.value.code == "invalid_actor"
        with pytest.raises(OrderConflict) as exc:
            submit_order(
                session,
                scope=scope,
                command=replace(command, idempotency_key="new-checkout-key"),
            )
        assert exc.value.code == "order_identity_conflict"
        session.rollback()

    with _session(user_url, tenant_id) as session:
        submit_order(
            session,
            scope=scope,
            command=_submit_command(key="rolled-back", reference="ORD-ROLLBACK"),
        )
        session.rollback()
    with _session(user_url, tenant_id) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Order)
                .where(Order.order_reference == "ORD-ROLLBACK")
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.key == "rolled-back")
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.payload["order_reference"].as_string() == "ORD-ROLLBACK"
                )
            )
            == 0
        )


def test_concurrent_same_checkout_creates_one_order_and_replays_the_winner(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    barrier = threading.Barrier(2)
    results: list[tuple[uuid.UUID, bool]] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with _session(user_url, tenant_id) as session:
                session.execute(text("SELECT 1"))
                barrier.wait(timeout=15)
                result = submit_order(
                    session,
                    scope=TenantScope(tenant_id=tenant_id),
                    command=_submit_command(),
                )
                session.commit()
                results.append((result.order.order_id, result.replayed))
        except BaseException as exc:
            errors.append(exc)
            with contextlib.suppress(Exception):
                barrier.abort()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors
    assert len({order_id for order_id, _ in results}) == 1
    assert sorted(replayed for _, replayed in results) == [False, True]
    with _session(user_url, tenant_id) as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1


def test_partial_out_of_order_and_unregistered_coverage_converge_once(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant_id) as session:
        submitted = submit_order(
            session,
            scope=TenantScope(tenant_id=tenant_id),
            command=_submit_command(lines=(_line("a"), _line("b"))),
        )
        order_id = submitted.order.order_id
        _accept(session, tenant_id, order_id)
        partial = _resolve(session, tenant_id, order_id, "settlement:1")
        assert partial.coverage_resolution is not None
        assert partial.coverage_resolution.obligation_ref == "settlement:1"
        assert partial.order.coverage.state == "open"
        assert len(partial.order.coverage.resolutions) == 1
        gate = session.scalar(select(CoverageGate))
        assert gate is not None
        assert gate.state == "open"
        assert session.scalar(select(func.count()).select_from(FulfillmentRequest)) == 0

        with pytest.raises(OrderConflict) as duplicate_resolution:
            record_coverage_resolution(
                session,
                scope=TenantScope(tenant_id=tenant_id),
                command=RecordCoverageResolutionCommand(
                    idempotency_key="coverage:duplicate-resolution-ref",
                    order_id=order_id,
                    obligation_ref="invoice:1",
                    resolution_ref="billing:settlement:1",
                    resolution_kind="billing.coverage_satisfied.v1",
                    resolved_at=NOW,
                    source_ref="dotmac-billing",
                    source_version="position:17",
                ),
            )
        assert (
            duplicate_resolution.value.code == "coverage_resolution_identity_conflict"
        )
        completed = _resolve(session, tenant_id, order_id, "invoice:1")
        assert completed.coverage_resolution is not None
        assert completed.coverage_resolution.source_ref == "dotmac-billing"
        assert completed.order.accepted_by is not None
        assert completed.order.accepted_by.actor_id == "approver-1"
        assert completed.order.accepted_at == NOW
        assert completed.order.covered_at == NOW
        assert completed.order.coverage.state == "satisfied"
        assert completed.order.coverage.satisfied_at == NOW
        assert len(completed.order.coverage.resolutions) == 2
        assert gate.state == "satisfied"
        assert session.scalar(select(func.count()).select_from(FulfillmentRequest)) == 2

        with pytest.raises(OrderError) as exc:
            _resolve(session, tenant_id, order_id, "not-in-the-bound-set")
        assert exc.value.code == "unregistered_coverage_obligation"
        assert (
            session.scalar(select(func.count()).select_from(CoverageResolutionReceipt))
            == 2
        )
        session.commit()


def test_concurrent_final_coverage_resolutions_publish_each_line_once(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant_id) as session:
        submitted = submit_order(
            session,
            scope=TenantScope(tenant_id=tenant_id),
            command=_submit_command(lines=(_line("a"), _line("b"))),
        )
        order_id = submitted.order.order_id
        _accept(session, tenant_id, order_id)
        session.commit()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(obligation: str) -> None:
        try:
            with _session(user_url, tenant_id) as session:
                session.execute(text("SELECT 1"))
                barrier.wait(timeout=15)
                _resolve(session, tenant_id, order_id, obligation)
                session.commit()
        except BaseException as exc:
            errors.append(exc)
            with contextlib.suppress(Exception):
                barrier.abort()

    threads = [
        threading.Thread(target=worker, args=(obligation,))
        for obligation in ("invoice:1", "settlement:1")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors
    with _session(user_url, tenant_id) as session:
        gate = session.scalar(select(CoverageGate))
        assert gate is not None
        assert gate.state == "satisfied"
        assert gate.resolved_count == 2
        assert session.scalar(select(func.count()).select_from(FulfillmentRequest)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == "orders.fulfillment_requested.v1")
            )
            == 2
        )
        assert (
            session.scalar(select(func.count()).select_from(CoverageResolutionReceipt))
            == 2
        )
        session.commit()

    with _session(user_url, tenant_id) as session:
        replay = _resolve(session, tenant_id, order_id, "invoice:1")
        assert replay.replayed is True
        assert replay.coverage_resolution is not None
        assert replay.coverage_resolution.resolution_ref == "billing:invoice:1"
        assert (
            session.scalar(select(func.count()).select_from(CoverageResolutionReceipt))
            == 2
        )
        assert session.scalar(select(func.count()).select_from(FulfillmentRequest)) == 2


def test_lost_fulfillment_publication_repairs_without_a_second_request_identity(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant_id) as session:
        submitted = submit_order(
            session,
            scope=TenantScope(tenant_id=tenant_id),
            command=_submit_command(obligations=("invoice:1",)),
        )
        order_id = submitted.order.order_id
        _accept(session, tenant_id, order_id)
        _resolve(session, tenant_id, order_id, "invoice:1")
        request = session.scalar(select(FulfillmentRequest))
        assert request is not None and request.last_outbox_event_id is not None
        request_id = request.id
        first_event_id = request.last_outbox_event_id
        session.commit()

    engine = create_engine(admin_url)
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        conn.execute(
            text(
                "UPDATE public.outbox_events SET status = 'dead' "
                "WHERE id = CAST(:event_id AS uuid)"
            ),
            {"event_id": str(first_event_id)},
        )
    engine.dispose()

    with _session(user_url, tenant_id) as session:
        report = reconcile_fulfillment_publications(
            session,
            scope=TenantScope(tenant_id=tenant_id),
            order_id=order_id,
            observed_at=NOW,
        )
        request = session.scalar(select(FulfillmentRequest))
        assert request is not None
        assert report.created_request_ids == ()
        assert report.restaged_request_ids == (request_id,)
        assert request.id == request_id
        assert request.publication_count == 2
        assert request.last_outbox_event_id != first_event_id


def test_concurrent_fulfillment_acceptances_keep_one_ordered_timeline(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant_id)
    with _session(user_url, tenant_id) as session:
        submitted = submit_order(
            session,
            scope=scope,
            command=_submit_command(
                lines=(_line("a"), _line("b")),
                obligations=("invoice:1",),
            ),
        )
        _accept(session, tenant_id, submitted.order.order_id)
        _resolve(session, tenant_id, submitted.order.order_id, "invoice:1")
        request_ids = tuple(session.scalars(select(FulfillmentRequest.id)))
        session.commit()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(request_id: uuid.UUID) -> None:
        try:
            with _session(user_url, tenant_id) as session:
                session.execute(text("SELECT 1"))
                barrier.wait(timeout=15)
                acknowledge_fulfillment(
                    session,
                    scope=scope,
                    command=AcknowledgeFulfillmentCommand(
                        idempotency_key=f"ack:{request_id}",
                        request_id=request_id,
                        acceptance_ref=f"fulfillment:{request_id}",
                        accepted_at=NOW,
                    ),
                )
                session.commit()
        except BaseException as exc:
            errors.append(exc)
            with contextlib.suppress(Exception):
                barrier.abort()

    threads = [
        threading.Thread(target=worker, args=(request_id,))
        for request_id in request_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors
    with _session(user_url, tenant_id) as session:
        timeline = get_order_timeline(
            session,
            scope=scope,
            order_id=submitted.order.order_id,
        )
        sequences = [event.sequence for event in timeline]
        assert sequences == list(range(1, len(sequences) + 1))
        assert (
            sum(
                event.event_type == "orders.fulfillment_accepted.v1"
                for event in timeline
            )
            == 2
        )


def test_cancellation_after_fulfillment_acceptance_is_recorded_and_refused(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant_id)
    with _session(user_url, tenant_id) as session:
        submitted = submit_order(
            session,
            scope=scope,
            command=_submit_command(obligations=("invoice:1",)),
        )
        order_id = submitted.order.order_id
        _accept(session, tenant_id, order_id)
        _resolve(session, tenant_id, order_id, "invoice:1")
        request = session.scalar(select(FulfillmentRequest))
        assert request is not None
        acknowledged = acknowledge_fulfillment(
            session,
            scope=scope,
            command=AcknowledgeFulfillmentCommand(
                idempotency_key="fulfillment-ack-1",
                request_id=request.id,
                acceptance_ref="fulfillment:accepted:1",
                accepted_at=NOW,
            ),
        )
        assert acknowledged.fulfillment_requests[0].state == "accepted"
        assert (
            acknowledged.fulfillment_requests[0].acceptance_ref
            == "fulfillment:accepted:1"
        )
        with pytest.raises(OrderConflict) as acceptance_conflict:
            acknowledge_fulfillment(
                session,
                scope=scope,
                command=AcknowledgeFulfillmentCommand(
                    idempotency_key="fulfillment-ack-conflict",
                    request_id=request.id,
                    acceptance_ref="fulfillment:accepted:1",
                    accepted_at=NOW.replace(hour=9),
                ),
            )
        assert acceptance_conflict.value.code == "fulfillment_acceptance_conflict"
        result = cancel_order(
            session,
            scope=scope,
            command=CancelOrderCommand(
                idempotency_key="cancel-1",
                order_id=order_id,
                cancelled_by=ActorRef(actor_type="user", actor_id="operator-1"),
                cancelled_at=NOW,
                reason="customer request",
            ),
        )
        assert result.refused is True
        assert (
            result.refusal_code == "cancellation_refused_after_fulfillment_acceptance"
        )
        assert result.order.state == "fulfillment_requested"
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "orders.cancellation_refused")
        )
        assert audit is not None
        assert audit.is_success is False
        session.commit()


def test_cancellation_before_fulfillment_acceptance_preserves_frozen_totals(
    scratch: tuple[str, str],
) -> None:
    admin_url, user_url = scratch
    (tenant_id,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant_id)
    with _session(user_url, tenant_id) as session:
        submitted = submit_order(
            session,
            scope=scope,
            command=_submit_command(),
        )
        with pytest.raises(OrderError) as invalid_acceptance:
            accept_order(
                session,
                scope=scope,
                command=AcceptOrderCommand(
                    idempotency_key="accept-as-cancelled",
                    order_id=submitted.order.order_id,
                    accepted_by=ActorRef(
                        actor_type="user",
                        actor_id="approver-1",
                    ),
                    accepted_at=NOW,
                    target_state="cancelled",
                ),
            )
        assert invalid_acceptance.value.code == "invalid_acceptance_target"
        with pytest.raises(OrderError) as invalid_cancellation:
            cancel_order(
                session,
                scope=scope,
                command=CancelOrderCommand(
                    idempotency_key="cancel-as-accepted",
                    order_id=submitted.order.order_id,
                    cancelled_by=ActorRef(
                        actor_type="user",
                        actor_id="operator-1",
                    ),
                    cancelled_at=NOW,
                    reason="customer request",
                    target_state="accepted",
                ),
            )
        assert invalid_cancellation.value.code == "invalid_cancellation_target"
        before = submitted.order.totals
        result = cancel_order(
            session,
            scope=scope,
            command=CancelOrderCommand(
                idempotency_key="cancel-before-fulfillment",
                order_id=submitted.order.order_id,
                cancelled_by=ActorRef(actor_type="user", actor_id="operator-1"),
                cancelled_at=NOW,
                reason="customer request",
            ),
        )

        assert result.refused is False
        assert result.order.state == "cancelled"
        assert result.order.totals == before
        assert result.order.cancelled_by is not None
        assert result.order.cancelled_by.actor_id == "operator-1"
        assert result.order.cancellation_reason == "customer request"
        timeline = get_order_timeline(
            session,
            scope=scope,
            order_id=submitted.order.order_id,
        )
        assert [event.event_type for event in timeline] == [
            "orders.order_submitted.v1",
            "orders.order_cancelled.v1",
        ]
        assert [event.sequence for event in timeline] == [1, 2]
        session.commit()


def test_outbox_status_fixture_uses_the_real_dead_letter_value() -> None:
    """Sensitivity for the repair test's terminal-status setup."""
    assert OutboxStatus.DEAD.value == "dead"
