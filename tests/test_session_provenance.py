"""Disabling a binding revokes the sessions IT produced, and nothing else.

The canary `dotmac_kernel.external_identity`'s contract asked for, in its own
words: *a session issued from binding A survives a disable of binding B, and
does not survive a disable of A.* Both halves matter and they fail differently —
the first catches a revocation that is too broad (a global logout wearing a
selective name), the second catches one that does not happen at all.

The RACE between a login and a disable is not here; it lives in
`test_external_identity_login_race.py` beside the other directional canaries,
because it needs that file's barrier machinery and belongs next to the
interleavings it is a variation of.

## Why this needs a real database

Four of the things under test are not Python:

* the composite FK `(tenant_id, party_id, external_identity_binding_id)` →
  `(tenant_id, party_id, id)`, which is what stops a session citing a binding
  that belongs to somebody else;
* `ON DELETE RESTRICT`, which is a decision expressed only in DDL;
* the partial index, which is only a plan choice;
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


def _party(session: Session, *, tenant_id: uuid.UUID) -> uuid.UUID:
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


def _binding(
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


def _session_row(
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
    """Two bindings, three sessions, one disable.

    The third session is a PASSWORD login (`external_identity_binding_id IS
    NULL`) and it is the case a naive implementation gets wrong most damagingly:
    a missing NULL guard, or a `!=` against the other binding, signs out every
    password user in the tenant because one federated binding was disabled.
    """
    from dotmac_kernel.external_identity import disable_external_identity_binding
    from dotmac_kernel.models import Tenant

    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        alice = _party(session, tenant_id=tenant_a.id)
        bob = _party(session, tenant_id=tenant_a.id)
        binding_a = _binding(
            session, tenant_id=tenant_a.id, party_id=alice, subject="subject-a"
        )
        binding_b = _binding(
            session, tenant_id=tenant_a.id, party_id=bob, subject="subject-b"
        )

        from_a = _session_row(
            session, tenant_id=tenant_a.id, party_id=alice, binding_id=binding_a
        )
        from_b = _session_row(
            session, tenant_id=tenant_a.id, party_id=bob, binding_id=binding_b
        )
        password = _session_row(
            session, tenant_id=tenant_a.id, party_id=alice, binding_id=None
        )
        session.flush()

        tenant = session.get(Tenant, tenant_a.id)
        assert tenant is not None
        disable_external_identity_binding(session, tenant=tenant, binding_id=binding_a)
        session.flush()

        assert _revoked(session, from_a), (
            "disabling binding A left a session it produced still live — the gap "
            "this column was added to close"
        )
        assert not _revoked(session, from_b), (
            "disabling binding A revoked a session from binding B: the "
            "revocation is not selective, which is a global logout wearing a "
            "selective name"
        )
        assert not _revoked(session, password), (
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
    from dotmac_kernel.exceptions import NotFoundError
    from dotmac_kernel.external_identity import disable_external_identity_binding
    from dotmac_kernel.models import Tenant

    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party = _party(session, tenant_id=tenant_a.id)
        binding = _binding(
            session, tenant_id=tenant_a.id, party_id=party, subject="subject-idem"
        )
        auth = _session_row(
            session, tenant_id=tenant_a.id, party_id=party, binding_id=binding
        )
        session.flush()

        tenant = session.get(Tenant, tenant_a.id)
        assert tenant is not None

        disable_external_identity_binding(session, tenant=tenant, binding_id=binding)
        session.flush()
        stamped = session.execute(
            text("SELECT revoked_at FROM auth_sessions WHERE id = :id"),
            {"id": str(auth)},
        ).scalar_one()
        assert stamped is not None

        # Disabling an already-disabled binding is not an error, and must not
        # move the clock on a session that was signed out a minute ago.
        try:
            disable_external_identity_binding(
                session, tenant=tenant, binding_id=binding
            )
            session.flush()
        except NotFoundError:  # pragma: no cover - would be a behaviour change
            pytest.fail("disabling an already-disabled binding became an error")

        again = session.execute(
            text("SELECT revoked_at FROM auth_sessions WHERE id = :id"),
            {"id": str(auth)},
        ).scalar_one()
        assert again == stamped, (
            "the revocation timestamp moved on a second disable — the recorded "
            "moment somebody was signed out is not a value to overwrite"
        )
    finally:
        session.rollback()
        session.close()


# ── What the schema refuses, so the service never has to ────────────────────


def test_a_session_cannot_cite_another_partys_binding(
    tenant_sessionmaker: sessionmaker[Session], tenant_a
) -> None:
    """The reason `party_id` is IN the foreign key.

    Same tenant, wrong person. With a `(tenant_id, binding_id)` FK this insert
    succeeds and nothing ever objects — and then disabling Bob's binding would
    revoke Alice's session, or an audit would attribute Alice's session to Bob's
    identity. Neither is recoverable by reading the code more carefully, which
    is why it is a constraint rather than a convention.
    """
    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        alice = _party(session, tenant_id=tenant_a.id)
        bob = _party(session, tenant_id=tenant_a.id)
        bobs_binding = _binding(
            session, tenant_id=tenant_a.id, party_id=bob, subject="subject-bob"
        )
        session.flush()

        with pytest.raises(IntegrityError):
            _session_row(
                session,
                tenant_id=tenant_a.id,
                party_id=alice,
                binding_id=bobs_binding,
            )
            session.flush()
    finally:
        session.rollback()
        session.close()


def test_a_session_cannot_cite_a_binding_from_another_tenant(
    tenant_sessionmaker: sessionmaker[Session], tenant_a, tenant_b
) -> None:
    """The other half of the same constraint, written by hand because no service
    would ever construct it — the point is that the DATABASE refuses it even
    when the id is real."""
    admin = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        foreign_party = _party(admin, tenant_id=tenant_b.id)
        foreign_binding = _binding(
            admin, tenant_id=tenant_b.id, party_id=foreign_party, subject="subj-foreign"
        )
        admin.commit()
    finally:
        admin.close()

    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party = _party(session, tenant_id=tenant_a.id)
        with pytest.raises(IntegrityError):
            _session_row(
                session,
                tenant_id=tenant_a.id,
                party_id=party,
                binding_id=foreign_binding,
            )
            session.flush()
    finally:
        session.rollback()
        session.close()

    cleanup = _as_tenant(tenant_sessionmaker, tenant_b.id)
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

    `SET NULL` would make this delete succeed and turn a session whose
    provenance is KNOWN into one shaped exactly like a password session —
    quietly, while leaving it live. A test is the only thing that keeps a future
    migration from "fixing" the inconvenience.
    """
    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party = _party(session, tenant_id=tenant_a.id)
        binding = _binding(
            session, tenant_id=tenant_a.id, party_id=party, subject="subject-restrict"
        )
        _session_row(session, tenant_id=tenant_a.id, party_id=party, binding_id=binding)
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
