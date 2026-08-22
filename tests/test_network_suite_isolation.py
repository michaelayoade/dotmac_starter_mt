"""Composed PostgreSQL isolation and evidence gate for all nine network modules."""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
NETWORK_ROOTS = (
    "dotmac_ipam",
    "dotmac_network_inventory",
    "dotmac_network_observability",
    "dotmac_network_topology",
    "dotmac_network_assurance",
    "dotmac_network_control",
    "dotmac_fiber_plant",
    "dotmac_network_access",
    "dotmac_pon_access",
)
NETWORK_VERSION_LOCATIONS = tuple(
    next(
        path / "src" / root / "migrations" / "versions"
        for path in (REPO_ROOT / "packages").iterdir()
        if (path / "src" / root).is_dir()
    )
    for root in NETWORK_ROOTS
)
ROOT_SELECTS = (
    ("mod_ipam.address_spaces", text("SELECT tenant_id FROM mod_ipam.address_spaces")),
    ("mod_netinv.sites", text("SELECT tenant_id FROM mod_netinv.sites")),
    (
        "mod_netobs.observations",
        text("SELECT tenant_id FROM mod_netobs.observations"),
    ),
    ("mod_nettop.links", text("SELECT tenant_id FROM mod_nettop.links")),
    (
        "mod_netassure.incidents",
        text("SELECT tenant_id FROM mod_netassure.incidents"),
    ),
    ("mod_netctrl.commands", text("SELECT tenant_id FROM mod_netctrl.commands")),
    ("mod_fiber.structures", text("SELECT tenant_id FROM mod_fiber.structures")),
    (
        "mod_netaccess.access_projections",
        text("SELECT tenant_id FROM mod_netaccess.access_projections"),
    ),
    ("mod_pon.olts", text("SELECT tenant_id FROM mod_pon.olts")),
)


@dataclass(frozen=True, slots=True)
class SuiteSeed:
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    incident_a: uuid.UUID
    command_a: uuid.UUID
    olt_a: uuid.UUID


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the network-suite gate needs Postgres")
    return url


def _url_for(base_url: str, database: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{database}"


@pytest.fixture(scope="module")
def network_suite_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"network_suite_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    previous_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        locations = (
            KERNEL_VERSIONS,
            ASSEMBLY_VERSIONS,
            *NETWORK_VERSION_LOCATIONS,
        )
        config.set_main_option(
            "version_locations", " ".join(str(path) for path in locations)
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        if previous_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous_url
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _insert_roots(
    connection: Connection, *, tenant_id: uuid.UUID, suffix: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    incident_id, command_id, olt_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    common = {"tenant": tenant_id, "suffix": suffix}
    connection.execute(
        text(
            "INSERT INTO mod_ipam.address_spaces "
            "(id, tenant_id, code, name, family, prefix) VALUES "
            "(:id, :tenant, :code, 'Suite IPv4', 'ipv4', :prefix)"
        ),
        {
            **common,
            "id": uuid.uuid4(),
            "code": f"space-{suffix}",
            "prefix": f"10.{int(suffix[-2:], 16) % 250}.0.0/16",
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_netinv.sites "
            "(id, tenant_id, code, name, site_kind) VALUES "
            "(:id, :tenant, :code, 'Suite site', 'pop')"
        ),
        {**common, "id": uuid.uuid4(), "code": f"site-{suffix}"},
    )
    connection.execute(
        text(
            "INSERT INTO mod_netobs.observations "
            "(id, tenant_id, subject_ref, kind, source_ref, observed_at, "
            "fingerprint, attributes) VALUES "
            "(:id, :tenant, :subject, 'reachability', :source, now(), "
            ":fingerprint, CAST(:attributes AS json))"
        ),
        {
            **common,
            "id": uuid.uuid4(),
            "subject": f"node:{suffix}",
            "source": f"seed:{suffix}",
            "fingerprint": f"observation-{suffix}",
            "attributes": "[]",
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_nettop.links "
            "(id, tenant_id, left_ref, right_ref, kind, state, direction, cost, "
            "source_ref, created_at) VALUES "
            "(:id, :tenant, :left, :right, 'logical', 'declared', "
            "'bidirectional', 1, :source, now())"
        ),
        {
            **common,
            "id": uuid.uuid4(),
            "left": f"left:{suffix}",
            "right": f"right:{suffix}",
            "source": f"declared:{suffix}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_netassure.incidents "
            "(id, tenant_id, code, summary, severity, state, detection_ref, "
            "source_observation_refs, detected_at) VALUES "
            "(:id, :tenant, :code, 'Suite incident', 'major', 'open', "
            ":detection, CAST(:observations AS json), now())"
        ),
        {
            **common,
            "id": incident_id,
            "code": f"incident-{suffix}",
            "detection": f"alert:{suffix}",
            "observations": "[]",
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_netctrl.commands "
            "(id, tenant_id, operation_code, target_ref, capability_code, "
            "parameters, request_fingerprint, correlation_ref, requested_by_ref, "
            "state, requested_at) VALUES "
            "(:id, :tenant, 'probe', :target, 'network-control.v1', "
            "CAST(:parameters AS json), :fingerprint, :correlation, "
            "'operator:seed', 'requested', now())"
        ),
        {
            **common,
            "id": command_id,
            "target": f"node:{suffix}",
            "parameters": "[]",
            "fingerprint": f"request-{suffix}",
            "correlation": f"correlation-{suffix}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_fiber.structures "
            "(id, tenant_id, code, name, kind, location_ref, created_at) VALUES "
            "(:id, :tenant, :code, 'Suite closure', 'closure', :location, now())"
        ),
        {
            **common,
            "id": uuid.uuid4(),
            "code": f"structure-{suffix}",
            "location": f"location:{suffix}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_netaccess.access_projections "
            "(id, tenant_id, subject_ref, desired_state, policy_code, "
            "policy_version, attributes, decision_ref, desired_fingerprint, "
            "projected_at) VALUES "
            "(:id, :tenant, :subject, 'enabled', 'standard', 'v1', "
            "CAST(:attributes AS json), :decision, :fingerprint, now())"
        ),
        {
            **common,
            "id": uuid.uuid4(),
            "subject": f"service:{suffix}",
            "attributes": "[]",
            "decision": f"decision:{suffix}",
            "fingerprint": f"access-{suffix}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_pon.olts "
            "(id, tenant_id, code, name, management_ref, vendor_family, "
            "capability_codes, state, created_at) VALUES "
            "(:id, :tenant, :code, 'Suite OLT', :management, 'neutral', "
            "CAST(:capabilities AS json), 'active', now())"
        ),
        {
            **common,
            "id": olt_id,
            "code": f"olt-{suffix}",
            "management": f"management:{suffix}",
            "capabilities": "[]",
        },
    )
    return incident_id, command_id, olt_id


def _seed_suite(admin_url: str) -> SuiteSeed:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    suffix_a, suffix_b = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        for tenant_id, suffix in ((tenant_a, suffix_a), (tenant_b, suffix_b)):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {
                    "id": tenant_id,
                    "slug": f"suite-{suffix}",
                    "name": f"Suite {suffix}",
                },
            )
        incident_a, command_a, olt_a = _insert_roots(
            connection, tenant_id=tenant_a, suffix=suffix_a
        )
        _insert_roots(connection, tenant_id=tenant_b, suffix=suffix_b)
    engine.dispose()
    return SuiteSeed(tenant_a, tenant_b, incident_a, command_a, olt_a)


def test_every_declared_network_table_has_live_forced_rls_and_online_access(
    network_suite_database: tuple[str, str],
) -> None:
    admin_url, _ = network_suite_database
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            for root in NETWORK_ROOTS:
                manifest = importlib.import_module(f"{root}.manifest").module
                assert connection.scalar(
                    text("SELECT has_schema_privilege('app_user', :schema, 'USAGE')"),
                    {"schema": manifest.db_schema},
                )
                for table_name in manifest.tables:
                    catalog = connection.execute(
                        text(
                            "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                            "(SELECT count(*) FROM pg_policy p "
                            "WHERE p.polrelid = c.oid) "
                            "FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid = c.relnamespace "
                            "WHERE n.nspname = :schema AND c.relname = :table"
                        ),
                        {"schema": manifest.db_schema, "table": table_name},
                    ).one()
                    assert tuple(catalog) == (True, True, 1)
                    qualified = f"{manifest.db_schema}.{table_name}"
                    assert connection.scalar(
                        text(
                            "SELECT has_table_privilege"
                            "('app_user', :qualified, 'SELECT') AND "
                            "has_table_privilege('app_user', :qualified, 'INSERT')"
                        ),
                        {"qualified": qualified},
                    )
    finally:
        engine.dispose()


def test_two_tenants_are_isolated_across_every_network_owner(
    network_suite_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = network_suite_database
    seed = _seed_suite(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as connection:
            for tenant_id in (seed.tenant_a, seed.tenant_b):
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, false)"),
                    {"tenant": str(tenant_id)},
                )
                for table_name, statement in ROOT_SELECTS:
                    visible = connection.execute(statement).scalars().all()
                    assert visible == [tenant_id], table_name
    finally:
        engine.dispose()


def test_cross_tenant_pon_parentage_is_refused(
    network_suite_database: tuple[str, str],
) -> None:
    admin_url, _ = network_suite_database
    seed = _seed_suite(admin_url)
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO mod_pon.pon_ports "
                    "(id, tenant_id, olt_id, slot, port, label, capacity, "
                    "created_at) VALUES "
                    "(:id, :tenant, :olt, 0, 1, 'cross-tenant', 64, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": seed.tenant_b,
                    "olt": seed.olt_a,
                },
            )
    finally:
        engine.dispose()


def test_every_network_owner_refuses_evidence_rewrite(
    network_suite_database: tuple[str, str],
) -> None:
    admin_url, _ = network_suite_database
    seed = _seed_suite(admin_url)
    evidence_ids = {name: uuid.uuid4() for name in NETWORK_ROOTS}
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mod_ipam.ipam_events "
                "(id, tenant_id, aggregate_ref, event_type, payload, occurred_at) "
                "VALUES (:id, :tenant, 'address:1', 'reserved', "
                "CAST(:payload AS json), now())"
            ),
            {
                "id": evidence_ids["dotmac_ipam"],
                "tenant": seed.tenant_a,
                "payload": "{}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO mod_netinv.network_inventory_events "
                "(id, tenant_id, aggregate_ref, event_type, payload, occurred_at) "
                "VALUES (:id, :tenant, 'node:1', 'admitted', "
                "CAST(:payload AS json), now())"
            ),
            {
                "id": evidence_ids["dotmac_network_inventory"],
                "tenant": seed.tenant_a,
                "payload": "{}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO mod_netobs.observations "
                "(id, tenant_id, subject_ref, kind, source_ref, observed_at, "
                "fingerprint, attributes) VALUES "
                "(:id, :tenant, 'node:evidence', 'reachability', "
                "'source:evidence', now(), :fingerprint, CAST(:attributes AS json))"
            ),
            {
                "id": evidence_ids["dotmac_network_observability"],
                "tenant": seed.tenant_a,
                "fingerprint": uuid.uuid4().hex,
                "attributes": "[]",
            },
        )
        connection.execute(
            text(
                "INSERT INTO mod_nettop.topology_events "
                "(id, tenant_id, aggregate_ref, event_type, payload, occurred_at) "
                "VALUES (:id, :tenant, 'path:1', 'changed', "
                "CAST(:payload AS json), now())"
            ),
            {
                "id": evidence_ids["dotmac_network_topology"],
                "tenant": seed.tenant_a,
                "payload": "{}",
            },
        )
        for statement, root in (
            (
                text(
                    "INSERT INTO mod_fiber.fiber_events "
                    "(id, tenant_id, aggregate_ref, event_type, evidence_ref, "
                    "payload, occurred_at) VALUES "
                    "(:id, :tenant, :aggregate, 'recorded', 'evidence:1', "
                    "CAST(:payload AS json), now())"
                ),
                "dotmac_fiber_plant",
            ),
            (
                text(
                    "INSERT INTO mod_netaccess.access_events "
                    "(id, tenant_id, aggregate_ref, event_type, evidence_ref, "
                    "payload, occurred_at) VALUES "
                    "(:id, :tenant, :aggregate, 'recorded', 'evidence:1', "
                    "CAST(:payload AS json), now())"
                ),
                "dotmac_network_access",
            ),
            (
                text(
                    "INSERT INTO mod_pon.pon_events "
                    "(id, tenant_id, aggregate_ref, event_type, evidence_ref, "
                    "payload, occurred_at) VALUES "
                    "(:id, :tenant, :aggregate, 'recorded', 'evidence:1', "
                    "CAST(:payload AS json), now())"
                ),
                "dotmac_pon_access",
            ),
        ):
            connection.execute(
                statement,
                {
                    "id": evidence_ids[root],
                    "tenant": seed.tenant_a,
                    "aggregate": f"{root}:1",
                    "payload": "{}",
                },
            )
        connection.execute(
            text(
                "INSERT INTO mod_netassure.incident_events "
                "(id, tenant_id, incident_id, event_type, evidence_ref, payload, "
                "occurred_at) VALUES "
                "(:id, :tenant, :incident, 'opened', 'evidence:1', "
                "CAST(:payload AS json), now())"
            ),
            {
                "id": evidence_ids["dotmac_network_assurance"],
                "tenant": seed.tenant_a,
                "incident": seed.incident_a,
                "payload": "{}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO mod_netctrl.command_events "
                "(id, tenant_id, command_id, event_type, evidence_ref, payload, "
                "occurred_at) VALUES "
                "(:id, :tenant, :command, 'requested', 'evidence:1', "
                "CAST(:payload AS json), now())"
            ),
            {
                "id": evidence_ids["dotmac_network_control"],
                "tenant": seed.tenant_a,
                "command": seed.command_a,
                "payload": "{}",
            },
        )

    mutations = (
        (
            text(
                "UPDATE mod_ipam.ipam_events SET event_type = 'rewritten' "
                "WHERE id = :id"
            ),
            "dotmac_ipam",
        ),
        (
            text(
                "UPDATE mod_netinv.network_inventory_events "
                "SET event_type = 'rewritten' WHERE id = :id"
            ),
            "dotmac_network_inventory",
        ),
        (
            text(
                "UPDATE mod_netobs.observations SET kind = 'rewritten' WHERE id = :id"
            ),
            "dotmac_network_observability",
        ),
        (
            text(
                "UPDATE mod_nettop.topology_events SET event_type = 'rewritten' "
                "WHERE id = :id"
            ),
            "dotmac_network_topology",
        ),
        (
            text(
                "UPDATE mod_netassure.incident_events SET event_type = 'rewritten' "
                "WHERE id = :id"
            ),
            "dotmac_network_assurance",
        ),
        (
            text(
                "UPDATE mod_netctrl.command_events SET event_type = 'rewritten' "
                "WHERE id = :id"
            ),
            "dotmac_network_control",
        ),
        (
            text(
                "UPDATE mod_fiber.fiber_events SET event_type = 'rewritten' "
                "WHERE id = :id"
            ),
            "dotmac_fiber_plant",
        ),
        (
            text(
                "UPDATE mod_netaccess.access_events SET event_type = 'rewritten' "
                "WHERE id = :id"
            ),
            "dotmac_network_access",
        ),
        (
            text(
                "UPDATE mod_pon.pon_events SET event_type = 'rewritten' "
                "WHERE id = :id"
            ),
            "dotmac_pon_access",
        ),
    )
    try:
        for statement, root in mutations:
            with (
                engine.begin() as connection,
                pytest.raises(DBAPIError, match="append-only"),
            ):
                connection.execute(statement, {"id": evidence_ids[root]})
    finally:
        engine.dispose()


def test_rls_canary_detects_a_disabled_network_guard(
    network_suite_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = network_suite_database
    seed = _seed_suite(admin_url)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    online = create_engine(app_user_url)
    try:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_ipam.address_spaces DISABLE ROW LEVEL SECURITY")
            )
        with online.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(seed.tenant_a)},
            )
            visible = (
                connection.execute(
                    text("SELECT tenant_id FROM mod_ipam.address_spaces")
                )
                .scalars()
                .all()
            )
        assert seed.tenant_a in visible
        assert any(tenant_id != seed.tenant_a for tenant_id in visible)
    finally:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_ipam.address_spaces ENABLE ROW LEVEL SECURITY")
            )
            connection.execute(
                text("ALTER TABLE mod_ipam.address_spaces FORCE ROW LEVEL SECURITY")
            )
        online.dispose()
        admin.dispose()
