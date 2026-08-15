"""`mod_intg` composed and audited against a REAL Postgres, in a scratch database.

Why a scratch database rather than the reference assembly: `dotmac-integration`
is composed by the separate `dotmac_integrator` deployment, NOT by this
repository's runtime (ADR-0024 §§ 6-7). Adding it to `app/assembly.py` or the
shipped `alembic.ini` merely to get CI coverage would contradict the deployment
boundary the module exists to establish — every starter deployment would grow a
`mod_intg` schema it never uses.

So this follows the optional-module pattern `test_files_isolation.py` and
`test_imports_isolation.py` already use: create a throwaway database, compose
the kernel lineage plus the `ig` lineage in a temporary Alembic configuration,
migrate as `app_admin`, and audit.

**Without this file a normal CI push exercises neither `ig_0001` nor
`ig_0002`**, because neither lineage is part of the Starter composition. The
migrations would first run in production.

## What is proved here that a unit test cannot

* the two lineages actually compose and apply, in order;
* the live schema holds EXACTLY the tables the manifest declares — read from
  `module.platform_tables`, never a second hand-written list that can drift;
* the full platform-plane catalog contract: no `tenant_id`, no RLS, and
  `app_user` REVOKEd across all seven privileges and their column-level forms;
* the concurrency claims are real. The unit tests drive them through SQLite,
  which cannot demonstrate that two sessions racing one row produce one winner.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[1]
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
INTEGRATION_VERSIONS = (
    REPO_ROOT / "packages/dotmac-integration/src/dotmac_integration/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the composition proof needs Postgres")
    return url


def _url_for(base: str, name: str, user: str | None = None) -> str:
    from sqlalchemy.engine import make_url

    url = make_url(base).set(database=name)
    if user:
        url = url.set(username=user, password=user)
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def migrated_scratch() -> Iterator[tuple[str, str]]:
    """A throwaway database holding kernel + `ig`, migrated as `app_admin`."""
    superuser = _superuser_url()
    name = f"intg_compose_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        # A TEMPORARY composition. The shipped alembic.ini is untouched, because
        # this repository does not deploy the Integrator.
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {INTEGRATION_VERSIONS}"
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
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


# ── Composition ─────────────────────────────────────────────────────────────


def test_the_live_schema_holds_exactly_what_the_manifest_declares(
    migrated_scratch: tuple[str, str],
) -> None:
    """Read from `module.platform_tables`, never a second list.

    A hand-written expected set in this file would be a duplicate of the
    manifest that drifts from it — and it would pass while the manifest and the
    database disagreed, which is the one thing this test exists to catch.
    """
    from dotmac_integration import module

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        live = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'mod_intg'")
            )
        }
    engine.dispose()

    # NOT vacuous: an empty manifest and an empty schema would satisfy the
    # equality above while proving nothing ran. The count is asserted against
    # the declaration's own length, so adding a table does not edit this test,
    # but a manifest that declares NOTHING fails it.
    assert live, "mod_intg holds no table — the ig lineage did not apply"
    assert len(module.platform_tables) == len(set(module.platform_tables)) >= 8
    assert live == set(module.platform_tables)
    assert module.tables == (), "this module owns no tenant-plane table"


def test_the_module_registers_and_claims_only_its_own_schema(
    migrated_scratch: tuple[str, str],
) -> None:
    """Registration is the first thing a kernel below the floor breaks.

    `from_manifests` raises `UnallocatedNamespaceError` without the ledger row,
    so this fails loudly on a kernel predating the allocation rather than
    surfacing as a confusing migration error later.
    """
    from dotmac_integration import module
    from dotmac_kernel.migrations.catalog import audited_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry

    registry = NamespaceRegistry.from_manifests([module])
    assert set(audited_schemas(registry)) == {"mod_intg"}
    assert module.platform_tables, "a platform-only module with no platform table"


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        # The platform runtime role must REACH the plane: a table grant is
        # ineffective without schema USAGE, so a plane nobody can read is broken
        # even when every prohibition passes.
        ("platform_api", True),
        # And the tenant role must not. Kernel 0.1.0a57 stopped REQUIRING this
        # USAGE on a platform-only schema; nothing in the kernel forbids it, so
        # the module's own migration is what must not grant it — which makes
        # this an assertion the module owns, not one the kernel makes for it.
        ("app_user", False),
    ],
)
def test_schema_usage_follows_reachability(
    migrated_scratch: tuple[str, str], role: str, expected: bool
) -> None:
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        held = conn.execute(
            text("SELECT has_schema_privilege(CAST(:r AS text), 'mod_intg', 'USAGE')"),
            {"r": role},
        ).scalar_one()
    engine.dispose()
    assert held is expected, (
        f"{role} {'lacks' if expected else 'holds'} USAGE on mod_intg — "
        "USAGE belongs to the role that must reach the plane, and only to it"
    )


def test_the_audit_actually_bites_on_this_schema(
    migrated_scratch: tuple[str, str],
) -> None:
    """The sensitivity proof for the clean audit above (ADR-0018).

    `audit_live_schemas` returning no violations is only evidence if it CAN
    return one here. A detector that silently skipped `mod_intg` — a bad schema
    filter, a plane misread as tenant-only, an exception swallowed — would look
    exactly like a passing contract.

    So a violation is manufactured against the live schema and the audit is made
    to report it. Rolled back inside the transaction it was granted in, because
    the scratch database is module-scoped and shared with the canaries below.
    """
    from dotmac_integration import module
    from dotmac_kernel.migrations.catalog import audit_live_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry

    registry = NamespaceRegistry.from_manifests([module])
    victim = sorted(module.platform_tables)[0]

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text(f'GRANT SELECT ON mod_intg."{victim}" TO app_user'))
            violations = audit_live_schemas(conn, registry)
        finally:
            transaction.rollback()

        assert violations, (
            "app_user was granted SELECT on a platform table and the audit "
            "reported nothing — the clean run above proves nothing"
        )
        assert any(victim in v for v in violations)

        # and the rollback restored the contract, so the shared database is not
        # left poisoned for every test that runs after this one
        assert not audit_live_schemas(conn, registry)
    engine.dispose()


def test_the_platform_plane_catalog_contract_holds(
    migrated_scratch: tuple[str, str],
) -> None:
    """The kernel's own audit, run over the real schema.

    `audit_live_schemas` is what enforces ADR-0023's platform contract: no
    tenant column, no RLS, and `app_user` holding nothing. Running the module's
    registry through it is the whole point of composing a real database.
    """
    from dotmac_integration import module
    from dotmac_kernel.migrations.catalog import audit_live_schemas, audited_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry

    registry = NamespaceRegistry.from_manifests([module])
    assert "mod_intg" in audited_schemas(registry)

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        violations = audit_live_schemas(conn, registry)
    engine.dispose()

    assert not violations, "platform-plane violations:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "privilege",
    ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"],
)
def test_app_user_holds_nothing_on_any_table(
    migrated_scratch: tuple[str, str], privilege: str
) -> None:
    """On this plane the REVOKE is the isolation, so it is checked directly and
    across ALL SEVEN privileges — a DML-only check would pass a table
    `app_user` could still TRUNCATE."""
    from dotmac_integration import module

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        for table in module.platform_tables:
            held = conn.execute(
                text(
                    "SELECT has_table_privilege('app_user', "
                    "  format('mod_intg.%I', CAST(:t AS text)), "
                    "  CAST(:p AS text))"
                ),
                {"t": table, "p": privilege},
            ).scalar_one()
            assert not held, f"app_user holds {privilege} on mod_intg.{table}"
    engine.dispose()


# ── Live concurrency canaries ───────────────────────────────────────────────
#
# SQLite cannot demonstrate that two sessions racing one row produce exactly one
# winner. These do, against the mechanism that actually ships.


def _installation_and_binding(conn, request=None) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    """A distinctly-named installation per call.

    The scratch database is MODULE-scoped and stays that way: sharing it is what
    exposed `uq_connector_installations_key_name` doing its job when three
    canaries reused one name. The constraint is right; the factory was wrong.

    Deliberately NOT fixed by relaxing the constraint, truncating between tests,
    or making the database function-scoped — each of those hides the collision
    rather than removing the reason for it, and the last would also throw away
    the shared-state coverage that found it.

    The name carries the TEST NODE plus a random suffix: the node makes a
    failure traceable to the test that created the row, and the suffix keeps
    repeated calls inside one test distinct.
    """
    node = "shared"
    if request is not None:
        node = re.sub(r"[^a-z0-9]+", "-", request.node.name.lower()).strip("-")[:80]
    installation_id, binding_id = uuid.uuid4(), uuid.uuid4()
    unique_name = f"{node}-{uuid.uuid4().hex[:8]}"
    conn.execute(
        text(
            "INSERT INTO mod_intg.connector_installations ("
            "id, connector_key, connector_version, spi_range, manifest_digest, "
            "name, environment, state) VALUES ("
            ":id, 'fake', '1.0.0', '>=1.0,<2.0', :digest, :name, "
            "'production', 'enabled')"
        ),
        {"id": installation_id, "digest": "d" * 64, "name": unique_name},
    )
    conn.execute(
        text(
            "INSERT INTO mod_intg.capability_bindings ("
            "id, installation_id, capability_id, state) VALUES ("
            ":id, :installation, 'conformance.echo.v1', 'enabled')"
        ),
        {"id": binding_id, "installation": installation_id},
    )
    return installation_id, binding_id


def test_inbox_deduplication_is_enforced_by_the_database(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """Two workers racing one redelivery is the NORMAL case, not the edge case,
    so the constraint must be in the database rather than in a service."""
    from sqlalchemy.exc import IntegrityError

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        _, binding_id = _installation_and_binding(conn, request)
        installation_id = conn.execute(
            text(
                "SELECT installation_id FROM mod_intg.capability_bindings WHERE id=:b"
            ),
            {"b": binding_id},
        ).scalar_one()

        insert = text(
            "INSERT INTO mod_intg.inbox_receipts ("
            "id, installation_id, capability_binding_id, provider_event_id, "
            "event_type, payload_digest, state) VALUES ("
            ":id, :inst, :binding, 'evt_1', 'e', :digest, 'verified')"
        )
        conn.execute(
            insert,
            {
                "id": uuid.uuid4(),
                "inst": installation_id,
                "binding": binding_id,
                "digest": "a" * 64,
            },
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                insert,
                {
                    "id": uuid.uuid4(),
                    "inst": installation_id,
                    "binding": binding_id,
                    "digest": "a" * 64,
                },
            )
    engine.dispose()


def test_only_one_session_can_claim_a_delivery(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """The lease, proved as a race rather than asserted.

    Two sessions issue the same conditional UPDATE; exactly one must report a
    row. If both did, two dispatchers would call the provider with one payload.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    delivery_id = uuid.uuid4()
    with setup.begin() as conn:
        installation_id, binding_id = _installation_and_binding(conn, request)
        conn.execute(
            text(
                "INSERT INTO mod_intg.delivery_attempts ("
                "id, installation_id, capability_binding_id, event_type, "
                "idempotency_key, payload_digest, state) VALUES ("
                ":id, :inst, :binding, 'e', 'k1', :digest, 'pending')"
            ),
            {
                "id": delivery_id,
                "inst": installation_id,
                "binding": binding_id,
                "digest": "c" * 64,
            },
        )
    setup.dispose()

    claim = text(
        "UPDATE mod_intg.delivery_attempts SET state='in_flight', "
        "attempt_count = attempt_count + 1, "
        "leased_until = now() + interval '300 seconds' "
        "WHERE id = :id AND state NOT IN "
        "('delivered','dead_letter','reconciliation_required') "
        "AND (leased_until IS NULL OR leased_until < now())"
    )
    first, second = create_engine(admin_url), create_engine(admin_url)
    with first.begin() as a:
        won = a.execute(claim, {"id": delivery_id}).rowcount
    with second.begin() as b:
        lost = b.execute(claim, {"id": delivery_id}).rowcount
    first.dispose()
    second.dispose()

    assert (won, lost) == (1, 0)


def test_only_one_session_can_advance_a_checkpoint(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """The optimistic version, proved as a race.

    Without it the slower writer wins and the window between the two cursors is
    never polled again — a silent gap, not a visible failure.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    checkpoint_id = uuid.uuid4()
    with setup.begin() as conn:
        _, binding_id = _installation_and_binding(conn, request)
        conn.execute(
            text(
                "INSERT INTO mod_intg.polling_checkpoints ("
                "id, capability_binding_id, job_key, version) VALUES ("
                ":id, :binding, 'live_tail', 1)"
            ),
            {"id": checkpoint_id, "binding": binding_id},
        )
    setup.dispose()

    advance = text(
        "UPDATE mod_intg.polling_checkpoints SET version = version + 1, "
        "advanced_at = now() WHERE id = :id AND version = 1"
    )
    first, second = create_engine(admin_url), create_engine(admin_url)
    with first.begin() as a:
        won = a.execute(advance, {"id": checkpoint_id}).rowcount
    with second.begin() as b:
        lost = b.execute(advance, {"id": checkpoint_id}).rowcount
    first.dispose()
    second.dispose()

    assert (won, lost) == (1, 0)


def test_only_one_session_can_settle_a_delivery(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """The settlement race, proved with two real sessions.

    The reviewed implementation read the row, compared in Python, then wrote —
    leaving a window in which a takeover lands between the read and the write
    and the loser overwrites the winner's outcome. Two outcomes for one attempt,
    with nothing recording which actually ran.

    Both sessions here issue the guarded UPDATE for the SAME claimed attempt.
    Exactly one must report a row; the loser must change nothing.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    delivery_id = uuid.uuid4()
    with setup.begin() as conn:
        installation_id, binding_id = _installation_and_binding(conn, request)
        conn.execute(
            text(
                "INSERT INTO mod_intg.delivery_attempts ("
                "id, installation_id, capability_binding_id, event_type, "
                "idempotency_key, payload_digest, state, attempt_count, "
                "leased_until) VALUES ("
                ":id, :inst, :binding, 'e', 'settle-race', :digest, 'in_flight', "
                "1, now() + interval '300 seconds')"
            ),
            {
                "id": delivery_id,
                "inst": installation_id,
                "binding": binding_id,
                "digest": "e" * 64,
            },
        )
    setup.dispose()

    # The guard settle() issues: state, attempt number and a live lease.
    settle_sql = text(
        "UPDATE mod_intg.delivery_attempts SET state = :state, "
        "leased_until = NULL, delivered_at = now() "
        "WHERE id = :id AND state = 'in_flight' AND attempt_count = 1 "
        "AND leased_until IS NOT NULL AND leased_until >= now()"
    )
    first, second = create_engine(admin_url), create_engine(admin_url)
    with first.begin() as a:
        won = a.execute(settle_sql, {"id": delivery_id, "state": "delivered"}).rowcount
    with second.begin() as b:
        lost = b.execute(
            settle_sql, {"id": delivery_id, "state": "dead_letter"}
        ).rowcount
    first.dispose()
    second.dispose()

    assert (won, lost) == (1, 0)

    check = create_engine(admin_url)
    with check.connect() as conn:
        state = conn.execute(
            text("SELECT state FROM mod_intg.delivery_attempts WHERE id = :id"),
            {"id": delivery_id},
        ).scalar_one()
    check.dispose()
    assert state == "delivered", "the loser overwrote the winner's outcome"


def test_a_delivery_scheduled_for_the_future_is_not_claimable(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """Backoff, enforced by the claim predicate against a real clock.

    Without the `next_attempt_at` guard the public dispatch seam claims work the
    engine deliberately deferred, and a failing provider is hammered instead of
    backed off.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    delivery_id = uuid.uuid4()
    with setup.begin() as conn:
        installation_id, binding_id = _installation_and_binding(conn, request)
        conn.execute(
            text(
                "INSERT INTO mod_intg.delivery_attempts ("
                "id, installation_id, capability_binding_id, event_type, "
                "idempotency_key, payload_digest, state, next_attempt_at) VALUES ("
                ":id, :inst, :binding, 'e', 'backoff', :digest, 'retryable', "
                "now() + interval '1 hour')"
            ),
            {
                "id": delivery_id,
                "inst": installation_id,
                "binding": binding_id,
                "digest": "f" * 64,
            },
        )

        claim = text(
            "UPDATE mod_intg.delivery_attempts SET state='in_flight', "
            "attempt_count = attempt_count + 1, "
            "leased_until = now() + interval '300 seconds' "
            "WHERE id = :id AND state NOT IN "
            "('delivered','dead_letter','reconciliation_required') "
            "AND (leased_until IS NULL OR leased_until < now()) "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= now())"
        )
        assert conn.execute(claim, {"id": delivery_id}).rowcount == 0
    setup.dispose()


# ── The minted ingress endpoint (ig_0003) ───────────────────────────────────
#
# SQLite treats a unique index over NULLs the same way Postgres does, so the
# unit tests appear to cover this. What they cannot cover is the migration
# actually applying, the column-privilege assumption `ig_0003` relies on, the
# savepoint the mint retry depends on, and the concurrent-insert path that turns
# a raw driver error into a typed refusal.


def test_the_ingress_endpoint_column_is_unique_and_nullable(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """A unique INDEX, not a UniqueConstraint.

    Every UNMINTED binding must coexist — Postgres treats NULLs as distinct in a
    unique index, which is exactly the property that lets "an endpoint is a
    deliberate act" be expressed as a nullable column rather than a second
    table. And a minted key must never be claimable twice, or one provider's
    traffic would resolve to another operator's configuration.
    """
    from sqlalchemy.exc import IntegrityError

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        installation_id, first = _installation_and_binding(conn, request)
        second = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO mod_intg.capability_bindings ("
                "id, installation_id, capability_id, state) VALUES ("
                ":id, :installation, 'conformance.other.v1', 'enabled')"
            ),
            {"id": second, "installation": installation_id},
        )
        # Two unminted bindings coexist: both keys are NULL.
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM mod_intg.capability_bindings "
                    "WHERE ingress_endpoint_key IS NULL AND id IN (:a, :b)"
                ),
                {"a": first, "b": second},
            ).scalar_one()
            == 2
        )

        key = "a" * 48
        conn.execute(
            text(
                "UPDATE mod_intg.capability_bindings SET ingress_endpoint_key = :k "
                "WHERE id = :id"
            ),
            {"k": key, "id": first},
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "UPDATE mod_intg.capability_bindings "
                    "SET ingress_endpoint_key = :k WHERE id = :id"
                ),
                {"k": key, "id": second},
            )
    engine.dispose()


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "REFERENCES"])
def test_app_user_holds_nothing_on_the_new_column(
    migrated_scratch: tuple[str, str], privilege: str
) -> None:
    """`ig_0003` issues no GRANT and no REVOKE, and this is why that is safe.

    Table-level privileges cover columns added later, and column-level
    privileges exist only where an explicit per-column GRANT created one.
    `app_user` holds nothing on `capability_bindings` (revoked in `ig_0001`), so
    it acquires nothing here. Re-issuing the REVOKE would read as load-bearing
    when it is not — so the assumption is ASSERTED instead, at the column grain
    a table-level check would miss.

    This matters more for this column than for any other in the schema: it holds
    a BEARER credential, and a tenant role that could read it could drive every
    connector the fleet receives through.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        held = conn.execute(
            text(
                "SELECT has_column_privilege('app_user', "
                "  'mod_intg.capability_bindings', 'ingress_endpoint_key', "
                "  CAST(:p AS text))"
            ),
            {"p": privilege},
        ).scalar_one()
    engine.dispose()
    assert not held, (
        f"app_user holds {privilege} on the ingress endpoint column — the "
        "table-level-covers-new-columns assumption in ig_0003 is wrong"
    )


def test_the_ig_lineage_added_exactly_what_it_declared(
    migrated_scratch: tuple[str, str],
) -> None:
    """Two migrations, two different kinds of change, both accounted for.

    `ig_0003` adds a COLUMN to an existing table; `ig_0004` adds exactly ONE
    table. Asserted from both sides — the declaration says eight and the live
    schema holds exactly those eight — plus the ADR-0023 contract, which either
    a new column or a new table could break by carrying a tenant scope.

    The count is stated rather than derived on purpose: a table arriving in a
    migration without arriving in `platform_tables` is precisely the drift this
    catches, and comparing the schema only to the declaration would let both
    move together silently.
    """
    from dotmac_integration import module
    from dotmac_kernel.migrations.catalog import audit_live_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry

    assert len(module.platform_tables) == 8
    assert "capability_destination_revisions" in module.platform_tables
    assert module.tables == ()

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        live = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'mod_intg'")
            )
        }
        column = conn.execute(
            text(
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_schema='mod_intg' AND table_name='capability_bindings' "
                "AND column_name='ingress_endpoint_key'"
            )
        ).one()
        violations = audit_live_schemas(
            conn, NamespaceRegistry.from_manifests([module])
        )
    engine.dispose()

    assert live == set(module.platform_tables)
    assert column.is_nullable == "YES", "an unminted binding must stay unminted"
    assert not violations, "platform-plane violations:\n" + "\n".join(violations)


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_a_route_is_not_reachable_by_the_tenant_application_role(
    migrated_scratch: tuple[str, str], privilege: str
) -> None:
    """`ig_0004` REVOKEs, and this proves the REVOKE took.

    The destination table is where routing decisions live, so `app_user`
    holding anything on it would be the most consequential privilege on this
    plane — a tenant-facing role able to read, or rewrite, where another
    application's traffic lands. On the platform plane there is no RLS to fall
    back on: the revoke IS the isolation (ADR-0023).

    Asked at the TABLE grain via `has_table_privilege`, which accounts for
    privileges held directly, by role membership, or through PUBLIC — a
    `pg_class.relacl` inspection would miss all three.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        held = conn.execute(
            text(
                "SELECT has_table_privilege('app_user', "
                "  'mod_intg.capability_destination_revisions', CAST(:p AS text))"
            ),
            {"p": privilege},
        ).scalar_one()
    engine.dispose()
    assert not held, (
        f"app_user holds {privilege} on capability_destination_revisions — a "
        "tenant-facing role can reach the table that decides where traffic goes"
    )


def test_the_unique_index_is_the_one_the_mint_retry_depends_on(
    migrated_scratch: tuple[str, str],
) -> None:
    """`_fresh_endpoint_key` catches `IntegrityError` and tries once more.

    That is only a retry if the database actually refuses the duplicate — an
    index created non-unique, or on the wrong column, would make the retry
    unreachable and every "192 bits makes a collision fictional" claim
    unenforced.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        rows = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname='mod_intg' AND tablename='capability_bindings'"
                )
            )
        }
    engine.dispose()
    definition = rows.get("uq_capability_bindings_ingress_endpoint")
    assert definition, sorted(rows)
    assert "UNIQUE" in definition
    assert "ingress_endpoint_key" in definition


def test_a_colliding_mint_leaves_the_session_usable(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """The SAVEPOINT, proved against a database that really aborts.

    `_fresh_endpoint_key` assigns inside `db.begin_nested()` rather than
    flushing bare. The reason only exists on a real transactional database:
    Postgres aborts the whole transaction on a failed statement, so a bare flush
    that violated the unique index would leave the caller's session unusable —
    and this module never rolls back a caller's unit of work, so it would have
    no way to recover. A savepoint scopes the failure to the attempt.

    Staged here at the mechanism grain, which is what a caller's session
    actually experiences. SQLite cannot show it: it does not abort the
    surrounding transaction, so the bare-flush version passes there.
    """
    from dotmac_integration import CapabilityBinding
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    admin_url, _ = migrated_scratch
    taken = "b" * 48
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        _, other = _installation_and_binding(conn, request)
        _, mine = _installation_and_binding(conn, request)
        conn.execute(
            text(
                "UPDATE mod_intg.capability_bindings "
                "SET ingress_endpoint_key = :k WHERE id = :id"
            ),
            {"k": taken, "id": other},
        )
    setup.dispose()

    engine = create_engine(admin_url)
    session = Session(engine)
    binding = session.get(CapabilityBinding, mine)
    assert binding is not None

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            binding.ingress_endpoint_key = taken
            session.flush()

    # THE POINT: the session still works. Without the savepoint the transaction
    # would be aborted and every statement below would raise
    # `InFailedSqlTransaction`.
    binding.ingress_endpoint_key = "c" * 48
    session.flush()
    session.commit()
    session.close()

    check = create_engine(admin_url)
    with check.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT ingress_endpoint_key FROM mod_intg.capability_bindings "
                "WHERE id = :id"
            ),
            {"id": mine},
        ).scalar_one()
    check.dispose()
    engine.dispose()
    assert stored == "c" * 48


def test_two_sessions_racing_one_provider_event_produce_one_typed_refusal(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """`receive_verified` is SELECT-then-INSERT with no upsert.

    Two workers racing one redelivery therefore reach the unique index, and the
    loser would otherwise surface a raw `IntegrityError` — which reaches a route
    handler as a 500 whose driver message embeds the bound parameters: the
    normalized payload and the provider event id, verbatim. Whole-batch rollback
    was already correct under that; it just was not typed, and an untyped
    refusal is one nobody can answer deliberately.

    SQLite cannot stage this: it needs two real sessions holding two real
    transactions against one index.

    ## The interleaving is staged, not raced

    The obvious script — insert on session one, insert on session two, THEN
    commit session one — cannot work in one thread. Session two's INSERT blocks
    on session one's uncommitted index entry, and session one's `commit()` is
    the next statement, which never runs: the job hangs until the runner's
    wall-clock kill rather than failing.

    So the interleaving is staged instead. Session two takes its snapshot under
    REPEATABLE READ BEFORE session one commits: its SELECT sees no receipt, and
    by the time it inserts, the winner's row is committed — a unique violation
    raised immediately with nothing to wait on. That is exactly the state a real
    loser is in, and it is deterministic. `lock_timeout` is set as well, so a
    future edit that reintroduces the blocking version FAILS rather than hangs.
    """
    from dotmac_integration import (
        InboundEvent,
        PreparedIngress,
        ReceiptWriteRaced,
        record_batch,
    )
    from sqlalchemy.orm import Session

    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        installation_id, binding_id = _installation_and_binding(conn, request)
    setup.dispose()

    prepared = PreparedIngress(
        installation_id=installation_id,
        binding_id=binding_id,
        connector_key="fake",
        capability_id="conformance.echo.v1",
    )
    events = (
        InboundEvent(
            provider_event_id=f"race_{uuid.uuid4().hex[:8]}",
            event_type="thing.happened",
            payload={"n": 1},
        ),
    )

    first_engine, second_engine = create_engine(admin_url), create_engine(admin_url)
    first, second = Session(first_engine), Session(second_engine)
    try:
        # The loser opens first and pins a snapshot in which no receipt exists.
        # `lock_timeout` guarantees a fast failure rather than a hung job if a
        # later edit ever puts an uncommitted row in its way again.
        second.connection(
            execution_options={"isolation_level": "REPEATABLE READ"}
        ).execute(text("SET LOCAL lock_timeout = '5s'"))
        second.execute(text("SELECT 1"))

        # The winner records and COMMITS, so the index entry is live and no
        # transaction holds it.
        record_batch(first, prepared, events)
        first.commit()

        # The loser's SELECT still sees nothing under its older snapshot, so it
        # inserts — straight into the committed unique index entry.
        with pytest.raises(ReceiptWriteRaced):
            record_batch(second, prepared, events)
    finally:
        second.rollback()
        second.close()
        first.close()
        first_engine.dispose()
        second_engine.dispose()

    check = create_engine(admin_url)
    with check.connect() as conn:
        surviving = conn.execute(
            text(
                "SELECT count(*) FROM mod_intg.inbox_receipts "
                "WHERE capability_binding_id = :b"
            ),
            {"b": binding_id},
        ).scalar_one()
    check.dispose()
    assert surviving == 1, "the race produced two receipts for one provider event"


def test_a_collision_mid_batch_leaves_no_partial_row_in_postgres(
    migrated_scratch: tuple[str, str],
    request: pytest.FixtureRequest,
) -> None:
    """The rollback proof against a REAL transaction, not SQLite's.

    A partial write leaves the provider believing the batch was accepted while
    some events were never recorded, and it will not resend the ones that
    landed — so it looks delivered and is not. SQLite appeared to give this;
    Postgres' abort semantics are what actually have to.
    """
    from dotmac_integration import (
        EventIdentityCollision,
        InboundEvent,
        PreparedIngress,
        receive_verified,
        record_batch,
    )
    from sqlalchemy.orm import Session

    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        installation_id, binding_id = _installation_and_binding(conn, request)
    setup.dispose()

    prepared = PreparedIngress(
        installation_id=installation_id,
        binding_id=binding_id,
        connector_key="fake",
        capability_id="conformance.echo.v1",
    )
    events = tuple(
        InboundEvent(provider_event_id=i, event_type="e", payload={"i": i})
        for i in ("batch_a", "batch_b", "batch_c")
    )

    engine = create_engine(admin_url)
    seed = Session(engine)
    receive_verified(
        seed,
        installation_id=installation_id,
        capability_binding_id=binding_id,
        provider_event_id="batch_b",
        event_type="e",
        payload={"i": "something else"},
    )
    seed.commit()
    seed.close()

    session = Session(engine)
    with pytest.raises(EventIdentityCollision):
        record_batch(session, prepared, events)
    # The unit of work the deployment owns unwinds here; the module never did.
    session.rollback()
    session.close()

    with engine.connect() as conn:
        surviving = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT provider_event_id FROM mod_intg.inbox_receipts "
                    "WHERE capability_binding_id = :b"
                ),
                {"b": binding_id},
            )
        }
    engine.dispose()
    assert surviving == {
        "batch_b"
    }, "an event recorded before the collision survived the rollback"
