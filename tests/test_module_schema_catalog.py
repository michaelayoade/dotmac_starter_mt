"""Post-migration live-catalog gate over every registered module schema.

The executable half of ADR-0006 D1 item 7. `tests/unit/test_live_catalog_
contract.py` covers the decision logic exhaustively from synthetic snapshots;
this file drives the same contract against a REAL, migrated Postgres —
`fetch_snapshot`'s catalog queries plus `audit_live_schemas`' loop.

**No module schema is registered yet** (`MIGRATION_OWNER_LEDGER` ships the two
host owners only), so `test_registered_module_schemas_are_compliant` walks an
empty set today — deliberately, and visibly: it asserts on the set it walked
rather than passing silently. It bites the moment the first stateful module is
allocated, which is the point of landing the seam with D1 rather than with the
module that needs it.

The sensitivity self-test below is what proves the seam is real NOW: it builds
a `mod_` schema with a deliberately broken table inside a rolled-back
transaction and asserts the audit flags it — the same technique
`tests/test_rls_catalog.py::test_audit_flags_a_broken_table` uses.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

from dotmac_kernel.features import load_manifests
from dotmac_kernel.migrations.catalog import audit_live_schemas, audited_schemas
from dotmac_kernel.namespaces import (
    HOST_MIGRATION_OWNERS,
    MigrationOwner,
    NamespaceRegistry,
    module_schema,
)
from sqlalchemy import text

from app.features import FEATURE_MODULES

# A synthetic allocation used ONLY by the sensitivity self-test below. It is
# not in the shipped ledger and no migration creates it; the probe builds and
# rolls back its schema inside one transaction.
_PROBE_OWNER = MigrationOwner(
    owner="probe", prefix="pb", branch_label="probe", db_schema=module_schema("probe")
)


def test_registered_module_schemas_are_compliant(admin_engine) -> None:
    registry = NamespaceRegistry.from_manifests(load_manifests(FEATURE_MODULES))
    schemas = audited_schemas(registry)
    # Visibly empty, not silently skipped: every feature this assembly ships is
    # a host feature whose tables live in the `public` compatibility namespace.
    assert schemas == (), f"unexpected registered module schemas: {schemas}"
    with admin_engine.connect() as conn:
        violations = audit_live_schemas(conn, registry)
    assert not violations, "module schema violations:\n" + "\n".join(violations)


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
