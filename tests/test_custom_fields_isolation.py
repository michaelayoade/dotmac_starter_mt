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
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import Party
from tests.conftest import client_for, provision_and_login

PASSWORD = "correct horse battery staple"


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


# ---------------------------------------------------------------------------
# Task 10 — THE ACCEPTANCE CANARY: the spec's literal end-to-end scenario,
# driven entirely through the real HTTP API against a real, already-migrated
# Postgres. This is the proof of the phase's headline requirement — "fields
# are data, zero migrations per field" — precisely BECAUSE this test never
# touches alembic/the migration runner at any point between defining
# `eye_color` and using it: no migration fixture is invoked, no `alembic
# upgrade` call appears anywhere in this test or its fixtures. The schema
# was migrated once, before the test session started (`make test-db-up`);
# everything below — the field *definition* and its *value* — is ordinary
# row data written and read through `custom_field_definitions` and
# `parties.custom_fields`, not new columns or new migrations.
# ---------------------------------------------------------------------------


def _provision_admin_and_login(
    admin_session: Session, tenant, client: TestClient, email: str
) -> tuple[str, str]:
    """Provisions the tenant's admin and logs in, returning
    (access_token, party_id).

    Registration no longer grants any role (control-plane security Task 2:
    admins are provisioned, not registered), so the admin party +
    credential + "admin" role grant are created directly via the admin
    engine (`provision_and_login`, `tests/conftest.py`). The party id is
    read back through the same admin engine rather than `GET /auth/me` to
    preserve this canary's mutations-before-any-GET CSRF ordering (see the
    test docstring below).
    """
    token = provision_and_login(admin_session, tenant, client, email)
    party_id = admin_session.scalars(
        select(Party.id).where(Party.tenant_id == tenant.id, Party.email == email)
    ).one()
    return token, str(party_id)


def test_eye_color_custom_field_end_to_end_canary(
    app_client: TestClient, admin_session: Session, tenant_a, tenant_b
) -> None:
    """Tenant A admin defines `eye_color` (SELECT, runtime data — no
    migration), sets it on a person-type party, reads it back; tenant B's
    admin sees neither the definition nor the value; an invalid SELECT
    option 400s with the envelope shape.

    NOTE on request ordering: every mutating call (POST/PUT) on a given
    client happens BEFORE that client's first GET. `CSRFMiddleware`
    (`app/core/middleware/csrf.py`) double-submit-checks any non-safe method
    once a `csrf_token` cookie exists (a GET response sets one), and this
    test sends no `x-csrf-token` header — same ordering constraint already
    followed by `tests/test_settings_isolation.py`'s API canary. Nothing
    tenancy-related here; purely a TestClient/CSRF-cookie sequencing detail.
    """
    a = client_for(app_client, tenant_a.slug)
    a_token, a_party_id = _provision_admin_and_login(
        admin_session, tenant_a, a, "eyecolor-a@tenant-a.example.com"
    )

    create_resp = a.post(
        "/custom-fields/definitions",
        headers={"Authorization": f"Bearer {a_token}"},
        json={
            "entity_type": "party",
            "field_code": "eye_color",
            "field_name": "Eye color",
            "field_type": "SELECT",
            "field_options": {
                "options": [
                    {"value": "brown", "label": "Brown"},
                    {"value": "blue", "label": "Blue"},
                ]
            },
        },
    )
    assert create_resp.status_code == 201, create_resp.text

    put_resp = a.put(
        f"/custom-fields/party/{a_party_id}/values",
        headers={"Authorization": f"Bearer {a_token}"},
        json={"eye_color": "brown"},
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json() == {"eye_color": "brown"}

    # --- Invalid SELECT option -> 400 envelope (still before any GET on `a`). ---
    invalid_resp = a.put(
        f"/custom-fields/party/{a_party_id}/values",
        headers={"Authorization": f"Bearer {a_token}"},
        json={"eye_color": "green"},
    )
    assert invalid_resp.status_code == 400
    body = invalid_resp.json()
    assert body["code"] == "bad_request"
    assert "allowed options" in body["message"]

    # --- Tenant B: PUT before any GET, same CSRF-ordering reason. ---
    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token, b_party_id = _provision_admin_and_login(
        admin_session, tenant_b, b, "eyecolor-b@tenant-b.example.org"
    )

    # Tenant B has no `eye_color` definition of its own yet, so setting it on
    # tenant B's own party is an unknown-field-code 400 — proving the
    # definition really is per-tenant, not global.
    b_put_undefined = b.put(
        f"/custom-fields/party/{b_party_id}/values",
        headers={"Authorization": f"Bearer {b_token}"},
        json={"eye_color": "brown"},
    )
    assert b_put_undefined.status_code == 400
    assert b_put_undefined.json()["code"] == "bad_request"

    # --- Reads (safe methods — order-independent w.r.t. CSRF). ---

    get_resp = a.get(
        f"/custom-fields/party/{a_party_id}/values",
        headers={"Authorization": f"Bearer {a_token}"},
    )
    assert get_resp.status_code == 200
    # Tenant A's value is unchanged by the earlier rejected write.
    assert get_resp.json() == {"eye_color": "brown"}

    # Tenant B sees neither the definition nor the value.
    b_definitions = b.get(
        "/custom-fields/definitions",
        headers={"Authorization": f"Bearer {b_token}"},
        params={"entity_type": "party"},
    )
    assert b_definitions.status_code == 200
    assert b_definitions.json() == []

    # Tenant B cannot even reach tenant A's party row (RLS-invisible) to read
    # its value.
    b_reads_a_value = b.get(
        f"/custom-fields/party/{a_party_id}/values",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert b_reads_a_value.status_code == 404
