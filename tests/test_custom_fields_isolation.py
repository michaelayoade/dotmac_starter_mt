"""Custom field definitions (`custom_field_definitions`) isolation canaries.

`CustomFieldDefinition` (Task 8) is a standard tenant-scoped table — same RLS
shape as `parties`/`roles`: a single `USING (tenant_id = app_current_tenant_id())
WITH CHECK (...)` policy covering all four commands (see
`tests/test_party_isolation.py` for the convention this file follows and
`alembic/versions/*_custom_fields.py` for the migration).

Three properties, each load-bearing:
(a) a definition created by tenant A is invisible to tenant B.
(b) tenant B can create its OWN definition with the same `field_code` for the
    same `entity_type` — uniqueness is per-tenant (`UniqueConstraint(tenant_id,
    entity_type, field_code)`), not global.
(c) tenant B cannot INSERT a definition row carrying tenant A's `tenant_id` —
    the DB write policy must reject it, not just filter it out on read.

A fourth property proves the `Party.custom_fields` JSONB column added in this
same migration doesn't leak either — it rides on `parties`' existing RLS
policy (Task 6), but is worth a direct check since it's a new column.

Requires a real Postgres (RLS doesn't exist on SQLite) — see
`tests/test_party_isolation.py` for the `_as_tenant`/`tenant_sessionmaker`
convention this file follows.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="module")
def tenant_engine() -> Generator[Engine, None, None]:
    """Engine bound as `app_user` — the RLS-enforced, tenant-facing role."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def tenant_sessionmaker(tenant_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=tenant_engine, autocommit=False, autoflush=False)


def _as_tenant(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> Session:
    """A fresh `app_user` session with `app.current_tenant` set for this transaction."""
    session = factory()
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    return session


def _insert_definition(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    entity_type: str = "party",
    field_code: str = "eye_color",
    field_name: str = "Eye Color",
    field_type: str = "TEXT",
) -> uuid.UUID:
    def_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO custom_field_definitions "
            "(id, tenant_id, entity_type, field_code, field_name, field_type) "
            "VALUES (:id, :tenant_id, :entity_type, :field_code, :field_name, "
            ":field_type)"
        ),
        {
            "id": str(def_id),
            "tenant_id": str(tenant_id),
            "entity_type": entity_type,
            "field_code": field_code,
            "field_name": field_name,
            "field_type": field_type,
        },
    )
    return def_id


def test_tenant_a_definition_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        def_id = _insert_definition(a, tenant_id=tenant_a.id)
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM custom_field_definitions"),
            ).fetchall()
            assert rows == []
        finally:
            b.rollback()
            b.close()

        # And it IS visible from tenant A's own context.
        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text("SELECT id FROM custom_field_definitions WHERE id = :id"),
                {"id": str(def_id)},
            ).fetchone()
            assert row is not None
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM custom_field_definitions WHERE id = :id"),
            {"id": str(def_id)},
        )
        admin_session.commit()


def test_tenant_b_can_create_same_field_code_definition(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Uniqueness is per-tenant (`tenant_id, entity_type, field_code`) — tenant
    B defining its own `eye_color` for `party` must succeed even though tenant
    A already has one.
    """
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        a_def_id = _insert_definition(a, tenant_id=tenant_a.id)
        a.commit()
    finally:
        a.close()

    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        b_def_id = _insert_definition(b, tenant_id=tenant_b.id)
        b.commit()
    finally:
        b.close()

    try:
        b2 = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            row = b2.execute(
                text("SELECT field_code FROM custom_field_definitions WHERE id = :id"),
                {"id": str(b_def_id)},
            ).fetchone()
            assert row is not None
            assert row[0] == "eye_color"
        finally:
            b2.rollback()
            b2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM custom_field_definitions WHERE id IN (:a, :b)"),
            {"a": str(a_def_id), "b": str(b_def_id)},
        )
        admin_session.commit()


def test_tenant_b_cannot_insert_definition_row_for_tenant_a(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Write-denial canary: tenant B's session inserting a row that carries
    tenant A's `tenant_id` must be rejected by the RLS `WITH CHECK` clause —
    not merely filtered out on read afterwards.
    """
    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_definition(b, tenant_id=tenant_a.id, field_code="intruder-field")
    finally:
        b.rollback()
        b.close()

    remaining = admin_session.execute(
        text(
            "SELECT count(*) FROM custom_field_definitions "
            "WHERE field_code = 'intruder-field'"
        )
    ).scalar_one()
    assert remaining == 0


def test_party_custom_fields_value_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The `Party.custom_fields` JSONB column added by this same migration
    rides on `parties`' existing RLS policy — prove it doesn't leak either.
    """
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party_id = uuid.uuid4()
        a.execute(
            text(
                "INSERT INTO parties (id, tenant_id, party_type, display_name, "
                "custom_fields) VALUES (:id, :tenant_id, 'person', 'Ada Lovelace', "
                '\'{"eye_color": "brown"}\')'
            ),
            {"id": str(party_id), "tenant_id": str(tenant_a.id)},
        )
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT custom_fields FROM parties WHERE id = :id"),
                {"id": str(party_id)},
            ).fetchall()
            assert rows == []
        finally:
            b.rollback()
            b.close()

        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text("SELECT custom_fields FROM parties WHERE id = :id"),
                {"id": str(party_id)},
            ).fetchone()
            assert row is not None
            assert row[0] == {"eye_color": "brown"}
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()
