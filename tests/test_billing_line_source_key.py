"""Real PostgreSQL provider-key probes; these are not a deployed Sub schema."""

from __future__ import annotations

# Identifiers below come only from the closed Billing plane selection.
# ruff: noqa: S608
import os
import uuid

import pytest
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from alembic.config import Config
from tests import test_billing_isolation as billing

REVISION = "bi_0010_line_source_key"
PREDECESSOR = "bi_0009_opening_window"


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


def _security_catalog(connection):
    """Snapshot existing keys/RLS/ACL/guards, excluding only the two new keys."""
    queries = (
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mod_billing' AND c.relkind='r' ORDER BY c.relname",
        "SELECT tablename, policyname, roles::text, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname='mod_billing' "
        "ORDER BY tablename, policyname",
        "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mod_billing' AND NOT t.tgisinternal "
        "ORDER BY c.relname,t.tgname",
        "SELECT p.proname, pg_get_functiondef(p.oid), p.proacl::text "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='mod_billing' ORDER BY p.proname,p.oid",
        "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) "
        "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mod_billing' AND k.contype='f' "
        "ORDER BY c.relname,k.conname",
        "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) "
        "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mod_billing' AND k.contype IN ('u','p') "
        "AND k.conname NOT IN ('uq_document_lines_source_key', "
        "'uq_platform_document_lines_source_key') ORDER BY c.relname,k.conname",
        "SELECT c.relname,x.relname,pg_get_indexdef(i.indexrelid) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid "
        "JOIN pg_class x ON x.oid=i.indexrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mod_billing' AND i.indisunique "
        "AND x.relname NOT IN ('uq_document_lines_source_key', "
        "'uq_platform_document_lines_source_key') ORDER BY c.relname,x.relname",
    )
    return tuple(tuple(connection.execute(text(sql))) for sql in queries)


def _key(connection, table):
    return connection.execute(
        text("""SELECT k.contype, k.condeferrable, k.convalidated,
          i.indisunique, i.indpred IS NULL AS nonpartial,
          i.indexprs IS NULL AS ordinary,
          ARRAY(SELECT a.attname::text FROM unnest(k.conkey)
            WITH ORDINALITY AS x(attnum, position)
            JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.attnum
            ORDER BY x.position) AS columns
          FROM pg_constraint k JOIN pg_index i ON i.indexrelid=k.conindid
          WHERE k.conrelid=to_regclass(:table) AND k.conname=:name"""),
        {"table": f"mod_billing.{table}", "name": f"uq_{table}_source_key"},
    ).one_or_none()


def _financial_rows(connection, planes):
    tables = []
    if ModulePlane.TENANT in planes:
        tables.extend(billing.TENANT_TABLES)
    if ModulePlane.PLATFORM in planes:
        tables.extend(billing.PLATFORM_TABLES)
    return {
        table: tuple(
            connection.scalars(
                text(
                    f"SELECT to_jsonb(x)::text FROM mod_billing.{table} x "
                    "ORDER BY to_jsonb(x)::text"
                )
            )
        )
        for table in tables
    }


def _seed(urls, plane):
    tenant = plane is ModulePlane.TENANT
    tenant_ids = billing._create_tenants(urls[0], 2) if tenant else []
    scope = TenantScope(tenant_ids[0]) if tenant else PlatformScope()
    other_scope = TenantScope(tenant_ids[1]) if tenant else PlatformScope()
    with billing._session(urls[1 if tenant else 2], scope) as session:
        account, _, document = billing._seed_invoice(
            session, scope, suffix=uuid.uuid4().hex
        )
        other_account, _, other_document = billing._seed_invoice(
            session, scope, suffix=uuid.uuid4().hex
        )
        source = scope, account.id, document.id, other_account.id, other_document.id
    if tenant:
        with billing._session(urls[1], other_scope) as session:
            billing._seed_invoice(session, other_scope, suffix=uuid.uuid4().hex)
    return (*source, other_scope)


def _probe(connection, plane, source):
    tenant = plane is ModulePlane.TENANT
    table = "document_lines" if tenant else "platform_document_lines"
    columns = ["tenant_id", "id", "document_id", "billing_account_id"]
    if not tenant:
        columns.remove("tenant_id")
    scope, account_id, document_id, other_account_id, other_document_id, other_scope = (
        source
    )
    line_id = connection.scalar(
        text(f"SELECT id FROM mod_billing.{table} WHERE document_id=:document"),
        {"document": document_id},
    )
    qualified = f"billing_line_probe.{table}"
    # A genuine cross-schema FK is the canary for provider target suitability.
    # No product migration, product privileges or deployed Sub schema is claimed.
    connection.execute(
        text(
            f"CREATE TABLE {qualified} ("
            + ",".join(f"{column} uuid NOT NULL" for column in columns)
            + f", FOREIGN KEY ({','.join(columns)}) "
            + f"REFERENCES mod_billing.{table} ({','.join(columns)}) "
            + "ON DELETE RESTRICT)"
        )
    )
    values = {
        "id": line_id,
        "document_id": document_id,
        "billing_account_id": account_id,
    }
    if tenant:
        values["tenant_id"] = scope.tenant_id
    insert = text(
        f"INSERT INTO {qualified} ({','.join(columns)}) "
        f"VALUES ({','.join(':' + column for column in columns)})"
    )
    connection.execute(insert, values)
    other_line_id = connection.scalar(
        text(f"SELECT id FROM mod_billing.{table} WHERE document_id=:document"),
        {"document": other_document_id},
    )
    assert other_line_id is not None
    wrong = {
        "id": other_line_id,
        "document_id": other_document_id,
        "billing_account_id": other_account_id,
    }
    if tenant:
        wrong["tenant_id"] = other_scope.tenant_id
    prefix = "" if tenant else "platform_"
    target_columns = {
        "id": (table, "id"),
        "document_id": (f"{prefix}documents", "id"),
        "billing_account_id": (f"{prefix}billing_accounts", "id"),
        "tenant_id": (table, "tenant_id"),
    }
    for column in columns:
        # Every mismatched identity already exists independently. Refusal must
        # prove exact affinity, not merely the absence of a referenced identity.
        target_table, target_column = target_columns[column]
        assert connection.scalar(
            text(
                f"SELECT EXISTS (SELECT 1 FROM mod_billing.{target_table} "
                f"WHERE {target_column}=:value)"
            ),
            {"value": wrong[column]},
        )
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(insert, {**values, column: wrong[column]})
        assert error.value.orig.sqlstate == "23503"

    # The new key grants no new runtime privileges and retains line closure.
    role = "app_user" if tenant else "platform_api"
    assert billing._actual_table_privileges(
        connection, role=role, table=f"mod_billing.{table}"
    ) == frozenset({"SELECT", "INSERT"})
    if not tenant:
        assert not billing._actual_table_privileges(
            connection, role="app_user", table=f"mod_billing.{table}"
        )
    for mutation in (
        f"UPDATE mod_billing.{table} SET description='changed' WHERE id=:id",
        f"DELETE FROM mod_billing.{table} WHERE id=:id",
    ):
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(text(mutation), {"id": line_id})
        assert error.value.orig.sqlstate == "23001"
    # Copying the issued row as another line still reaches the old INSERT guard.
    copied = billing._table_columns(connection, table)
    ordered = sorted(copied)
    expressions = [
        ":new_id" if column == "id" else "2" if column == "line_number" else column
        for column in ordered
    ]
    with pytest.raises(DBAPIError) as error, connection.begin_nested():
        connection.execute(
            text(
                f"INSERT INTO mod_billing.{table} ({','.join(ordered)}) "
                f"SELECT {','.join(expressions)} FROM mod_billing.{table} WHERE id=:id"
            ),
            {"id": line_id, "new_id": uuid.uuid4()},
        )
    assert error.value.orig.sqlstate == "23001"


@pytest.mark.parametrize(
    "planes",
    [
        (ModulePlane.TENANT,),
        (ModulePlane.PLATFORM,),
        (ModulePlane.TENANT, ModulePlane.PLATFORM),
    ],
)
@pytest.mark.parametrize("start", ["fresh", "predecessor"])
def test_real_lineage_source_key_and_consumer_canary(planes, start):
    # Required PostgreSQL proof must fail setup rather than quietly skip.
    assert os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    target = REVISION if start == "fresh" else PREDECESSOR
    with billing._scratch_database(planes, target_revision=target) as urls:
        config = _config(planes)
        sources = {plane: _seed(urls, plane) for plane in planes}
        engine = create_engine(urls[0])
        try:
            with engine.connect() as connection:
                before = _security_catalog(connection)
                financial_before = _financial_rows(connection, planes)
                if start == "predecessor":
                    for table in ("document_lines", "platform_document_lines"):
                        assert _key(connection, table) is None
            if start == "predecessor":
                command.upgrade(config, REVISION)
            with engine.begin() as connection:
                assert _security_catalog(connection) == before
                assert _financial_rows(connection, planes) == financial_before
                for plane, table in (
                    (ModulePlane.TENANT, "document_lines"),
                    (ModulePlane.PLATFORM, "platform_document_lines"),
                ):
                    if plane not in planes:
                        assert (
                            connection.scalar(
                                text("SELECT to_regclass(:table)"),
                                {"table": f"mod_billing.{table}"},
                            )
                            is None
                        )
                        continue
                    key = _key(connection, table)
                    assert key is not None
                    assert tuple(key[:6]) == ("u", False, True, True, True, True)
                    expected = ["id", "document_id", "billing_account_id"]
                    if plane is ModulePlane.TENANT:
                        expected.insert(0, "tenant_id")
                    assert key.columns == expected
                connection.execute(text("CREATE SCHEMA billing_line_probe"))
                for plane in planes:
                    _probe(connection, plane, sources[plane])
            # PostgreSQL's default RESTRICT refuses removing a referenced key.
            with pytest.raises(DBAPIError) as error:
                command.downgrade(config, PREDECESSOR)
            assert error.value.orig.sqlstate == "2BP01"
            with engine.begin() as connection:
                for plane in planes:
                    table = (
                        "document_lines"
                        if plane is ModulePlane.TENANT
                        else "platform_document_lines"
                    )
                    assert _key(connection, table) is not None
                    connection.execute(text(f"DROP TABLE billing_line_probe.{table}"))
                connection.execute(text("DROP SCHEMA billing_line_probe"))
            command.downgrade(config, PREDECESSOR)
            with engine.connect() as connection:
                assert _security_catalog(connection) == before
                assert _financial_rows(connection, planes) == financial_before
                for table in ("document_lines", "platform_document_lines"):
                    assert _key(connection, table) is None
            # The same populated immutable financial history can upgrade again.
            command.upgrade(config, REVISION)
            with engine.connect() as connection:
                assert _financial_rows(connection, planes) == financial_before
        finally:
            engine.dispose()
