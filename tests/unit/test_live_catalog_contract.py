"""The post-migration live-catalog contract (ADR-0006 D1, item 7).

`dotmac_kernel.migrations.catalog` is deliberately split into parameterised SQL
builders and a PURE decision function, so the whole contract is exercisable
from synthetic snapshots. That is what this file does: every violation the
contract can report is driven from a `SchemaSnapshot`, with no Postgres.

The executable half (`fetch_snapshot` / `audit_live_schemas`) needs a real,
migrated database and is covered by the assembly's integration suite; see
`tests/test_module_schema_catalog.py`.
"""

from __future__ import annotations

from dotmac_kernel.migrations.catalog import (
    DEFAULT_APP_ROLE,
    ForeignKeyFacts,
    PolicyFacts,
    SchemaSnapshot,
    TableFacts,
    audit_snapshot,
    audited_schemas,
    catalog_queries,
)
from dotmac_kernel.namespaces import (
    HOST_MIGRATION_OWNERS,
    MigrationOwner,
    NamespaceRegistry,
    module_schema,
)

TENANT_POLICY = PolicyFacts(name="tenant_isolation", qual="tenant_id = ...")


def _compliant_table(name: str = "invoices", **kwargs: object) -> TableFacts:
    defaults: dict[str, object] = {
        "name": name,
        "rls_enabled": True,
        "rls_forced": True,
        "has_tenant_column": True,
        "tenant_column_nullable": False,
        "policies": (TENANT_POLICY,),
        "unique_constraints": (("uq_invoices_number", ("tenant_id", "number")),),
    }
    defaults.update(kwargs)
    return TableFacts(**defaults)  # type: ignore[arg-type]


def _snapshot(*tables: TableFacts, **kwargs: object) -> SchemaSnapshot:
    defaults: dict[str, object] = {
        "schema": "mod_bill",
        "tables": tables,
        "declared_tables": frozenset(t.name for t in tables),
    }
    defaults.update(kwargs)
    return SchemaSnapshot(**defaults)  # type: ignore[arg-type]


# ── The compliant baseline ──────────────────────────────────────────────────


def test_a_compliant_schema_reports_nothing() -> None:
    assert audit_snapshot(_snapshot(_compliant_table())) == ()


def test_the_subtype_exists_join_pattern_is_accepted() -> None:
    """The kernel's own `party_persons`/`party_organizations` shape: no
    `tenant_id` of its own, isolation via an EXISTS-join to a scoped parent."""
    subtype = _compliant_table(
        "invoice_details",
        has_tenant_column=False,
        policies=(
            PolicyFacts(
                name="via_parent",
                qual="(EXISTS (SELECT 1 FROM mod_bill.invoices i WHERE ...))",
            ),
        ),
        unique_constraints=(),
    )
    assert audit_snapshot(_snapshot(_compliant_table(), subtype)) == ()


# ── Hard rule 11, generalised across module schemas ─────────────────────────


def test_rls_must_be_enabled_and_forced() -> None:
    violations = audit_snapshot(_snapshot(_compliant_table(rls_forced=False)))
    assert any("RLS must be ENABLEd AND FORCEd" in v for v in violations)
    violations = audit_snapshot(_snapshot(_compliant_table(rls_enabled=False)))
    assert any("RLS must be ENABLEd AND FORCEd" in v for v in violations)


def test_a_table_with_no_policy_is_flagged() -> None:
    violations = audit_snapshot(_snapshot(_compliant_table(policies=())))
    assert any("no RLS policy" in v for v in violations)


def test_a_nullable_tenant_id_is_flagged() -> None:
    violations = audit_snapshot(
        _snapshot(_compliant_table(tenant_column_nullable=True))
    )
    assert any("tenant_id must be NOT NULL" in v for v in violations)


def test_an_unscoped_table_is_flagged() -> None:
    violations = audit_snapshot(
        _snapshot(_compliant_table(has_tenant_column=False, unique_constraints=()))
    )
    assert any("unscoped tenant data" in v for v in violations)


def test_a_unique_constraint_without_tenant_id_is_flagged() -> None:
    """A bare unique lets one tenant's value block another tenant's insert,
    which leaks existence across the isolation boundary."""
    violations = audit_snapshot(
        _snapshot(
            _compliant_table(unique_constraints=(("uq_invoices_number", ("number",)),))
        )
    )
    assert any("omits tenant_id" in v for v in violations)


def test_a_non_composite_tenant_fk_is_flagged() -> None:
    snapshot = _snapshot(
        _compliant_table("invoices"),
        _compliant_table("invoice_lines"),
        foreign_keys=(
            ForeignKeyFacts(
                table="invoice_lines",
                name="fk_lines_invoice",
                columns=("invoice_id",),
                referenced_table="invoices",
            ),
        ),
    )
    violations = audit_snapshot(snapshot)
    assert any("must be composite with tenant_id" in v for v in violations)


def test_a_composite_tenant_fk_is_accepted() -> None:
    snapshot = _snapshot(
        _compliant_table("invoices"),
        _compliant_table("invoice_lines"),
        foreign_keys=(
            ForeignKeyFacts(
                table="invoice_lines",
                name="fk_lines_invoice",
                columns=("tenant_id", "invoice_id"),
                referenced_table="invoices",
            ),
        ),
    )
    assert audit_snapshot(snapshot) == ()


# ── Ownership and reachability ──────────────────────────────────────────────


def test_a_missing_schema_is_the_only_thing_reported() -> None:
    violations = audit_snapshot(SchemaSnapshot(schema="mod_bill", exists=False))
    assert len(violations) == 1
    assert "does not exist" in violations[0]


def test_an_undeclared_live_table_is_flagged() -> None:
    snapshot = _snapshot(
        _compliant_table("invoices"),
        _compliant_table("secret_side_table"),
        declared_tables=frozenset({"invoices"}),
    )
    violations = audit_snapshot(snapshot)
    assert any("does not declare" in v for v in violations)


def test_a_declared_but_absent_table_is_flagged() -> None:
    snapshot = _snapshot(
        _compliant_table("invoices"),
        declared_tables=frozenset({"invoices", "credit_notes"}),
    )
    violations = audit_snapshot(snapshot)
    assert any("absent from the live schema" in v for v in violations)


def test_a_module_table_squatting_in_public_is_flagged() -> None:
    snapshot = _snapshot(
        _compliant_table("invoices"), host_schema_squatters=("invoices",)
    )
    violations = audit_snapshot(snapshot)
    assert any("compatibility namespace" in v for v in violations)


def test_missing_schema_usage_for_the_app_role_is_flagged() -> None:
    snapshot = _snapshot(_compliant_table(), app_role_has_usage=False)
    violations = audit_snapshot(snapshot)
    assert any(f"{DEFAULT_APP_ROLE!r} has no USAGE" in v for v in violations)


# ── SQL surface ─────────────────────────────────────────────────────────────


def test_no_query_interpolates_a_schema_name() -> None:
    """Every statement binds `:schema` — no f-string reaches SQL, and nothing
    resolves a relation through `search_path`."""
    queries = catalog_queries()
    assert queries
    for name, sql in queries.items():
        assert "{" not in sql and "%" not in sql, name
        if name != "host_squatters":
            assert ":schema" in sql, name


def test_audited_schemas_is_the_registered_module_set() -> None:
    """The gate walks registered MODULE schemas — `public` keeps its own,
    exception-carrying audit and is deliberately not in this set."""
    allocated = MigrationOwner("billing", "bl", "billing", module_schema("bill"))
    registry = NamespaceRegistry((*HOST_MIGRATION_OWNERS, allocated))
    assert audited_schemas(registry) == ("mod_bill",)
    assert audited_schemas(NamespaceRegistry(HOST_MIGRATION_OWNERS)) == ()
