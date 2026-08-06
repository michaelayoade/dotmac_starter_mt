"""Dynamic RLS/grants catalog audit (control-plane security Task 3).

Not a fixture list: this test derives its expectations from the LIVE
Postgres catalogs (`pg_class`, `pg_policies`, `pg_constraint`,
`information_schema`) plus `Base.metadata`, so any FUTURE table that
forgets tenant_id / RLS / FORCE / policy / grants / composite-FK — or
escapes Alembic entirely — fails CI without anyone remembering to add a
test. The only hand-maintained inputs are the explicit allowlists below,
each entry with its reason.

Table classes audited:
- PLATFORM catalog tables (`_PLATFORM_TABLES`): no tenant_id, no RLS. The
  PRIVATE subset must be completely invisible to `app_user`; the resolver
  set (`tenants`/`tenant_domains`) is readable but never writable by
  `app_user`.
- Tenant-scoped tables: `tenant_id NOT NULL` + ENABLE + FORCE RLS + a
  policy — OR a subtype table whose policy is an EXISTS-join back to a
  tenant-scoped parent (`party_persons`/`party_organizations` pattern,
  detected from `pg_policies.qual`, not from a name list).
- `domain_settings`: the ONE documented exception — nullable tenant_id
  (platform-default rows) with a split read/write policy pair.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

from dotmac_kernel.migrations.catalog import TENANT_COLUMN, catalog_queries
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Allowlists — every entry needs a reason. Additions are a design decision,
# not a convenience: a new platform table must ALSO get its grants right in
# its migration (the audit checks that too).
# ---------------------------------------------------------------------------

# Platform catalog tables app_user may READ (tenant resolution runs as
# app_user before any tenant context exists) but never write.
_PLATFORM_READABLE = {
    "tenants",
    "tenant_domains",
}

# Platform catalog tables app_user must not even SELECT: credential /
# session material for platform actors (control-plane security Task 1) and
# the platform-scoped audit + idempotency ledgers (0009) + the platform outbox
# (0012) — records of platform-level operations that carry no tenant and are
# fully REVOKEd from the tenant application role.
_PLATFORM_PRIVATE = {
    "platform_admins",
    "platform_sessions",
    "platform_audit_events",
    "platform_inbox_records",
    "platform_outbox_events",
}

# Bookkeeping, invisible to the app roles entirely.
_INFRA_TABLES = {"alembic_version"}

_PLATFORM_TABLES = _PLATFORM_READABLE | _PLATFORM_PRIVATE | _INFRA_TABLES

# The one nullable-tenant_id split-policy exception (see module docstring
# and docs/ARCHITECTURE.md "Settings resolution order + platform-row RLS
# design").
_SPLIT_POLICY_EXCEPTION = "domain_settings"

# Tenant-scoped FKs that may reference a tenant-scoped table WITHOUT
# carrying tenant_id, each justified inline. Currently empty — keep it so.
_COMPOSITE_FK_ALLOWLIST: set[str] = set()


def _live_tables(conn) -> dict[str, tuple[bool, bool]]:
    """{table: (rls_enabled, rls_forced)} for ordinary public tables."""
    rows = conn.execute(
        text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r'"
        )
    ).all()
    return {r[0]: (r[1], r[2]) for r in rows}


def _policies(conn) -> dict[str, list[tuple[str, str | None, str | None]]]:
    rows = conn.execute(
        text(
            "SELECT tablename, policyname, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'public'"
        )
    ).all()
    out: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for tablename, policyname, qual, with_check in rows:
        out.setdefault(tablename, []).append((policyname, qual, with_check))
    return out


def _tenant_id_columns(conn) -> dict[str, bool]:
    """{table: tenant_id_is_nullable} for tables that HAVE a tenant_id."""
    rows = conn.execute(
        text(
            "SELECT table_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id'"
        )
    ).all()
    return {r[0]: r[1] == "YES" for r in rows}


def _foreign_keys(conn) -> list[tuple[str, str, list[str], str]]:
    rows = conn.execute(
        text(
            "SELECT conrelid::regclass::text, conname, "
            "  (SELECT array_agg(a.attname ORDER BY x.ord) "
            "   FROM unnest(conkey) WITH ORDINALITY AS x(attnum, ord) "
            "   JOIN pg_attribute a "
            "     ON a.attrelid = conrelid AND a.attnum = x.attnum), "
            "  confrelid::regclass::text "
            "FROM pg_constraint "
            "WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
        )
    ).all()
    return [(r[0], r[1], list(r[2]), r[3]) for r in rows]


def _unique_constraints(conn) -> list[tuple[str, str, list[str]]]:
    """The shared kernel query, adapted to the host `public` namespace."""
    rows = conn.execute(
        text(catalog_queries()["unique_constraints"]), {"schema": "public"}
    ).all()
    return [(r[0], r[1], list(r[2] or ())) for r in rows]


def audit_live_catalog(conn) -> list[str]:
    """Return every violation found in the live catalog (empty == compliant).

    Factored out so the sensitivity self-test can run the SAME audit against
    a deliberately broken table and prove the checker bites.
    """
    violations: list[str] = []
    tables = _live_tables(conn)
    policies = _policies(conn)
    tenant_cols = _tenant_id_columns(conn)

    for table, (rls_on, rls_forced) in tables.items():
        if table.startswith("pg_") or table in _PLATFORM_TABLES:
            continue
        table_policies = policies.get(table, [])
        if not rls_on or not rls_forced:
            violations.append(
                f"{table}: RLS must be ENABLEd AND FORCEd "
                f"(enabled={rls_on}, forced={rls_forced})"
            )
        if not table_policies:
            violations.append(f"{table}: no RLS policy in pg_policies")
            continue
        if table == _SPLIT_POLICY_EXCEPTION:
            if len(table_policies) < 2:
                violations.append(
                    f"{table}: documented split-policy exception must keep "
                    f"its read/write policy PAIR (found {len(table_policies)})"
                )
            if not tenant_cols.get(table, False):
                violations.append(
                    f"{table}: expected NULLABLE tenant_id (platform rows)"
                )
            continue
        if table in tenant_cols:
            if tenant_cols[table]:
                violations.append(f"{table}: tenant_id must be NOT NULL")
        else:
            # No tenant_id — only legitimate as a subtype table whose
            # isolation is an EXISTS-join policy back to a scoped parent.
            has_exists_join = any(
                qual is not None and "EXISTS" in qual.upper()
                for (_, qual, _) in table_policies
            )
            if not has_exists_join:
                violations.append(
                    f"{table}: no tenant_id column and no EXISTS-join RLS "
                    "policy — unscoped tenant data"
                )

    # Role hygiene: the online roles can never bypass RLS (the relay dispatcher
    # included — its cross-tenant reach is confined to the claim/settle functions,
    # never a role-level BYPASSRLS).
    for role in ("app_user", "platform_api", "outbox_dispatcher"):
        row = conn.execute(
            text(
                "SELECT rolbypassrls, rolsuper FROM pg_roles " "WHERE rolname = :role"
            ),
            {"role": role},
        ).one_or_none()
        if row is None:
            violations.append(f"role {role} does not exist")
            continue
        if row[0] or row[1]:
            violations.append(
                f"role {role}: must have rolbypassrls=false, rolsuper=false "
                f"(got bypassrls={row[0]}, super={row[1]})"
            )

    # Grant boundaries on the platform catalog.
    for table in sorted(_PLATFORM_PRIVATE):
        if table not in tables:
            continue
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            allowed = conn.execute(
                text("SELECT has_table_privilege('app_user', :t, :p)"),
                {"t": table, "p": priv},
            ).scalar_one()
            if allowed:
                violations.append(
                    f"{table}: app_user must have NO {priv} privilege "
                    "(platform-private table)"
                )
    for table in sorted(_PLATFORM_READABLE):
        if table not in tables:
            continue
        for priv in ("INSERT", "UPDATE", "DELETE"):
            allowed = conn.execute(
                text("SELECT has_table_privilege('app_user', :t, :p)"),
                {"t": table, "p": priv},
            ).scalar_one()
            if allowed:
                violations.append(
                    f"{table}: app_user may READ but never {priv} "
                    "(platform-owned catalog)"
                )

    # Composite-FK rule: a tenant-scoped FK into another tenant-scoped
    # table must carry tenant_id, or RLS lets a same-id row from another
    # tenant satisfy the constraint.
    scoped = {
        t
        for t in tables
        if t not in _PLATFORM_TABLES and t in tenant_cols and not tenant_cols[t]
    }
    for table, conname, cols, ref_table in _foreign_keys(conn):
        if table not in scoped or ref_table not in scoped:
            continue
        if conname in _COMPOSITE_FK_ALLOWLIST:
            continue
        if "tenant_id" not in cols:
            violations.append(
                f"{table}.{conname} -> {ref_table}: tenant-scoped FK must be "
                f"composite with tenant_id (has {cols})"
            )

    # Composite-unique rule: business uniqueness on a tenant-scoped table
    # includes the discriminator, or one tenant's value blocks another's
    # insert. This is deliberately derived from the same scoped set as the FK
    # rule, so platform catalog constraints remain outside the tenant contract.
    for relation, conname, cols in _unique_constraints(conn):
        table = relation.rpartition(".")[2].strip('"')
        if table not in scoped or TENANT_COLUMN in cols:
            continue
        violations.append(
            f"{table}.{conname}: unique constraint on {cols} omits "
            f"{TENANT_COLUMN} — one tenant's value blocks another tenant's insert"
        )
    return violations


def test_live_catalog_is_compliant(admin_engine) -> None:
    with admin_engine.connect() as conn:
        violations = audit_live_catalog(conn)
    assert not violations, "RLS/grants catalog violations:\n" + "\n".join(violations)


def test_metadata_matches_live_tables(admin_engine) -> None:
    """`Base.metadata` == live schema (modulo infra bookkeeping): a model
    missing a migration, or a migration-created table with no model, both
    fail here."""
    # Import every model-bearing module so metadata is fully populated —
    # explicitly, not relying on another test module having imported them first
    # (messaging registers outbox_events/inbox_records/platform_inbox_records).
    from dotmac_kernel import audit, models_platform, settings_models  # noqa: F401
    from dotmac_kernel.messaging import models as messaging_models  # noqa: F401
    from dotmac_kernel.models import Base

    from app.features.custom_fields import models as custom_fields  # noqa: F401

    with admin_engine.connect() as conn:
        live = set(_live_tables(conn)) - _INFRA_TABLES
    declared = set(Base.metadata.tables)
    missing_migrations = declared - live
    unmodelled = live - declared
    assert (
        not missing_migrations
    ), f"models with no live table (missing migration?): {missing_migrations}"
    assert not unmodelled, f"live tables with no model: {unmodelled}"


def test_audit_flags_a_broken_table(admin_engine) -> None:
    """Sensitivity self-test: a table with tenant_id and NO RLS must be
    flagged — created inside a rolled-back transaction so nothing leaks."""
    with admin_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(
                text(
                    "CREATE TABLE _rls_canary_probe ("
                    "id uuid PRIMARY KEY, tenant_id uuid NOT NULL, "
                    "number text NOT NULL, CONSTRAINT uq_rls_canary_number "
                    "UNIQUE (number))"
                )
            )
            # ... and a grant-boundary breach (GRANT is transactional too):
            # app_user given SELECT on a platform-PRIVATE table.
            conn.execute(text("GRANT SELECT ON platform_admins TO app_user"))
            violations = audit_live_catalog(conn)
            probe_hits = [v for v in violations if "_rls_canary_probe" in v]
            assert probe_hits, (
                "the catalog audit failed to flag a tenant-scoped table "
                "with no RLS — the checker is blind"
            )
            unique_hits = [v for v in violations if "uq_rls_canary_number" in v]
            assert unique_hits, (
                "the public-schema audit failed to flag a tenant-scoped "
                "unique constraint without tenant_id — the checker is blind"
            )
            grant_hits = [
                v for v in violations if "platform_admins" in v and "SELECT" in v
            ]
            assert grant_hits, (
                "the catalog audit failed to flag app_user SELECT on a "
                "platform-private table — the grant check is blind"
            )
        finally:
            trans.rollback()
