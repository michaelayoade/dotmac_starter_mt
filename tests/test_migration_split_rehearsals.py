"""Migration-split acceptance suite (kernel-boundary Task 1c).

The seven rehearsals the merged design
(`docs/superpowers/reviews/2026-07-30-kernel-migration-split-design.md`) requires
before the kernel/assembly migration split can be trusted — this suite, not a
code review, is what proves existing-v0.8 databases are safe.

Runs against the real test Postgres cluster (needs RLS + the app_user/
platform_api/app_admin roles, which the cluster already has from
`make test-db-up`). Each rehearsal provisions its OWN scratch database on the
cluster so the migrations run end-to-end in isolation.

Migrations are driven through Alembic's Python API (`alembic.command`) with a
Config built from the repo's real `alembic.ini`, so the version_locations
composition and branch labels under test are exactly the ones shipped.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"


def _superuser_url() -> str:
    # Cluster superuser (postgres) — used only to CREATE/DROP the scratch
    # database and hand its schema to app_admin. Migrations themselves run as
    # app_admin, exactly as production does (see `scratch_db`).
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — migration rehearsals need Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def scratch_db() -> Iterator[str]:
    """Create an isolated scratch database and yield an APP_ADMIN url for it.

    Migrations run as `app_admin` (the production MIGRATION role, BYPASSRLS)
    so the adopted table's owner and grant set match production — the
    full-contract verification's "no unsafe grants" check is only faithful
    when the migrator is the real role, not the cluster superuser. The
    superuser is used solely to create the DB and give its public schema to
    app_admin (mirroring scripts/dev-db-init.sh)."""
    superuser = _superuser_url()
    name = f"cf_rehearsal_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    # Hand the new DB's public schema to app_admin so it can create objects
    # and issue the migrations' grants.
    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
    setup.dispose()
    try:
        yield _url_for(superuser, name, user="app_admin")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _alembic_config(url: str, *, version_locations: str | None = None):
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    if version_locations is not None:
        cfg.set_main_option("version_locations", version_locations)
    # env.py reads the URL from MIGRATION_DATABASE_URL.
    os.environ["MIGRATION_DATABASE_URL"] = url
    return cfg


def _both_locations() -> str:
    return f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS}"


def _kernel_only_locations() -> str:
    # Simulates the OLD v0.8 migrator, which has no assembly lineage / no a001.
    return str(KERNEL_VERSIONS)


def _upgrade(url: str, target: str = "heads", *, version_locations: str | None = None):
    from alembic import command

    command.upgrade(
        _alembic_config(url, version_locations=version_locations or _both_locations()),
        target,
    )


def _stamp(url: str, target: str, *, version_locations: str | None = None):
    from alembic import command

    command.stamp(
        _alembic_config(url, version_locations=version_locations or _both_locations()),
        target,
    )


def _downgrade(url: str, target: str, *, version_locations: str | None = None):
    from alembic import command

    command.downgrade(
        _alembic_config(url, version_locations=version_locations or _both_locations()),
        target,
    )


def _q(url: str, sql: str, **params):
    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            return conn.execute(text(sql), params).scalar()
    finally:
        eng.dispose()


def _versions(url: str) -> set[str]:
    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            if not conn.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            ).scalar():
                return set()
            return set(
                conn.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )
    finally:
        eng.dispose()


def _column_exists(url: str, table: str, column: str) -> bool:
    return bool(
        _q(
            url,
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t AND column_name=:c",
            t=table,
            c=column,
        )
    )


def _table_exists(url: str, table: str) -> bool:
    return bool(_q(url, "SELECT to_regclass('public.' || :t) IS NOT NULL", t=table))


# ─────────────────────────────────────────────────────────────────────────────
# Rehearsal 1 — fresh empty-assembly (kernel base only)
# ─────────────────────────────────────────────────────────────────────────────


def test_rehearsal_1_fresh_empty_assembly(scratch_db: str) -> None:
    # Kernel lineage only: upgrade the `kernel` branch head.
    _upgrade(scratch_db, "kernel@head")
    assert _column_exists(scratch_db, "parties", "custom_fields")
    assert not _table_exists(scratch_db, "custom_field_definitions")
    # Kernel head advanced to 0011 (outbox relay leasing); the 0008 outbox/inbox
    # tables and the 0009 platform tables all exist, the assembly's
    # custom_field_definitions still does not.
    assert _table_exists(scratch_db, "outbox_events")
    assert _table_exists(scratch_db, "inbox_records")
    assert _table_exists(scratch_db, "platform_audit_events")
    assert _table_exists(scratch_db, "platform_inbox_records")
    assert _table_exists(scratch_db, "tenant_entitlement_grants")
    assert _versions(scratch_db) == {"0012_platform_outbox"}


# ─────────────────────────────────────────────────────────────────────────────
# Rehearsal 2 — fresh reference-assembly (both lineages)
# ─────────────────────────────────────────────────────────────────────────────


def test_rehearsal_2_fresh_reference_assembly(scratch_db: str) -> None:
    _upgrade(scratch_db, "heads")
    assert _table_exists(scratch_db, "custom_field_definitions")
    # The kernel schema is fully applied (platform_admins is 0007's table).
    assert _table_exists(scratch_db, "platform_admins")
    # Runtime version table: a001 `depends_on` 0007 subsumes ONLY the kernel head
    # it pins. The kernel has since advanced to 0011 (outbox relay leasing), which a001
    # does NOT depend on — so both lineage heads now appear, exactly the case the
    # split design anticipated ("if the kernel advances past what a001 depends
    # on, both heads would appear"). The assembly head is a002 (applied licences).
    assert _versions(scratch_db) == {"0012_platform_outbox", "a003_revocation_lists"}
    # RLS + grants correct: FORCE RLS on, the isolation policy present, and
    # app_user holds DML (a proxy for the full contract the migration verifies).
    assert _q(
        scratch_db,
        "SELECT relforcerowsecurity FROM pg_class "
        "WHERE oid='custom_field_definitions'::regclass",
    )
    assert _q(
        scratch_db,
        "SELECT has_table_privilege('app_user','custom_field_definitions','SELECT')",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rehearsal 3 — existing-v0.8 adoption (table pre-exists, a001 not recorded)
# ─────────────────────────────────────────────────────────────────────────────


def _simulate_v08(url: str) -> None:
    """Reach the state of an existing v0.8 database: kernel schema at 0007 with
    `custom_field_definitions` ALREADY present (as the old un-reduced 0004 left
    it) but `a001` NOT recorded. Built by running a001's own create path, then
    stamping the version table back to the kernel head only."""
    _upgrade(url, "heads")  # creates everything incl. the table + records a001
    # Un-record the assembly branch (a001) while keeping the kernel head + table.
    # Branch-aware `assembly@base` removes only a001 — `kernel@head` no longer
    # collapses it now the kernel head (0008) has advanced past a001's pin.
    _stamp(url, "assembly@base")
    assert _table_exists(url, "custom_field_definitions")
    # kernel@head is now 0011 (relay leasing added atop 0010 entitlements),
    # but a001 is un-recorded — the "table present, a001 not recorded" state adoption
    # repairs.
    assert _versions(url) == {"0012_platform_outbox"}


def test_rehearsal_3_existing_v08_adoption(scratch_db: str) -> None:
    _simulate_v08(scratch_db)
    # Seed a row to prove adoption never touches data.
    eng = create_engine(scratch_db)
    with eng.begin() as conn:
        tid = conn.execute(
            text(
                "INSERT INTO tenants (id, slug, name) "
                "VALUES (gen_random_uuid(), 'adopt', 'Adopt') RETURNING id"
            )
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO custom_field_definitions "
                "(id, tenant_id, entity_type, field_code, field_name, field_type) "
                "VALUES (gen_random_uuid(), :tid, 'party', 'eye_color', "
                "'Eye color', 'TEXT')"
            ),
            {"tid": tid},
        )
    eng.dispose()

    # Adopt: a001 sees the table, verifies the full contract, records itself.
    # a001 subsumes only 0007 (its depends_on pin); the kernel head has advanced
    # to 0011, so both lineage heads appear (see rehearsal 2). The assembly
    # lineage continues to a002 (applied licences) on the same upgrade.
    _upgrade(scratch_db, "heads")
    assert _versions(scratch_db) == {"0012_platform_outbox", "a003_revocation_lists"}
    # Data survived untouched.
    assert _q(scratch_db, "SELECT count(*) FROM custom_field_definitions") == 1
    assert (
        _q(scratch_db, "SELECT field_code FROM custom_field_definitions LIMIT 1")
        == "eye_color"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rehearsal 4 — per-element drift guard (each broken element => a001 raises)
# ─────────────────────────────────────────────────────────────────────────────

_DRIFT_MUTATIONS = {
    "drop_unique": (
        "ALTER TABLE custom_field_definitions "
        "DROP CONSTRAINT uq_custom_field_definitions_tenant_entity_code"
    ),
    "drop_check": (
        "ALTER TABLE custom_field_definitions "
        "DROP CONSTRAINT ck_custom_field_definitions_field_type"
    ),
    "drop_index": "DROP INDEX ix_custom_field_definitions_tenant_id",
    "disable_force_rls": (
        "ALTER TABLE custom_field_definitions NO FORCE ROW LEVEL SECURITY"
    ),
    "alter_policy": (
        "ALTER POLICY custom_field_definitions_tenant_isolation "
        "ON custom_field_definitions USING (true)"
    ),
    "revoke_grant": ("REVOKE INSERT ON custom_field_definitions FROM app_user"),
    "unsafe_public_grant": "GRANT SELECT ON custom_field_definitions TO PUBLIC",
    "drop_column_default": (
        "ALTER TABLE custom_field_definitions ALTER COLUMN show_in_form DROP DEFAULT"
    ),
    "drop_fk": (
        "ALTER TABLE custom_field_definitions "
        "DROP CONSTRAINT custom_field_definitions_tenant_id_fkey"
    ),
}


@pytest.mark.parametrize("mutation", sorted(_DRIFT_MUTATIONS))
def test_rehearsal_4_adoption_drift_guard(scratch_db: str, mutation: str) -> None:
    _simulate_v08(scratch_db)
    eng = create_engine(scratch_db)
    with eng.begin() as conn:
        conn.execute(text(_DRIFT_MUTATIONS[mutation]))
    eng.dispose()

    # Adoption must FAIL closed and NOT record a001. a001's _AdoptionContractError
    # is a RuntimeError; alembic propagates the migration's own exception.
    with pytest.raises(RuntimeError):
        _upgrade(scratch_db, "heads")
    assert "a001_adopt_cfd" not in _versions(scratch_db)


# ─────────────────────────────────────────────────────────────────────────────
# Rehearsal 5 — destructive-downgrade guard
# ─────────────────────────────────────────────────────────────────────────────


def test_rehearsal_5_destructive_downgrade_guard(scratch_db: str, monkeypatch) -> None:
    _upgrade(scratch_db, "heads")
    assert _table_exists(scratch_db, "custom_field_definitions")
    assert _table_exists(scratch_db, "tenant_applied_licences")
    assert _table_exists(scratch_db, "tenant_revocation_lists")

    # Without the authorization flags: refuses (RuntimeError), tables intact.
    # a003 (revocation lists) guards its drop first in the downgrade path, then
    # a002 (applied licences) — both share the LICENCE flag, since dropping
    # either re-opens a licensing hole and it is one operator decision — then
    # a001 guards custom_field_definitions behind its own flag.
    monkeypatch.delenv("DOTMAC_ALLOW_DESTRUCTIVE_CF_DOWNGRADE", raising=False)
    monkeypatch.delenv("DOTMAC_ALLOW_DESTRUCTIVE_LICENCE_DOWNGRADE", raising=False)
    with pytest.raises(RuntimeError):
        _downgrade(scratch_db, "assembly@base")
    assert _table_exists(scratch_db, "custom_field_definitions")
    assert _table_exists(scratch_db, "tenant_applied_licences")
    assert _table_exists(scratch_db, "tenant_revocation_lists")
    assert "a003_revocation_lists" in _versions(scratch_db)

    # Licence drops authorized alone: a001 still refuses; the CF table survives.
    monkeypatch.setenv("DOTMAC_ALLOW_DESTRUCTIVE_LICENCE_DOWNGRADE", "1")
    with pytest.raises(RuntimeError):
        _downgrade(scratch_db, "assembly@base")
    assert _table_exists(scratch_db, "custom_field_definitions")

    # With both flags (approved runbook): drops both.
    monkeypatch.setenv("DOTMAC_ALLOW_DESTRUCTIVE_CF_DOWNGRADE", "1")
    _downgrade(scratch_db, "assembly@base")
    assert not _table_exists(scratch_db, "custom_field_definitions")
    assert not _table_exists(scratch_db, "tenant_applied_licences")
    assert not _table_exists(scratch_db, "tenant_revocation_lists")
    # The kernel column is untouched.
    assert _column_exists(scratch_db, "parties", "custom_fields")


# ─────────────────────────────────────────────────────────────────────────────
# Rehearsal 6 — runtime rollback (the stamp is load-bearing; negative control)
# ─────────────────────────────────────────────────────────────────────────────


def test_rehearsal_6_runtime_rollback(scratch_db: str) -> None:
    # New code deployed: the assembly head (a002, subsuming a001) recorded.
    _upgrade(scratch_db, "heads")
    assert "a003_revocation_lists" in _versions(scratch_db)

    # NEGATIVE CONTROL: the old v0.8 migrator (kernel-only version_locations,
    # no assembly scripts) run against this database — reproduces the real
    # failure `deploy.sh <old-tag>` would hit: alembic cannot locate the
    # recorded assembly head.
    from alembic.util.exc import CommandError

    with pytest.raises(CommandError) as exc:
        _upgrade(scratch_db, "heads", version_locations=_kernel_only_locations())
    assert "a002" in str(exc.value).lower() or "locate" in str(exc.value).lower()

    # THE ROLLBACK PROCEDURE (new code): un-record the assembly branch via a
    # branch-aware stamp to `assembly@base`, tables preserved — NOT a downgrade
    # (that would drop them). This removes the whole assembly branch (a001 +
    # a002) and leaves the kernel head; `kernel@head` no longer collapses the
    # branch now the kernel lineage has advanced past a001's `depends_on` pin.
    _stamp(scratch_db, "assembly@base")
    assert _versions(scratch_db) == {"0012_platform_outbox"}
    assert _table_exists(scratch_db, "custom_field_definitions")

    # Now the kernel-only migrator succeeds: it sees only the kernel head (0008),
    # which it knows — a001 is no longer recorded.
    _upgrade(scratch_db, "heads", version_locations=_kernel_only_locations())
    assert _versions(scratch_db) == {"0012_platform_outbox"}


# ─────────────────────────────────────────────────────────────────────────────
# Rehearsal 7 — expected heads per lineage
# ─────────────────────────────────────────────────────────────────────────────


def test_rehearsal_7_expected_heads_per_lineage() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    heads = set(script.get_heads())
    # Three lineages now: the two grandfathered host owners plus the first
    # installed stateful MODULE (ADR-0006 M1). One head per owner is the
    # invariant — a second head inside ONE lineage would be the real defect.
    assert heads == {
        "0012_platform_outbox",
        "a003_revocation_lists",
        "ts_0001_templates",
    }, f"unexpected head set: {heads}"

    # Each head carries the expected branch label.
    kernel_head = script.get_revision("kernel@head")
    assembly_head = script.get_revision("assembly@head")
    module_head = script.get_revision("template_studio@head")
    assert kernel_head.revision == "0012_platform_outbox"
    assert assembly_head.revision == "a003_revocation_lists"
    assert module_head.revision == "ts_0001_templates"
