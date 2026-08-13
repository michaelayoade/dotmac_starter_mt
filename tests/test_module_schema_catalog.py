"""Post-migration live-catalog gate over every registered module schema.

The executable half of ADR-0006 D1 item 7. `tests/unit/test_live_catalog_
contract.py` covers the decision logic exhaustively from synthetic snapshots;
this file drives the same contract against a REAL, migrated Postgres —
`fetch_snapshot`'s catalog queries plus `audit_live_schemas`' loop.

`dotmac-template-studio` (ADR-0006 M1) is the first allocated module, so
`test_registered_module_schemas_are_compliant` now audits a REAL `mod_tstudio`
schema instead of walking the empty set it was landed with. It asserts on what
it FOUND — the schema is audited, and it holds exactly the tables the manifest
declares — because an audit over a schema whose tables were never created
reports no violations and no coverage, and the two look identical in a green run.

The sensitivity self-test below is what proves the seam is real NOW: it builds
a `mod_` schema with a deliberately broken table inside a rolled-back
transaction and asserts the audit flags it — the same technique
`tests/test_rls_catalog.py::test_audit_flags_a_broken_table` uses.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

from dotmac_kernel.migrations.catalog import audit_live_schemas, audited_schemas
from dotmac_kernel.namespaces import (
    HOST_MIGRATION_OWNERS,
    MigrationOwner,
    NamespaceRegistry,
    module_schema,
)
from sqlalchemy import text

from app.assembly import assembly

# A synthetic allocation used ONLY by the sensitivity self-test below. It is
# not in the shipped ledger and no migration creates it; the probe builds and
# rolls back its schema inside one transaction.
_PROBE_OWNER = MigrationOwner(
    owner="probe", prefix="pb", branch_label="probe", db_schema=module_schema("probe")
)


def test_registered_module_schemas_are_compliant(admin_engine) -> None:
    """The gate D1 landed, now driven by a REAL module schema.

    Built from `assembly.modules` — the composition the app boots — rather than
    `load_manifests(FEATURE_MODULES)`, which covers the assembly's own features
    only. With the narrower set this test walked an empty tuple and passed over
    nothing, which is exactly the vacuity the file's docstring warned about.
    """
    registry = NamespaceRegistry.from_manifests(assembly.modules)
    schemas = audited_schemas(registry)
    assert (
        "mod_tstudio" in schemas
    ), f"the first stateful module's schema is not being audited: {schemas}"
    with admin_engine.connect() as conn:
        violations = audit_live_schemas(conn, registry)
        # Assert on what was FOUND, not only on what was flagged: an audit over
        # a schema whose tables were never created reports no violations and no
        # coverage, and the two look identical in a green run.
        live = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'mod_tstudio'")
            )
        }
    assert live == {
        "templates",
        "template_versions",
    }, f"mod_tstudio does not hold the tables its manifest declares: {live}"
    assert not violations, "module schema violations:\n" + "\n".join(violations)


def test_the_ticketing_module_schema_holds_both_planes(admin_engine) -> None:
    """The DUAL-PLANE module's live proof (ADR-0023).

    `test_registered_module_schemas_are_compliant` above already audits every
    registered schema, `mod_tkt` included — but a green audit over a schema
    whose platform tables were never created is indistinguishable from a green
    audit that covered them. This asserts all FOUR tables exist, so the platform
    half cannot pass vacuously, and then re-checks the plane facts the pure
    contract can only assert against synthetic snapshots:

    - the platform tables carry no `tenant_id` and no RLS;
    - the tenant application role holds NOTHING on them;
    - the online platform role has schema USAGE and row DML, through the gate.
    """
    registry = NamespaceRegistry.from_manifests(assembly.modules)
    assert "mod_tkt" in audited_schemas(registry)

    with admin_engine.connect() as conn:
        live = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'mod_tkt'")
            )
        }
        assert live == {
            "tickets",
            "ticket_comments",
            "platform_tickets",
            "platform_ticket_comments",
        }, f"mod_tkt does not hold both declared planes: {live}"

        for table in ("platform_tickets", "platform_ticket_comments"):
            rls_on, rls_forced = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'mod_tkt' AND c.relname = :table"
                ),
                {"table": table},
            ).one()
            assert not rls_on and not rls_forced, (
                f"mod_tkt.{table} is a PLATFORM table with RLS — with no tenant "
                "column its predicate can only deny everything"
            )
            has_tenant = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'mod_tkt' AND table_name = :table "
                    "AND column_name = 'tenant_id'"
                ),
                {"table": table},
            ).scalar_one()
            assert not has_tenant, f"mod_tkt.{table} carries a tenant_id"

            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                held = conn.execute(
                    text(
                        # Both parameters are cast: `has_table_privilege` is
                        # overloaded, so Postgres cannot infer an untyped
                        # placeholder's type and fails with
                        # IndeterminateDatatype rather than running the check.
                        "SELECT has_table_privilege('app_user', "
                        "  format('mod_tkt.%I', CAST(:table AS text)), "
                        "  CAST(:privilege AS text))"
                    ),
                    {"table": table, "privilege": privilege},
                ).scalar_one()
                assert not held, (
                    f"app_user holds {privilege} on mod_tkt.{table} — on the "
                    "platform plane the REVOKE is the isolation"
                )


def test_the_gate_flags_a_column_level_platform_grant(admin_engine) -> None:
    """LIVE sensitivity proof for the column-level privilege seam.

    `has_table_privilege` deliberately remains false for this grant; only
    `has_any_column_privilege` can see it. The rollback leaves the migrated
    schema exactly as the following tests found it.
    """
    registry = NamespaceRegistry.from_manifests(assembly.modules)
    with admin_engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(
                text("GRANT SELECT (title) ON mod_tkt.platform_tickets TO app_user")
            )
            table_level = conn.execute(
                text(
                    "SELECT has_table_privilege("
                    "'app_user', 'mod_tkt.platform_tickets', 'SELECT')"
                )
            ).scalar_one()
            column_level = conn.execute(
                text(
                    "SELECT has_any_column_privilege("
                    "'app_user', 'mod_tkt.platform_tickets', 'SELECT')"
                )
            ).scalar_one()
            assert not table_level and column_level

            violations = audit_live_schemas(conn, registry)
            assert any(
                "mod_tkt.platform_tickets" in violation and "SELECT" in violation
                for violation in violations
            ), violations
        finally:
            transaction.rollback()


def test_the_gate_flags_missing_platform_schema_usage(admin_engine) -> None:
    """LIVE sensitivity proof: table DML cannot bypass missing schema USAGE."""
    registry = NamespaceRegistry.from_manifests(assembly.modules)
    with admin_engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text("REVOKE USAGE ON SCHEMA mod_tkt FROM platform_api"))
            violations = audit_live_schemas(conn, registry)
            assert any(
                "mod_tkt" in violation
                and "platform role 'platform_api' has no USAGE" in violation
                for violation in violations
            ), violations
        finally:
            transaction.rollback()


def test_a_missing_module_schema_is_flagged(admin_engine) -> None:
    """A registered module whose migrations did not run must fail the gate —
    its models would map to tables that do not exist."""
    registry = NamespaceRegistry((*HOST_MIGRATION_OWNERS, _PROBE_OWNER))
    with admin_engine.connect() as conn:
        violations = audit_live_schemas(conn, registry)
    assert any("does not exist" in v for v in violations), violations


def test_the_gate_flags_a_broken_module_table(admin_engine) -> None:
    """Sensitivity self-test: a tenant-scoped table in a module schema with no
    RLS, and a unique constraint that omits tenant_id, must both be flagged.
    Created inside a rolled-back transaction so nothing leaks."""
    registry = NamespaceRegistry((*HOST_MIGRATION_OWNERS, _PROBE_OWNER))
    schema = _PROBE_OWNER.db_schema
    with admin_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
            conn.execute(
                text(
                    f"CREATE TABLE {schema}.probe ("
                    "id uuid PRIMARY KEY, tenant_id uuid NOT NULL, "
                    "number text NOT NULL, CONSTRAINT uq_probe_number "
                    "UNIQUE (number))"
                )
            )
            violations = audit_live_schemas(conn, registry)
            assert any(
                "RLS must be ENABLEd AND FORCEd" in v for v in violations
            ), f"the module-schema audit is blind to missing RLS: {violations}"
            assert any(
                "uq_probe_number" in v and "omits tenant_id" in v for v in violations
            ), f"the composite-unique check is blind: {violations}"
        finally:
            trans.rollback()
