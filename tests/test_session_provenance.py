"""Disabling a binding revokes the sessions IT produced, and nothing else.

The canary `dotmac_kernel.external_identity`'s deferred contract asked for, in
its own words: *a session issued from binding A survives a disable of binding B,
and does not survive a disable of A.* Both halves matter and they fail
differently — the first catches a revocation that is too broad (a global logout
wearing a selective name), the second catches one that does not happen at all.

## Why this needs a real database

Three of the things under test are not Python:

* the composite FK `(tenant_id, external_identity_binding_id)` →
  `(tenant_id, id)`, which is what makes a cross-tenant citation impossible even
  if somebody writes the id in by hand;
* `ON DELETE RESTRICT`, which is a decision expressed only in DDL;
* the RLS policy on `auth_sessions`, which decides what a tenant connection can
  see and therefore what it can revoke.

An in-memory SQLite run would assert the `UPDATE`'s `WHERE` clause and call it
tenant isolation. It is not, and `tests/unit` is deliberately not where this
lives.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

PROVIDER = "provenance-canary-idp"
ISSUER = "https://idp.provenance.example.com"


@pytest.fixture(scope="module")
def tenant_engine() -> Generator[Engine, None, None]:
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
    session = factory()
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    return session


def _insert_party(session: Session, *, tenant_id: uuid.UUID) -> uuid.UUID:
    party_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO parties (id, tenant_id, party_type, display_name, "
            "email, is_active) VALUES (:id, :tenant_id, 'person', :name, "
            ":email, true)"
        ),
        {
            "id": str(party_id),
            "tenant_id": str(tenant_id),
            "name": f"Provenance {party_id}",
            "email": f"provenance-{party_id}@example.com",
        },
    )
    return party_id


def _insert_binding(
    session: Session, *, tenant_id: uuid.UUID, party_id: uuid.UUID, subject: str
) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO external_identity_bindings "
            "(id, tenant_id, party_id, provider_binding, issuer, subject, "
            " is_active, bound_by, bind_reason) "
            "VALUES (:id, :tenant_id, :party_id, :provider, :issuer, :subject, "
            " true, :bound_by, :reason)"
        ),
        {
            "id": str(row_id),
            "tenant_id": str(tenant_id),
            "party_id": str(party_id),
            "provider": PROVIDER,
            "issuer": ISSUER,
            "subject": subject,
            "bound_by": "canary@example.com",
            "reason": "provenance canary",
        },
    )
    return row_id


def _insert_session(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    party_id: uuid.UUID,
    binding_id: uuid.UUID | None,
) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO auth_sessions "
            "(id, tenant_id, party_id, token_hash, expires_at, "
            " external_identity_binding_id) "
            "VALUES (:id, :tenant_id, :party_id, :token_hash, "
            " now() + interval '1 hour', :binding_id)"
        ),
        {
            "id": str(row_id),
            "tenant_id": str(tenant_id),
            "party_id": str(party_id),
            "token_hash": uuid.uuid4().hex + uuid.uuid4().hex,
            "binding_id": str(binding_id) if binding_id else None,
        },
    )
    return row_id


def _revoked(session: Session, session_id: uuid.UUID) -> bool:
    row = session.execute(
        text("SELECT revoked_at FROM auth_sessions WHERE id = :id"),
        {"id": str(session_id)},
    ).first()
    assert row is not None, "the session row vanished"
    return row[0] is not None


# ── The contract's point 5, both halves ─────────────────────────────────────


def test_disabling_a_binding_revokes_its_sessions_and_only_its_sessions(
    tenant_sessionmaker: sessionmaker[Session], tenant_a
) -> None:
    """One party, two bindings, three sessions.

    The third session is a PASSWORD login (`external_identity_binding_id IS
    NULL`) and it is the case a naive implementation gets wrong in the most
    damaging way: `WHERE external_identity_binding_id != :other` or a missing
    NULL guard would sign out every password user in the tenant because one
    federated binding was disabled.
    """
    from dotmac_kernel.external_identity import disable_external_identity_binding
    from dotmac_kernel.models import Tenant

    session = _as_tenant(tenant_sessionmaker, tenant_a)
    try:
        party = _insert_party(session, tenant_id=tenant_a)
        binding_a = _insert_binding(
            session, tenant_id=tenant_a, party_id=party, subject="subject-a"
        )
        other_party = _insert_party(session, tenant_id=tenant_a)
        binding_b = _insert_binding(
            session, tenant_id=tenant_a, party_id=other_party, subject="subject-b"
        )

        from_a = _insert_session(
            session, tenant_id=tenant_a, party_id=party, binding_id=binding_a
        )
        from_b = _insert_session(
            session, tenant_id=tenant_a, party_id=other_party, binding_id=binding_b
        )
        from_password = _insert_session(
            session, tenant_id=tenant_a, party_id=party, binding_id=None
        )
        session.flush()

        tenant = session.get(Tenant, tenant_a)
        assert tenant is not None
        disable_external_identity_binding(session, tenant=tenant, binding_id=binding_a)
        session.flush()

        assert _revoked(session, from_a), (
            "disabling binding A left a session it produced still live — the "
            "gap this column was added to close"
        )
        assert not _revoked(session, from_b), (
            "disabling binding A revoked a session from binding B — the "
            "revocation is not selective, which is a global logout wearing a "
            "selective name"
        )
        assert not _revoked(session, from_password), (
            "disabling a federated binding revoked a PASSWORD session — every "
            "password user in the tenant would be signed out by one binding "
            "being disabled"
        )
    finally:
        session.rollback()
        session.close()


def test_a_second_disable_revokes_nothing_and_keeps_the_first_timestamp(
    tenant_sessionmaker: sessionmaker[Session], tenant_a
) -> None:
    """Idempotent by predicate, not by bookkeeping.

    `revoked_at IS NULL` is the whole mechanism. The timestamp must not move on
    a second call: it records the moment somebody was signed out, and that is
    the one fact the column carries.
    """
    from dotmac_kernel.external_identity import revoke_sessions_for_binding
    from dotmac_kernel.models import Tenant

    session = _as_tenant(tenant_sessionmaker, tenant_a)
    try:
        party = _insert_party(session, tenant_id=tenant_a)
        binding = _insert_binding(
            session, tenant_id=tenant_a, party_id=party, subject="subject-idem"
        )
        auth = _insert_session(
            session, tenant_id=tenant_a, party_id=party, binding_id=binding
        )
        session.flush()

        tenant = session.get(Tenant, tenant_a)
        assert tenant is not None

        first = revoke_sessions_for_binding(session, tenant=tenant, binding_id=binding)
        assert first == 1
        stamped = session.execute(
            text("SELECT revoked_at FROM auth_sessions WHERE id = :id"),
            {"id": str(auth)},
        ).scalar_one()

        second = revoke_sessions_for_binding(session, tenant=tenant, binding_id=binding)
        assert second == 0, "a second call revoked rows it had already revoked"
        again = session.execute(
            text("SELECT revoked_at FROM auth_sessions WHERE id = :id"),
            {"id": str(auth)},
        ).scalar_one()
        assert again == stamped, (
            "the revocation timestamp moved on a second call — the recorded "
            "moment somebody was signed out is not a value to overwrite"
        )
    finally:
        session.rollback()
        session.close()


# ── What the schema itself refuses ──────────────────────────────────────────


def test_a_session_cannot_cite_a_binding_from_another_tenant(
    tenant_sessionmaker: sessionmaker[Session], tenant_a, tenant_b
) -> None:
    """The composite FK, doing the job a single-column one could not.

    Written by hand rather than through the service, because the service would
    never construct this — the point is that the DATABASE refuses it even when
    the id is correct and real.
    """
    admin = _as_tenant(tenant_sessionmaker, tenant_b)
    try:
        foreign_party = _insert_party(admin, tenant_id=tenant_b)
        foreign_binding = _insert_binding(
            admin, tenant_id=tenant_b, party_id=foreign_party, subject="subject-foreign"
        )
        admin.commit()
    finally:
        admin.close()

    session = _as_tenant(tenant_sessionmaker, tenant_a)
    try:
        party = _insert_party(session, tenant_id=tenant_a)
        with pytest.raises(IntegrityError):
            _insert_session(
                session,
                tenant_id=tenant_a,
                party_id=party,
                binding_id=foreign_binding,
            )
            session.flush()
    finally:
        session.rollback()
        session.close()

    cleanup = _as_tenant(tenant_sessionmaker, tenant_b)
    try:
        cleanup.execute(
            text("DELETE FROM external_identity_bindings WHERE id = :id"),
            {"id": str(foreign_binding)},
        )
        cleanup.commit()
    finally:
        cleanup.close()


def test_a_binding_with_sessions_cannot_be_deleted(
    tenant_sessionmaker: sessionmaker[Session], tenant_a
) -> None:
    """`ON DELETE RESTRICT`, asserted rather than described.

    `SET NULL` would have made this delete succeed and turn a session whose
    provenance is KNOWN into one shaped exactly like a password session —
    silently, while leaving it live. That is why the delete rule is what it is,
    and a test is the only thing that keeps a future migration from "fixing"
    the inconvenience.
    """
    session = _as_tenant(tenant_sessionmaker, tenant_a)
    try:
        party = _insert_party(session, tenant_id=tenant_a)
        binding = _insert_binding(
            session, tenant_id=tenant_a, party_id=party, subject="subject-restrict"
        )
        _insert_session(session, tenant_id=tenant_a, party_id=party, binding_id=binding)
        session.flush()

        with pytest.raises(IntegrityError):
            session.execute(
                text("DELETE FROM external_identity_bindings WHERE id = :id"),
                {"id": str(binding)},
            )
            session.flush()
    finally:
        session.rollback()
        session.close()
