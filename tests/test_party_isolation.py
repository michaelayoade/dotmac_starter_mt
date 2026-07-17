"""Party identity isolation canaries.

`Party` (+ subtype tables `party_persons`/`party_organizations`) replaces
`Person` as the identity source of truth (Task 6). Subtype tables carry no
`tenant_id` of their own — they inherit isolation through the FK to
`parties`, enforced by an `EXISTS (... WHERE p.tenant_id = app_current_tenant_id())`
RLS policy. This file is the load-bearing proof that pattern actually works,
alongside `tests/test_cross_tenant_isolation.py` (the API-level `/people`
canaries, unchanged in shape this task).

Requires a real Postgres (RLS doesn't exist on SQLite) — see
`tests/test_settings_isolation.py` for the `_as_tenant`/`tenant_sessionmaker`
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


def _insert_person_party(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    email: str,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
) -> uuid.UUID:
    party_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO parties (id, tenant_id, party_type, display_name, email) "
            "VALUES (:id, :tenant_id, 'person', :display_name, :email)"
        ),
        {
            "id": str(party_id),
            "tenant_id": str(tenant_id),
            "display_name": f"{first_name} {last_name}",
            "email": email,
        },
    )
    session.execute(
        text(
            "INSERT INTO party_persons (party_id, first_name, last_name) "
            "VALUES (:party_id, :first_name, :last_name)"
        ),
        {"party_id": str(party_id), "first_name": first_name, "last_name": last_name},
    )
    return party_id


def _insert_org_party(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    legal_name: str,
) -> uuid.UUID:
    party_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO parties (id, tenant_id, party_type, display_name) "
            "VALUES (:id, :tenant_id, 'organization', :display_name)"
        ),
        {"id": str(party_id), "tenant_id": str(tenant_id), "display_name": legal_name},
    )
    session.execute(
        text(
            "INSERT INTO party_organizations (party_id, legal_name) "
            "VALUES (:party_id, :legal_name)"
        ),
        {"party_id": str(party_id), "legal_name": legal_name},
    )
    return party_id


def test_party_created_in_tenant_a_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    email = f"party-canary-{uuid.uuid4().hex[:8]}@tenant-a.example.com"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party_id = _insert_person_party(a, tenant_id=tenant_a.id, email=email)
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM parties WHERE id = :id"), {"id": str(party_id)}
            ).fetchall()
            assert rows == []
        finally:
            b.rollback()
            b.close()

        # And it IS visible from tenant A's own context.
        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text("SELECT id FROM parties WHERE id = :id"), {"id": str(party_id)}
            ).fetchone()
            assert row is not None
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()


def test_subtype_row_unreachable_cross_tenant_via_join(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """`party_persons` carries no `tenant_id` of its own — its RLS policy is an
    `EXISTS` join back to `parties`. Prove that join-based isolation actually
    holds under tenant B's context, not just direct-table-lookup isolation.
    """
    email = f"subtype-canary-{uuid.uuid4().hex[:8]}@tenant-a.example.com"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party_id = _insert_person_party(
            a,
            tenant_id=tenant_a.id,
            email=email,
            first_name="Grace",
            last_name="Hopper",
        )
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text(
                    "SELECT pp.first_name FROM party_persons pp "
                    "JOIN parties p ON p.id = pp.party_id "
                    "WHERE pp.party_id = :id"
                ),
                {"id": str(party_id)},
            ).fetchall()
            assert rows == []

            # Even a direct (non-joined) read of party_persons is blocked —
            # the EXISTS policy applies regardless of query shape.
            direct_rows = b.execute(
                text("SELECT party_id FROM party_persons WHERE party_id = :id"),
                {"id": str(party_id)},
            ).fetchall()
            assert direct_rows == []
        finally:
            b.rollback()
            b.close()

        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text(
                    "SELECT pp.first_name FROM party_persons pp "
                    "JOIN parties p ON p.id = pp.party_id "
                    "WHERE pp.party_id = :id"
                ),
                {"id": str(party_id)},
            ).fetchone()
            assert row is not None
            assert row[0] == "Grace"
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()


def test_organization_party_creatable_and_isolated(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    legal_name = f"Acme Org {uuid.uuid4().hex[:8]}"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party_id = _insert_org_party(a, tenant_id=tenant_a.id, legal_name=legal_name)
        a.commit()
    finally:
        a.close()

    try:
        # Visible + correctly typed from tenant A.
        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text(
                    "SELECT p.party_type, po.legal_name FROM parties p "
                    "JOIN party_organizations po ON po.party_id = p.id "
                    "WHERE p.id = :id"
                ),
                {"id": str(party_id)},
            ).fetchone()
            assert row is not None
            assert row[0] == "organization"
            assert row[1] == legal_name
        finally:
            a2.rollback()
            a2.close()

        # Invisible from tenant B, both the party row and the subtype join.
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM parties WHERE id = :id"), {"id": str(party_id)}
            ).fetchall()
            assert rows == []
            join_rows = b.execute(
                text(
                    "SELECT po.legal_name FROM party_organizations po "
                    "JOIN parties p ON p.id = po.party_id "
                    "WHERE po.party_id = :id"
                ),
                {"id": str(party_id)},
            ).fetchall()
            assert join_rows == []
        finally:
            b.rollback()
            b.close()
    finally:
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()


def _insert_bare_party(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    party_type: str,
    display_name: str,
) -> uuid.UUID:
    """Insert only the `parties` row — no subtype row.

    Used by the write-denial canaries below so the cross-tenant subtype
    INSERT attempt is the only insert on the subtype table (a PK conflict
    from a pre-existing subtype row would mask the RLS rejection we're
    trying to prove).
    """
    party_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO parties (id, tenant_id, party_type, display_name) "
            "VALUES (:id, :tenant_id, :party_type, :display_name)"
        ),
        {
            "id": str(party_id),
            "tenant_id": str(tenant_id),
            "party_type": party_type,
            "display_name": display_name,
        },
    )
    return party_id


def test_tenant_b_cannot_insert_party_person_row_for_tenant_a_party(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Subtype write-denial canary: a `party_persons` INSERT whose `party_id`
    belongs to tenant A, issued under tenant B's session context, must be
    rejected by the `EXISTS`-based RLS `WITH CHECK` clause — not merely
    filtered out on read. This is the test that would fail if the migration's
    subtype policy were missing its `WITH CHECK` (read-only `USING`).
    """
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party_id = _insert_bare_party(
            a,
            tenant_id=tenant_a.id,
            party_type="person",
            display_name="Bare Person",
        )
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            with pytest.raises(DBAPIError, match="row-level security"):
                b.execute(
                    text(
                        "INSERT INTO party_persons (party_id, first_name, last_name) "
                        "VALUES (:party_id, :first_name, :last_name)"
                    ),
                    {
                        "party_id": str(party_id),
                        "first_name": "Eve",
                        "last_name": "Intruder",
                    },
                )
        finally:
            b.rollback()
            b.close()

        remaining = admin_session.execute(
            text("SELECT count(*) FROM party_persons WHERE party_id = :id"),
            {"id": str(party_id)},
        ).scalar_one()
        assert remaining == 0
    finally:
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()


def test_tenant_b_cannot_insert_party_organization_row_for_tenant_a_party(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Same write-denial canary as above, for `party_organizations` — cheap
    to add given `_insert_bare_party` is type-agnostic, and proves the
    `WITH CHECK` clause is correct for BOTH subtype tables, not just one.
    """
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party_id = _insert_bare_party(
            a,
            tenant_id=tenant_a.id,
            party_type="organization",
            display_name="Bare Org",
        )
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            with pytest.raises(DBAPIError, match="row-level security"):
                b.execute(
                    text(
                        "INSERT INTO party_organizations (party_id, legal_name) "
                        "VALUES (:party_id, :legal_name)"
                    ),
                    {"party_id": str(party_id), "legal_name": "Intruder Org LLC"},
                )
        finally:
            b.rollback()
            b.close()

        remaining = admin_session.execute(
            text("SELECT count(*) FROM party_organizations WHERE party_id = :id"),
            {"id": str(party_id)},
        ).scalar_one()
        assert remaining == 0
    finally:
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()
