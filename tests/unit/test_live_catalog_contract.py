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

import pytest
from dotmac_kernel.migrations.catalog import (
    DEFAULT_APP_ROLE,
    DEFAULT_PLATFORM_ROLE,
    TABLE_PRIVILEGES,
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


# ── The platform plane (ADR-0023) ───────────────────────────────────────────


def _platform_table(name: str = "platform_tickets", **kwargs: object) -> TableFacts:
    """A COMPLIANT platform table: no tenant column, no RLS, fully revoked."""
    defaults: dict[str, object] = {
        "name": name,
        "rls_enabled": False,
        "rls_forced": False,
        "has_tenant_column": False,
        "policies": (),
        "unique_constraints": (("uq_platform_tickets_number", ("number",)),),
        "app_role_privileges": (),
    }
    defaults.update(kwargs)
    return TableFacts(**defaults)  # type: ignore[arg-type]


def test_a_declared_platform_table_is_held_to_the_platform_contract() -> None:
    """The baseline. Before ADR-0023 every one of these facts — no RLS, no
    policy, no tenant column — was three separate violations, which is why a
    dual-plane module could not compose at all."""
    snapshot = _snapshot(
        _compliant_table(),
        _platform_table(),
        platform_tables=frozenset({"platform_tickets"}),
    )
    assert audit_snapshot(snapshot) == ()


def test_a_platform_table_with_a_tenant_column_is_flagged() -> None:
    """The nullable-tenant_id dodge, refused at the gate rather than in review."""
    snapshot = _snapshot(
        _platform_table(has_tenant_column=True, tenant_column_nullable=True),
        platform_tables=frozenset({"platform_tickets"}),
    )
    assert any("has a tenant_id column" in v for v in audit_snapshot(snapshot))


def test_a_platform_table_the_tenant_role_can_read_is_flagged() -> None:
    """SENSITIVITY PROOF for the plane. On this side the REVOKE is the whole
    isolation mechanism, so an un-revoked platform table must fail as loudly as
    a tenant table with no RLS policy — otherwise "declare it platform" would be
    a supported way to switch isolation off."""
    snapshot = _snapshot(
        _platform_table(app_role_privileges=("SELECT",)),
        platform_tables=frozenset({"platform_tickets"}),
    )
    violations = audit_snapshot(snapshot)
    assert any(
        f"tenant role {DEFAULT_APP_ROLE!r} effectively holds" in v for v in violations
    ), violations


def test_a_platform_table_carrying_an_rls_policy_is_flagged() -> None:
    """It has no tenant column to test, so the predicate can only deny
    everything or nothing — its presence means the plane was misunderstood."""
    snapshot = _snapshot(
        _platform_table(rls_enabled=True, rls_forced=True, policies=(TENANT_POLICY,)),
        platform_tables=frozenset({"platform_tickets"}),
    )
    assert any("carries RLS policies" in v for v in audit_snapshot(snapshot))


def test_a_platform_table_with_rls_enabled_and_no_policy_is_flagged() -> None:
    """The worse of the two RLS failures, and the one that reads as protected.

    RLS with no matching policy denies EVERY row, so the control plane silently
    gets an empty result rather than an error. Checked separately from the
    policy case above, which this shape would otherwise slip past entirely.
    """
    snapshot = _snapshot(
        _platform_table(rls_enabled=True, rls_forced=True, policies=()),
        platform_tables=frozenset({"platform_tickets"}),
    )
    assert any("has row-level security" in v for v in audit_snapshot(snapshot))


def test_a_platform_table_no_platform_role_can_reach_is_flagged() -> None:
    """Declared-and-unusable is a violation too.

    The prohibitions alone would pass a table nobody can read: fully isolated,
    fully useless, and broken only at the first control-plane request.
    """
    snapshot = _snapshot(
        _platform_table(platform_role_privileges=()),
        platform_tables=frozenset({"platform_tickets"}),
    )
    violations = audit_snapshot(snapshot)
    assert any(
        f"platform role {DEFAULT_PLATFORM_ROLE!r} holds NO DML privilege" in v
        for v in violations
    ), violations


@pytest.mark.parametrize("privilege", ("REFERENCES", "TRUNCATE", "TRIGGER"))
def test_a_non_dml_privilege_does_not_make_a_platform_table_reachable(
    privilege: str,
) -> None:
    """Metadata/destructive privileges are not online data-plane access.

    A role that can only point an FK at a table, truncate it, or attach a
    trigger still cannot perform the SELECT/INSERT/UPDATE/DELETE work the
    platform surface exists to serve.
    """
    snapshot = _snapshot(
        _platform_table(platform_role_privileges=(privilege,)),
        platform_tables=frozenset({"platform_tickets"}),
    )
    violations = audit_snapshot(snapshot)
    assert any("holds NO DML privilege" in v for v in violations), (
        privilege,
        violations,
    )


def test_platform_role_needs_schema_usage_to_reach_a_platform_table() -> None:
    """A table grant is ineffective when the role cannot enter its schema."""
    snapshot = _snapshot(
        _platform_table(),
        platform_tables=frozenset({"platform_tickets"}),
        platform_role_has_usage=False,
    )
    violations = audit_snapshot(snapshot)
    assert any(
        f"platform role {DEFAULT_PLATFORM_ROLE!r} has no USAGE" in v for v in violations
    ), violations


@pytest.mark.parametrize("privilege", TABLE_PRIVILEGES)
def test_every_table_privilege_counts_as_an_unrevoked_platform_table(
    privilege: str,
) -> None:
    """SENSITIVITY PROOF across the WHOLE privilege set, not just DML.

    An earlier version checked only SELECT/INSERT/UPDATE/DELETE. PostgreSQL also
    grants TRUNCATE (empties the table), REFERENCES (lets an FK be pointed at
    it) and TRIGGER (attaches code to it) — none harmless, all previously
    invisible. This fails for any one of the seven, so the set cannot quietly
    shrink back.
    """
    snapshot = _snapshot(
        _platform_table(app_role_privileges=(privilege,)),
        platform_tables=frozenset({"platform_tickets"}),
    )
    violations = audit_snapshot(snapshot)
    assert any("effectively holds" in v for v in violations), (privilege, violations)


def test_the_privilege_query_asks_about_column_level_grants_too() -> None:
    """A column-level `GRANT SELECT (title)` does not register as a table-level
    SELECT, so table-level inquiry alone reports a still-readable table as
    fully revoked."""
    sql = catalog_queries()["role_table_privileges"]
    assert "has_any_column_privilege" in sql
    for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
        assert f"has_any_column_privilege(:role, c.oid, '{privilege}')" in sql
    # The three that have no column-level form must still be asked at table level.
    for privilege in ("DELETE", "TRUNCATE", "TRIGGER"):
        assert f"has_table_privilege(:role, c.oid, '{privilege}')" in sql


def test_an_undeclared_platform_table_still_gets_the_tenant_contract() -> None:
    """SPECIFICITY for the test above it: the platform contract applies because
    the table was DECLARED platform, not because it happens to lack a tenant
    column. A table that merely forgot its `tenant_id` must still fail."""
    snapshot = _snapshot(_platform_table(), platform_tables=frozenset())
    violations = audit_snapshot(snapshot)
    assert any("RLS must be ENABLEd AND FORCEd" in v for v in violations), violations


def test_a_platform_only_schema_does_not_demand_tenant_role_usage() -> None:
    """The self-contradiction, and the reason this correction exists.

    A platform-only module grants schema USAGE to the platform roles and nothing
    to `app_user` — correctly, since that role must hold no privilege on any
    table in it. The audit nonetheless demanded tenant-role USAGE, so a correct
    schema failed. Granting it to pass would have been worse than the false
    positive: reachability on a schema the tenant role must never read.
    """
    snapshot = _snapshot(
        _platform_table(),
        platform_tables=frozenset({"platform_tickets"}),
        app_role_has_usage=False,
        platform_role_has_usage=True,
    )
    assert audit_snapshot(snapshot) == ()


def test_a_platform_only_schema_still_needs_the_platform_role_to_reach_it() -> None:
    """A table grant is ineffective without schema USAGE, so a plane nobody can
    reach is broken even when every prohibition passes."""
    snapshot = _snapshot(
        _platform_table(),
        platform_tables=frozenset({"platform_tickets"}),
        app_role_has_usage=False,
        platform_role_has_usage=False,
    )
    violations = audit_snapshot(snapshot)
    assert any(f"{DEFAULT_PLATFORM_ROLE!r} has no USAGE" in v for v in violations)
    # And the message must name the PLANE, not just the role.
    assert any("platform" in v for v in violations)


def test_a_tenant_only_schema_still_demands_tenant_role_usage() -> None:
    """SPECIFICITY. The relaxation is for platform-ONLY schemas; every module
    shipped before ADR-0023 keeps this check exactly as it was."""
    snapshot = _snapshot(_compliant_table(), app_role_has_usage=False)
    violations = audit_snapshot(snapshot)
    assert any(
        f"tenant role {DEFAULT_APP_ROLE!r} has no USAGE" in v for v in violations
    )
    assert any("TENANT-plane" in v for v in violations)


def test_a_mixed_schema_demands_both_roles() -> None:
    """Both planes present means both roles must be able to reach the schema."""
    snapshot = _snapshot(
        _compliant_table(),
        _platform_table(),
        platform_tables=frozenset({"platform_tickets"}),
        app_role_has_usage=False,
        platform_role_has_usage=False,
    )
    violations = audit_snapshot(snapshot)
    assert any(
        f"tenant role {DEFAULT_APP_ROLE!r} has no USAGE" in v for v in violations
    )
    assert any(f"{DEFAULT_PLATFORM_ROLE!r} has no USAGE" in v for v in violations)


def test_a_declared_but_unmigrated_tenant_table_still_demands_usage() -> None:
    """Declared beats live. A tenant table whose migration has not run still
    needs the tenant role to reach the schema, and reporting only the missing
    table would send someone to fix the wrong thing."""
    snapshot = _snapshot(
        _platform_table(),
        declared_tables=frozenset({"platform_tickets", "not_yet_created"}),
        platform_tables=frozenset({"platform_tickets"}),
        app_role_has_usage=False,
    )
    violations = audit_snapshot(snapshot)
    assert any(
        f"tenant role {DEFAULT_APP_ROLE!r} has no USAGE" in v for v in violations
    )


def test_every_table_privilege_and_rls_check_survives_the_correction() -> None:
    """The correction touches reachability only. A platform table that breaks a
    prohibition must still fail, or relaxing USAGE would have relaxed the plane.
    """
    leaky = _snapshot(
        _platform_table(app_role_privileges=("TRUNCATE",)),
        platform_tables=frozenset({"platform_tickets"}),
        app_role_has_usage=False,
    )
    assert any("effectively holds" in v for v in audit_snapshot(leaky))

    with_rls = _snapshot(
        _platform_table(rls_enabled=True, rls_forced=True),
        platform_tables=frozenset({"platform_tickets"}),
        app_role_has_usage=False,
    )
    assert audit_snapshot(with_rls)


def test_a_foreign_key_across_the_planes_is_flagged() -> None:
    """They share a lifecycle, never a row. An FK is the one crossing the
    database itself would enforce and therefore permit."""
    snapshot = _snapshot(
        _compliant_table(name="tickets"),
        _platform_table(),
        platform_tables=frozenset({"platform_tickets"}),
        foreign_keys=(
            ForeignKeyFacts(
                table="tickets",
                name="fk_tickets_platform",
                columns=("platform_ticket_id",),
                referenced_table="platform_tickets",
            ),
        ),
    )
    assert any(
        "crosses the tenant/platform plane" in v for v in audit_snapshot(snapshot)
    )


def test_a_module_declaring_no_platform_tables_is_audited_exactly_as_before() -> None:
    """The compatibility half: every module shipped before ADR-0023 declares no
    platform plane, and none of them may change behaviour because of it."""
    tenant_only = _snapshot(_compliant_table(), platform_role_has_usage=False)
    assert audit_snapshot(tenant_only) == ()
    assert tenant_only.platform_tables == frozenset()


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


# ---------------------------------------------------------------------------
# ADR-0006, 2026-08-15: a legacy source and its module shadow, disambiguated.
#
# The vendor control plane composes `dotmac-entitlement-allocation` in SHADOW
# mode while its own writer stays authoritative. Both sets of tables exist at
# once by design — and its legacy tables were named `allocations` and
# `allocation_entries`, exactly what the module owns in `mod_ealloc`, so this
# audit refused the database. Refusing was correct.
#
# These two cases pin the before and after against the REAL audit rather than a
# parallel predicate, because only this one runs in production.
# ---------------------------------------------------------------------------

SHADOWED_TABLES = ("allocations", "allocation_entries")


def test_an_ambiguous_legacy_shadow_is_refused() -> None:
    """The exact shape that failed: legacy tables squatting under module names.

    A composition where a reader cannot tell which `allocations` a query means
    is broken whatever the migration state — a mis-set `search_path`, a
    hand-written query or an ORM mapping silently reads the wrong rows.
    """
    snapshot = _snapshot(
        *(_compliant_table(name) for name in SHADOWED_TABLES),
        declared_tables=frozenset(SHADOWED_TABLES),
        host_schema_squatters=SHADOWED_TABLES,
    )

    violations = audit_snapshot(snapshot)

    squatting = [v for v in violations if "compatibility namespace" in v]
    assert len(squatting) == len(SHADOWED_TABLES), violations
    for name in SHADOWED_TABLES:
        assert any(name in v for v in squatting), (name, squatting)


def test_a_disambiguated_legacy_shadow_is_permitted() -> None:
    """The permitted shape: the LEGACY table renamed, so nothing squats.

    `public.legacy_entitlement_allocations` no longer collides with
    `mod_ealloc.allocations`, so the module's schema and lineage can be
    exercised before any data moves. The module keeps the clean name it owns
    after cutover, which is why the legacy side is the side that gets renamed.

    The audit sees this as zero squatters — which is the point: disambiguation
    removes the finding rather than suppressing it.
    """
    snapshot = _snapshot(
        *(_compliant_table(name) for name in SHADOWED_TABLES),
        declared_tables=frozenset(SHADOWED_TABLES),
        host_schema_squatters=(),
    )

    violations = audit_snapshot(snapshot)

    assert not [v for v in violations if "compatibility namespace" in v], violations


def test_the_squatter_rule_still_bites_after_disambiguation() -> None:
    """Sensitivity proof: the permitted case must not pass for the wrong reason.

    If the squatter check ever stopped reporting, the permitted-shape test above
    would keep passing and stop meaning anything. This pins the difference — the
    same declared tables must be refused when they squat and accepted when they
    do not.
    """
    declared = frozenset(SHADOWED_TABLES)
    tables = tuple(_compliant_table(name) for name in SHADOWED_TABLES)

    refused = audit_snapshot(
        _snapshot(
            *tables, declared_tables=declared, host_schema_squatters=SHADOWED_TABLES
        )
    )
    permitted = audit_snapshot(
        _snapshot(*tables, declared_tables=declared, host_schema_squatters=())
    )

    assert [v for v in refused if "compatibility namespace" in v], (
        "the audit accepted the exact composition it refused in production — "
        "the squatter rule has stopped detecting anything"
    )
    assert not [v for v in permitted if "compatibility namespace" in v]
