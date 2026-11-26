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
    require_runtime_prerequisites,
    verify_identity_admin,
)
from dotmac_kernel.models import Party, Role
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.permissions import (
    PermissionCatalogue,
    PermissionSpec,
    active_permissions,
    install_permissions,
)
from dotmac_kernel.prerequisites import install_prerequisite_bindings
from dotmac_kernel.transactions import conflict_savepoint
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS
from tests.test_migration_split_rehearsals import _upgrade, scratch_db  # noqa: F401

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
        "REVOKE INSERT ON roles FROM app_user",
        # Column-level only: UPDATE elsewhere on the table must not satisfy it.
        "REVOKE UPDATE ON parties FROM app_user; "
        "GRANT UPDATE (display_name) ON parties TO app_user",
        "REVOKE UPDATE ON roles FROM app_user; "
        "GRANT UPDATE (slug) ON roles TO app_user",
    ),
)
def test_the_verifier_refuses_each_planted_defect(
    admin_session: Session, bound, statement: str
) -> None:
    connection = admin_session.connection()
    for part in statement.split("; "):
        connection.execute(text(part))
    try:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=r"identity_admin\.v1"):
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


# ── one lock_parties call per scope (F5) ────────────────────────────────────


def test_a_second_fresh_lock_parties_call_is_refused(
    admin_session: Session, tenant_a
) -> None:
    tid = tenant_a.id
    with _app_user(tid) as db:
        with conflict_savepoint(db):
            first = ia.create_person(db, values=_values(tid, "o1@tenant-a.example.com"))
            second = ia.create_person(
                db, values=_values(tid, "o2@tenant-a.example.com")
            )
        db.commit()
    with _app_user(tid) as db:
        ia.lock_parties(db, tenant_id=tid, party_ids=(first.id,))
        with pytest.raises(ia.IdentityAdminRefusal, match="once per scope"):
            ia.lock_parties(db, tenant_id=tid, party_ids=(second.id,))
    with _app_user(tid) as db:  # one call naming both is the supported shape
        assert (
            len(ia.lock_parties(db, tenant_id=tid, party_ids=(first.id, second.id)))
            == 2
        )


# ── set_party_active and tenant roles (F1) ──────────────────────────────────


def test_set_party_active_needs_the_lock_and_reports_before_and_after(
    admin_session: Session, tenant_a
) -> None:
    tid = tenant_a.id
    with _app_user(tid) as db:
        with conflict_savepoint(db):
            person = ia.create_person(
                db, values=_values(tid, "act@tenant-a.example.com")
            )
        db.commit()
    with _app_user(tid) as db:
        with pytest.raises(ia.IdentityAdminRefusal, match="party lock required"):
            ia.set_party_active(db, tenant_id=tid, party_id=person.id, active=False)
        with conflict_savepoint(db):
            ia.lock_parties(db, tenant_id=tid, party_ids=(person.id,))
            before, after = ia.set_party_active(
                db, tenant_id=tid, party_id=person.id, active=False
            )
            assert (before.is_active, after.is_active) == (True, False)
            same, again = ia.set_party_active(
                db, tenant_id=tid, party_id=person.id, active=False
            )
            assert (same.is_active, again.is_active) == (False, False)
        with pytest.raises(ia.IdentityAdminRefusal, match="must be a bool"):
            ia.set_party_active(db, tenant_id=tid, party_id=person.id, active=1)  # type: ignore[arg-type]
        db.commit()
    admin_session.rollback()
    assert (
        admin_session.scalars(
            select(Party.is_active).where(Party.id == person.id)
        ).one()
        is False
    )


@pytest.fixture
def code_roles():
    previous = active_permissions()
    install_permissions(
        PermissionCatalogue(
            [("probe", PermissionSpec(code="probe.read", default_roles=("treasury",)))]
        )
    )
    yield
    install_permissions(previous)


def test_tenant_roles_are_created_and_renamed_but_code_roles_are_refused(
    admin_session: Session, tenant_a, code_roles
) -> None:
    tid = tenant_a.id
    with _app_user(tid) as db:
        with conflict_savepoint(db):
            made = ia.create_tenant_role(
                db, tenant_id=tid, slug="approver", name="Approver"
            )
            assert (made.slug, made.name) == ("approver", "Approver")
            renamed = ia.rename_tenant_role(
                db, tenant_id=tid, role_id=made.id, name="Senior Approver"
            )
            assert renamed.name == "Senior Approver"
            with pytest.raises(ia.IdentityAdminRefusal, match="code role"):
                ia.create_tenant_role(db, tenant_id=tid, slug="treasury", name="Mine")
            with pytest.raises(ia.IdentityAdminRefusal, match="trimmed lower-case"):
                ia.create_tenant_role(db, tenant_id=tid, slug="Bad Slug", name="x")
            with pytest.raises(ia.IdentityAdminRefusal, match="role name"):
                ia.rename_tenant_role(db, tenant_id=tid, role_id=made.id, name=" ")
            with pytest.raises(ia.IdentityAdminRefusal, match="not in this tenant"):
                ia.rename_tenant_role(
                    db, tenant_id=tid, role_id=uuid.uuid4(), name="Nope"
                )
        db.commit()
    code_role = _role(admin_session, tenant_a, "treasury")
    with _app_user(tid) as db:
        with pytest.raises(
            ia.IdentityAdminRefusal, match="code role cannot be renamed"
        ):
            ia.rename_tenant_role(db, tenant_id=tid, role_id=code_role, name="Hijack")


def test_role_commands_fail_closed_when_the_catalogue_is_not_installed(
    admin_session: Session, tenant_a
) -> None:
    """Outside `create_app` (CLI, job runner, script) the permission catalogue is
    empty; that must be a refusal, never "no code roles"."""
    previous = active_permissions()
    install_permissions(PermissionCatalogue(()))
    try:
        role_id = _role(admin_session, tenant_a, "treasury")
        with _app_user(tenant_a.id) as db:
            with pytest.raises(ia.IdentityAdminRefusal, match="not installed"):
                ia.create_tenant_role(
                    db, tenant_id=tenant_a.id, slug="treasury2", name="X"
                )
            with pytest.raises(ia.IdentityAdminRefusal, match="not installed"):
                ia.rename_tenant_role(
                    db, tenant_id=tenant_a.id, role_id=role_id, name="Y"
                )
    finally:
        install_permissions(previous)


# ── runtime_requires proven against the database at migrate time (F3) ───────


def test_require_runtime_prerequisites_proves_enabled_modules_against_the_catalogue(
    admin_session: Session, bound
) -> None:
    needs = ModuleManifest(
        code="needs-identity", version="1.0.0", runtime_requires=("identity_admin.v1",)
    )
    plain = ModuleManifest(code="plain", version="1.0.0")
    connection = admin_session.connection()
    require_runtime_prerequisites(connection, [plain])  # nothing to prove
    require_runtime_prerequisites(connection, [needs, plain])
    connection.execute(text("REVOKE DELETE ON party_role_grants FROM app_user"))
    try:
        require_runtime_prerequisites(connection, [plain])  # still nothing to prove
        with pytest.raises(PrerequisiteNotSatisfiedError, match=r"identity_admin\.v1"):
            require_runtime_prerequisites(connection, [needs, plain])
    finally:
        admin_session.rollback()


def test_the_binding_claim_holds_at_exactly_0025(scratch_db: str, bound) -> None:  # noqa: F811
    from dotmac_kernel.prerequisites import binding_for

    assert binding_for("identity_admin.v1").provider_revision == (
        "0025_session_provenance"
    )
    _upgrade(scratch_db, "0025_session_provenance")
    engine = create_engine(scratch_db)
    try:
        with engine.connect() as conn:
            verify_identity_admin(conn)
    finally:
        engine.dispose()


# ── the migrate-time proof runs only on a COMPLETE database ─────────────────


def test_runtime_proof_skips_partial_upgrades_and_refuses_at_heads(
    scratch_db: str,
    bound,  # noqa: F811
) -> None:
    from alembic.script import ScriptDirectory
    from dotmac_kernel.migrations.verify import (
        require_runtime_prerequisites_at_heads,
    )

    from tests.test_migration_split_rehearsals import (
        _alembic_config,
        _both_locations,
    )

    needs = ModuleManifest(
        code="needs-identity", version="1.0.0", runtime_requires=("identity_admin.v1",)
    )
    script_heads = ScriptDirectory.from_config(
        _alembic_config(scratch_db, version_locations=_both_locations())
    ).get_heads()

    def current_heads(conn) -> set[str]:
        return set(
            conn.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )

    engine = create_engine(scratch_db)
    try:
        # Partial: `public.parties` does not exist yet. Must NOT fail.
        _upgrade(scratch_db, "0001_initial_tenant_schema")
        with engine.connect() as conn:
            assert (
                require_runtime_prerequisites_at_heads(
                    conn,
                    [needs],
                    current_heads=current_heads(conn),
                    script_heads=script_heads,
                )
                is False
            )
        # Complete: proven, and a missing privilege still refuses.
        _upgrade(scratch_db, "heads")
        with engine.connect() as conn:
            assert (
                require_runtime_prerequisites_at_heads(
                    conn,
                    [needs],
                    current_heads=current_heads(conn),
                    script_heads=script_heads,
                )
                is True
            )
            conn.rollback()
            conn.execute(text("REVOKE DELETE ON party_role_grants FROM app_user"))
            with pytest.raises(
                PrerequisiteNotSatisfiedError, match=r"identity_admin\.v1"
            ):
                require_runtime_prerequisites_at_heads(
                    conn,
                    [needs],
                    current_heads=current_heads(conn),
                    script_heads=script_heads,
                )
            conn.rollback()
    finally:
        engine.dispose()
