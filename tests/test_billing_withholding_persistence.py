"""Hosted PostgreSQL canaries for immutable noncash withholding storage."""

from __future__ import annotations

# Identifiers come solely from the two fixed module planes; values are bound.
# ruff: noqa: S608
import hashlib
import importlib.util
import shutil
import subprocess
import uuid
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest
from dotmac_billing import models
from dotmac_billing.contracts import (
    WithholdingDischargeEffectV1,
)
from dotmac_billing.service import _jsonable
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests import test_billing_isolation as billing

REVISION = "bi_0004_withholding_discharge"
MIGRATION = billing.BILLING_VERSIONS / f"{REVISION}.py"
RELEASE = "92a1626b16d7e068f92536d8cfcb2ef9b6f270c2"
RELEASE_DIGEST = "f82a962e7c0745cf11e2c8187042a5af412dd80a254be5b2e0975d0c7aa36373"


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _tables(plane):
    return (
        (models.WithholdingDischarge, models.PostingGroup, models.PostingEffect)
        if plane == "tenant"
        else (
            models.PlatformWithholdingDischarge,
            models.PlatformPostingGroup,
            models.PlatformPostingEffect,
        )
    )


def _invoice(connection, plane, tenant=None, *, legacy=False):
    """Raw historical issuance for predecessor and bad-account fixtures only."""
    prefix = "" if plane == "tenant" else "platform_"
    scope_columns = "tenant_id, " if tenant else ""
    scope_values = ":tenant, " if tenant else ""
    account, document = uuid.uuid4(), uuid.uuid4()
    values = {
        "tenant": tenant,
        "account": account,
        "document": document,
        "line": uuid.uuid4(),
        "number": f"invoice:{document}",
        "now": billing.NOW,
    }
    connection.execute(
        text(f"""
        INSERT INTO mod_billing.{prefix}billing_accounts
            (id, {scope_columns}external_account_ref, currency, minor_units)
        VALUES (:account, {scope_values}:number, 'NGN', 2)
    """),
        values,
    )
    connection.execute(
        text(f"""
        INSERT INTO mod_billing.{prefix}documents
            (id, {scope_columns}billing_account_id, document_kind, currency,
             minor_units,
             subtotal, tax_total, grand_total, due_date_basis, document_profile_code,
             document_profile_version, seller_snapshot, customer_snapshot,
             payment_instructions, brand_asset, locale, timezone)
        VALUES (:document, {scope_values}:account, 'invoice', 'NGN', 2,
                100, 0, 100, '{{}}', 'invoice.standard', '1', '{{}}', '{{}}',
                '{{}}', '{{}}', 'en-NG', 'Africa/Lagos')
    """),
        values,
    )
    connection.execute(
        text(f"""
        INSERT INTO mod_billing.{prefix}document_lines
            (id, {scope_columns}{'' if legacy else 'billing_account_id, '}document_id,
             line_number, description, quantity, unit_code, unit_amount,
             pre_tax_amount, tax_amount, total_amount, currency, minor_units,
             price_source_version)
        VALUES (:line, {scope_values}{'' if legacy else ':account, '}:document,
                1, 'historical service', 1, 'each', 100, 100, 0, 100, 'NGN', 2, '1')
    """),
        values,
    )
    connection.execute(
        text(f"""
        UPDATE mod_billing.{prefix}documents SET lifecycle = 'issued',
            document_number = :number, issued_at = :now WHERE id = :document
    """),
        values,
    )
    return account, document


def _row(connection, plane, account, document, tenant=None, *, original=None):
    discharge, group, _ = _tables(plane)
    row_id, group_id = uuid.uuid4(), uuid.uuid4()
    identity = str(uuid.uuid4())
    money = billing._money
    tax = billing._withholding_command(
        account,
        document,
        scope=TenantScope(tenant) if tenant else PlatformScope(),
        suffix=identity,
        claimed="20",
        accepted="10",
    ).tax_evidence
    effect = WithholdingDischargeEffectV1(
        discharge_id=row_id,
        document_id=document,
        claim_ref=f"claim:{identity}",
        claimed_amount=money("20"),
        accepted_delta=money("10"),
        tax_evidence=tax,
        authorization_actor_ref="authorized-reviewer",
        authorization_evidence_ref="decision:1",
    )
    row = {
        "id": row_id,
        "billing_account_id": account,
        "document_id": document,
        "posting_group_id": group_id,
        "source_owner": "customer",
        "source_ref": identity,
        "claim_ref": effect.claim_ref,
        "tax_source_owner": tax.source_owner,
        "determination_ref": tax.determination_ref,
        "component_ref": tax.component_ref,
        "claimed_amount": Decimal("20"),
        "amount_delta": Decimal("10"),
        "currency": "NGN",
        "minor_units": 2,
        "evidence": _jsonable(effect),
        "settlement_id": None,
        "allocation_id": None,
        "reverses_discharge_id": None,
    }
    if tenant:
        row["tenant_id"] = tenant
    if original:
        for key in (
            "billing_account_id",
            "document_id",
            "claim_ref",
            "tax_source_owner",
            "determination_ref",
            "component_ref",
            "claimed_amount",
            "currency",
            "minor_units",
            "settlement_id",
            "allocation_id",
        ):
            row[key] = original[key]
        row["amount_delta"] = -original["amount_delta"]
        row["reverses_discharge_id"] = original["id"]
        row["evidence"] = deepcopy(original["evidence"])
        row["evidence"].update(
            discharge_id=str(row_id), reverses_discharge_id=str(original["id"])
        )
        row["evidence"]["accepted_delta"]["amount"] = str(row["amount_delta"])
    # The function has no side effects; both model tables are read explicitly.
    assert discharge.__table__.name.endswith("withholding_discharges")
    version = connection.execute(
        select(group.__table__.c.source_version)
        .where(group.__table__.c.billing_account_id == account)
        .order_by(group.__table__.c.source_version.desc())
        .limit(1)
    ).scalar_one_or_none()
    posting = {
        "id": group_id,
        "billing_account_id": account,
        "group_kind": "withholding_reversed" if original else "withholding_accepted",
        "currency": "NGN",
        "minor_units": 2,
        "source_ref": identity,
        "source_version": (version or 0) + 1,
        "occurred_at": billing.NOW,
        "reverses_group_id": original["posting_group_id"] if original else None,
    }
    if tenant:
        posting["tenant_id"] = tenant
    return row, posting


def _next_raw_version(connection, plane, account_id, tenant=None):
    account = (
        models.BillingAccount if plane == "tenant" else models.PlatformBillingAccount
    ).__table__
    group = _tables(plane)[1].__table__
    statement = select(account.c.id).where(account.c.id == account_id)
    if tenant:
        statement = statement.where(account.c.tenant_id == tenant)
    connection.execute(statement.with_for_update()).scalar_one()
    statement = select(func.coalesce(func.max(group.c.source_version), 0)).where(
        group.c.billing_account_id == account_id
    )
    if tenant:
        statement = statement.where(group.c.tenant_id == tenant)
    return connection.execute(statement).scalar_one() + 1


def _insert(connection, plane, row, posting, *, effect=True, snapshots=True):
    discharge, group, posting_effect = _tables(plane)
    posting["source_version"] = _next_raw_version(
        connection, plane, posting["billing_account_id"], posting.get("tenant_id")
    )
    connection.execute(group.__table__.insert().values(**posting))
    connection.execute(discharge.__table__.insert().values(**row))
    if effect:
        values = {
            key: row[key]
            for key in (
                "billing_account_id",
                "posting_group_id",
                "currency",
                "minor_units",
            )
        }
        values.update(
            id=uuid.uuid4(), lane="receivable", amount_delta=-row["amount_delta"]
        )
        if "tenant_id" in row:
            values["tenant_id"] = row["tenant_id"]
        connection.execute(posting_effect.__table__.insert().values(**values))
    if snapshots:
        # Valid latest-lineage raw setup includes both immutable position proofs.
        # This import is local because the noncash canaries reuse these fixtures.
        from tests.test_billing_non_cash_persistence import _snapshots

        _snapshots(connection, plane, posting)


def _seed(scratch, plane):
    tenant = billing._create_tenants(scratch[0], 1)[0] if plane == "tenant" else None
    engine = create_engine(scratch[0])
    scope = TenantScope(tenant) if tenant else PlatformScope()
    url = scratch[1] if tenant else scratch[2]
    with billing._session(url, scope) as session:
        account = billing.create_billing_account(
            session,
            scope=scope,
            external_account_ref=f"sql-wht:{uuid.uuid4()}",
            currency="NGN",
            minor_units=2,
        )
        command = replace(
            billing._obligation_command(
                account.id, scope=scope, source_fact_id=f"sql-wht:{uuid.uuid4()}"
            ),
            tax_amount=billing._money("0"),
            total_amount=billing._money("100"),
            tax_snapshots=(),
        )
        obligation = billing.accept_rated_obligation(
            session,
            scope=scope,
            command=command,
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        draft = billing.create_draft_document(
            session, scope=scope, command=billing._draft_command(obligation.id)
        )
        document = billing.issue_document(
            session,
            scope=scope,
            command=billing._issue_command(draft.id),
            numbering=billing.NUMBERING,
        )
        account_id, document_id = account.id, document.id
        session.commit()
    with engine.begin() as connection:
        row, posting = _row(connection, plane, account_id, document_id, tenant)
        _insert(connection, plane, row, posting)
    return engine, row


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_online_grants_rls_and_owner_append_only(scratch, plane):
    engine, row = _seed(scratch, plane)
    prefix = "" if plane == "tenant" else "platform_"
    table = f"{prefix}withholding_discharges"
    role = "app_user" if plane == "tenant" else "platform_api"
    wrong = "platform_api" if plane == "tenant" else "app_user"
    with engine.begin() as connection:
        assert billing._actual_table_privileges(
            connection, table=f"mod_billing.{table}", role=role
        ) == frozenset({"SELECT", "INSERT"})
        assert not billing._actual_table_privileges(
            connection, table=f"mod_billing.{table}", role=wrong
        )
        catalog = connection.execute(
            text("""
            SELECT relrowsecurity, relforcerowsecurity FROM pg_class
            WHERE oid = to_regclass(:table)
        """),
            {"table": f"mod_billing.{table}"},
        ).one()
        assert tuple(catalog) == ((True, True) if plane == "tenant" else (False, False))
        for mutation in (
            f"UPDATE mod_billing.{table} SET id = id WHERE id = :id",
            f"UPDATE mod_billing.{table} SET amount_delta = amount_delta + 1 "
            "WHERE id = :id",
            f"DELETE FROM mod_billing.{table} WHERE id = :id",
        ):
            with pytest.raises(DBAPIError) as error, connection.begin_nested():
                connection.execute(text(mutation), {"id": row["id"]})
            assert error.value.orig.sqlstate == "23001"
        if plane == "tenant":
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("DELETE FROM public.tenants WHERE id = :id"),
                    {"id": row["tenant_id"]},
                )
        assert (
            connection.execute(
                text(f"SELECT count(*) FROM mod_billing.{table} WHERE id = :id"),
                {"id": row["id"]},
            ).scalar_one()
            == 1
        )
    online = (
        billing._tenant_engine(scratch[1], row["tenant_id"])
        if plane == "tenant"
        else create_engine(scratch[2])
    )
    with online.begin() as connection:
        assert (
            connection.execute(
                text(f"SELECT count(*) FROM mod_billing.{table} WHERE id = :id"),
                {"id": row["id"]},
            ).scalar_one()
            == 1
        )
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(
                text(f"UPDATE mod_billing.{table} SET id = id WHERE id = :id"),
                {"id": row["id"]},
            )
        assert error.value.orig.sqlstate == "42501"
    online.dispose()
    if plane == "tenant":
        other = billing._create_tenants(scratch[0], 1)[0]
        hidden = billing._tenant_engine(scratch[1], other)
        with hidden.begin() as connection:
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM mod_billing.{table} WHERE id = :id"),
                    {"id": row["id"]},
                ).scalar_one()
                == 0
            )
        hidden.dispose()
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "fault",
    [
        "fractional",
        "nan",
        "currency",
        "over_outstanding",
        "source_duplicate",
        "tax_component_duplicate",
        "missing_effect",
        "cash_effect",
        "wrong_account",
        "allocation_without_settlement",
        "evidence_scalar",
        "tax_component_scalar",
    ],
)
def test_owner_sql_admission_cannot_bypass_command_invariants(scratch, plane, fault):
    engine, original = _seed(scratch, plane)
    with engine.begin() as connection:
        tenant = original.get("tenant_id")
        row, posting = _row(
            connection,
            plane,
            original["billing_account_id"],
            original["document_id"],
            tenant,
        )
        if fault in ("fractional", "nan", "over_outstanding"):
            value = {
                "fractional": Decimal("0.001"),
                "nan": Decimal("NaN"),
                "over_outstanding": Decimal("95"),
            }[fault]
            row["amount_delta"] = value
            row["claimed_amount"] = value if fault != "fractional" else Decimal("20")
            row["evidence"]["accepted_delta"]["amount"] = str(value)
            row["evidence"]["claimed_amount"]["amount"] = str(row["claimed_amount"])
            row["evidence"]["tax_evidence"]["amount"]["amount"] = "100"
        elif fault == "currency":
            row["currency"] = posting["currency"] = "USD"
        elif fault == "source_duplicate":
            row["source_ref"] = posting["source_ref"] = original["source_ref"]
        elif fault == "tax_component_duplicate":
            for key in ("tax_source_owner", "determination_ref", "component_ref"):
                row[key] = original[key]
            row["evidence"]["tax_evidence"] = deepcopy(
                original["evidence"]["tax_evidence"]
            )
        elif fault == "wrong_account":
            other, _ = _invoice(connection, plane, tenant)
            row["billing_account_id"] = posting["billing_account_id"] = other
        elif fault == "allocation_without_settlement":
            row["allocation_id"] = uuid.uuid4()
            row["evidence"]["allocation_id"] = str(row["allocation_id"])
        elif fault == "evidence_scalar":
            row["evidence"]["claim_ref"] = "different"
        elif fault == "tax_component_scalar":
            row["evidence"]["tax_evidence"]["component_ref"] = "different"
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            _insert(
                connection,
                plane,
                row,
                posting,
                effect=fault != "missing_effect",
                snapshots=fault != "cash_effect",
            )
            if fault == "cash_effect":
                effect = _tables(plane)[2].__table__
                values = {
                    "id": uuid.uuid4(),
                    "posting_group_id": row["posting_group_id"],
                    "billing_account_id": row["billing_account_id"],
                    "lane": "available_credit",
                    "currency": "NGN",
                    "minor_units": 2,
                    "amount_delta": 10,
                }
                if tenant:
                    values["tenant_id"] = tenant
                connection.execute(effect.insert().values(**values))
                from tests.test_billing_non_cash_persistence import _snapshots

                _snapshots(connection, plane, posting)
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert error.value.orig.sqlstate in {"23514", "23505"}
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "fault",
    [
        "partial",
        "positive",
        "tax_evidence",
        "claim",
        "repeat",
        "reverse_correction",
        "valid",
    ],
)
def test_owner_sql_correction_is_single_exact_linked_inverse(scratch, plane, fault):
    engine, original = _seed(scratch, plane)
    with engine.begin() as connection:
        row, posting = _row(
            connection,
            plane,
            original["billing_account_id"],
            original["document_id"],
            original.get("tenant_id"),
            original=original,
        )
        if fault in ("partial", "positive"):
            row["amount_delta"] = Decimal("-5" if fault == "partial" else "10")
            row["evidence"]["accepted_delta"]["amount"] = str(row["amount_delta"])
        elif fault == "tax_evidence":
            row["evidence"]["tax_evidence"]["rule_version"] = "2"
        elif fault == "claim":
            row["claim_ref"] = row["evidence"]["claim_ref"] = "different"
        if fault == "valid":
            _insert(connection, plane, row, posting)
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            assert sorted(
                connection.execute(
                    select(_tables(plane)[0].__table__.c.amount_delta).where(
                        _tables(plane)[0].__table__.c.document_id
                        == original["document_id"]
                    )
                )
                .scalars()
                .all()
            ) == [Decimal("-10"), Decimal("10")]
        else:
            if fault in ("repeat", "reverse_correction"):
                _insert(connection, plane, row, posting)
                target = row if fault == "reverse_correction" else original
                row, posting = _row(
                    connection,
                    plane,
                    original["billing_account_id"],
                    original["document_id"],
                    original.get("tenant_id"),
                    original=target,
                )
            with pytest.raises(DBAPIError) as error, connection.begin_nested():
                _insert(connection, plane, row, posting)
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            assert error.value.orig.sqlstate in {"23514", "23505"}
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("child", ["allocation", "funding_effect"])
def test_committed_withholding_group_refuses_later_cash_children(scratch, plane, child):
    engine, original = _seed(scratch, plane)
    with engine.begin() as connection:
        values = {
            "id": uuid.uuid4(),
            "billing_account_id": original["billing_account_id"],
            "posting_group_id": original["posting_group_id"],
            "currency": "NGN",
            "minor_units": 2,
            "amount_delta": Decimal("1"),
        }
        if "tenant_id" in original:
            values["tenant_id"] = original["tenant_id"]
        if child == "allocation":
            settlement = (
                models.ConfirmedSettlement
                if plane == "tenant"
                else models.PlatformConfirmedSettlement
            ).__table__
            settlement_id = uuid.uuid4()
            source = {
                "id": settlement_id,
                "billing_account_id": original["billing_account_id"],
                "source_system": "payments",
                "source_settlement_key": str(settlement_id),
                "source_version": "1",
                "request_fingerprint": "a" * 64,
                "amount": Decimal("1"),
                "currency": "NGN",
                "minor_units": 2,
                "occurred_at": billing.NOW,
                "observed_at": billing.NOW,
                "confirmation_evidence": "confirmed",
                "funding_lane": "available_credit",
            }
            if "tenant_id" in original:
                source["tenant_id"] = original["tenant_id"]
            connection.execute(settlement.insert().values(**source))
            values.update(
                settlement_id=settlement_id,
                document_id=original["document_id"],
                effect_kind="allocation",
            )
            target = (
                models.AllocationEffect
                if plane == "tenant"
                else models.PlatformAllocationEffect
            ).__table__
        else:
            values["lane"] = "available_credit"
            target = _tables(plane)[2].__table__
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(target.insert().values(**values))
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert error.value.orig.sqlstate == "23514"
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_successful_upgrade_and_empty_downgrade_restore_caller_settings(plane):
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with billing._scratch_database(
        (selection,), target_revision="bi_0003_issued_line_freeze"
    ) as urls:
        engine = create_engine(urls[0])
        with engine.begin() as connection:
            before = _catalog(connection)
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = '137ms'; "
                    "SET LOCAL statement_timeout = '19s'; SET LOCAL row_security = on"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration = _module()
                migration.upgrade()
                for name, expected in (
                    ("lock_timeout", "137ms"),
                    ("statement_timeout", "19s"),
                    ("row_security", "on"),
                ):
                    assert (
                        connection.execute(text(f"SHOW {name}")).scalar_one()
                        == expected
                    )
                migration.downgrade()
                for name, expected in (
                    ("lock_timeout", "137ms"),
                    ("statement_timeout", "19s"),
                    ("row_security", "on"),
                ):
                    assert (
                        connection.execute(text(f"SHOW {name}")).scalar_one()
                        == expected
                    )
            assert _catalog(connection) == before
        engine.dispose()


def _config(planes):
    config = Config(str(billing.REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(billing.REPO_ROOT / "alembic"))
    config.set_main_option(
        "version_locations",
        f"{billing.KERNEL_VERSIONS} {billing.ASSEMBLY_VERSIONS} "
        f"{billing.BILLING_VERSIONS}",
    )
    config.attributes["module_plane_selections"] = (
        ModulePlaneSelection(module="billing", planes=planes),
    )
    return config


def _module():
    spec = importlib.util.spec_from_file_location(
        "billing_wht_migration_canary", MIGRATION
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog(connection):
    return connection.execute(
        text("""
        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid),
               pg_get_functiondef(t.tgfoid)
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mod_billing' AND NOT t.tgisinternal
        ORDER BY c.relname, t.tgname
    """)
    ).all()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_published_predecessor_is_preserved_and_empty_downgrade_restores_guards(
    tmp_path, monkeypatch, plane
):
    git = shutil.which("git")
    assert git, "git is required for genuine published migration reconstruction"
    path = (
        "packages/dotmac-billing/src/dotmac_billing/migrations/versions/"
        "bi_0001_billing.py"
    )
    source = subprocess.run(  # noqa: S603 # nosec B603
        [git, "show", f"{RELEASE}:{path}"],
        cwd=billing.REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    assert hashlib.sha256(source).hexdigest() == RELEASE_DIGEST
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "bi_0001_billing.py").write_bytes(source)
    for name in (
        "bi_0002_multiline.py",
        "bi_0003_issued_line_freeze.py",
        f"{REVISION}.py",
    ):
        shutil.copyfile(billing.BILLING_VERSIONS / name, versions / name)
    monkeypatch.setattr(billing, "BILLING_VERSIONS", versions)
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with billing._scratch_database(
        (selection,), target_revision="bi_0001_billing"
    ) as urls:
        tenant = billing._create_tenants(urls[0], 1)[0] if plane == "tenant" else None
        engine = create_engine(urls[0])
        prefix = "" if plane == "tenant" else "platform_"
        with engine.begin() as connection:
            _, invoice = _invoice(connection, plane, tenant, legacy=True)
            before = connection.execute(
                text(
                    f"SELECT to_jsonb(x) FROM mod_billing.{prefix}documents x "
                    "WHERE id = :id"
                ),
                {"id": invoice},
            ).scalar_one()
        config = _config((selection,))
        command.upgrade(config, "bi_0003_issued_line_freeze")
        with engine.connect() as connection:
            predecessor_catalog = _catalog(connection)
        with engine.connect() as writer:
            transaction = writer.begin()
            writer.execute(
                text(
                    f"LOCK TABLE mod_billing.{prefix}billing_accounts "
                    "IN ROW EXCLUSIVE MODE"
                )
            )
            try:
                with pytest.raises(DBAPIError) as error:
                    command.upgrade(config, REVISION)
                assert error.value.orig.sqlstate == "55P03"
            finally:
                transaction.rollback()
        with engine.connect() as connection:
            assert _catalog(connection) == predecessor_catalog
        command.upgrade(config, REVISION)
        with engine.begin() as connection:
            after = connection.execute(
                text(
                    f"SELECT to_jsonb(x) - 'authoring_mode' "
                    f"FROM mod_billing.{prefix}documents x WHERE id = :id"
                ),
                {"id": invoice},
            ).scalar_one()
            assert before == after
        command.downgrade(config, "bi_0003_issued_line_freeze")
        with engine.connect() as connection:
            assert _catalog(connection) == predecessor_catalog
            assert (
                connection.execute(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"mod_billing.{prefix}withholding_discharges"},
                ).scalar_one()
                is None
            )
        engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_populated_downgrade_fails_globally_before_ddl_and_restores_caller_timeouts(
    plane,
):
    planes = (ModulePlane.TENANT, ModulePlane.PLATFORM)
    with billing._scratch_database(planes) as urls:
        engine, row = _seed(urls, plane)
        migration = _module()
        with engine.begin() as connection:
            original_catalog = _catalog(connection)
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = '137ms'; "
                    "SET LOCAL statement_timeout = '19s'; "
                    "SET LOCAL row_security = on"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                with (
                    pytest.raises(RuntimeError, match="withholding evidence exists"),
                    connection.begin_nested(),
                ):
                    migration.downgrade()
            assert _catalog(connection) == original_catalog
            assert connection.execute(text("SHOW lock_timeout")).scalar_one() == "137ms"
            assert (
                connection.execute(text("SHOW statement_timeout")).scalar_one() == "19s"
            )
            assert connection.execute(text("SHOW row_security")).scalar_one() == "on"
            table = _tables(plane)[0].__table__
            assert (
                connection.execute(
                    select(table.c.id).where(table.c.id == row["id"])
                ).scalar_one()
                == row["id"]
            )
        engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("withholding_first", [False, True])
def test_sql_invoice_reversal_and_withholding_cannot_borrow_other_invoice_ar(
    scratch, plane, withholding_first
):
    from dotmac_billing.commands import ReversePostingGroupCommand
    from dotmac_billing.service import (
        accept_withholding_discharge,
        reverse_posting_group,
    )

    tenant = billing._create_tenants(scratch[0], 1)[0] if plane == "tenant" else None
    scope = TenantScope(tenant) if tenant else PlatformScope()
    url = scratch[1] if tenant else scratch[2]
    with billing._session(url, scope) as session:
        account, _, invoice = billing._seed_invoice(
            session, scope, suffix=f"sql-two-ar-{plane}-{withholding_first}"
        )
        draft = billing._additional_credit_invoice(
            session, scope, account.id, f"sql-second-ar-{plane}-{withholding_first}"
        )
        billing.issue_document(
            session,
            scope=scope,
            command=billing._issue_command(draft.id),
            numbering=billing.NUMBERING,
        )
        session.commit()
        account_id, invoice_id = account.id, invoice.id
        command_wht = billing._withholding_command(
            account_id,
            invoice_id,
            scope=scope,
            suffix=f"sql-two-{plane}-{withholding_first}",
        )
        _, group_model, effect_model = _tables(plane)
        original = session.execute(
            select(group_model).where(
                group_model.source_ref == f"document:{invoice_id}:issue"
            )
        ).scalar_one()
        original_id = original.id
        if withholding_first:
            row = accept_withholding_discharge(
                session, scope=scope, command=command_wht
            )
            row_id = row.id
            session.commit()
    engine = create_engine(scratch[0])
    with engine.begin() as connection:
        values = {
            "id": uuid.uuid4(),
            "billing_account_id": account_id,
            "group_kind": "reversal",
            "currency": "NGN",
            "minor_units": 2,
            "source_ref": f"raw-issue-reverse:{plane}:{withholding_first}",
            "source_version": _next_raw_version(connection, plane, account_id, tenant),
            "reverses_group_id": original_id,
            "occurred_at": billing.NOW,
        }
        if tenant:
            values["tenant_id"] = tenant
        if withholding_first:
            with (
                pytest.raises(
                    DBAPIError, match="invoice issuance with active withholding"
                ),
                connection.begin_nested(),
            ):
                connection.execute(group_model.__table__.insert().values(**values))
        else:
            connection.execute(group_model.__table__.insert().values(**values))
            effect = {
                "id": uuid.uuid4(),
                "posting_group_id": values["id"],
                "billing_account_id": account_id,
                "lane": "receivable",
                "amount_delta": Decimal("-120"),
                "currency": "NGN",
                "minor_units": 2,
            }
            if tenant:
                effect["tenant_id"] = tenant
            connection.execute(effect_model.__table__.insert().values(**effect))
            from tests.test_billing_non_cash_persistence import _snapshots

            _snapshots(connection, plane, values)
    with billing._session(url, scope) as session:
        if withholding_first:
            assert (
                accept_withholding_discharge(
                    session, scope=scope, command=command_wht
                ).id
                == row_id
            )
            # New attempts remain owner-gated.
            with pytest.raises(billing.BillingRuleViolation, match="void_document"):
                reverse_posting_group(
                    session,
                    scope=scope,
                    command=ReversePostingGroupCommand(
                        original_id, billing.NOW, "new-raw-retry"
                    ),
                )
        else:
            with pytest.raises(
                billing.BillingRuleViolation, match="structurally bound"
            ):
                accept_withholding_discharge(session, scope=scope, command=command_wht)
            raw, posting = _row(
                session.connection(), plane, account_id, invoice_id, tenant
            )
            with pytest.raises(DBAPIError), session.begin_nested():
                _insert(session.connection(), plane, raw, posting)
        # The other invoice leaves positive account AR in either order.
        assert billing.rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
            verify_latest=False,
        ).collectible_receivable == (
            Decimal("235") if withholding_first else Decimal("120")
        )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_raw_invoice_reversal_races_with_withholding_under_account_lock(scratch, plane):
    import threading

    from dotmac_billing.service import accept_withholding_discharge

    tenant = billing._create_tenants(scratch[0], 1)[0] if plane == "tenant" else None
    scope = TenantScope(tenant) if tenant else PlatformScope()
    url = scratch[1] if tenant else scratch[2]
    with billing._session(url, scope) as session:
        account, _, invoice = billing._seed_invoice(
            session, scope, suffix=f"raw-issue-race-{plane}"
        )
        draft = billing._additional_credit_invoice(
            session, scope, account.id, f"raw-issue-race-second-{plane}"
        )
        billing.issue_document(
            session,
            scope=scope,
            command=billing._issue_command(draft.id),
            numbering=billing.NUMBERING,
        )
        _, group, effect = _tables(plane)
        issuance = session.execute(
            select(group).where(group.source_ref == f"document:{invoice.id}:issue")
        ).scalar_one()
        account_id, invoice_id, issuance_id = account.id, invoice.id, issuance.id
        wht = billing._withholding_command(
            account_id, invoice_id, scope=scope, suffix=f"raw-reverse-race-{plane}"
        )
        session.commit()
    barrier = threading.Barrier(2)
    accepted, refused, errors = [], [], []
    engine = create_engine(scratch[0])

    def runner(withholding):
        try:
            barrier.wait(timeout=20)
            if withholding:
                with billing._session(url, scope) as session:
                    try:
                        accept_withholding_discharge(session, scope=scope, command=wht)
                        session.commit()
                        accepted.append("withholding")
                    except billing.BillingRuleViolation:
                        refused.append("withholding")
            else:
                try:
                    with engine.begin() as connection:
                        group_id = uuid.uuid4()
                        values = {
                            "id": group_id,
                            "billing_account_id": account_id,
                            "group_kind": "reversal",
                            "currency": "NGN",
                            "minor_units": 2,
                            "source_ref": f"raw-race-reversal:{plane}",
                            "source_version": _next_raw_version(
                                connection, plane, account_id, tenant
                            ),
                            "reverses_group_id": issuance_id,
                            "occurred_at": billing.NOW,
                        }
                        posting = {
                            "id": uuid.uuid4(),
                            "posting_group_id": group_id,
                            "billing_account_id": account_id,
                            "lane": "receivable",
                            "amount_delta": Decimal("-120"),
                            "currency": "NGN",
                            "minor_units": 2,
                        }
                        if tenant:
                            values["tenant_id"] = posting["tenant_id"] = tenant
                        connection.execute(group.__table__.insert().values(**values))
                        connection.execute(effect.__table__.insert().values(**posting))
                        from tests.test_billing_non_cash_persistence import _snapshots

                        _snapshots(connection, plane, values)
                    accepted.append("reversal")
                except DBAPIError as exc:
                    if (
                        exc.orig.sqlstate != "23514"
                        or "invoice issuance with active withholding"
                        not in str(exc.orig)
                    ):
                        raise
                    refused.append("reversal")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(kind,)) for kind in (False, True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "raw reversal / withholding admission deadlocked"
    assert errors == []
    assert len(accepted) == len(refused) == 1
    with billing._session(url, scope) as session:
        position = billing.rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account_id,
            currency="NGN",
            minor_units=2,
            verify_latest=False,
        )
        assert position.collectible_receivable == (
            Decimal("235") if accepted == ["withholding"] else Decimal("120")
        )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["reversal", "allocation", "withholding_reversed"])
def test_raw_sql_cannot_reverse_withholding_without_its_exact_inverse_row(
    scratch, plane, kind
):
    engine, row = _seed(scratch, plane)
    _, group, _ = _tables(plane)
    with engine.begin() as connection:
        values = {
            "id": uuid.uuid4(),
            "billing_account_id": row["billing_account_id"],
            "group_kind": kind,
            "currency": "NGN",
            "minor_units": 2,
            "source_ref": f"raw-wht-bypass:{uuid.uuid4()}",
            "source_version": _next_raw_version(
                connection, plane, row["billing_account_id"], row.get("tenant_id")
            ),
            "reverses_group_id": row["posting_group_id"],
            "occurred_at": billing.NOW,
        }
        if "tenant_id" in row:
            values["tenant_id"] = row["tenant_id"]
        expected = (
            "actual exact inverse discharge row"
            if kind == "withholding_reversed"
            else "dedicated exact inverse owner"
        )
        with pytest.raises(DBAPIError, match=expected), connection.begin_nested():
            connection.execute(group.__table__.insert().values(**values))
            from tests.test_billing_non_cash_persistence import _snapshots

            _snapshots(connection, plane, values)
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        # The permitted group kind works only with its actual exact inverse row.
        inverse, posting = _row(
            connection,
            plane,
            row["billing_account_id"],
            row["document_id"],
            row.get("tenant_id"),
            original=row,
        )
        _insert(connection, plane, inverse, posting)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        values.update(
            id=uuid.uuid4(),
            source_ref=f"raw-inverse-bypass:{uuid.uuid4()}",
            source_version=_next_raw_version(
                connection, plane, row["billing_account_id"], row.get("tenant_id")
            ),
            reverses_group_id=inverse["posting_group_id"],
        )
        with (
            pytest.raises(DBAPIError, match="dedicated exact inverse owner"),
            connection.begin_nested(),
        ):
            connection.execute(group.__table__.insert().values(**values))
    engine.dispose()
