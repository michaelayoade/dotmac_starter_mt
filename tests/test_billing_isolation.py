"""Live PostgreSQL canaries for Billing's two declared persistence planes.

The pure engine tests prove arithmetic.  These tests prove the properties that
SQLite and mocks cannot: FORCE RLS, the complete platform-role revoke, database
immutability, transactional idempotency under concurrency, and rebuild/hash
agreement over committed source effects.
"""

from __future__ import annotations

# SQL identifiers are fixed tenant/platform test table names; values are bound.
# ruff: noqa: S608
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_billing import GetInvoiceDocumentFactV1, get_invoice_document_fact, models
from dotmac_billing.commands import (
    AllocationCommand,
    ApplyAvailableCreditCommand,
    CreateDraftDocument,
    CreateMultiLineDraftDocument,
    DraftDocumentLine,
    IssueCreditNote,
    IssueDocument,
    RefundCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.contracts import (
    AcceptRatedObligationV1,
    AcceptSettlementV1,
    AppliedFxSnapshotV1,
    AppliedTaxSnapshotV1,
    DueDateBasisStatus,
    DueDateBasisV1,
    PartyDocumentSnapshotV1,
    PartyTaxIdentitySnapshotV1,
    PaymentInstructionsSnapshotV1,
    PostalAddressSnapshotV1,
    PresentationAssetReferenceV1,
    RecordDocumentArtifactV1,
    RepairDocumentArtifactV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
    SettlementFundingLane,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.service import (
    accept_rated_obligation,
    accept_settlement,
    allocate_settlement,
    apply_available_credit,
    create_billing_account,
    create_draft_document,
    create_multi_line_draft_document,
    issue_credit_note,
    issue_document,
    rebuild_receivable_position,
    record_document_artifact,
    refund_settlement,
    repair_document_artifact,
    reverse_posting_group,
    void_document,
)
from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict, fingerprint_of
from dotmac_kernel.idempotency_models import (
    IdempotencyRecord,
    PlatformIdempotencyRecord,
)
from dotmac_kernel.messaging.models import OutboxEvent, PlatformOutboxEvent
from dotmac_kernel.money import Currency, Money
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import ForeignKeyConstraint, create_engine, event, func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
BILLING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-billing/src/dotmac_billing/migrations/versions"
)

NOW = datetime(2026, 8, 17, 10, tzinfo=UTC)
PERIOD_START = datetime(2026, 8, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 9, 1, tzinfo=UTC)
TENANT_TABLES = models.TENANT_TABLES
PLATFORM_TABLES = models.PLATFORM_TABLES
ONLINE_MUTABLE_TABLES = frozenset({"documents", "document_artifacts"})


def _expected_online_privileges(table_name: str) -> frozenset[str]:
    base_name = table_name.removeprefix("platform_")
    privileges = {"SELECT", "INSERT"}
    if base_name in {
        "opening_windows",
        "opening_window_admissions",
        "opening_window_seals",
        "opening_cash_lots",
        "opening_receivable_bases",
        "opening_historical_discharge_claims",
    }:
        privileges.remove("INSERT")
    if base_name in ONLINE_MUTABLE_TABLES:
        privileges.add("UPDATE")
    return frozenset(privileges)


def _actual_table_privileges(
    connection: Connection, *, role: str, table: str
) -> frozenset[str]:
    rows = connection.execute(
        text(
            "SELECT privilege FROM unnest(ARRAY["
            "'SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'"
            "]) AS requested(privilege) "
            "WHERE has_table_privilege(:role, :table, privilege)"
        ),
        {"role": role, "table": table},
    )
    return frozenset(row.privilege for row in rows)


def _actual_column_privileges(
    connection: Connection,
    *,
    role: str,
    table_name: str,
    privilege: str,
) -> frozenset[str]:
    qualified = f"mod_billing.{table_name}"
    rows = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'mod_billing' AND table_name = :table_name "
            "AND has_column_privilege(:role, :qualified, column_name, :privilege)"
        ),
        {
            "role": role,
            "qualified": qualified,
            "table_name": table_name,
            "privilege": privilege,
        },
    )
    return frozenset(row.column_name for row in rows)


def _actual_all_column_privileges(
    connection: Connection, *, role: str, table_name: str
) -> frozenset[tuple[str, str]]:
    qualified = f"mod_billing.{table_name}"
    rows = connection.execute(
        text(
            "SELECT column_name, privilege FROM information_schema.columns "
            "CROSS JOIN unnest(ARRAY["
            "'SELECT','INSERT','UPDATE','REFERENCES'"
            "]) AS requested(privilege) "
            "WHERE table_schema = 'mod_billing' AND table_name = :table_name "
            "AND has_column_privilege(:role, :qualified, column_name, privilege)"
        ),
        {"role": role, "qualified": qualified, "table_name": table_name},
    )
    return frozenset((row.column_name, row.privilege) for row in rows)


def _table_columns(connection: Connection, table_name: str) -> frozenset[str]:
    rows = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'mod_billing' AND table_name = :table"
        ),
        {"table": table_name},
    )
    return frozenset(row.column_name for row in rows)


class _Numbering:
    """Provider-neutral test adapter; Billing never imports Numbering itself."""

    def allocate(
        self,
        _db: Session,
        *,
        scope: Scope,
        series_code: str,
        reference_date: date,
        idempotency_key: str,
    ) -> str:
        scope_code = "tenant" if isinstance(scope, TenantScope) else "platform"
        scope_identity = (
            f"tenant:{scope.tenant_id}"
            if isinstance(scope, TenantScope)
            else "platform"
        )
        identity = repr((scope_identity, series_code, idempotency_key))
        number = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        return f"{series_code.upper()}-{scope_code}-{number}"


NUMBERING = _Numbering()


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — Billing plane proofs need Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@contextmanager
def _scratch_database(
    planes: tuple[ModulePlane, ...],
    *,
    target_revision: str = "heads",
) -> Iterator[tuple[str, str, str]]:
    superuser = _superuser_url()
    name = f"billing_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    old_migration_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {BILLING_VERSIONS}",
        )
        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="billing", planes=planes),
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, target_revision)
        yield (
            admin_url,
            _url_for(superuser, name, user="app_user"),
            _url_for(superuser, name, user="platform_api"),
        )
    finally:
        if old_migration_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = old_migration_url
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


@pytest.fixture(scope="module")
def scratch() -> Iterator[tuple[str, str, str]]:
    with _scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _create_tenants(admin_url: str, count: int = 2) -> list[uuid.UUID]:
    tenant_ids = [uuid.uuid4() for _ in range(count)]
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        for tenant_id in tenant_ids:
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, name, slug, is_active) "
                    "VALUES (:id, :name, :slug, true)"
                ),
                {
                    "id": tenant_id,
                    "name": f"billing-{tenant_id.hex[:8]}",
                    "slug": f"billing-{tenant_id.hex[:8]}",
                },
            )
    engine.dispose()
    return tenant_ids


def _tenant_engine(url: str, tenant_id: uuid.UUID) -> Engine:
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _set_tenant(dbapi_connection, _record) -> None:
        with dbapi_connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_tenant', %s, false)",
                (str(tenant_id),),
            )
        # Session-level SET inside the implicit connect transaction is
        # reverted when that transaction later rolls back; commit it so
        # rollback proofs keep their tenant visibility.
        dbapi_connection.commit()

    return engine


@contextmanager
def _session(url: str, scope: Scope) -> Iterator[Session]:
    engine = (
        _tenant_engine(url, scope.tenant_id)
        if isinstance(scope, TenantScope)
        else create_engine(url)
    )
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _money(amount: str, currency: str = "NGN") -> Money:
    return Money.of(amount, Currency(currency, 2))


def _basis() -> DueDateBasisV1:
    return DueDateBasisV1(
        status=DueDateBasisStatus.VERIFIED,
        source_authority="contract.v1",
        evidence_ref="contract:line:1:v1",
        payment_terms_code="net_30",
        payment_terms_version="1",
        issued_at=NOW,
        effective_at=NOW,
        timezone="Africa/Lagos",
        derivation_policy="calendar_days",
        derivation_version="1",
    )


def _obligation_command(
    account_id: uuid.UUID,
    *,
    scope: Scope,
    source_fact_id: str = "occurrence-1",
    currency: str = "NGN",
) -> AcceptRatedObligationV1:
    return AcceptRatedObligationV1(
        scope=scope,
        billing_account_id=account_id,
        contract_line_ref="contract-line-1",
        contract_version="v1",
        charge_component="recurring_access",
        source_system="subscriptions",
        source_kind="recurring_charge_occurrence",
        source_fact_id=source_fact_id,
        source_fact_version="1",
        subject_ref="customer:example",
        service_ref="service:example",
        service_period=ServicePeriodEvidenceV1(
            status=ServicePeriodStatus.VERIFIED,
            starts_at=PERIOD_START,
            ends_at=PERIOD_END,
        ),
        collection_timing="arrears",
        pre_tax_amount=_money("100.00", currency),
        tax_amount=_money("20.00", currency),
        total_amount=_money("120.00", currency),
        rated_at=NOW,
        price_version_id="price:v1",
        tax_snapshots=(
            AppliedTaxSnapshotV1(
                treatment_code="standard",
                jurisdiction_code="source-supplied-zone",
                policy_id="tax-policy",
                policy_version="1",
                rate=Decimal("0.200000"),
                taxable_basis=_money("100.00", currency),
                tax_amount=_money("20.00", currency),
            ),
        ),
        fx_snapshot=AppliedFxSnapshotV1(
            observation_id="fx-observation-1",
            observation_version="1",
            base_currency=currency,
            quote_currency=currency,
            rate=Decimal("1.000000"),
            rate_purpose="document_currency",
            observed_at=NOW,
            effective_at=NOW,
            rounding_policy="minor_units",
            provenance="source-supplied",
        ),
    )


def _draft_command(obligation_id: uuid.UUID) -> CreateDraftDocument:
    return CreateDraftDocument(
        obligation_id=obligation_id,
        description="Monthly access",
        quantity=Decimal("1.000000"),
        unit_code="month",
        seller_snapshot=PartyDocumentSnapshotV1(
            legal_name="Dotmac Technologies Ltd",
            address=PostalAddressSnapshotV1(
                line_one="Plot 1",
                city="Abuja",
                country_code="NGA",
            ),
        ),
        customer_snapshot=PartyDocumentSnapshotV1(
            legal_name="Customer Ltd",
            address=PostalAddressSnapshotV1(
                line_one="Plot 2",
                city="Abuja",
                country_code="NGA",
            ),
        ),
        payment_instructions=PaymentInstructionsSnapshotV1(
            method_code="bank_transfer",
            bank_name="Source supplied bank",
            account_name="Dotmac Technologies Ltd",
            account_reference="source-supplied-reference",
        ),
        brand_asset=PresentationAssetReferenceV1.none(),
        locale="en-NG",
        timezone="Africa/Lagos",
        document_profile_code="invoice.standard",
        document_profile_version="1",
        due_date_basis=_basis(),
        party_tax_identities=(
            PartyTaxIdentitySnapshotV1(
                party_role="seller",
                identity_type="tax_id",
                identity_value="snapshot-value",
                country_code="NGA",
                source_authority="party.v1",
                source_version="1",
            ),
        ),
    )


def _issue_command(document_id: uuid.UUID) -> IssueDocument:
    return IssueDocument(
        document_id=document_id,
        series_code="invoice",
        reference_date=date(2026, 8, 17),
        due_at=datetime(2026, 9, 16, 10, tzinfo=UTC),
        due_date_basis=_basis(),
        actor_ref="billing-test",
        correlation_id=f"issue:{document_id}",
    )


def _settlement_command(
    account_id: uuid.UUID,
    *,
    scope: Scope,
    source_key: str = "settlement-1",
    amount: str = "120.00",
) -> AcceptSettlementV1:
    return AcceptSettlementV1(
        scope=scope,
        billing_account_id=account_id,
        source_system="integrator",
        source_settlement_key=source_key,
        source_version="1",
        amount=_money(amount),
        occurred_at=NOW,
        observed_at=NOW,
        confirmation_evidence="provider_settled",
        funding_lane=SettlementFundingLane.AVAILABLE_CREDIT,
    )


def _seed_invoice(session: Session, scope: Scope, *, suffix: str = "1"):
    account = create_billing_account(
        session,
        scope=scope,
        external_account_ref=f"customer-{suffix}",
        currency="NGN",
        minor_units=2,
    )
    obligation = accept_rated_obligation(
        session,
        scope=scope,
        command=_obligation_command(
            account.id,
            scope=scope,
            source_fact_id=f"occurrence-{suffix}",
        ),
        accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
    )
    draft = create_draft_document(
        session, scope=scope, command=_draft_command(obligation.id)
    )
    issued = issue_document(
        session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
    )
    session.commit()
    return account, obligation, issued


def test_catalog_proves_force_rls_and_exact_platform_revoke(scratch) -> None:
    admin_url, _, _ = scratch
    engine = create_engine(admin_url)
    with engine.connect() as connection:
        for table_name in TENANT_TABLES:
            column = connection.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'mod_billing' AND table_name = :table "
                    "AND column_name = 'tenant_id'"
                ),
                {"table": table_name},
            ).one()
            assert column == ("uuid", "NO")
            rls = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'mod_billing' AND c.relname = :table"
                ),
                {"table": table_name},
            ).one()
            assert rls == (True, True)
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_policies "
                        "WHERE schemaname = 'mod_billing' AND tablename = :table"
                    ),
                    {"table": table_name},
                ).scalar_one()
                == 1
            )
            qualified = f"mod_billing.{table_name}"
            assert _actual_table_privileges(
                connection,
                role="app_user",
                table=qualified,
            ) == _expected_online_privileges(table_name)
            columns = _table_columns(connection, table_name)
            update_columns = _actual_column_privileges(
                connection,
                role="app_user",
                table_name=table_name,
                privilege="UPDATE",
            )
            expected_update_columns = (
                columns
                if table_name in ONLINE_MUTABLE_TABLES
                else {"id"}
                if table_name
                in {
                    "billing_accounts",
                    "non_cash_credit_grants",
                    "non_cash_credit_effects",
                }
                else set()
            )
            assert update_columns == expected_update_columns

        assert connection.execute(
            text("SELECT has_schema_privilege('platform_api', 'mod_billing', 'USAGE')")
        ).scalar_one()
        for table_name in PLATFORM_TABLES:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = 'mod_billing' AND table_name = :table "
                        "AND column_name = 'tenant_id'"
                    ),
                    {"table": table_name},
                ).scalar_one()
                == 0
            )
            rls = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'mod_billing' AND c.relname = :table"
                ),
                {"table": table_name},
            ).one()
            assert rls == (False, False)
            qualified = f"mod_billing.{table_name}"
            assert not _actual_table_privileges(
                connection,
                role="app_user",
                table=qualified,
            )
            assert not _actual_all_column_privileges(
                connection,
                role="app_user",
                table_name=table_name,
            )
            assert _actual_table_privileges(
                connection,
                role="platform_api",
                table=qualified,
            ) == _expected_online_privileges(table_name)
            columns = _table_columns(connection, table_name)
            update_columns = _actual_column_privileges(
                connection,
                role="platform_api",
                table_name=table_name,
                privilege="UPDATE",
            )
            expected_update_columns = (
                columns
                if table_name.removeprefix("platform_") in ONLINE_MUTABLE_TABLES
                else {"id"}
                if table_name
                in {
                    "platform_billing_accounts",
                    "platform_non_cash_credit_grants",
                    "platform_non_cash_credit_effects",
                }
                else set()
            )
            assert update_columns == expected_update_columns
    engine.dispose()


def test_migrated_internal_foreign_keys_match_the_model_contract(scratch) -> None:
    """The migration and ORM must enforce the same complete relationship graph."""

    expected: set[tuple[object, ...]] = set()
    for table in models.Base.metadata.tables.values():
        if table.schema != models.SCHEMA:
            continue
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            elements = tuple(constraint.elements)
            target = elements[0].column.table
            if target.schema != models.SCHEMA:
                continue
            expected.add(
                (
                    table.name,
                    tuple(element.parent.name for element in elements),
                    target.name,
                    tuple(element.column.name for element in elements),
                )
            )

    admin_url, _, _ = scratch
    engine = create_engine(admin_url)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT src.relname AS source_table, dst.relname AS target_table, "
                "ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY "
                "AS key(attnum, ord) JOIN pg_attribute a "
                "ON a.attrelid = c.conrelid AND a.attnum = key.attnum "
                "ORDER BY key.ord) AS source_columns, "
                "ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY "
                "AS key(attnum, ord) JOIN pg_attribute a "
                "ON a.attrelid = c.confrelid AND a.attnum = key.attnum "
                "ORDER BY key.ord) AS target_columns "
                "FROM pg_constraint c "
                "JOIN pg_class src ON src.oid = c.conrelid "
                "JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace "
                "JOIN pg_class dst ON dst.oid = c.confrelid "
                "JOIN pg_namespace dst_ns ON dst_ns.oid = dst.relnamespace "
                "WHERE c.contype = 'f' AND src_ns.nspname = 'mod_billing' "
                "AND dst_ns.nspname = 'mod_billing'"
            )
        ).all()
    engine.dispose()

    actual = {
        (
            row.source_table,
            tuple(row.source_columns),
            row.target_table,
            tuple(row.target_columns),
        )
        for row in rows
    }
    assert actual == expected


def test_catalog_canary_is_sensitive_to_a_missing_force_and_revoke(scratch) -> None:
    admin_url, _, _ = scratch
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(
            text("ALTER TABLE mod_billing.billing_accounts NO FORCE ROW LEVEL SECURITY")
        )
        connection.execute(
            text("GRANT SELECT ON mod_billing.platform_billing_accounts TO app_user")
        )
        try:
            force = connection.execute(
                text(
                    "SELECT relforcerowsecurity FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace WHERE n.nspname = 'mod_billing' "
                    "AND c.relname = 'billing_accounts'"
                )
            ).scalar_one()
            leaked = connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'app_user', 'mod_billing.platform_billing_accounts', 'SELECT')"
                )
            ).scalar_one()
            assert (force, leaked) == (False, True)
        finally:
            connection.execute(
                text(
                    "ALTER TABLE mod_billing.billing_accounts FORCE ROW LEVEL SECURITY"
                )
            )
            connection.execute(
                text(
                    "REVOKE SELECT ON mod_billing.platform_billing_accounts "
                    "FROM app_user"
                )
            )
    engine.dispose()


def test_cross_tenant_and_wrong_plane_access_are_refused(scratch) -> None:
    admin_url, tenant_url, platform_url = scratch
    left, right = _create_tenants(admin_url)
    with _session(tenant_url, TenantScope(left)) as session:
        create_billing_account(
            session,
            scope=TenantScope(left),
            external_account_ref="left-only",
            currency="NGN",
            minor_units=2,
        )
        session.commit()
    with _session(tenant_url, TenantScope(right)) as session:
        assert (
            session.scalar(select(func.count()).select_from(models.BillingAccount)) == 0
        )

    tenant_engine = create_engine(tenant_url)
    with tenant_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text("SELECT count(*) FROM mod_billing.platform_billing_accounts")
            )
    tenant_engine.dispose()

    platform_engine = create_engine(platform_url)
    with platform_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text("SELECT count(*) FROM mod_billing.billing_accounts")
            )
    platform_engine.dispose()


def test_platform_only_composition_needs_no_tenant_or_fake_tenant() -> None:
    with _scratch_database((ModulePlane.PLATFORM,)) as (admin_url, _, platform_url):
        admin = create_engine(admin_url)
        with admin.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'mod_billing'"
                    )
                )
            }
            assert tables == set(PLATFORM_TABLES)
            assert (
                connection.execute(
                    text("SELECT count(*) FROM public.tenants")
                ).scalar_one()
                == 0
            )
        admin.dispose()

        with _session(platform_url, PlatformScope()) as session:
            account = create_billing_account(
                session,
                scope=PlatformScope(),
                external_account_ref="vendor-cp-account",
                currency="NGN",
                minor_units=2,
            )
            session.commit()
            assert account.id is not None


def test_obligation_corrections_are_append_only_single_successor_and_pre_document(
    scratch,
) -> None:
    _, _, platform_url = scratch
    scope = PlatformScope()
    with _session(platform_url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref="correction-account",
            currency="NGN",
            minor_units=2,
        )
        original = accept_rated_obligation(
            session,
            scope=scope,
            command=_obligation_command(
                account.id,
                scope=scope,
                source_fact_id="original-occurrence",
            ),
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        correction_command = replace(
            _obligation_command(
                account.id,
                scope=scope,
                source_fact_id="corrected-occurrence",
            ),
            source_fact_version="2",
            supersedes_obligation_id=original.id,
        )
        corrected = accept_rated_obligation(
            session,
            scope=scope,
            command=correction_command,
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        replay = accept_rated_obligation(
            session,
            scope=scope,
            command=correction_command,
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        assert replay.id == corrected.id

        with pytest.raises(BillingConflict, match="only one correction successor"):
            accept_rated_obligation(
                session,
                scope=scope,
                command=replace(
                    correction_command,
                    source_fact_id="competing-correction",
                    source_fact_version="3",
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )

        create_draft_document(
            session,
            scope=scope,
            command=_draft_command(corrected.id),
        )
        with pytest.raises(BillingConflict, match="corrected by credit note"):
            accept_rated_obligation(
                session,
                scope=scope,
                command=replace(
                    correction_command,
                    source_fact_id="too-late-correction",
                    source_fact_version="4",
                    supersedes_obligation_id=corrected.id,
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )

        session.commit()


def test_concurrent_duplicate_obligation_issuance_and_settlement_replay_once(
    scratch,
) -> None:
    admin_url, tenant_url, _ = scratch
    (tenant_id,) = _create_tenants(admin_url, 1)
    scope = TenantScope(tenant_id)
    with _session(tenant_url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref="concurrent",
            currency="NGN",
            minor_units=2,
        )
        session.commit()
        account_id = account.id

    def parallel(callable_) -> list[uuid.UUID]:
        barrier = threading.Barrier(2)
        results: list[uuid.UUID] = []
        errors: list[BaseException] = []

        def runner() -> None:
            try:
                with _session(tenant_url, scope) as session:
                    barrier.wait(timeout=20)
                    row = callable_(session)
                    session.commit()
                    results.append(row.id)
            except BaseException as exc:  # captured for one assertion below
                errors.append(exc)

        threads = [threading.Thread(target=runner) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "Billing duplicate attempt deadlocked"
        assert errors == []
        assert len(set(results)) == 1
        return results

    obligation_ids = parallel(
        lambda session: accept_rated_obligation(
            session,
            scope=scope,
            command=_obligation_command(
                account_id,
                scope=scope,
                source_fact_id="concurrent-occ",
            ),
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
    )
    with _session(tenant_url, scope) as session:
        draft = create_draft_document(
            session, scope=scope, command=_draft_command(obligation_ids[0])
        )
        session.commit()
        document_id = draft.id
    parallel(
        lambda session: issue_document(
            session,
            scope=scope,
            command=_issue_command(document_id),
            numbering=NUMBERING,
        )
    )
    parallel(
        lambda session: accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account_id,
                scope=scope,
                source_key="concurrent-settlement",
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
    )

    with _session(tenant_url, scope) as session:
        assert (
            session.scalar(select(func.count()).select_from(models.RatedObligation))
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(models.BillingDocument))
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(models.ConfirmedSettlement))
            == 1
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_settlement_replay_binds_persisted_account_lane_evidence_and_source(
    scratch, plane
) -> None:
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    ledger = IdempotencyRecord if plane == "tenant" else PlatformIdempotencyRecord
    with _session(url, scope) as session:
        accounts = [
            create_billing_account(
                session,
                scope=scope,
                external_account_ref=f"binding-{plane}-{index}",
                currency="NGN",
                minor_units=2,
            )
            for index in range(2)
        ]
        command = replace(
            _settlement_command(accounts[0].id, scope=scope),
            source_system="binding:integrator",
            source_settlement_key=f"binding-{plane}",
        )
        accepted = frozenset({"provider_settled", "bank_confirmed"})
        settlement = accept_settlement(
            session,
            scope=scope,
            command=command,
            accepted_confirmation_evidence=accepted,
        )
        settlement_id = settlement.id
        # Pin the published algorithm: existing ledger rows must remain replayable.
        legacy_fingerprint = fingerprint_of(
            {
                "amount": command.amount.amount,
                "currency": command.amount.currency.code,
                "minor_units": command.amount.currency.minor_units,
                "occurred_at": command.occurred_at,
                "source_version": command.source_version,
            }
        )
        record = session.execute(
            select(ledger).where(
                ledger.scope == "billing.settlement",
                ledger.key
                == f"{command.source_system}:{command.source_settlement_key}",
            )
        ).scalar_one()
        assert (
            record.fingerprint == settlement.request_fingerprint == legacy_fingerprint
        )
        session.commit()
        assert (
            accept_settlement(
                session,
                scope=scope,
                command=command,
                accepted_confirmation_evidence=accepted,
            ).id
            == settlement_id
        )
        retry = accept_settlement(
            session,
            scope=scope,
            command=replace(command, observed_at=NOW + timedelta(days=1)),
            accepted_confirmation_evidence=accepted,
        )
        assert retry.id == settlement_id
        assert retry.observed_at == NOW
        changes = (
            {"billing_account_id": accounts[1].id},
            {"funding_lane": SettlementFundingLane.PREPAID_FUNDING},
            {"confirmation_evidence": "bank_confirmed"},
            # These two source pairs have the same historical colon-joined key.
            {
                "source_system": "binding",
                "source_settlement_key": f"integrator:binding-{plane}",
            },
        )
        for change in changes:
            changed = replace(command, **change)
            # Retained ORM state must neither authorize a changed retry nor deny
            # the original command. No pending UPDATE is planted in the session.
            for field, value in change.items():
                set_committed_value(
                    settlement,
                    field,
                    value.value if isinstance(value, SettlementFundingLane) else value,
                )
            set_committed_value(settlement, "observed_at", NOW + timedelta(days=2))
            with pytest.raises(IdempotencyConflict, match="different request"):
                accept_settlement(
                    session,
                    scope=scope,
                    command=changed,
                    accepted_confirmation_evidence=accepted,
                )
            original = accept_settlement(
                session,
                scope=scope,
                command=command,
                accepted_confirmation_evidence=accepted,
            )
            assert original.id == settlement_id
            assert original.billing_account_id == command.billing_account_id
            assert original.funding_lane == command.funding_lane.value
            assert original.confirmation_evidence == command.confirmation_evidence
            assert original.source_system == command.source_system
            assert original.source_settlement_key == command.source_settlement_key
            assert original.observed_at == command.observed_at
            session.expire(settlement)
        session.commit()
        for account in accounts:
            position = rebuild_receivable_position(
                session,
                scope=scope,
                billing_account_id=account.id,
                currency="NGN",
                minor_units=2,
            )
            assert position.available_credit == (
                Decimal("120")
                if account.id == command.billing_account_id
                else Decimal("0")
            )
            assert position.prepaid_funding == Decimal("0")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_settlement_different_account_race_has_one_credit_and_one_refusal(
    scratch, plane
) -> None:
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account_ids = [
            create_billing_account(
                session,
                scope=scope,
                external_account_ref=f"binding-race-{plane}-{index}",
                currency="NGN",
                minor_units=2,
            ).id
            for index in range(2)
        ]
        session.commit()
    settlement_model = (
        models.ConfirmedSettlement
        if plane == "tenant"
        else models.PlatformConfirmedSettlement
    )
    barrier = threading.Barrier(2)
    results: list[tuple[uuid.UUID, uuid.UUID]] = []
    errors: list[BaseException] = []

    def runner(account_id: uuid.UUID) -> None:
        try:
            with _session(url, scope) as session:
                # Both attempts pass the replay lookup before either INSERT.
                # This exercises the kernel's losing SAVEPOINT rollback as well
                # as Billing's persisted binding check on the winner.
                @event.listens_for(session, "before_flush")
                def synchronize_acceptance(_session, _context, _instances) -> None:
                    if any(isinstance(row, settlement_model) for row in _session.new):
                        barrier.wait(timeout=20)

                row = accept_settlement(
                    session,
                    scope=scope,
                    command=_settlement_command(
                        account_id, scope=scope, source_key=f"binding-race-{plane}"
                    ),
                    accepted_confirmation_evidence=frozenset({"provider_settled"}),
                )
                settlement_id = row.id
                session.commit()
                results.append((account_id, settlement_id))
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=runner, args=(account_id,))
        for account_id in account_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "Billing account race deadlocked"
    assert len(results) == len(errors) == 1
    assert isinstance(errors[0], IdempotencyConflict)
    with _session(url, scope) as session:
        for account_id in account_ids:
            position = rebuild_receivable_position(
                session,
                scope=scope,
                billing_account_id=account_id,
                currency="NGN",
                minor_units=2,
            )
            assert position.available_credit == (
                Decimal("120") if account_id == results[0][0] else Decimal("0")
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(settlement_model)
                .where(settlement_model.billing_account_id.in_(account_ids))
            )
            == 1
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_settlement_rollback_removes_money_outbox_and_replay_binding(
    scratch, plane
) -> None:
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    settlement_model = (
        models.ConfirmedSettlement
        if plane == "tenant"
        else models.PlatformConfirmedSettlement
    )
    group_model = (
        models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
    )
    effect_model = (
        models.PostingEffect if plane == "tenant" else models.PlatformPostingEffect
    )
    ledger = IdempotencyRecord if plane == "tenant" else PlatformIdempotencyRecord
    outbox = OutboxEvent if plane == "tenant" else PlatformOutboxEvent
    with _session(url, scope) as session:
        account_id = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"binding-rollback-{plane}",
            currency="NGN",
            minor_units=2,
        ).id
        session.commit()
        command = _settlement_command(
            account_id, scope=scope, source_key=f"binding-rollback-{plane}"
        )
        abandoned_id = accept_settlement(
            session,
            scope=scope,
            command=command,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        ).id
        session.rollback()
    with _session(url, scope) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(settlement_model)
                .where(settlement_model.id == abandoned_id)
            )
            == 0
        )
        for model in (group_model, effect_model):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.billing_account_id == account_id)
                )
                == 0
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ledger)
                .where(
                    ledger.scope == "billing.settlement",
                    ledger.key == f"integrator:binding-rollback-{plane}",
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(outbox)
                .where(
                    outbox.event_type == "billing.settlement.accepted.v1",
                    outbox.payload["settlement_id"].as_string() == str(abandoned_id),
                )
            )
            == 0
        )
        accepted_id = accept_settlement(
            session,
            scope=scope,
            command=replace(
                command, funding_lane=SettlementFundingLane.PREPAID_FUNDING
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        ).id
        assert accepted_id != abandoned_id
        session.commit()


def test_settlement_replay_conflict_money_guards_and_rebuild_hash(scratch) -> None:
    _, _, platform_url = scratch
    scope = PlatformScope()
    with _session(platform_url, scope) as session:
        account, _, invoice = _seed_invoice(session, scope, suffix="path")
        command = _settlement_command(account.id, scope=scope)
        with pytest.raises(BillingRuleViolation, match="persistence scope differs"):
            accept_settlement(
                session,
                scope=scope,
                command=replace(command, scope=TenantScope(uuid.uuid4())),
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
        settlement = accept_settlement(
            session,
            scope=scope,
            command=command,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        replay = accept_settlement(
            session,
            scope=scope,
            command=command,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        assert replay.id == settlement.id
        with pytest.raises(IdempotencyConflict, match="different request"):
            accept_settlement(
                session,
                scope=scope,
                command=_settlement_command(
                    account.id,
                    scope=scope,
                    amount="119.00",
                ),
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
        with pytest.raises(BillingRuleViolation, match="only independently confirmed"):
            accept_settlement(
                session,
                scope=scope,
                command=replace(
                    command,
                    source_settlement_key="unverified",
                    confirmation_evidence="uploaded_proof",
                ),
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
        with pytest.raises(BillingRuleViolation, match="confirmed settlement"):
            allocate_settlement(
                session,
                scope=scope,
                command=AllocationCommand(
                    settlement_id=settlement.id,
                    document_id=invoice.id,
                    amount=_money("121.00"),
                    occurred_at=NOW,
                    source_ref="allocate-too-much",
                ),
            )
        with pytest.raises(BillingRuleViolation, match="currency"):
            allocate_settlement(
                session,
                scope=scope,
                command=AllocationCommand(
                    settlement_id=settlement.id,
                    document_id=invoice.id,
                    amount=_money("1.00", "USD"),
                    occurred_at=NOW,
                    source_ref="cross-currency",
                ),
            )
        allocate_settlement(
            session,
            scope=scope,
            command=AllocationCommand(
                settlement_id=settlement.id,
                document_id=invoice.id,
                amount=_money("50.00"),
                occurred_at=NOW,
                source_ref="allocate-50",
            ),
        )
        rebuilt = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        latest = session.scalars(
            select(models.PlatformReceivablePositionFact)
            .where(
                models.PlatformReceivablePositionFact.billing_account_id == account.id
            )
            .order_by(models.PlatformReceivablePositionFact.source_version.desc())
        ).first()
        assert latest is not None
        assert rebuilt.collectible_receivable == Decimal("70.000000")
        assert rebuilt.available_credit == Decimal("70.000000")
        assert rebuilt.prepaid_funding == Decimal("0.000000")
        assert rebuilt.state_fingerprint == latest.state_fingerprint
        accounting_events = tuple(
            session.scalars(
                select(PlatformOutboxEvent).where(
                    PlatformOutboxEvent.event_type == "billing.accounting.fact.v1"
                )
            )
        )
        invoice_fact = next(
            event.payload
            for event in accounting_events
            if event.payload["effect_kind"] == "invoice_issued"
        )
        allocation_fact = next(
            event.payload
            for event in accounting_events
            if event.payload["effect_kind"] == "allocation"
        )
        assert invoice_fact["scope"] == {}
        assert invoice_fact["tax_snapshots"][0]["policy_version"] == "1"
        assert invoice_fact["fx_snapshot"]["observation_version"] == "1"
        assert allocation_fact["effects"][0]["amount_delta"] == {
            "amount": "-50.00",
            "currency": "NGN",
            "minor_units": 2,
        }
        assert allocation_fact["allocations"][0]["effect_kind"] == "allocation"
        position_event = session.scalars(
            select(PlatformOutboxEvent)
            .where(PlatformOutboxEvent.event_type == "billing.receivable.position.v1")
            .order_by(PlatformOutboxEvent.created_at.desc())
        ).first()
        assert position_event is not None
        assert position_event.payload["scope"] == {}
        assert set(position_event.payload) >= {
            "collectible_receivable",
            "available_credit",
            "prepaid_funding",
        }
        assert "exposure_ref" not in position_event.payload
        exposure_event = session.scalars(
            select(PlatformOutboxEvent)
            .where(PlatformOutboxEvent.event_type == "billing.receivable.exposure.v1")
            .order_by(PlatformOutboxEvent.created_at.desc())
        ).first()
        assert exposure_event is not None
        assert exposure_event.payload["scope"] == {}
        assert exposure_event.payload["exposure_ref"] == f"document:{invoice.id}"
        assert exposure_event.payload["due_date_basis"]["status"] == "verified"
        assert set(exposure_event.payload) >= {
            "collectible_receivable",
            "service_period",
        }
        assert {"available_credit", "prepaid_funding"}.isdisjoint(
            exposure_event.payload
        )
        session.commit()


def test_issued_settled_and_posting_evidence_refuses_direct_mutation(scratch) -> None:
    _, _, platform_url = scratch
    scope = PlatformScope()
    with _session(platform_url, scope) as session:
        account, _, invoice = _seed_invoice(session, scope, suffix="immutable")
        settlement = accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id,
                scope=scope,
                source_key="immutable-settlement",
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        session.commit()
        account_id = account.id
        invoice_id, settlement_id = invoice.id, settlement.id

    engine = create_engine(platform_url)
    statements = (
        (
            "UPDATE mod_billing.platform_documents SET grand_total = 1 WHERE id = :id",
            invoice_id,
        ),
        (
            "UPDATE mod_billing.platform_confirmed_settlements "
            "SET amount = 1 WHERE id = :id",
            settlement_id,
        ),
        (
            "UPDATE mod_billing.platform_posting_effects "
            "SET amount_delta = 1 WHERE billing_account_id = :id",
            account_id,
        ),
    )
    with engine.connect() as connection:
        for statement, row_id in statements:
            transaction = connection.begin()
            with pytest.raises(DBAPIError):
                connection.execute(text(statement), {"id": row_id})
            transaction.rollback()
    engine.dispose()


def test_document_artifact_relation_enforces_exact_fact_content(scratch) -> None:
    _, _, platform_url = scratch
    scope = PlatformScope()
    with _session(platform_url, scope) as session:
        _, _, invoice = _seed_invoice(session, scope, suffix="artifact")
        fact = session.scalars(
            select(models.PlatformInvoiceDocumentFact).where(
                models.PlatformInvoiceDocumentFact.document_id == invoice.id
            )
        ).one()
        with pytest.raises(BillingConflict, match="semantic content"):
            record_document_artifact(
                session,
                scope=scope,
                command=RecordDocumentArtifactV1(
                    scope=scope,
                    fact_id=fact.id,
                    invoice_id=invoice.id,
                    fact_version=fact.fact_version,
                    media_type="application/pdf",
                    file_id=uuid.uuid4(),
                    checksum_sha256="a" * 64,
                    byte_length=128,
                    renderer_code="document-rendering",
                    renderer_version="1.0.0",
                    template_version="invoice-v1",
                    presentation_model_digest="b" * 64,
                    rendered_at=NOW,
                    correlation_id="render:artifact:mismatch",
                ),
            )
        artifact = record_document_artifact(
            session,
            scope=scope,
            command=RecordDocumentArtifactV1(
                scope=scope,
                fact_id=fact.id,
                invoice_id=invoice.id,
                fact_version=fact.fact_version,
                media_type="application/pdf",
                file_id=uuid.uuid4(),
                checksum_sha256="a" * 64,
                byte_length=128,
                renderer_code="document-rendering",
                renderer_version="1.0.0",
                template_version="invoice-v1",
                presentation_model_digest=fact.presentation_model_digest,
                rendered_at=NOW,
                correlation_id="render:artifact:accepted",
            ),
        )
        replay = record_document_artifact(
            session,
            scope=scope,
            command=RecordDocumentArtifactV1(
                scope=scope,
                fact_id=fact.id,
                invoice_id=invoice.id,
                fact_version=fact.fact_version,
                media_type="application/pdf",
                file_id=artifact.file_id,
                checksum_sha256="a" * 64,
                byte_length=128,
                renderer_code="document-rendering",
                renderer_version="1.0.0",
                template_version="invoice-v1",
                presentation_model_digest=fact.presentation_model_digest,
                rendered_at=NOW,
                correlation_id="render:artifact:accepted",
            ),
        )
        assert replay.id == artifact.id
        assert artifact.renderer_code == "document-rendering"
        assert artifact.renderer_version == "1.0.0"
        assert artifact.template_version == "invoice-v1"
        assert artifact.document_number == invoice.document_number
        with pytest.raises(IdempotencyConflict, match="different request"):
            record_document_artifact(
                session,
                scope=scope,
                command=RecordDocumentArtifactV1(
                    scope=scope,
                    fact_id=fact.id,
                    invoice_id=invoice.id,
                    fact_version=fact.fact_version,
                    media_type="application/pdf",
                    file_id=uuid.uuid4(),
                    checksum_sha256="c" * 64,
                    byte_length=129,
                    renderer_code="document-rendering",
                    renderer_version="1.0.0",
                    template_version="invoice-v1",
                    presentation_model_digest=fact.presentation_model_digest,
                    rendered_at=NOW,
                    correlation_id="render:artifact:racing-record",
                ),
            )
        replacement_command = RepairDocumentArtifactV1(
            scope=scope,
            current_artifact_id=artifact.id,
            replacement_file_id=uuid.uuid4(),
            checksum_sha256="a" * 64,
            byte_length=128,
            presentation_model_digest=fact.presentation_model_digest,
            rendered_at=NOW,
            correlation_id="render:artifact:repair",
            supersession_reason="repair_corrupt_bytes",
        )
        replacement = repair_document_artifact(
            session,
            scope=scope,
            command=replacement_command,
            declared_supersession_reasons=frozenset({"repair_corrupt_bytes"}),
        )
        replacement_replay = repair_document_artifact(
            session,
            scope=scope,
            command=replacement_command,
            declared_supersession_reasons=frozenset({"repair_corrupt_bytes"}),
        )
        assert replacement_replay.id == replacement.id
        session.refresh(artifact)
        assert artifact.superseded_by_artifact_id == replacement.id
        assert artifact.supersession_reason == "repair_corrupt_bytes"
        assert replacement.superseded_at is None
        void_document(
            session,
            scope=scope,
            document_id=invoice.id,
            actor_ref="billing-test",
            occurred_at=NOW,
            source_ref="void:artifact-invoice",
        )
        session.refresh(replacement)
        assert replacement.withdrawn_at == NOW
        assert replacement.withdrawal_reason == "void:artifact-invoice"
        replay_after_void = record_document_artifact(
            session,
            scope=scope,
            command=RecordDocumentArtifactV1(
                scope=scope,
                fact_id=fact.id,
                invoice_id=invoice.id,
                fact_version=fact.fact_version,
                media_type="application/pdf",
                file_id=artifact.file_id,
                checksum_sha256="a" * 64,
                byte_length=128,
                renderer_code="document-rendering",
                renderer_version="1.0.0",
                template_version="invoice-v1",
                presentation_model_digest=fact.presentation_model_digest,
                rendered_at=NOW,
                correlation_id="render:artifact:accepted",
            ),
        )
        assert replay_after_void.id == artifact.id
        assert replay_after_void.superseded_by_artifact_id == replacement.id
        with pytest.raises(BillingRuleViolation, match="cancelled"):
            record_document_artifact(
                session,
                scope=scope,
                command=RecordDocumentArtifactV1(
                    scope=scope,
                    fact_id=fact.id,
                    invoice_id=invoice.id,
                    fact_version=fact.fact_version,
                    media_type="text/html",
                    file_id=uuid.uuid4(),
                    checksum_sha256="d" * 64,
                    byte_length=256,
                    renderer_code="document-rendering",
                    renderer_version="1.0.0",
                    template_version="invoice-v1",
                    presentation_model_digest=fact.presentation_model_digest,
                    rendered_at=NOW,
                    correlation_id="render:artifact:after-void",
                ),
            )
        session.commit()


def test_void_and_credit_append_corrections_without_editing_the_invoice(
    scratch,
) -> None:
    _, _, platform_url = scratch
    scope = PlatformScope()
    with _session(platform_url, scope) as session:
        _, _, voided_invoice = _seed_invoice(session, scope, suffix="void")
        void_group = void_document(
            session,
            scope=scope,
            document_id=voided_invoice.id,
            actor_ref="billing-test",
            occurred_at=NOW,
            source_ref="void:invoice",
        )
        assert void_group.reverses_group_id is not None
        assert voided_invoice.lifecycle == "issued"

        _, _, credited_invoice = _seed_invoice(session, scope, suffix="credit")
        credit = issue_credit_note(
            session,
            scope=scope,
            command=IssueCreditNote(
                original_document_id=credited_invoice.id,
                pre_tax_amount=_money("20.00"),
                tax_amount=_money("0.00"),
                total_amount=_money("20.00"),
                reason="service correction",
                series_code="credit",
                reference_date=date(2026, 8, 17),
                actor_ref="billing-test",
                correlation_id="credit:invoice",
                occurred_at=NOW,
                document_profile_code="credit.standard",
                document_profile_version="1",
            ),
            numbering=NUMBERING,
        )
        assert credit.document_kind == "credit_note"
        assert credit.credits_document_id == credited_invoice.id
        assert credited_invoice.grand_total == Decimal("120.000000")
        voided_exposure = session.scalars(
            select(models.PlatformReceivableExposureFact)
            .where(
                models.PlatformReceivableExposureFact.document_id == voided_invoice.id
            )
            .order_by(models.PlatformReceivableExposureFact.source_version.desc())
        ).first()
        credited_exposure = session.scalars(
            select(models.PlatformReceivableExposureFact)
            .where(
                models.PlatformReceivableExposureFact.document_id == credited_invoice.id
            )
            .order_by(models.PlatformReceivableExposureFact.source_version.desc())
        ).first()
        assert voided_exposure is not None
        assert voided_exposure.financial_state == "cancelled"
        assert voided_exposure.collectible_receivable == Decimal("0.000000")
        assert credited_exposure is not None
        assert credited_exposure.financial_state == "partially_resolved"
        assert credited_exposure.collectible_receivable == Decimal("100.000000")

        void_document(
            session,
            scope=scope,
            document_id=credit.id,
            actor_ref="billing-test",
            occurred_at=NOW,
            source_ref="void:credit-note",
        )
        restored_exposure = session.scalars(
            select(models.PlatformReceivableExposureFact)
            .where(
                models.PlatformReceivableExposureFact.document_id == credited_invoice.id
            )
            .order_by(models.PlatformReceivableExposureFact.source_version.desc())
        ).first()
        assert restored_exposure is not None
        assert restored_exposure.financial_state == "open"
        assert restored_exposure.collectible_receivable == Decimal("120.000000")
        session.commit()


def _additional_credit_invoice(session, scope, account_id, suffix):
    obligation = accept_rated_obligation(
        session,
        scope=scope,
        command=_obligation_command(account_id, scope=scope, source_fact_id=suffix),
        accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
    )
    draft = create_draft_document(
        session, scope=scope, command=_draft_command(obligation.id)
    )
    return draft


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_position_hash_uses_persisted_values_and_still_detects_drift(
    scratch, plane
) -> None:
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    effect_model = (
        models.PostingEffect if plane == "tenant" else models.PlatformPostingEffect
    )
    group_model = (
        models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
    )
    retained = []

    def retain_effect(_session: Session, instance: object) -> None:
        if isinstance(instance, effect_model):
            retained.append(instance)

    with _session(url, scope) as session:
        account, _, _ = _seed_invoice(session, scope, suffix=f"hash-cache-{plane}")
        account_id = account.id
        invoice_group_id = session.execute(
            select(group_model.id).where(
                group_model.billing_account_id == account_id,
                group_model.group_kind == "invoice_issued",
            )
        ).scalar_one()
        event.listen(session, "pending_to_persistent", retain_effect)
        try:
            settlement = accept_settlement(
                session,
                scope=scope,
                command=_settlement_command(
                    account_id,
                    scope=scope,
                    source_key=f"hash-cache-{plane}",
                    amount="12.34",
                ),
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
        finally:
            event.remove(session, "pending_to_persistent", retain_effect)
        # Keep the freshly flushed two-decimal entity alive: a scalar database
        # read must still hash the same six-decimal values as a fresh session.
        assert len(retained) == 1
        assert retained[0].amount_delta.as_tuple().exponent == -2
        before = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        )
        acceptance = session.execute(
            select(group_model).where(
                group_model.source_ref == f"settlement:{settlement.id}"
            )
        ).scalar_one()
        acceptance_id = acceptance.id
        session.expire_all()
        after = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        )
        assert before.state_fingerprint == after.state_fingerprint
        reversal = reverse_posting_group(
            session,
            scope=scope,
            command=ReversePostingGroupCommand(
                acceptance_id, NOW, f"hash-zero-{plane}"
            ),
        )
        zero = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        )
        assert zero.available_credit == 0
        # Restore and reverse again so the touched-zero lane is also rebuilt
        # after a real reversal chain, expiration and a session boundary.
        restoration = reverse_posting_group(
            session,
            scope=scope,
            command=ReversePostingGroupCommand(
                reversal.id, NOW, f"hash-restored-{plane}"
            ),
        )
        reverse_posting_group(
            session,
            scope=scope,
            command=ReversePostingGroupCommand(
                restoration.id, NOW, f"hash-zero-again-{plane}"
            ),
        )
        session.expire_all()
        final = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        )
        assert final.state_fingerprint == zero.state_fingerprint
        session.commit()

    with _session(url, scope) as fresh:
        rebuilt = rebuild_receivable_position(
            fresh,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        )
        assert rebuilt.state_fingerprint == final.state_fingerprint
        # A planted unmatched effect must still fail the strict attestation
        # comparison. The scalar-read repair never accepts or rewrites drift.
        with fresh.begin_nested() as savepoint:
            values = {
                "id": uuid.uuid4(),
                "posting_group_id": invoice_group_id,
                "billing_account_id": account_id,
                "lane": "available_credit",
                "amount_delta": Decimal("1.00"),
                "currency": "NGN",
                "minor_units": 2,
            }
            if isinstance(scope, TenantScope):
                values["tenant_id"] = scope.tenant_id
            fresh.add(effect_model(**values))
            fresh.flush()
            with pytest.raises(BillingConflict, match="rebuild hash differs"):
                rebuild_receivable_position(
                    fresh,
                    scope=scope,
                    billing_account_id=account_id,
                    currency="NGN",
                    minor_units=2,
                )
            savepoint.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_available_credit_draw_is_exact_backed_and_replayable(scratch, plane) -> None:
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(session, scope, suffix=f"draw-{plane}")
        sources = []
        for index, amount in enumerate(("30.00", "110.00")):
            sources.append(
                accept_settlement(
                    session,
                    scope=scope,
                    command=replace(
                        _settlement_command(
                            account.id,
                            scope=scope,
                            source_key=f"draw-{plane}-{index}",
                            amount=amount,
                        ),
                        occurred_at=NOW.replace(minute=index),
                    ),
                    accepted_confirmation_evidence=frozenset({"provider_settled"}),
                )
            )
        refund_settlement(
            session,
            scope=scope,
            command=RefundCommand(
                sources[1].id, _money("20.00"), NOW, f"draw-refund:{plane}"
            ),
        )
        command = ApplyAvailableCreditCommand(
            account.id, invoice.id, _money("120.00"), NOW, f"draw:{plane}"
        )
        group = apply_available_credit(session, scope=scope, command=command)
        allocation = (
            models.AllocationEffect
            if plane == "tenant"
            else models.PlatformAllocationEffect
        )
        rows = (
            session.execute(
                select(allocation).where(allocation.posting_group_id == group.id)
            )
            .scalars()
            .all()
        )
        assert {row.settlement_id: row.amount_delta for row in rows} == {
            sources[0].id: Decimal("30"),
            sources[1].id: Decimal("90"),
        }
        assert all(row.document_id == invoice.id for row in rows)
        assert (
            apply_available_credit(session, scope=scope, command=command).id == group.id
        )
        with pytest.raises(IdempotencyConflict):
            apply_available_credit(
                session, scope=scope, command=replace(command, amount=_money("1.00"))
            )
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert position.available_credit == 0
        assert position.collectible_receivable == 0
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_available_credit_refuses_ineligible_sources_and_targets(
    scratch, plane
) -> None:
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(
            session, scope, suffix=f"draw-guards-{plane}"
        )
        command = ApplyAvailableCreditCommand(
            account.id, invoice.id, _money("1.00"), NOW, f"draw-guards:{plane}"
        )
        prepaid = replace(
            _settlement_command(account.id, scope=scope, source_key=f"prepaid-{plane}"),
            funding_lane=SettlementFundingLane.PREPAID_FUNDING,
        )
        accept_settlement(
            session,
            scope=scope,
            command=prepaid,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        with pytest.raises(BillingRuleViolation, match="cash-backed"):
            apply_available_credit(session, scope=scope, command=command)
        settlement = accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id, scope=scope, source_key=f"guard-cash-{plane}"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        for amount in (
            _money("0.00"),
            _money("-1.00"),
            _money("121.00"),
            _money("1.00", "USD"),
            Money.of("1.000", Currency("NGN", 3)),
        ):
            with pytest.raises(BillingRuleViolation):
                apply_available_credit(
                    session, scope=scope, command=replace(command, amount=amount)
                )
        _, _, other_invoice = _seed_invoice(
            session, scope, suffix=f"draw-other-{plane}"
        )
        with pytest.raises(BillingRuleViolation, match="account"):
            apply_available_credit(
                session,
                scope=scope,
                command=replace(command, document_id=other_invoice.id),
            )
        void_document(
            session,
            scope=scope,
            document_id=invoice.id,
            actor_ref="test",
            occurred_at=NOW,
            source_ref=f"draw-void-{plane}",
        )
        with pytest.raises(BillingRuleViolation, match="cancelled"):
            apply_available_credit(session, scope=scope, command=command)
        group_model = (
            models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
        )
        acceptance = session.execute(
            select(group_model).where(
                group_model.source_ref == f"settlement:{settlement.id}"
            )
        ).scalar_one()
        reversal = reverse_posting_group(
            session,
            scope=scope,
            command=ReversePostingGroupCommand(
                acceptance.id, NOW, f"draw-reverse-{plane}"
            ),
        )
        draft = _additional_credit_invoice(
            session, scope, account.id, f"draw-second-{plane}"
        )
        with pytest.raises(BillingRuleViolation, match="issued invoice"):
            apply_available_credit(
                session, scope=scope, command=replace(command, document_id=draft.id)
            )
        target = issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        # The reversed source has room on its allocation edges but no cash.
        with pytest.raises(BillingRuleViolation, match="active confirmed"):
            allocate_settlement(
                session,
                scope=scope,
                command=AllocationCommand(
                    settlement.id,
                    target.id,
                    _money("1.00"),
                    NOW,
                    f"direct-reversed-{plane}",
                ),
            )
        with pytest.raises(BillingRuleViolation, match="cash-backed"):
            apply_available_credit(
                session, scope=scope, command=replace(command, document_id=target.id)
            )
        second = accept_settlement(
            session,
            scope=scope,
            command=replace(
                _settlement_command(
                    account.id,
                    scope=scope,
                    source_key=f"second-cash-{plane}",
                    amount="10.00",
                ),
                occurred_at=NOW.replace(minute=1),
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        draw = apply_available_credit(
            session,
            scope=scope,
            command=replace(command, document_id=target.id, amount=_money("10.00")),
        )
        allocation_model = (
            models.AllocationEffect
            if plane == "tenant"
            else models.PlatformAllocationEffect
        )
        edges = (
            session.execute(
                select(allocation_model).where(
                    allocation_model.posting_group_id == draw.id
                )
            )
            .scalars()
            .all()
        )
        assert [(edge.settlement_id, edge.amount_delta) for edge in edges] == [
            (second.id, Decimal("10.00"))
        ]
        # Reversal-of-reversal restores A, and the exhausted B cannot fund this.
        reverse_posting_group(
            session,
            scope=scope,
            command=ReversePostingGroupCommand(
                reversal.id, NOW, f"draw-restore-{plane}"
            ),
        )
        restored = apply_available_credit(
            session,
            scope=scope,
            command=replace(
                command,
                document_id=target.id,
                amount=_money("110.00"),
                source_ref=f"draw-restored-{plane}",
            ),
        )
        edges = (
            session.execute(
                select(allocation_model).where(
                    allocation_model.posting_group_id == restored.id
                )
            )
            .scalars()
            .all()
        )
        assert [(edge.settlement_id, edge.amount_delta) for edge in edges] == [
            (settlement.id, Decimal("110.00"))
        ]
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_available_credit_global_cap_survives_reversed_spent_source(
    scratch, plane
) -> None:
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(session, scope, suffix=f"draw-cap-{plane}")
        first = accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id, scope=scope, source_key=f"cap-first-{plane}"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        allocate_settlement(
            session,
            scope=scope,
            command=AllocationCommand(
                first.id, invoice.id, _money("60.00"), NOW, f"cap-spend-{plane}"
            ),
        )
        group_model = (
            models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
        )
        acceptance = session.execute(
            select(group_model).where(
                group_model.source_ref == f"settlement:{first.id}"
            )
        ).scalar_one()
        # Replenish first: every committed position must stay nonnegative.
        accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id, scope=scope, source_key=f"cap-second-{plane}"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        reverse_posting_group(
            session,
            scope=scope,
            command=ReversePostingGroupCommand(
                acceptance.id, NOW, f"cap-reverse-{plane}"
            ),
        )
        draft = _additional_credit_invoice(
            session, scope, account.id, f"cap-target-{plane}"
        )
        target = issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        command = ApplyAvailableCreditCommand(
            account.id, target.id, _money("61.00"), NOW, f"cap-draw-{plane}"
        )
        with pytest.raises(BillingRuleViolation, match="cash-backed"):
            apply_available_credit(session, scope=scope, command=command)
        apply_available_credit(
            session, scope=scope, command=replace(command, amount=_money("60.00"))
        )
        assert (
            rebuild_receivable_position(
                session,
                scope=scope,
                billing_account_id=account.id,
                currency="NGN",
                minor_units=2,
            ).available_credit
            == 0
        )
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_concurrent_available_credit_draws_cannot_spend_twice(scratch, plane) -> None:
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(
            session, scope, suffix=f"draw-concurrent-{plane}"
        )
        accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id, scope=scope, source_key=f"draw-concurrent-{plane}"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        draft = _additional_credit_invoice(
            session, scope, account.id, f"draw-race-target-{plane}"
        )
        second_invoice = issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        session.commit()
        account_id, document_ids = account.id, (invoice.id, second_invoice.id)
    barrier = threading.Barrier(2)
    results: list[uuid.UUID] = []
    rejected: list[BillingRuleViolation] = []
    errors: list[BaseException] = []

    def runner(index: int) -> None:
        try:
            with _session(url, scope) as session:
                barrier.wait(timeout=20)
                try:
                    group = apply_available_credit(
                        session,
                        scope=scope,
                        command=ApplyAvailableCreditCommand(
                            account_id,
                            document_ids[index],
                            _money("120.00"),
                            NOW,
                            f"draw-race-{plane}-{index}",
                        ),
                    )
                    session.commit()
                    results.append(group.id)
                except BillingRuleViolation as exc:
                    rejected.append(exc)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "available-credit draw deadlocked"
    assert errors == []
    assert len(results) == len(rejected) == 1
    with _session(url, scope) as session:
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        )
        assert position.available_credit == 0
        assert position.collectible_receivable == Decimal("120")
        allocation_model = (
            models.AllocationEffect
            if plane == "tenant"
            else models.PlatformAllocationEffect
        )
        assert session.scalar(
            select(func.sum(allocation_model.amount_delta)).where(
                allocation_model.billing_account_id == account_id
            )
        ) == Decimal("120")


def test_available_credit_draw_cannot_cross_tenants(scratch) -> None:
    admin_url, tenant_url, _ = scratch
    left, right = _create_tenants(admin_url)
    with _session(tenant_url, TenantScope(left)) as session:
        account, _, invoice = _seed_invoice(
            session, TenantScope(left), suffix="draw-private"
        )
        accept_settlement(
            session,
            scope=TenantScope(left),
            command=_settlement_command(account.id, scope=TenantScope(left)),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        session.commit()
        account_id, invoice_id = account.id, invoice.id
    with _session(tenant_url, TenantScope(right)) as session:
        own, _, _ = _seed_invoice(session, TenantScope(right), suffix="draw-private")
        command = ApplyAvailableCreditCommand(
            account_id, invoice_id, _money("1.00"), NOW, "draw-private"
        )
        with pytest.raises(BillingRuleViolation, match="not found"):
            apply_available_credit(session, scope=TenantScope(right), command=command)
        with pytest.raises(BillingRuleViolation, match="not found"):
            apply_available_credit(
                session,
                scope=TenantScope(right),
                command=replace(command, billing_account_id=own.id),
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_available_credit_refuses_unbacked_position_without_posting(
    scratch, plane
) -> None:
    # Synthetic noncash evidence exercises the fail-closed boundary; this is
    # deliberately not a public grant command or an adopter migration path.
    from dotmac_billing.engine import EffectLane
    from dotmac_billing.service import _post_group

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(
            session, scope, suffix=f"draw-unbacked-{plane}"
        )
        _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="credit_note_issued",
            currency="NGN",
            minor_units=2,
            source_ref=f"test-unbacked-{plane}",
            occurred_at=NOW,
            effects=((EffectLane.AVAILABLE_CREDIT, Decimal("10")),),
        )
        accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id,
                scope=scope,
                source_key=f"unbacked-partial-{plane}",
                amount="5.00",
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        group_model = (
            models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
        )
        before = session.scalar(select(func.count()).select_from(group_model))
        command = ApplyAvailableCreditCommand(
            account.id, invoice.id, _money("10.00"), NOW, f"draw-unbacked-{plane}"
        )
        with pytest.raises(BillingRuleViolation, match="cash-backed"):
            apply_available_credit(session, scope=scope, command=command)
        assert session.scalar(select(func.count()).select_from(group_model)) == before
        assert rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        ).available_credit == Decimal("15")
        # Reusing the failed command after real funding arrives must succeed;
        # no failed idempotency result or partial allocation was recorded.
        accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id,
                scope=scope,
                source_key=f"unbacked-now-cash-{plane}",
                amount="5.00",
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        assert apply_available_credit(session, scope=scope, command=command).id


def _multiline_command(account_id, obligations):
    from dataclasses import fields

    header = _draft_command(obligations[0].id)
    excluded = {"obligation_id", "description", "quantity", "unit_code"}
    return CreateMultiLineDraftDocument(
        billing_account_id=account_id,
        idempotency_key=f"multi:{obligations[0].id}",
        lines=tuple(
            DraftDocumentLine(
                obligation_id=row.id,
                description=f"Access {index}",
                quantity=Decimal("2"),
                unit_code="month",
                unit_amount=_money("50.00"),
            )
            for index, row in enumerate(obligations)
        ),
        **{
            field.name: getattr(header, field.name)
            for field in fields(header)
            if field.name not in excluded
        },
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_multiline_exact_totals_snapshots_and_replay(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"multi:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"multi:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(2)
        )
        command = _multiline_command(account.id, obligations)
        draft = create_multi_line_draft_document(session, scope=scope, command=command)
        assert (draft.subtotal, draft.tax_total, draft.grand_total) == (
            Decimal("200"),
            Decimal("40"),
            Decimal("240"),
        )
        issued = issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        session.commit()
        assert (
            create_multi_line_draft_document(session, scope=scope, command=command).id
            == issued.id
        )
        with pytest.raises(IdempotencyConflict):
            create_multi_line_draft_document(
                session, scope=scope, command=replace(command, locale="en-US")
            )
        fact_model = (
            models.InvoiceDocumentFact
            if plane == "tenant"
            else models.PlatformInvoiceDocumentFact
        )
        fact = session.execute(
            select(fact_model).where(fact_model.document_id == issued.id)
        ).scalar_one()
        assert len(fact.fact_payload["lines"]) == 2
        assert len(fact.fact_payload["tax_snapshots"]) == 2
        assert fact.fact_payload["fx_snapshot"]["observation_id"] == "fx-observation-1"
        assert [line["line_number"] for line in fact.fact_payload["lines"]] == [1, 2]

        exposure_model = (
            models.ReceivableExposureFact
            if plane == "tenant"
            else models.PlatformReceivableExposureFact
        )
        exposure = session.execute(
            select(exposure_model).where(exposure_model.document_id == issued.id)
        ).scalar_one()
        assert exposure.collectible_receivable == Decimal("240")
        assert exposure.subject_ref == "customer:example"
        assert exposure.service_ref == "service:example"
        accounting_model = (
            models.AccountingFact
            if plane == "tenant"
            else models.PlatformAccountingFact
        )
        accounting = session.execute(
            select(accounting_model).where(
                accounting_model.posting_group_id == exposure.posting_group_watermark
            )
        ).scalar_one()
        assert len(accounting.fact_payload["tax_snapshots"]) == 2
        assert (
            accounting.fact_payload["fx_snapshot"]["observation_id"]
            == "fx-observation-1"
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "single",
        "duplicate",
        "quantity",
        "precision",
        "unit",
        "subject",
        "period",
        "timing",
        "fx",
    ],
)
def test_multiline_refuses_invalid_evidence(scratch, plane, fault):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"guard:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        inputs = [
            _obligation_command(
                account.id, scope=scope, source_fact_id=f"guard:{uuid.uuid4()}"
            )
            for _ in range(2)
        ]
        changes = {
            "subject": {"subject_ref": "other"},
            "period": {
                "service_period": ServicePeriodEvidenceV1(
                    status=ServicePeriodStatus.NOT_APPLICABLE
                )
            },
            "timing": {"collection_timing": "advance"},
            "fx": {"fx_snapshot": None},
        }
        if fault in changes:
            inputs[1] = replace(inputs[1], **changes[fault])
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=item,
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for item in inputs
        )
        command = _multiline_command(account.id, obligations)
        if fault == "empty":
            command = replace(command, lines=())
        if fault == "single":
            command = replace(command, lines=command.lines[:1])
        if fault == "duplicate":
            command = replace(command, lines=(command.lines[0], command.lines[0]))
        if fault in {"quantity", "precision", "unit"}:
            changes = {
                "quantity": {"quantity": Decimal("NaN")},
                "precision": {"quantity": Decimal("2.0000001")},
                "unit": {"unit_amount": _money("49.99")},
            }
            command = replace(
                command,
                lines=(replace(command.lines[0], **changes[fault]), command.lines[1]),
            )
        with pytest.raises((BillingRuleViolation, BillingConflict)):
            create_multi_line_draft_document(session, scope=scope, command=command)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_multiline_database_affinity_and_issued_line_freeze(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    prefix = "" if plane == "tenant" else "platform_"
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"fk:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        other = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"fk-other:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"fk:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(2)
        )
        foreign = accept_rated_obligation(
            session,
            scope=scope,
            command=_obligation_command(
                other.id, scope=scope, source_fact_id=f"fk:{uuid.uuid4()}"
            ),
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        draft = create_multi_line_draft_document(
            session, scope=scope, command=_multiline_command(account.id, obligations)
        )
        draft_id, foreign_id, account_id = draft.id, foreign.id, account.id
        session.commit()
    # Exercise real constraints as the migration owner, without online privilege
    # denial masking the database invariant under test.
    engine = create_engine(admin_url)
    tenant_column = "tenant_id," if plane == "tenant" else ""
    tenant_value = ":tenant_id," if plane == "tenant" else ""
    insert = f"""INSERT INTO mod_billing.{prefix}document_lines
        ({tenant_column}id,document_id,billing_account_id,obligation_id,line_number,
         description,quantity,unit_code,unit_amount,pre_tax_amount,tax_amount,total_amount,
         currency,minor_units,price_source_version)
        VALUES ({tenant_value}:id,:document_id,:account_id,:obligation_id,3,
         'foreign',1,'month',100,100,20,120,'NGN',2,'price:v1')"""
    params = {
        "id": uuid.uuid4(),
        "document_id": draft_id,
        "account_id": account_id,
        "obligation_id": foreign_id,
        "tenant_id": scope.tenant_id if plane == "tenant" else None,
    }
    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(text(insert), params)
        assert error.value.orig.sqlstate == "23503"
    with _session(url, scope) as session:
        issue_document(
            session, scope=scope, command=_issue_command(draft_id), numbering=NUMBERING
        )
        session.commit()
    with engine.begin() as connection:
        for statement in (
            f"UPDATE mod_billing.{prefix}document_lines "
            "SET description='changed' WHERE document_id=:document_id",
            f"DELETE FROM mod_billing.{prefix}document_lines "
            "WHERE document_id=:document_id",
            insert,
        ):
            with pytest.raises(DBAPIError) as error, connection.begin_nested():
                connection.execute(
                    text(statement),
                    {**params, "id": uuid.uuid4(), "obligation_id": None},
                )
            assert error.value.orig.sqlstate == "23001"
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_multiline_issue_rechecks_rated_totals_and_grouping(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    prefix = "" if plane == "tenant" else "platform_"
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"edit:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"edit:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(2)
        )
        draft = create_multi_line_draft_document(
            session, scope=scope, command=_multiline_command(account.id, obligations)
        )
        document_id = draft.id
        session.commit()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}documents SET grand_total=239 WHERE id=:id"
            ),
            {"id": document_id},
        )
    with _session(url, scope) as session:
        with pytest.raises(BillingRuleViolation, match="totals"):
            issue_document(
                session,
                scope=scope,
                command=_issue_command(document_id),
                numbering=NUMBERING,
            )
    with engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}documents SET grand_total=240 WHERE id=:id"
            ),
            {"id": document_id},
        )
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}document_lines "
                "SET quantity=3 WHERE document_id=:id AND line_number=2"
            ),
            {"id": document_id},
        )
    with _session(url, scope) as session:
        with pytest.raises(BillingRuleViolation, match="unit price"):
            issue_document(
                session,
                scope=scope,
                command=_issue_command(document_id),
                numbering=NUMBERING,
            )
    with engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}document_lines "
                "SET quantity=2, unit_amount=50.001 "
                "WHERE document_id=:id AND line_number=2"
            ),
            {"id": document_id},
        )
    with _session(url, scope) as session:
        with pytest.raises(BillingRuleViolation, match="precision"):
            issue_document(
                session,
                scope=scope,
                command=_issue_command(document_id),
                numbering=NUMBERING,
            )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_multiline_upgrade_backfills_historical_issued_lines_without_financial_edits(
    plane,
):
    from alembic import command as alembic_command
    from alembic.config import Config

    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    prefix = "" if plane == "tenant" else "platform_"
    with _scratch_database((selection,), target_revision="bi_0001_billing") as (
        admin_url,
        tenant_url,
        platform_url,
    ):
        scope = (
            TenantScope(_create_tenants(admin_url, 1)[0])
            if plane == "tenant"
            else PlatformScope()
        )
        url = tenant_url if plane == "tenant" else platform_url
        document_model = (
            models.BillingDocument
            if plane == "tenant"
            else models.PlatformBillingDocument
        )
        with _session(url, scope) as session:
            account = create_billing_account(
                session,
                scope=scope,
                external_account_ref=f"history:{uuid.uuid4()}",
                currency="NGN",
                minor_units=2,
            )
            obligation = accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"history:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            header = _draft_command(obligation.id)
            from dotmac_billing.service import _wire_value

            values = {
                "id": uuid.uuid4(),
                "billing_account_id": account.id,
                "obligation_id": obligation.id,
                "document_kind": "invoice",
                "credits_document_id": None,
                "lifecycle": "issued",
                "series_code": "invoice",
                "document_number": f"historical:{uuid.uuid4()}",
                "currency": "NGN",
                "minor_units": 2,
                "subtotal": Decimal("100"),
                "tax_total": Decimal("20"),
                "grand_total": Decimal("120"),
                "due_at": NOW,
                "issued_at": NOW,
            }
            for name in (
                "due_date_basis",
                "document_profile_code",
                "document_profile_version",
                "seller_snapshot",
                "customer_snapshot",
                "payment_instructions",
                "brand_asset",
                "locale",
                "timezone",
            ):
                values[name] = _wire_value(getattr(header, name))
            if plane == "tenant":
                values["tenant_id"] = scope.tenant_id
            from sqlalchemy import column, table

            legacy_document_table = table(
                document_model.__tablename__,
                *(
                    column(item.name, item.type)
                    for item in document_model.__table__.columns
                    if item.name != "authoring_mode"
                ),
                schema=models.SCHEMA,
            )
            session.execute(legacy_document_table.insert().values(**values))
            tenant_column = "tenant_id," if plane == "tenant" else ""
            tenant_value = ":tenant_id," if plane == "tenant" else ""
            session.execute(
                text(f"""INSERT INTO mod_billing.{prefix}document_lines
                ({tenant_column}id,document_id,obligation_id,line_number,description,quantity,unit_code,
                 unit_amount,pre_tax_amount,tax_amount,total_amount,currency,minor_units,price_source_version)
                VALUES ({tenant_value}:id,:document_id,:obligation_id,1,'historical',
                    1,'month',100,100,20,120,'NGN',2,'price:v1')"""),
                {
                    "id": uuid.uuid4(),
                    "document_id": values["id"],
                    "obligation_id": obligation.id,
                    "tenant_id": values.get("tenant_id"),
                },
            )
            session.commit()
        engine = create_engine(admin_url)
        with engine.connect() as connection:
            before_document = connection.execute(
                text(
                    f"SELECT to_jsonb(d) FROM mod_billing.{prefix}documents d "
                    "WHERE id=:id"
                ),
                {"id": values["id"]},
            ).scalar_one()
            before_line = connection.execute(
                text(
                    f"SELECT to_jsonb(l) FROM mod_billing.{prefix}document_lines l "
                    "WHERE document_id=:id"
                ),
                {"id": values["id"]},
            ).scalar_one()
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {BILLING_VERSIONS}",
        )
        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="billing", planes=(selection,)),
        )
        alembic_command.upgrade(config, "bi_0002_multiline")
        with engine.connect() as connection:
            after_document = connection.execute(
                text(
                    f"SELECT to_jsonb(d) FROM mod_billing.{prefix}documents d "
                    "WHERE id=:id"
                ),
                {"id": values["id"]},
            ).scalar_one()
            after_line = connection.execute(
                text(
                    f"SELECT to_jsonb(l) FROM mod_billing.{prefix}document_lines l "
                    "WHERE document_id=:id"
                ),
                {"id": values["id"]},
            ).scalar_one()
            assert after_document.pop("authoring_mode") == "legacy_single"
            assert after_document == before_document
            assert after_line.pop("billing_account_id") == str(
                values["billing_account_id"]
            )
            assert after_line == before_line
        alembic_command.downgrade(config, "bi_0001_billing")
        alembic_command.upgrade(config, "bi_0002_multiline")
        with _session(url, scope) as session:
            obligations = tuple(
                accept_rated_obligation(
                    session,
                    scope=scope,
                    command=_obligation_command(
                        values["billing_account_id"],
                        scope=scope,
                        source_fact_id=f"downgrade:{uuid.uuid4()}",
                    ),
                    accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
                )
                for _ in range(2)
            )
            multiline = create_multi_line_draft_document(
                session,
                scope=scope,
                command=_multiline_command(values["billing_account_id"], obligations),
            )
            multiline_id = multiline.id
            session.commit()
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"DELETE FROM mod_billing.{prefix}document_lines "
                    "WHERE document_id=:id AND line_number=2"
                ),
                {"id": multiline_id},
            )
            connection.execute(
                text(
                    f"UPDATE mod_billing.{prefix}documents "
                    "SET subtotal=100,tax_total=20,grand_total=120 WHERE id=:id"
                ),
                {"id": multiline_id},
            )
        with pytest.raises(RuntimeError, match="multiline evidence"):
            alembic_command.downgrade(config, "bi_0001_billing")
        engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "fault",
    [
        "documented",
        "superseded",
        "cross_account",
        "currency",
        "service",
        "fx_different",
    ],
)
def test_multiline_refuses_ineligible_obligations(scratch, plane, fault):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"eligibility:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        selected = account
        currency = "NGN"
        if fault in {"cross_account", "currency"}:
            currency = "USD" if fault == "currency" else "NGN"
            selected = create_billing_account(
                session,
                scope=scope,
                external_account_ref=f"other:{uuid.uuid4()}",
                currency=currency,
                minor_units=2,
            )
        inputs = [
            _obligation_command(
                account.id, scope=scope, source_fact_id=f"eligibility:{uuid.uuid4()}"
            ),
            _obligation_command(
                selected.id,
                scope=scope,
                currency=currency,
                source_fact_id=f"eligibility:{uuid.uuid4()}",
            ),
        ]
        if fault == "service":
            inputs[1] = replace(inputs[1], service_ref="service:other")
        if fault == "fx_different":
            inputs[1] = replace(
                inputs[1],
                fx_snapshot=replace(inputs[1].fx_snapshot, observation_version="2"),
            )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=item,
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for item in inputs
        )
        if fault == "documented":
            create_draft_document(
                session, scope=scope, command=_draft_command(obligations[1].id)
            )
        if fault == "superseded":
            accept_rated_obligation(
                session,
                scope=scope,
                command=replace(
                    inputs[1],
                    source_fact_id=f"successor:{uuid.uuid4()}",
                    source_fact_version="2",
                    supersedes_obligation_id=obligations[1].id,
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
        with pytest.raises((BillingRuleViolation, BillingConflict)):
            create_multi_line_draft_document(
                session,
                scope=scope,
                command=_multiline_command(account.id, obligations),
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_concurrent_multiline_drafts_cannot_share_an_obligation(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"shared:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"shared:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(3)
        )
        commands = tuple(
            replace(
                _multiline_command(account.id, pair),
                idempotency_key=f"shared:{uuid.uuid4()}",
            )
            for pair in (
                (obligations[0], obligations[1]),
                (obligations[0], obligations[2]),
            )
        )
        session.commit()
    barrier = threading.Barrier(2)
    results, errors = [], []

    def runner(command):
        try:
            with _session(url, scope) as session:
                barrier.wait(timeout=20)
                document = create_multi_line_draft_document(
                    session, scope=scope, command=command
                )
                document_id = document.id
                session.commit()
                results.append(document_id)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=runner, args=(command,)) for command in commands]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], BillingConflict)
    assert errors[0].code == "billing.obligation_already_documented"


def test_multiline_cannot_read_other_tenant_obligation(scratch):
    admin_url, tenant_url, _ = scratch
    first, second = (TenantScope(tenant) for tenant in _create_tenants(admin_url, 2))
    with _session(tenant_url, first) as session:
        account = create_billing_account(
            session,
            scope=first,
            external_account_ref=f"foreign:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=first,
                command=_obligation_command(
                    account.id, scope=first, source_fact_id=f"foreign:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(2)
        )
        command = _multiline_command(account.id, obligations)
        session.commit()
    with _session(tenant_url, second) as session:
        account = create_billing_account(
            session,
            scope=second,
            external_account_ref=f"local:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        with pytest.raises(BillingRuleViolation):
            create_multi_line_draft_document(
                session,
                scope=second,
                command=replace(command, billing_account_id=account.id),
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_credit_note_snapshots_do_not_leak_from_unrelated_document_parents(
    scratch, plane
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        _, _, invoice = _seed_invoice(
            session, scope, suffix=f"tax-isolation:{uuid.uuid4()}"
        )
        credit_ids = []
        for index in range(2):
            credit = issue_credit_note(
                session,
                scope=scope,
                command=IssueCreditNote(
                    original_document_id=invoice.id,
                    pre_tax_amount=_money("10"),
                    tax_amount=_money("2"),
                    total_amount=_money("12"),
                    reason=f"tax correction {index}",
                    series_code="credit",
                    reference_date=NOW.date(),
                    actor_ref="billing-test",
                    correlation_id=f"credit-tax:{index}",
                    occurred_at=NOW.replace(minute=index),
                    document_profile_code="credit.standard",
                    document_profile_version="1",
                    tax_snapshots=(
                        AppliedTaxSnapshotV1(
                            treatment_code=f"credit-tax-{index}",
                            jurisdiction_code="zone",
                            policy_id="policy",
                            policy_version="1",
                            rate=Decimal("0.2"),
                            taxable_basis=_money("10"),
                            tax_amount=_money("2"),
                        ),
                    ),
                ),
                numbering=NUMBERING,
            )
            credit_ids.append(credit.id)
        fact_model = (
            models.InvoiceDocumentFact
            if plane == "tenant"
            else models.PlatformInvoiceDocumentFact
        )
        for index, credit_id in enumerate(credit_ids):
            fact = session.execute(
                select(fact_model).where(fact_model.document_id == credit_id)
            ).scalar_one()
            assert [
                tax["treatment_code"] for tax in fact.fact_payload["tax_snapshots"]
            ] == [f"credit-tax-{index}"]
            assert fact.fact_payload["fx_snapshot"] is None
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_multiline_authoring_mode_survives_draft_shrink_and_cannot_be_changed(
    scratch, plane
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    prefix = "" if plane == "tenant" else "platform_"
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"mode:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"mode:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(2)
        )
        draft = create_multi_line_draft_document(
            session, scope=scope, command=_multiline_command(account.id, obligations)
        )
        assert draft.authoring_mode == "explicit_multiline"
        document_id = draft.id
        session.commit()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(
                text(
                    f"UPDATE mod_billing.{prefix}documents "
                    "SET authoring_mode='legacy_single' WHERE id=:id"
                ),
                {"id": document_id},
            )
        assert error.value.orig.sqlstate == "23001"
        connection.execute(
            text(
                f"DELETE FROM mod_billing.{prefix}document_lines "
                "WHERE document_id=:id AND line_number=2"
            ),
            {"id": document_id},
        )
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}documents "
                "SET subtotal=100,tax_total=20,grand_total=120 WHERE id=:id"
            ),
            {"id": document_id},
        )
        connection.execute(
            text(
                f"UPDATE mod_billing.{prefix}document_lines "
                "SET quantity=3 WHERE document_id=:id"
            ),
            {"id": document_id},
        )
    with _session(url, scope) as session:
        with pytest.raises(BillingRuleViolation, match="at least two"):
            issue_document(
                session,
                scope=scope,
                command=_issue_command(document_id),
                numbering=NUMBERING,
            )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("fault", ["missing", "currency", "precision", "amount"])
def test_multiline_refuses_tax_evidence_that_disagrees_with_rated_money(
    scratch, plane, fault
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"tax-guard:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        inputs = [
            _obligation_command(
                account.id, scope=scope, source_fact_id=f"tax-guard:{uuid.uuid4()}"
            )
            for _ in range(2)
        ]
        snapshot = inputs[1].tax_snapshots[0]
        if fault == "currency":
            snapshot = replace(
                snapshot,
                taxable_basis=_money("100", "USD"),
                tax_amount=_money("20", "USD"),
            )
        if fault == "precision":
            snapshot = replace(
                snapshot,
                taxable_basis=Money.of("100", Currency("NGN", 3)),
                tax_amount=Money.of("20", Currency("NGN", 3)),
            )
        if fault == "amount":
            snapshot = replace(snapshot, tax_amount=_money("19"))
        inputs[1] = replace(
            inputs[1], tax_snapshots=() if fault == "missing" else (snapshot,)
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=command,
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for command in inputs
        )
        document_model = (
            models.BillingDocument
            if plane == "tenant"
            else models.PlatformBillingDocument
        )
        before = session.scalar(select(func.count()).select_from(document_model))
        with pytest.raises(BillingRuleViolation, match="tax"):
            create_multi_line_draft_document(
                session,
                scope=scope,
                command=_multiline_command(account.id, obligations),
            )
        assert (
            session.scalar(select(func.count()).select_from(document_model)) == before
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("fault", ["sum", "precision", "currency", "minor_units"])
def test_multiline_issue_revalidates_appended_tax_evidence_atomically(
    scratch, plane, fault
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    prefix = "" if plane == "tenant" else "platform_"
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"tax-tamper:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"tax-tamper:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(2)
        )
        draft = create_multi_line_draft_document(
            session, scope=scope, command=_multiline_command(account.id, obligations)
        )
        document_id, obligation_id = draft.id, obligations[1].id
        session.commit()
    engine = create_engine(admin_url)
    tenant_column = "tenant_id," if plane == "tenant" else ""
    tenant_value = ":tenant_id," if plane == "tenant" else ""
    with engine.begin() as connection:
        connection.execute(
            text(f"""INSERT INTO mod_billing.{prefix}applied_tax_snapshots
            ({tenant_column}id,obligation_id,document_id,treatment_code,jurisdiction_code,
             policy_id,policy_version,rate,taxable_basis,tax_amount,currency,minor_units)
            VALUES ({tenant_value}:id,:obligation_id,NULL,'extra','zone','policy','1',
                    0.2,1,:tax_amount,:currency,:minor_units)"""),
            {
                "id": uuid.uuid4(),
                "obligation_id": obligation_id,
                "tenant_id": scope.tenant_id if plane == "tenant" else None,
                "tax_amount": Decimal("0.001")
                if fault == "precision"
                else Decimal("1")
                if fault == "sum"
                else Decimal("0"),
                "currency": "USD" if fault == "currency" else "NGN",
                "minor_units": 3 if fault == "minor_units" else 2,
            },
        )
    with _session(url, scope) as session:
        with pytest.raises(BillingRuleViolation, match="tax"):
            issue_document(
                session,
                scope=scope,
                command=_issue_command(document_id),
                numbering=NUMBERING,
            )
        document_model = (
            models.BillingDocument
            if plane == "tenant"
            else models.PlatformBillingDocument
        )
        document = session.get(document_model, document_id)
        assert document.issued_at is None
        assert document.document_number is None
        fact_model = (
            models.InvoiceDocumentFact
            if plane == "tenant"
            else models.PlatformInvoiceDocumentFact
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(fact_model)
                .where(fact_model.document_id == document_id)
            )
            == 0
        )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_legacy_single_draft_keeps_its_quantity_contract_and_issued_replay(
    scratch, plane
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"legacy:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligation = accept_rated_obligation(
            session,
            scope=scope,
            command=_obligation_command(
                account.id, scope=scope, source_fact_id=f"legacy:{uuid.uuid4()}"
            ),
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        command = replace(_draft_command(obligation.id), quantity=Decimal("2"))
        draft = create_draft_document(session, scope=scope, command=command)
        assert draft.authoring_mode == "legacy_single"
        issued = issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        document_id = issued.id
        session.commit()
        assert (
            create_draft_document(session, scope=scope, command=command).id
            == document_id
        )
        with pytest.raises(IdempotencyConflict):
            create_draft_document(
                session, scope=scope, command=replace(command, quantity=Decimal("3"))
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("currency,amount", [("NGN", "1"), ("USD", "0")])
def test_multiline_rejects_document_owned_tax_and_issue_key_survives_rollback(
    scratch, plane, currency, amount
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    tax_model = (
        models.AppliedTaxSnapshot
        if plane == "tenant"
        else models.PlatformAppliedTaxSnapshot
    )
    fact_model = (
        models.InvoiceDocumentFact
        if plane == "tenant"
        else models.PlatformInvoiceDocumentFact
    )
    group_model = (
        models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
    )
    calls = []

    class CountingNumbering(_Numbering):
        def allocate(self, *args, **kwargs):
            calls.append(kwargs["idempotency_key"])
            return super().allocate(*args, **kwargs)

    numbering = CountingNumbering()
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"tax-owner:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"tax-owner:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(2)
        )
        draft = create_multi_line_draft_document(
            session, scope=scope, command=_multiline_command(account.id, obligations)
        )
        document_id, account_id = draft.id, account.id
        issue = _issue_command(document_id)
        session.commit()
        values = {
            "id": uuid.uuid4(),
            "document_id": document_id,
            "obligation_id": None,
            "treatment_code": "extra",
            "jurisdiction_code": "zone",
            "policy_id": "policy",
            "policy_version": "1",
            "rate": Decimal("0.2"),
            "taxable_basis": Decimal("1"),
            "tax_amount": Decimal(amount),
            "currency": currency,
            "minor_units": 2,
        }
        if plane == "tenant":
            values["tenant_id"] = scope.tenant_id
        with (
            pytest.raises(BillingRuleViolation, match="line obligations"),
            session.begin_nested(),
        ):
            session.add(tax_model(**values))
            session.flush()
            issue_document(session, scope=scope, command=issue, numbering=numbering)
        assert calls == []
        assert draft.issued_at is None
        assert draft.document_number is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(fact_model)
                .where(fact_model.document_id == document_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(group_model)
                .where(group_model.billing_account_id == account_id)
            )
            == 0
        )
        issued = issue_document(
            session, scope=scope, command=issue, numbering=numbering
        )
        assert issued.id == document_id
        assert issued.document_number is not None
        assert len(calls) == 1
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_document_fact_query_is_exact_and_isolated(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    left, right = _create_tenants(admin_url)
    scope = TenantScope(left) if plane == "tenant" else PlatformScope()
    url = tenant_url if plane == "tenant" else platform_url
    fact_model = (
        models.InvoiceDocumentFact
        if plane == "tenant"
        else models.PlatformInvoiceDocumentFact
    )
    with _session(url, scope) as session:
        _, _, invoice = _seed_invoice(session, scope, suffix=f"fact-query-{plane}")
        invoice_id = invoice.id
        request = GetInvoiceDocumentFactV1(scope, invoice_id, 1)
        persisted = session.scalars(
            select(fact_model).where(fact_model.document_id == invoice_id)
        ).one()
        original = get_invoice_document_fact(session, scope=scope, request=request)
        assert original.fact_id == persisted.id
        assert original.recorded_at == persisted.created_at
        assert original.contract_version == "billing.invoice.document.fact.v1"
        assert original.fact.invoice_id == invoice_id
        assert original.fact.fact_version == 1
        assert original.fact.document_state == "issued"
        assert original.fact.grand_total.amount == Decimal("120.00")
        for missing in (
            replace(request, invoice_id=uuid.uuid4()),
            replace(request, fact_version=2),
        ):
            with pytest.raises(BillingRuleViolation) as error:
                get_invoice_document_fact(session, scope=scope, request=missing)
            assert error.value.code == "billing.document_fact_not_found"

        # Reject routed-scope mismatch before touching the database, even when
        # that route would select a table forbidden to the connected role.
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(session.get_bind(), "before_cursor_execute", capture)
        try:
            wrong_scopes = (
                (TenantScope(right), PlatformScope())
                if plane == "tenant"
                else (TenantScope(left),)
            )
            for wrong_scope in wrong_scopes:
                with pytest.raises(BillingRuleViolation) as error:
                    get_invoice_document_fact(
                        session,
                        scope=scope,
                        request=replace(request, scope=wrong_scope),
                    )
                assert error.value.code == "billing.scope_mismatch"
            assert statements == []
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", capture)

        credit = issue_credit_note(
            session,
            scope=scope,
            command=IssueCreditNote(
                original_document_id=invoice_id,
                pre_tax_amount=_money("20.00"),
                tax_amount=_money("0.00"),
                total_amount=_money("20.00"),
                reason="historical correction",
                series_code="credit",
                reference_date=date(2026, 8, 17),
                actor_ref="billing-test",
                correlation_id=f"fact-query-credit-{plane}",
                occurred_at=NOW,
                document_profile_code="credit.standard",
                document_profile_version="1",
            ),
            numbering=NUMBERING,
        )
        assert (
            get_invoice_document_fact(session, scope=scope, request=request) == original
        )
        void_document(
            session,
            scope=scope,
            document_id=credit.id,
            actor_ref="billing-test",
            occurred_at=NOW + timedelta(days=1),
            source_ref=f"fact-query-void-credit-{plane}",
        )
        assert (
            get_invoice_document_fact(session, scope=scope, request=request) == original
        )
        void_document(
            session,
            scope=scope,
            document_id=invoice_id,
            actor_ref="billing-test",
            occurred_at=NOW + timedelta(days=1),
            source_ref=f"fact-query-void-{plane}",
        )
        cancelled = get_invoice_document_fact(
            session, scope=scope, request=replace(request, fact_version=2)
        )
        assert cancelled.fact.document_state == "cancelled"
        assert cancelled.fact_id != original.fact_id
        assert (
            get_invoice_document_fact(session, scope=scope, request=request) == original
        )
        session.commit()
    with _session(url, scope) as fresh:
        assert (
            get_invoice_document_fact(fresh, scope=scope, request=request) == original
        )
    if plane == "tenant":
        with _session(tenant_url, TenantScope(right)) as other:
            with pytest.raises(BillingRuleViolation) as error:
                get_invoice_document_fact(
                    other,
                    scope=TenantScope(right),
                    request=replace(request, scope=TenantScope(right)),
                )
            assert error.value.code == "billing.document_fact_not_found"
        with _session(platform_url, PlatformScope()) as other_plane:
            with pytest.raises(BillingRuleViolation) as error:
                get_invoice_document_fact(
                    other_plane,
                    scope=PlatformScope(),
                    request=replace(request, scope=PlatformScope()),
                )
            assert error.value.code == "billing.document_fact_not_found"
    else:
        with _session(tenant_url, TenantScope(left)) as other_plane:
            with pytest.raises(BillingRuleViolation) as error:
                get_invoice_document_fact(
                    other_plane,
                    scope=TenantScope(left),
                    request=replace(request, scope=TenantScope(left)),
                )
            assert error.value.code == "billing.document_fact_not_found"


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_document_fact_query_ignores_dirty_orm_and_preserves_caller_rollback(
    scratch, plane
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    fact_model = (
        models.InvoiceDocumentFact
        if plane == "tenant"
        else models.PlatformInvoiceDocumentFact
    )
    with _session(url, scope) as session:
        _, _, invoice = _seed_invoice(session, scope, suffix=f"fact-dirty-{plane}")
        request = GetInvoiceDocumentFactV1(scope, invoice.id, 1)
        original = get_invoice_document_fact(session, scope=scope, request=request)
        pending_account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"query-rollback-{plane}",
            currency="NGN",
            minor_units=2,
        )
        pending_id = pending_account.id
        fact_row = session.get(fact_model, original.fact_id)
        fact_row.fact_payload = {"corrupt": "identity-map-only"}
        fact_row.presentation_model_digest = "b" * 64
        invoice.document_number = "dirty-unflushed-number"
        assert fact_row in session.dirty
        assert invoice in session.dirty
        assert (
            get_invoice_document_fact(session, scope=scope, request=request) == original
        )
        assert fact_row in session.dirty
        assert invoice in session.dirty
        session.rollback()
        assert (
            get_invoice_document_fact(session, scope=scope, request=request) == original
        )
    account_model = (
        models.BillingAccount if plane == "tenant" else models.PlatformBillingAccount
    )
    with _session(url, scope) as fresh:
        assert fresh.get(account_model, pending_id) is None


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "fault,code",
    [
        ("unsupported_contract", "document_fact_contract_unsupported"),
        ("payload_not_object", "document_fact_corrupt"),
        ("missing_field", "document_fact_corrupt"),
        ("wrong_scope", "document_fact_corrupt"),
        ("wrong_invoice", "document_fact_corrupt"),
        ("wrong_version", "document_fact_corrupt"),
        ("wrong_nested_type", "document_fact_corrupt"),
        ("instant_precision", "document_fact_corrupt"),
        ("offset_precision", "document_fact_corrupt"),
        ("digest_and_payload", "document_fact_digest_mismatch"),
        ("payload_digest", "document_fact_digest_mismatch"),
        ("envelope_digest", "document_fact_digest_mismatch"),
        ("changed_content", "document_fact_digest_mismatch"),
    ],
)
def test_document_fact_query_refuses_persisted_corruption(scratch, plane, fault, code):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    fact_model = (
        models.InvoiceDocumentFact
        if plane == "tenant"
        else models.PlatformInvoiceDocumentFact
    )
    with _session(url, scope) as session:
        _, _, invoice = _seed_invoice(
            session, scope, suffix=f"fact-corrupt-{plane}-{fault}"
        )
        source = session.scalars(
            select(fact_model).where(fact_model.document_id == invoice.id)
        ).one()
        payload = deepcopy(source.fact_payload)
        payload["fact_version"] = 2
        if fault == "missing_field":
            del payload["payment_instructions"]
        elif fault == "wrong_scope":
            payload["scope"] = (
                {} if plane == "tenant" else {"tenant_id": str(uuid.uuid4())}
            )
        elif fault == "wrong_invoice":
            payload["invoice_id"] = str(uuid.uuid4())
        elif fault == "wrong_version":
            payload["fact_version"] = 3
        elif fault == "wrong_nested_type":
            payload["lines"][0]["quantity"] = 1.5
        elif fault == "instant_precision":
            payload["issued_at"] = "2026-08-17T10:00:00.1234567+00:00"
        elif fault == "offset_precision":
            payload["fx_snapshot"]["observed_at"] = (
                "2026-08-17T10:00:00+01:00:00.1234567"
            )
        semantic = {
            key: value
            for key, value in payload.items()
            if key != "presentation_model_digest"
        }
        digest = fingerprint_of(semantic)
        payload["presentation_model_digest"] = digest
        if fault == "payload_digest":
            payload["presentation_model_digest"] = "a" * 64
        elif fault == "envelope_digest":
            digest = "b" * 64
        elif fault == "changed_content":
            payload["document_number"] = "different-content-with-original-hash"
        elif fault == "payload_not_object":
            payload = []
        elif fault == "digest_and_payload":
            # Both are corrupt: original JSON digest verification must refuse
            # before decoding this invalid nested value.
            payload["lines"][0]["quantity"] = 1.5
        values = {
            "id": uuid.uuid4(),
            "document_id": invoice.id,
            "fact_version": 2,
            "contract_version": "billing.invoice.document.fact.v99"
            if fault == "unsupported_contract"
            else source.contract_version,
            "fact_payload": payload,
            "presentation_model_digest": digest,
        }
        if isinstance(scope, TenantScope):
            values["tenant_id"] = scope.tenant_id
        # Append a deliberately bad version through the same INSERT permission
        # as the producer. Historical immutable rows are never edited for tests.
        session.add(fact_model(**values))
        session.flush()
        with pytest.raises(BillingRuleViolation) as error:
            get_invoice_document_fact(
                session,
                scope=scope,
                request=GetInvoiceDocumentFactV1(scope, invoice.id, 2),
            )
        assert error.value.code == f"billing.{code}"
        assert error.value.details == {}
        assert "2026-08-17" not in str(error.value)
        session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "offset,microseconds",
    [
        ("Z", 0),
        ("+00:00:00.123456", 123456),
        ("-00:00:00.123456", -123456),
    ],
)
def test_document_fact_query_hashes_raw_stored_json_not_decoder_reserialization(
    scratch, plane, offset, microseconds
):
    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    fact_model = (
        models.InvoiceDocumentFact
        if plane == "tenant"
        else models.PlatformInvoiceDocumentFact
    )
    with _session(url, scope) as session:
        _, _, invoice = _seed_invoice(
            session, scope, suffix=f"fact-raw-hash-{plane}-{offset}"
        )
        source = session.scalars(
            select(fact_model).where(fact_model.document_id == invoice.id)
        ).one()
        payload = deepcopy(source.fact_payload)
        payload["fact_version"] = 2
        payload["issued_at"] = payload["issued_at"].replace("+00:00", offset)
        payload["grand_total"]["amount"] += "e0"
        digest = fingerprint_of(
            {
                key: value
                for key, value in payload.items()
                if key != "presentation_model_digest"
            }
        )
        payload["presentation_model_digest"] = digest
        values = {
            "id": uuid.uuid4(),
            "document_id": invoice.id,
            "fact_version": 2,
            "contract_version": source.contract_version,
            "fact_payload": payload,
            "presentation_model_digest": digest,
        }
        if isinstance(scope, TenantScope):
            values["tenant_id"] = scope.tenant_id
        session.add(fact_model(**values))
        session.flush()
        result = get_invoice_document_fact(
            session, scope=scope, request=GetInvoiceDocumentFactV1(scope, invoice.id, 2)
        )
        assert result.fact.presentation_model_digest == digest
        assert str(result.fact.grand_total.amount) == "120.00"
        expected_offset = timedelta(microseconds=microseconds)
        assert result.fact.issued_at.utcoffset() == expected_offset
        assert result.fact.issued_at == NOW.replace(tzinfo=timezone(expected_offset))
        assert result.fact.issued_at.isoformat() == payload["issued_at"].replace(
            "Z", "+00:00"
        )
        session.rollback()


def _withholding_command(
    account_id, document_id, *, scope, suffix="1", claimed="5.00", accepted=None
):
    """Frozen Tax owner evidence; fixtures never authorize finance adoption."""
    from dotmac_billing.commands import AcceptWithholdingDischarge
    from dotmac_billing.contracts import WithholdingTaxEvidenceV1, WithholdingTaxLineV1

    evidence = WithholdingTaxEvidenceV1(
        source_owner="dotmac-tax",
        determination_ref=f"tax-set:{suffix}",
        source_version="opaque-source-v1",
        source_fingerprint=f"source:{suffix}",
        result_fingerprint="rv1:" + "a" * 64,
        evidence_ref=f"tax-evidence:{suffix}",
        source_ref=f"document:{document_id}",
        component_ref=f"component:{suffix}",
        rule_ref="configured-rule",
        rule_version="1",
        treatment_code="withholding",
        calculation_base_code="subtotal",
        inclusive=False,
        taxable_basis=_money("100.00"),
        amount=_money(claimed),
        lines=(WithholdingTaxLineV1(1, _money("100.00"), None, _money(claimed)),),
        scope=scope,
        billing_account_id=account_id,
        document_id=document_id,
        recognition_basis_code="invoice_issued",
        fact_kind="invoice",
        transaction_side="sale",
        determined_at=NOW,
    )
    return AcceptWithholdingDischarge(
        scope=scope,
        billing_account_id=account_id,
        document_id=document_id,
        idempotency_key=f"wht-retry:{suffix}",
        source_owner="customer-remittance",
        source_ref=f"wht-source:{suffix}",
        claim_ref=f"wht-claim:{suffix}",
        claimed_amount=_money(claimed),
        accepted_amount=_money(accepted or claimed),
        tax_evidence=evidence,
        authorization_actor_ref="finance-user:fixture",
        authorization_evidence_ref="trusted-assembly-audit:fixture",
        occurred_at=NOW,
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_withholding_partial_replay_correction_and_bypass_refusal(scratch, plane):
    from dotmac_billing.commands import ReverseWithholdingDischarge
    from dotmac_billing.service import (
        accept_withholding_discharge,
        reverse_withholding_discharge,
    )

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(session, scope, suffix=f"wht-{plane}")
        command = _withholding_command(
            account.id, invoice.id, scope=scope, suffix=plane, accepted="3.00"
        )
        from dotmac_billing.contracts import WithholdingCertificateObservationV1

        command = replace(
            command,
            certificate_observation=WithholdingCertificateObservationV1(
                "customer-evidence",
                "certificate-observation:1",
                "observed_unverified",
                "certificate:file:1",
                NOW,
            ),
        )
        # Constructing a claim or certificate is observational and posts nothing.
        before = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert before.collectible_receivable == Decimal("120")
        row = accept_withholding_discharge(session, scope=scope, command=command)
        after = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert after.collectible_receivable == Decimal("117")
        assert after.available_credit == before.available_credit == 0
        assert after.prepaid_funding == before.prepaid_funding == 0
        assert (
            accept_withholding_discharge(session, scope=scope, command=command).id
            == row.id
        )
        with pytest.raises(IdempotencyConflict):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=replace(command, authorization_evidence_ref="changed"),
            )
        with pytest.raises(BillingConflict):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=replace(command, idempotency_key="fresh-retry"),
            )
        with pytest.raises(BillingConflict):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=replace(
                    command,
                    idempotency_key="fresh-source-retry",
                    source_ref="changed-source",
                    claim_ref="changed-claim",
                ),
            )
        with pytest.raises(BillingRuleViolation, match="withholding"):
            void_document(
                session,
                scope=scope,
                document_id=invoice.id,
                source_ref="void-wht",
                actor_ref="fixture",
                occurred_at=NOW,
            )
        with pytest.raises(BillingRuleViolation, match="withholding"):
            reverse_posting_group(
                session,
                scope=scope,
                command=ReversePostingGroupCommand(
                    row.posting_group_id, NOW, "generic-wht-reverse"
                ),
            )
        correction = ReverseWithholdingDischarge(
            scope,
            row.id,
            "wht-correction-retry",
            "wht-correction",
            "finance-user:fixture",
            "audit:correction",
            NOW,
        )
        inverse = reverse_withholding_discharge(
            session, scope=scope, command=correction
        )
        assert inverse.amount_delta == Decimal("-3")
        assert inverse.reverses_discharge_id == row.id
        assert inverse.evidence["tax_evidence"] == row.evidence["tax_evidence"]
        assert (
            reverse_withholding_discharge(session, scope=scope, command=correction).id
            == inverse.id
        )
        assert rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        ).collectible_receivable == Decimal("120")
        fact_model = (
            models.AccountingFact
            if plane == "tenant"
            else models.PlatformAccountingFact
        )
        facts = (
            session.execute(
                select(fact_model).where(
                    fact_model.posting_group_id.in_(
                        (row.posting_group_id, inverse.posting_group_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {fact.fact_version for fact in facts} == {2}
        assert all("withholding_discharges" in fact.fact_payload for fact in facts)
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_withholding_107500_invoice_102500_cash_5000_discharge(scratch, plane):
    from dotmac_billing.service import accept_withholding_discharge

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"wht-net-cash-{plane}",
            currency="NGN",
            minor_units=2,
        )
        rated = replace(
            _obligation_command(
                account.id, scope=scope, source_fact_id=f"gross-{plane}"
            ),
            pre_tax_amount=_money("100000"),
            tax_amount=_money("7500"),
            total_amount=_money("107500"),
            tax_snapshots=(
                AppliedTaxSnapshotV1(
                    "standard",
                    "source-zone",
                    "vat-policy",
                    "1",
                    Decimal("0.075"),
                    _money("100000"),
                    _money("7500"),
                ),
            ),
        )
        obligation = accept_rated_obligation(
            session,
            scope=scope,
            command=rated,
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        draft = create_draft_document(
            session, scope=scope, command=_draft_command(obligation.id)
        )
        invoice = issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        cash = accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id, scope=scope, source_key=f"actual-{plane}", amount="102500"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        cash_group = allocate_settlement(
            session,
            scope=scope,
            command=AllocationCommand(
                cash.id, invoice.id, _money("102500"), NOW, f"cash-allocate:{plane}"
            ),
        )
        allocation_model = (
            models.AllocationEffect
            if plane == "tenant"
            else models.PlatformAllocationEffect
        )
        cash_allocation = session.execute(
            select(allocation_model).where(
                allocation_model.posting_group_id == cash_group.id
            )
        ).scalar_one()
        command = _withholding_command(
            account.id, invoice.id, scope=scope, suffix=f"net-{plane}", claimed="5000"
        )
        command = replace(
            command, settlement_id=cash.id, allocation_id=cash_allocation.id
        )
        row = accept_withholding_discharge(session, scope=scope, command=command)
        assert (
            accept_withholding_discharge(session, scope=scope, command=command).id
            == row.id
        )  # replay after AR reached zero
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert (
            position.collectible_receivable
            == position.available_credit
            == position.prepaid_funding
            == 0
        )
        assert cash.amount == Decimal("102500")
        assert (
            session.execute(
                select(func.count())
                .select_from(allocation_model)
                .where(allocation_model.posting_group_id == row.posting_group_id)
            ).scalar_one()
            == 0
        )
        exposure_model = (
            models.ReceivableExposureFact
            if plane == "tenant"
            else models.PlatformReceivableExposureFact
        )
        assert (
            session.execute(
                select(exposure_model).where(
                    exposure_model.posting_group_watermark == row.posting_group_id
                )
            )
            .scalar_one()
            .collectible_receivable
            == 0
        )
        session.commit()


def test_withholding_cannot_replay_or_post_across_tenants(scratch):
    from dotmac_billing.service import accept_withholding_discharge

    admin_url, tenant_url, _ = scratch
    left, right = _create_tenants(admin_url)
    scope_a, scope_b = TenantScope(left), TenantScope(right)
    with _session(tenant_url, scope_a) as session:
        account, _, invoice = _seed_invoice(session, scope_a, suffix="wht-isolation")
        command = _withholding_command(account.id, invoice.id, scope=scope_a)
        row = accept_withholding_discharge(session, scope=scope_a, command=command)
        discharge_id = row.id
        session.commit()
    with _session(tenant_url, scope_b) as session:
        with pytest.raises(BillingRuleViolation, match="scope"):
            accept_withholding_discharge(session, scope=scope_b, command=command)
        offered = replace(
            command,
            scope=scope_b,
            tax_evidence=replace(command.tax_evidence, scope=scope_b),
        )
        with pytest.raises(BillingRuleViolation, match="not found"):
            accept_withholding_discharge(session, scope=scope_b, command=offered)
        assert (
            session.execute(
                select(models.WithholdingDischarge).where(
                    models.WithholdingDischarge.id == discharge_id
                )
            ).first()
            is None
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_withholding_rollback_removes_discharge_position_fact_and_retry(scratch, plane):
    from dotmac_billing.service import accept_withholding_discharge

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(
            session, scope, suffix=f"wht-rollback-{plane}"
        )
        command = _withholding_command(
            account.id, invoice.id, scope=scope, suffix=f"rollback-{plane}"
        )
        row = accept_withholding_discharge(session, scope=scope, command=command)
        discharge_id, group_id, account_id = row.id, row.posting_group_id, account.id
        session.rollback()
    with _session(url, scope) as session:
        discharge_model = (
            models.WithholdingDischarge
            if plane == "tenant"
            else models.PlatformWithholdingDischarge
        )
        group_model = (
            models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
        )
        fact_model = (
            models.AccountingFact
            if plane == "tenant"
            else models.PlatformAccountingFact
        )
        outbox_model = OutboxEvent if plane == "tenant" else PlatformOutboxEvent
        assert session.get(discharge_model, discharge_id) is None
        assert session.get(group_model, group_id) is None
        assert (
            session.execute(
                select(fact_model).where(fact_model.posting_group_id == group_id)
            ).first()
            is None
        )
        assert (
            session.execute(
                select(outbox_model).where(
                    outbox_model.event_type == "billing.accounting.fact.v2",
                    outbox_model.payload["posting_group_id"].as_string()
                    == str(group_id),
                    outbox_model.payload["billing_account_id"].as_string()
                    == str(account_id),
                    outbox_model.payload["source_ref"].as_string()
                    == command.source_ref,
                )
            ).first()
            is None
        )
        assert rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        ).collectible_receivable == Decimal("120")
        retried = accept_withholding_discharge(session, scope=scope, command=command)
        assert retried.id != discharge_id
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_withholding_and_cash_allocation_serialize_invoice_room(scratch, plane):
    from dotmac_billing.service import accept_withholding_discharge

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(session, scope, suffix=f"wht-race-{plane}")
        settlement = accept_settlement(
            session,
            scope=scope,
            command=_settlement_command(
                account.id, scope=scope, source_key=f"wht-race-{plane}", amount="120"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        wht_command = _withholding_command(
            account.id, invoice.id, scope=scope, suffix=f"race-{plane}", claimed="120"
        )
        cash_command = AllocationCommand(
            settlement.id, invoice.id, _money("120"), NOW, f"cash-race:{plane}"
        )
        session.commit()
        account_id = account.id
    barrier = threading.Barrier(2)
    accepted, refused, errors = [], [], []

    def runner(withholding):
        try:
            with _session(url, scope) as session:
                barrier.wait(timeout=20)
                try:
                    if withholding:
                        result = accept_withholding_discharge(
                            session, scope=scope, command=wht_command
                        )
                    else:
                        result = allocate_settlement(
                            session, scope=scope, command=cash_command
                        )
                    session.commit()
                    accepted.append(result.id)
                except BillingRuleViolation as exc:
                    refused.append(exc)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(kind,)) for kind in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "withholding/cash admission deadlocked"
    assert errors == []
    assert len(accepted) == len(refused) == 1
    with _session(url, scope) as session:
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        )
        assert position.collectible_receivable == 0
        assert position.available_credit in (Decimal("0"), Decimal("120"))
        assert position.prepaid_funding == 0


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_withholding_and_credit_note_serialize_invoice_room(scratch, plane):
    from dotmac_billing.service import accept_withholding_discharge

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(
            session, scope, suffix=f"wht-credit-race-{plane}"
        )
        wht_command = _withholding_command(
            account.id,
            invoice.id,
            scope=scope,
            suffix=f"credit-race-{plane}",
            claimed="120",
        )
        credit_command = IssueCreditNote(
            invoice.id,
            _money("120"),
            _money("0"),
            _money("120"),
            "correction",
            "credit",
            NOW.date(),
            "fixture",
            f"credit-wht-race:{plane}",
            NOW,
            "credit.standard",
            "1",
        )
        account_id = account.id
    barrier = threading.Barrier(2)
    accepted, refused, errors = [], [], []

    def runner(withholding):
        try:
            with _session(url, scope) as session:
                barrier.wait(timeout=20)
                try:
                    if withholding:
                        result = accept_withholding_discharge(
                            session, scope=scope, command=wht_command
                        )
                    else:
                        result = issue_credit_note(
                            session,
                            scope=scope,
                            command=credit_command,
                            numbering=NUMBERING,
                        )
                    session.commit()
                    accepted.append(result.id)
                except BillingRuleViolation as exc:
                    refused.append(exc)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(kind,)) for kind in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "withholding/credit-note admission deadlocked"
    assert errors == []
    assert len(accepted) == len(refused) == 1
    with _session(url, scope) as session:
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
        )
        assert (
            position.collectible_receivable
            == position.available_credit
            == position.prepaid_funding
            == 0
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_invoice_generic_reversal_cannot_borrow_another_invoice_ar(scratch, plane):
    from dotmac_billing.service import accept_withholding_discharge

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(
            session, scope, suffix=f"wht-two-invoices-{plane}"
        )
        draft = _additional_credit_invoice(
            session, scope, account.id, f"second-ar-{plane}"
        )
        issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        group_model = (
            models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
        )
        issuance = session.execute(
            select(group_model).where(
                group_model.source_ref == f"document:{invoice.id}:issue"
            )
        ).scalar_one()
        reverse = ReversePostingGroupCommand(
            issuance.id, NOW, f"generic-issue-reverse:{plane}"
        )
        with pytest.raises(BillingRuleViolation, match="void_document"):
            reverse_posting_group(session, scope=scope, command=reverse)
        command = _withholding_command(
            account.id, invoice.id, scope=scope, suffix=f"two-ar-{plane}"
        )
        row = accept_withholding_discharge(session, scope=scope, command=command)
        with pytest.raises(BillingRuleViolation, match="void_document"):
            reverse_posting_group(session, scope=scope, command=reverse)
        assert (
            accept_withholding_discharge(session, scope=scope, command=command).id
            == row.id
        )
        assert rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        ).collectible_receivable == Decimal("235")
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_legacy_invoice_reverse_exact_replay_precedes_new_owner_refusal(scratch, plane):
    from dataclasses import asdict

    from dotmac_billing.service import (
        _idempotent,
        _reverse_group,
        accept_withholding_discharge,
    )

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(
            session, scope, suffix=f"legacy-reverse-{plane}"
        )
        draft = _additional_credit_invoice(
            session, scope, account.id, f"legacy-second-{plane}"
        )
        issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        group_model = (
            models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
        )
        original = session.execute(
            select(group_model).where(
                group_model.source_ref == f"document:{invoice.id}:issue"
            )
        ).scalar_one()
        command = ReversePostingGroupCommand(
            original.id, NOW, f"legacy-invoice-reverse:{plane}"
        )

        def predecessor_operation(db):
            # Seed the exact previous public operation, not a fabricated replay result.
            reversal = _reverse_group(
                db,
                scope=scope,
                original=original,
                occurred_at=command.occurred_at,
                source_ref=command.source_ref,
            )
            return {"posting_group_id": str(reversal.id)}

        outcome = _idempotent(
            session,
            scope=scope,
            operation_scope="billing.posting.reverse",
            key=command.source_ref,
            fingerprint=fingerprint_of(asdict(command)),
            operation=predecessor_operation,
        )
        session.commit()
        assert reverse_posting_group(
            session, scope=scope, command=command
        ).id == uuid.UUID(str(outcome.result["posting_group_id"]))
        with pytest.raises(BillingRuleViolation, match="void_document"):
            reverse_posting_group(
                session,
                scope=scope,
                command=replace(command, source_ref="new-invoice-reverse"),
            )
        wht = _withholding_command(
            account.id, invoice.id, scope=scope, suffix=f"legacy-reversed-wht-{plane}"
        )
        with pytest.raises(BillingRuleViolation, match="structurally bound"):
            accept_withholding_discharge(session, scope=scope, command=wht)
        assert rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        ).collectible_receivable == Decimal("120")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_private_generic_reverse_cannot_bypass_withholding_inverse_owner(
    scratch, plane
):
    from dotmac_billing.commands import ReverseWithholdingDischarge
    from dotmac_billing.service import (
        _reverse_group,
        accept_withholding_discharge,
        reverse_withholding_discharge,
    )

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account, _, invoice = _seed_invoice(
            session, scope, suffix=f"private-wht-reverse-{plane}"
        )
        row = accept_withholding_discharge(
            session,
            scope=scope,
            command=_withholding_command(
                account.id, invoice.id, scope=scope, suffix=f"private-reverse-{plane}"
            ),
        )
        group = (
            models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
        )
        original = session.get(group, row.posting_group_id)
        with pytest.raises(BillingRuleViolation, match="withholding correction"):
            _reverse_group(
                session,
                scope=scope,
                original=original,
                occurred_at=NOW,
                source_ref="private-wht-bypass",
            )
        correction = ReverseWithholdingDischarge(
            scope,
            row.id,
            f"dedicated-retry:{plane}",
            f"dedicated-reverse:{plane}",
            "finance:fixture",
            "audit:fixture",
            NOW,
        )
        inverse = reverse_withholding_discharge(
            session, scope=scope, command=correction
        )
        assert inverse.amount_delta == -row.amount_delta
        assert inverse.reverses_discharge_id == row.id
        assert (
            reverse_withholding_discharge(session, scope=scope, command=correction).id
            == inverse.id
        )
        with pytest.raises(BillingRuleViolation, match="withholding correction"):
            _reverse_group(
                session,
                scope=scope,
                original=session.get(group, inverse.posting_group_id),
                occurred_at=NOW,
                source_ref="private-inverse-bypass",
            )
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_persisted_public_document_fact_maps_to_complete_v2_render(scratch, plane):
    """Real migrated query -> assembly map -> V2 bytes, on both security planes."""
    from dotmac_billing import RegisteredIdentifierSnapshotV1
    from dotmac_document_rendering import (
        DeterministicHtmlRendererV2,
        RenderOutcome,
        render_document_v2,
    )

    from tests.document_rendering_fact_adapter import map_stored_fact
    from tests.unit.test_billing_document_rendering_contract import projected_values
    from tests.unit.test_document_rendering_v2 import catalog, request

    admin_url, tenant_url, platform_url = scratch
    scope = (
        TenantScope(_create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = tenant_url if plane == "tenant" else platform_url
    with _session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"render-v2:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        obligations = tuple(
            accept_rated_obligation(
                session,
                scope=scope,
                command=_obligation_command(
                    account.id, scope=scope, source_fact_id=f"render-v2:{uuid.uuid4()}"
                ),
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            for _ in range(9)
        )
        command = _multiline_command(account.id, obligations)
        command = replace(
            command,
            document_profile_version="opaque:billing:v7",
            seller_snapshot=replace(
                command.seller_snapshot,
                trading_name="Frozen seller trading name",
                registered_identifiers=(
                    RegisteredIdentifierSnapshotV1("company", "RC-QUERY", "NGA"),
                    RegisteredIdentifierSnapshotV1("licence", "LIC-QUERY", "NGA"),
                ),
            ),
            payment_instructions=replace(
                command.payment_instructions, narrative="Frozen payment narrative"
            ),
        )
        draft = create_multi_line_draft_document(session, scope=scope, command=command)
        issued = issue_document(
            session, scope=scope, command=_issue_command(draft.id), numbering=NUMBERING
        )
        invoice_id = issued.id
        session.commit()
    with _session(url, scope) as fresh:
        stored = get_invoice_document_fact(
            fresh, scope=scope, request=GetInvoiceDocumentFactV1(scope, invoice_id, 1)
        )
        mapped = map_stored_fact(stored)
        assert mapped.persisted_fact_id == stored.fact_id
        assert mapped.recorded_at == stored.recorded_at
        assert (
            mapped.original_presentation_model_digest
            == stored.fact.presentation_model_digest
        )
        assert mapped.series_version is None
        assert mapped.document_profile_version == "opaque:billing:v7"
        assert stored.fact.due_date_basis.payment_terms_code == "net_30"
        assert stored.fact.due_date_basis.payment_terms_version == "1"
        assert mapped.due_date_basis.payment_terms_code == "net_30"
        assert mapped.due_date_basis.payment_terms_version == "1"
        assert mapped.payment_instructions.payment_link is None
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(fresh.get_bind(), "before_cursor_execute", capture)
        try:
            result = render_document_v2(
                request(mapped),
                catalog=catalog(mapped),
                renderer=DeterministicHtmlRendererV2(),
            )
        finally:
            event.remove(fresh.get_bind(), "before_cursor_execute", capture)
        assert statements == []
        assert result.outcome is RenderOutcome.RENDERED
        assert result.scope == scope
        assert result.persisted_fact_id == stored.fact_id
        assert (
            result.original_presentation_model_digest
            == stored.fact.presentation_model_digest
        )
        assert result.projection is not None
        assert (
            result.projection.source_fact_fingerprint == result.source_fact_fingerprint
        )
        values = projected_values(result.projection)
        assert (
            values["seller_snapshot.trading_name"].raw == "Frozen seller trading name"
        )
        assert (
            values["seller_snapshot.registered_identifiers[1].identifier_value"].raw
            == "LIC-QUERY"
        )
        assert (
            values["payment_instructions.narrative"].raw == "Frozen payment narrative"
        )
        assert values["grand_total"].raw == format(stored.fact.grand_total.amount, "f")
        for index, line in enumerate(stored.fact.lines):
            prefix = f"lines[{index}]"
            assert values[f"{prefix}.line_number"].raw == str(index + 1)
            assert mapped.lines[index].recorded_line.fact_id == stored.fact_id
            assert mapped.lines[index].price_source_version == line.price_source_version
            for field in (
                "unit_amount",
                "pre_tax_amount",
                "discount_total",
                "tax_amount",
                "total_amount",
            ):
                assert values[f"{prefix}.{field}"].raw == format(
                    getattr(line, field).amount, "f"
                )
            assert line.description.encode() in result.payload
        assert len(stored.fact.lines) == 9
        assert b"Frozen payment narrative" in result.payload
        assert b"Source supplied bank" in result.payload
        assert b"https://" not in result.payload
