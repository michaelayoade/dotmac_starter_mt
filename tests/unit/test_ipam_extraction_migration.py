"""``ip_0002_extracted`` is additive, refuses template data, and matches the models.

No database: the migration is rendered as offline PostgreSQL DDL and compared
with the mapped tables, so a model change that forgets the migration (or the
reverse) fails here, and the template-data refusal and the byte-frozen ``ip_0001``
are asserted from source.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
from pathlib import Path
from types import ModuleType
from unittest import mock

import dotmac_ipam
import pytest
from dotmac_ipam import models

from alembic.migration import MigrationContext
from alembic.operations import Operations

VERSIONS = Path(dotmac_ipam.__file__).parent / "migrations" / "versions"
IP_0001_SHA256 = "ac7292ca9cf580d5a9d00f56160e15482dc57ce3491940559f76056c987e5198"
NEW_TABLES = {m.__tablename__ for m in models.ALL_MODELS} - {"ipam_events"}
TEMPLATE_TABLES = {
    "address_spaces",
    "pools",
    "addresses",
    "assignments",
    "utilization_snapshots",
}


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    return _load("ip_0002_extracted")


def _render(migration: ModuleType, direction: str) -> str:
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    with (
        Operations.context(context),
        mock.patch.object(migration, "require_prerequisites"),
        mock.patch.object(migration, "_refuse_if_template_rows"),
        mock.patch.object(migration, "_refuse_if_extracted_rows"),
    ):
        getattr(migration, direction)()
    return buffer.getvalue()


def test_ip_0001_bytes_are_untouched() -> None:
    digest = hashlib.sha256((VERSIONS / "ip_0001_ipam.py").read_bytes()).hexdigest()
    assert digest == IP_0001_SHA256


def test_revision_chain_is_additive_in_the_same_lineage(migration) -> None:
    assert migration.revision == "ip_0002_extracted"
    assert migration.down_revision == "ip_0001_ipam"
    assert len(migration.revision) <= 32
    assert migration.branch_labels is None  # same lineage, label stays on ip_0001
    assert set(migration._TEMPLATE_DROP_ORDER) == TEMPLATE_TABLES
    assert set(migration._NEW_TABLES) == NEW_TABLES
    assert "ipam_events" not in migration._TEMPLATE_DROP_ORDER  # evidence is kept


def test_upgrade_refuses_when_any_template_table_holds_a_row(migration) -> None:
    class Bind:
        def __init__(self, nonempty: str | None) -> None:
            self.nonempty = nonempty
            self.statements: list[str] = []

        def scalar(self, statement) -> bool:
            text = str(statement)
            self.statements.append(text)
            return self.nonempty is not None and f"mod_ipam.{self.nonempty})" in text

    for table in sorted(TEMPLATE_TABLES):
        bind = Bind(table)
        with (
            mock.patch.object(migration.op, "get_bind", return_value=bind),
            mock.patch.object(migration.op, "execute"),
            pytest.raises(RuntimeError, match=f"mod_ipam.{table} holds data"),
        ):
            migration._refuse_if_template_rows("upgrade")
    clean = Bind(None)
    with (
        mock.patch.object(migration.op, "get_bind", return_value=clean),
        mock.patch.object(migration.op, "execute"),
    ):
        migration._refuse_if_template_rows("upgrade")
    assert len(clean.statements) == len(TEMPLATE_TABLES)  # every table is checked


def test_upgrade_locks_before_it_checks_then_drops_the_template(migration) -> None:
    source = (VERSIONS / "ip_0002_extracted.py").read_text()
    upgrade = source[source.index("def upgrade") : source.index("def downgrade")]
    assert upgrade.index("_refuse_if_template_rows") < upgrade.index("drop_table")
    assert upgrade.index("require_prerequisites") < upgrade.index(
        "_refuse_if_template_rows"
    )
    assert "ACCESS EXCLUSIVE" in source


def _tables(ddl: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"CREATE TABLE mod_ipam\.(\w+) \(\n(.*?)\n\)", ddl, flags=re.S
        )
    }


def test_upgrade_creates_exactly_the_mapped_tables_and_columns(migration) -> None:
    ddl = _render(migration, "upgrade")
    created = _tables(ddl)
    assert set(created) == NEW_TABLES
    for model in models.ALL_MODELS:
        table = model.__table__
        if table.name not in NEW_TABLES:
            continue
        body = created[table.name]
        columns = set(re.findall(r"^    (\w+) ", body, flags=re.M)) - {
            "PRIMARY",
            "CONSTRAINT",
        }
        assert columns == {c.name for c in table.columns}, table.name
        mapped_constraints = {c.name for c in table.constraints if c.name}
        for name in mapped_constraints:
            assert name in body, f"{table.name}: constraint {name} not migrated"
        for index in table.indexes:
            assert f"INDEX {index.name} " in ddl, f"index {index.name} not migrated"
        not_null_mapped = {c.name for c in table.columns if not c.nullable}
        not_null_ddl = set(
            re.findall(r"^    (\w+) [^\n]*NOT NULL", body, flags=re.M)
        ) - {"CONSTRAINT"}
        assert not_null_mapped == not_null_ddl, table.name


def test_every_new_table_has_forced_rls_a_tenant_policy_and_grants(migration) -> None:
    ddl = _render(migration, "upgrade")
    for table in NEW_TABLES:
        assert f"ALTER TABLE mod_ipam.{table} ENABLE ROW LEVEL SECURITY" in ddl
        assert f"ALTER TABLE mod_ipam.{table} FORCE ROW LEVEL SECURITY" in ddl
        assert f"CREATE POLICY {table}_tenant_isolation ON mod_ipam.{table}" in ddl
        assert f"ON mod_ipam.{table} TO app_user" in ddl
        assert f"ON mod_ipam.{table} TO platform_api" in ddl
    assert "app_current_tenant_id()" in ddl


def test_upgrade_drops_the_template_tables_and_keeps_the_events_table(
    migration,
) -> None:
    ddl = _render(migration, "upgrade")
    for table in TEMPLATE_TABLES:
        assert f"DROP TABLE mod_ipam.{table}" in ddl
    assert "ipam_events" not in ddl


def test_partial_indexes_are_rendered_with_their_predicates(migration) -> None:
    ddl = _render(migration, "upgrade")
    assert re.search(
        r"CREATE UNIQUE INDEX uq_ip_assignments_ipv4_active "
        r"ON mod_ipam\.ip_assignments "
        r"\(tenant_id, ipv4_address_id\) WHERE is_active",
        ddl,
    )
    assert re.search(
        r"CREATE UNIQUE INDEX uq_ip_assignments_primary_ipv4_active .*"
        r"WHERE is_active AND is_primary AND ipv4_address_id IS NOT NULL "
        r"AND subscription_id IS NOT NULL",
        ddl,
    )


def test_downgrade_restores_the_template_shape_when_empty(migration) -> None:
    ddl = _render(migration, "downgrade")
    created = _tables(ddl)
    assert set(created) == TEMPLATE_TABLES
    for table in NEW_TABLES:
        assert f"DROP TABLE mod_ipam.{table}" in ddl
    assert "ipam_utilization_snapshots_append_only" in ddl
    # the restored shape is ip_0001's, column for column
    original = _load("ip_0001_ipam")
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with (
        Operations.context(context),
        mock.patch.object(original, "require_prerequisites"),
    ):
        original.upgrade()
    first = _tables(buffer.getvalue())
    for table in TEMPLATE_TABLES:
        assert created[table] == first[table], table


@pytest.mark.parametrize(
    "revision,parent,requirement,provider",
    [
        (
            "ip_0003_outbox_relay",
            "ip_0002_extracted",
            "outbox_relay.v1",
            "0012_platform_outbox",
        ),
        (
            "ip_0004_import_idempotency",
            "ip_0003_outbox_relay",
            "idempotency_ledger.v1",
            "0018_idempotency_one_owner",
        ),
    ],
)
def test_import_revisions_require_and_verify_their_kernel_effect(
    monkeypatch,
    revision,
    parent,
    requirement,
    provider,
) -> None:
    from dotmac_ipam import module
    from dotmac_kernel.prerequisites import (
        BINDINGS_ENV_VAR,
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    previous = installed_bindings()
    try:
        # Graph inspection runs without env.py; the module cannot choose providers.
        with monkeypatch.context() as patch:
            patch.delenv(BINDINGS_ENV_VAR, raising=False)
            install_prerequisite_bindings(())
            assert _load(revision).depends_on == ()
        # Upgrade loads revisions after the assembly installs its provider bindings.
        install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
        migration = _load(revision)
    finally:
        install_prerequisite_bindings(previous)
    assert migration.REQUIRES == (requirement,)
    assert {"outbox_relay.v1", "idempotency_ledger.v1"} <= set(module.requires)
    assert migration.depends_on == (provider,)
    assert migration.down_revision == parent
    connection = object()
    with (
        mock.patch.object(migration.op, "get_bind", return_value=connection),
        mock.patch.object(migration, "require_prerequisites") as verify,
        mock.patch.object(migration.op, "execute") as execute,
    ):
        migration.upgrade()
        verify.assert_called_once_with(connection, migration.REQUIRES)
        execute.assert_not_called()
        verify.side_effect = RuntimeError("required provider unavailable")
        with pytest.raises(RuntimeError, match="required provider unavailable"):
            migration.upgrade()
        execute.assert_not_called()
