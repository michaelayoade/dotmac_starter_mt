"""`dotmac_kernel.identity_admin` and its `identity_admin.v1` prerequisite.

Hosted PostgreSQL: real row locks, RLS under `app_user`, the real catalogue.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from dotmac_kernel import identity_admin as ia
from dotmac_kernel.migrations.verify import (
    PrerequisiteNotSatisfiedError,
    verify_identity_admin,
)
from dotmac_kernel.models import Role
from dotmac_kernel.prerequisites import install_prerequisite_bindings
from dotmac_kernel.transactions import conflict_savepoint
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

NOW = datetime(2026, 11, 25, tzinfo=UTC)


@contextmanager
def _app_user(tenant_id: uuid.UUID) -> Iterator[Session]:
    engine = create_engine(os.environ["TEST_DATABASE_URL"], future=True)
    db = Session(engine)
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, false)"),
            {"t": str(tenant_id)},
        )
        yield db
    finally:
        db.rollback()
        db.close()
        engine.dispose()


def _values(tenant_id, email: str, *, active: bool = True) -> ia.NewPersonValues:
    return ia.NewPersonValues(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=email,
        display_name="Test Person",
        first_name="Test",
        last_name="Person",
        is_active=active,
        custom_fields={},
        created_at=NOW,
        updated_at=NOW,
    )


def _role(admin_session: Session, tenant, slug: str = "admin") -> uuid.UUID:
    role = Role(tenant_id=tenant.id, slug=slug, name=slug.title())
    admin_session.add(role)
    admin_session.commit()
    return role.id


# ── the prerequisite ────────────────────────────────────────────────────────


@pytest.fixture
def bound():
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    yield
    install_prerequisite_bindings(())


def test_the_verifier_accepts_the_migrated_catalogue(admin_session: Session) -> None:
    verify_identity_admin(admin_session.connection())


@pytest.mark.parametrize(
    "statement",
    (
        "REVOKE DELETE ON party_role_grants FROM app_user",
        "REVOKE INSERT ON party_persons FROM app_user",
        "REVOKE UPDATE ON auth_sessions FROM app_user",
        "ALTER TABLE party_role_grants NO FORCE ROW LEVEL SECURITY",
        "DROP INDEX uq_parties_tenant_lower_email",
    ),
)
def test_the_verifier_refuses_each_planted_defect(
    admin_session: Session, bound, statement: str
) -> None:
    connection = admin_session.connection()
    connection.execute(text(statement))
    try:
        with pytest.raises(PrerequisiteNotSatisfiedError, match="identity_admin.v1"):
            verify_identity_admin(connection)
    finally:
        admin_session.rollback()


# ── the commands ────────────────────────────────────────────────────────────


def test_create_grant_then_revoke_in_the_governed_order(
    admin_session: Session, tenant_a
) -> None:
    role_id = _role(admin_session, tenant_a)
    tid = tenant_a.id
    with _app_user(tid) as db:
        assert (
            ia.find_party_normalized_email(
                db, tenant_id=tid, normalized_email="new@tenant-a.example.com"
            )
            is None
        )
        with conflict_savepoint(db):
            person = ia.create_person(
                db, values=_values(tid, "new@tenant-a.example.com")
            )
            grant = ia.ensure_grant(
                db, tenant_id=tid, party_id=person.id, role_id=role_id
            )
            assert (
                ia.ensure_grant(db, tenant_id=tid, party_id=person.id, role_id=role_id)
                == grant
            )
        found = ia.find_party_normalized_email(
            db, tenant_id=tid, normalized_email="new@tenant-a.example.com"
        )
        assert found is not None and found.id == person.id
        db.execute(
            text(
                "INSERT INTO auth_sessions (id, tenant_id, party_id, token_hash, "
                "expires_at) VALUES (:i, :t, :p, :h, now() + interval '1 hour')"
            ),
            {"i": str(uuid.uuid4()), "t": str(tid), "p": str(person.id), "h": "h" * 64},
        )
        db.commit()

    with _app_user(tid) as db:
        with conflict_savepoint(db):
            ia.lock_parties(db, tenant_id=tid, party_ids=(person.id,))
            grants = ia.lock_grants(db, tenant_id=tid, party_ids=(person.id,))
            assert grants == (grant,)
            assert (
                ia.remove_grants(db, tenant_id=tid, party_id=person.id, expected=grants)
                == grants
            )
            revoked = ia.revoke_party_sessions(
                db,
                tenant_id=tid,
                party_id=person.id,
                session_ids=None,
                revoked_at=NOW,
            )
            assert len(revoked) == 1
        db.commit()

    left = admin_session.execute(
        text("SELECT count(*) FROM party_role_grants WHERE party_id = :p"),
        {"p": str(person.id)},
    ).scalar_one()
    unrevoked = admin_session.execute(
        text(
            "SELECT count(*) FROM auth_sessions WHERE party_id = :p "
            "AND revoked_at IS NULL"
        ),
        {"p": str(person.id)},
    ).scalar_one()
    assert (left, unrevoked) == (0, 0)


def test_subordinate_commands_refuse_an_unlocked_party(
    admin_session: Session, tenant_a
) -> None:
    role_id = _role(admin_session, tenant_a)
    tid = tenant_a.id
    with _app_user(tid) as db:
        with conflict_savepoint(db):
            person = ia.create_person(
                db, values=_values(tid, "p1@tenant-a.example.com")
            )
        db.commit()
    with _app_user(tid) as db:
        with pytest.raises(ia.IdentityAdminRefusal, match="party lock required"):
            ia.ensure_grant(db, tenant_id=tid, party_id=person.id, role_id=role_id)
        with pytest.raises(ia.IdentityAdminRefusal, match="party lock required"):
            ia.lock_grants(db, tenant_id=tid, party_ids=(person.id,))
        with pytest.raises(ia.IdentityAdminRefusal, match="party lock required"):
            ia.revoke_party_sessions(
                db,
                tenant_id=tid,
                party_id=person.id,
                session_ids=None,
                revoked_at=NOW,
            )


def test_party_locks_must_precede_subordinate_locks(
    admin_session: Session, tenant_a
) -> None:
    tid = tenant_a.id
    with _app_user(tid) as db:
        with conflict_savepoint(db):
            first = ia.create_person(db, values=_values(tid, "a1@tenant-a.example.com"))
            second = ia.create_person(
                db, values=_values(tid, "a2@tenant-a.example.com")
            )
        db.commit()
    with _app_user(tid) as db:
        ia.lock_parties(db, tenant_id=tid, party_ids=(first.id,))
        ia.lock_grants(db, tenant_id=tid, party_ids=(first.id,))
        with pytest.raises(ia.IdentityAdminRefusal, match="precede subordinate"):
            ia.lock_parties(db, tenant_id=tid, party_ids=(second.id,))
        # re-locking what is already held is not a new party
        ia.lock_parties(db, tenant_id=tid, party_ids=(first.id,))


def test_lock_parties_refuses_an_absent_or_foreign_party(
    tenant_a, tenant_b, admin_session: Session
) -> None:
    with _app_user(tenant_b.id) as db:
        with conflict_savepoint(db):
            foreign = ia.create_person(
                db, values=_values(tenant_b.id, "b@tenant-b.example.com")
            )
        db.commit()
    with _app_user(tenant_a.id) as db:
        with pytest.raises(ia.IdentityAdminRefusal, match="absent"):
            ia.lock_parties(
                db, tenant_id=tenant_a.id, party_ids=(foreign.id, uuid.uuid4())
            )


def test_ensure_grant_refuses_an_inactive_party_and_a_foreign_role(
    admin_session: Session, tenant_a, tenant_b
) -> None:
    role_id = _role(admin_session, tenant_a)
    foreign_role = _role(admin_session, tenant_b)
    tid = tenant_a.id
    with _app_user(tid) as db:
        with conflict_savepoint(db):
            inactive = ia.create_person(
                db, values=_values(tid, "off@tenant-a.example.com", active=False)
            )
            active = ia.create_person(
                db, values=_values(tid, "on@tenant-a.example.com")
            )
            with pytest.raises(ia.IdentityAdminRefusal, match="inactive party"):
                ia.ensure_grant(
                    db, tenant_id=tid, party_id=inactive.id, role_id=role_id
                )
            with pytest.raises(ia.IdentityAdminRefusal, match="not in this tenant"):
                ia.ensure_grant(
                    db, tenant_id=tid, party_id=active.id, role_id=foreign_role
                )


@pytest.mark.parametrize("email", ("Mixed@Example.com", " padded@example.com", ""))
def test_unnormalised_email_is_refused(admin_session: Session, tenant_a, email) -> None:
    with _app_user(tenant_a.id) as db:
        with pytest.raises(ia.IdentityAdminRefusal, match="lower-cased"):
            ia.find_party_normalized_email(
                db, tenant_id=tenant_a.id, normalized_email=email
            )
        if email:
            with pytest.raises(ia.IdentityAdminRefusal, match="lower-cased"):
                ia.create_person(db, values=_values(tenant_a.id, email))


def test_a_lock_taken_inside_a_rolled_back_savepoint_is_not_honoured(
    admin_session: Session, tenant_a
) -> None:
    role_id = _role(admin_session, tenant_a)
    tid = tenant_a.id
    with _app_user(tid) as db:
        with conflict_savepoint(db):
            person = ia.create_person(
                db, values=_values(tid, "sp@tenant-a.example.com")
            )
        db.commit()
    with _app_user(tid) as db:
        with pytest.raises(RuntimeError, match="abandon"):
            with conflict_savepoint(db):
                ia.lock_parties(db, tenant_id=tid, party_ids=(person.id,))
                raise RuntimeError("abandon")
        with pytest.raises(ia.IdentityAdminRefusal, match="party lock required"):
            ia.ensure_grant(db, tenant_id=tid, party_id=person.id, role_id=role_id)
        assert db.scalar(select(Role.id).where(Role.id == role_id)) == role_id
