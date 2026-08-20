"""PostgreSQL RLS and cross-tenant relationship canaries for dotmac-inbox."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
INBOX_VERSIONS = (
    REPO_ROOT / "packages/dotmac-inbox/src/dotmac_inbox/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the inbox canary needs Postgres")
    return url


def _url_for(base_url: str, database: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{database}"


@pytest.fixture
def inbox_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"inbox_{uuid.uuid4().hex[:12]}"
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
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {INBOX_VERSIONS}",
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


def _seed_conversations(admin_url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    conversation_a = uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
        connection.execute(
            text(
                "INSERT INTO mod_inbox.conversations "
                "(id, tenant_id, channel, account_scope, contact, thread_key, status) "
                "VALUES "
                "(:a_id, :a_tenant, 'email', 'support-a', 'a@example.net', "
                "'email:support-a:c:a@example.net', 'open'), "
                "(:b_id, :b_tenant, 'email', 'support-b', 'b@example.net', "
                "'email:support-b:c:b@example.net', 'open')"
            ),
            {
                "a_id": conversation_a,
                "a_tenant": tenant_a,
                "b_id": uuid.uuid4(),
                "b_tenant": tenant_b,
            },
        )
    engine.dispose()
    return tenant_a, tenant_b, conversation_a


def test_a_tenant_sees_only_its_conversations(
    inbox_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = inbox_database
    tenant_a, tenant_b, _ = _seed_conversations(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            assert connection.execute(
                text("SELECT tenant_id FROM mod_inbox.conversations")
            ).scalars().all() == [tenant_a]
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_b)},
            )
            assert connection.execute(
                text("SELECT tenant_id FROM mod_inbox.conversations")
            ).scalars().all() == [tenant_b]
    finally:
        engine.dispose()


def test_a_cross_tenant_message_reference_is_impossible(
    inbox_database: tuple[str, str],
) -> None:
    admin_url, _ = inbox_database
    tenant_a, tenant_b, conversation_a = _seed_conversations(admin_url)
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO mod_inbox.messages "
                    "(id, tenant_id, conversation_id, channel, direction, "
                    "message_key, occurred_at) VALUES "
                    "(:id, :tenant, :conversation, 'email', 'inbound', 'm-1', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_b,
                    "conversation": conversation_a,
                },
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO mod_inbox.messages "
                    "(id, tenant_id, conversation_id, channel, direction, "
                    "message_key, occurred_at) VALUES "
                    "(:id, :tenant, :conversation, 'email', 'inbound', 'm-2', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_a,
                    "conversation": conversation_a,
                },
            )
    finally:
        engine.dispose()


def test_the_rls_canary_is_sensitive_to_a_disabled_guard(
    inbox_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = inbox_database
    tenant_a, _, _ = _seed_conversations(admin_url)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    online = create_engine(app_user_url)
    try:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_inbox.conversations DISABLE ROW LEVEL SECURITY")
            )
        with online.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            visible = (
                connection.execute(
                    text(
                        "SELECT tenant_id FROM mod_inbox.conversations "
                        "ORDER BY tenant_id"
                    )
                )
                .scalars()
                .all()
            )
        assert len(visible) == 2 and tenant_a in visible
    finally:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_inbox.conversations ENABLE ROW LEVEL SECURITY")
            )
            connection.execute(
                text("ALTER TABLE mod_inbox.conversations FORCE ROW LEVEL SECURITY")
            )
        online.dispose()
        admin.dispose()
