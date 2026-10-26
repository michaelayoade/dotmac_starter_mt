"""Hosted migrated PostgreSQL canaries, authored NOT RUN.

Real DatabaseRuntime, Session/root and migrated admin_engine fixture; no metadata
DDL or authority substitutes. The bypass role deliberately tests tenant
predicates independently of RLS. Synthetic credential sentinels cannot sign in.
"""

import inspect
import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_kernel import identity_storage as store
from dotmac_kernel.models import (
    Party,
    PartyPerson,
    PartyRoleGrant,
    Role,
    Tenant,
    UserCredential,
)
from dotmac_kernel.session_runtime import DatabaseRuntime
from dotmac_kernel.transactions import conflict_savepoint
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.test_credential_tenant_key import _assert_installed_key

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def _values(tenant_id, email):
    return store.NewPersonValues(
        id=uuid4(),
        tenant_id=tenant_id,
        email=email,
        display_name=" Caller display ",
        first_name=" First ",
        last_name=" Last ",
        is_active=False,
        custom_fields={"nested": [1]},
        created_at=NOW,
        updated_at=NOW,
    )


class _RollbackFixture(Exception):
    """Ask the real runtime boundary to roll back synthetic fixture rows."""


@pytest.fixture
def storage_case(identity_reference_engine):
    admin_engine = identity_reference_engine
    assert os.environ.get("CI") == "true", "hosted only"
    assert admin_engine.dialect.name == "postgresql"
    runtime = DatabaseRuntime(engine=admin_engine)
    try:
        with runtime.platform_session() as db:
            assert tuple(
                db.execute(
                    text(
                        "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles W"
                        "HERE rolname=current_user"
                    )
                ).one()
            ) == ("app_admin", False, True)
            # Existing fixture proves the actual migrated canonical key/catalog.
            _assert_installed_key(db.connection())
            tenants = (uuid4(), uuid4())
            for tid in tenants:
                db.execute(
                    insert(Tenant).values(
                        id=tid,
                        slug="identity-store-" + tid.hex,
                        name="Storage canary",
                        is_active=True,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            yield SimpleNamespace(db=db, tenants=tenants, root=db.get_transaction())
            raise _RollbackFixture()
    except _RollbackFixture:
        pass


def test_two_flush_creation_values_detachment_and_exact_queries(
    storage_case, monkeypatch
):
    c = storage_case
    values = _values(c.tenants[0], " Mixed-" + uuid4().hex + "@Example.Test ")
    calls = []
    original = c.db.flush

    def flush(*args, **kwargs):
        calls.append(inspect.currentframe().f_back.f_code.co_name)
        return original(*args, **kwargs)

    monkeypatch.setattr(c.db, "flush", flush)
    party = store.create_person(c.db, values=values)
    assert calls.count("create_person") == 2
    assert c.db.get_transaction() is c.root
    assert c.db.get_nested_transaction() is None
    assert party is c.db.get(Party, values.id)
    fact = store.find_party_exact_email(
        c.db, tenant_id=c.tenants[0], email=values.email
    )
    assert (fact.email, fact.display_name, fact.is_active) == (
        values.email,
        values.display_name,
        False,
    )
    with pytest.raises(FrozenInstanceError):
        fact.email = "changed"
    values.custom_fields["nested"].append(2)
    row = c.db.get(Party, fact.id)
    assert row.custom_fields == {"nested": [1]}
    assert (row.created_at, row.updated_at) == (NOW, NOW)
    assert store.lock_person(
        c.db, tenant_id=c.tenants[0], party_id=fact.id
    ) == store.PersonFact(fact.id, " First ", " Last ")
    assert (
        store.find_party_exact_email(c.db, tenant_id=c.tenants[0], email=values.email)
        == fact
    )
    assert (
        store.find_party_exact_email(
            c.db, tenant_id=c.tenants[0], email=values.email.strip().lower()
        )
        is None
    )
    assert (
        store.find_party_normalized_email(
            c.db, tenant_id=c.tenants[0], normalized_email=values.email.strip().lower()
        )
        == fact
    )


def test_same_uuid_wrong_tenant_all_reads_and_delete_are_bound(storage_case):
    c = storage_case
    tid, wrong = c.tenants
    values = _values(tid, uuid4().hex + "@example.test")
    fact = store.create_person(c.db, values=values)
    roles = store.provision_missing_roles(
        c.db,
        tenant_id=tid,
        catalog=(),
        configured_names={"operator": "Caller role"},
        missing_role_ids={"operator": uuid4()},
        created_at=NOW,
        updated_at=NOW,
    )
    store.lock_parties(c.db, tenant_id=tid, party_ids=(fact.id,))
    grant = store.ensure_grant(
        c.db,
        tenant_id=tid,
        party_id=fact.id,
        role_id=roles[0].id,
        new_grant_id=None,
        created_at=None,
        updated_at=None,
    )
    assert (
        store.find_party_exact_email(c.db, tenant_id=wrong, email=values.email) is None
    )
    assert (
        store.find_party_normalized_email(
            c.db, tenant_id=wrong, normalized_email=values.email
        )
        is None
    )
    assert store.lock_parties(c.db, tenant_id=wrong, party_ids=(fact.id,)) == ()
    assert store.lock_person(c.db, tenant_id=wrong, party_id=fact.id) is None
    assert store.lock_roles(c.db, tenant_id=wrong) == ()
    assert store.lock_grants(c.db, tenant_id=wrong, party_ids=(fact.id,)) == ()
    assert store.credential_member_ids(c.db, tenant_id=wrong, party_id=fact.id) == ()
    with pytest.raises(ValueError, match="deletion outcome"), conflict_savepoint(c.db):
        store.delete_person(c.db, tenant_id=wrong, party_id=fact.id)
    assert c.db.get(Party, fact.id) is not None
    assert c.db.get(PartyRoleGrant, grant.id) is not None
    assert c.db.get_transaction() is c.root


def test_additive_catalog_preserves_names_and_omitted_grant_defaults(storage_case):
    c = storage_case
    tid = c.tenants[0]
    person = store.create_person(
        c.db, values=_values(tid, uuid4().hex + "@example.test")
    )
    store.lock_parties(c.db, tenant_id=tid, party_ids=(person.id,))
    names = {"custom-z": " Exact custom ", "custom-a": "Assembly supplied"}
    ids = {slug: uuid4() for slug in names}
    roles = store.provision_missing_roles(
        c.db,
        tenant_id=tid,
        catalog=store.lock_roles(c.db, tenant_id=tid),
        configured_names=names,
        missing_role_ids=ids,
        created_at=NOW,
        updated_at=NOW,
    )
    repeated = store.provision_missing_roles(
        c.db,
        tenant_id=tid,
        catalog=store.lock_roles(c.db, tenant_id=tid),
        configured_names={"custom-z": "do not rename"},
        missing_role_ids={},
        created_at=NOW,
        updated_at=NOW,
    )
    assert {r.slug: (r.id, r.name) for r in repeated} == {
        s: (ids[s], n) for s, n in names.items()
    }
    grant = store.ensure_grant(
        c.db,
        tenant_id=tid,
        party_id=person.id,
        role_id=roles[0].id,
        new_grant_id=None,
        created_at=None,
        updated_at=None,
    )
    row = c.db.get(PartyRoleGrant, grant.id)
    assert (
        row.id is not None and row.created_at is not None and row.updated_at is not None
    )
    assert (
        store.ensure_grant(
            c.db,
            tenant_id=tid,
            party_id=person.id,
            role_id=roles[0].id,
            new_grant_id=uuid4(),
            created_at=NOW,
            updated_at=NOW,
        )
        == grant
    )
    assert store.lock_grants(c.db, tenant_id=tid, party_ids=(person.id,)) == (grant,)
    assert [r.id for r in store.lock_roles(c.db, tenant_id=tid)] == sorted(ids.values())
    assert c.db.get(Role, ids["custom-z"]).name == " Exact custom "


def test_whole_credential_cohort_reads_ids_only(storage_case):
    c = storage_case
    tid, wrong = c.tenants
    person = store.create_person(
        c.db, values=_values(tid, uuid4().hex + "@example.test")
    )
    ids = (uuid4(), uuid4())
    # Same non-authenticating setup pattern as test_credential_tenant_key.py.
    for cid in ids:
        c.db.execute(
            insert(UserCredential).values(
                id=cid,
                tenant_id=tid,
                party_id=person.id,
                password_hash="synthetic-non-authenticating-credential",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    store.lock_parties(c.db, tenant_id=tid, party_ids=(person.id,))
    assert store.credential_member_ids(
        c.db, tenant_id=tid, party_id=person.id
    ) == tuple(sorted(ids))
    assert store.credential_member_ids(c.db, tenant_id=wrong, party_id=person.id) == ()
    assert c.db.get_transaction() is c.root


def test_locked_reads_refresh_existing_identity_map_and_uuid_order(storage_case):
    c = storage_case
    tid = c.tenants[0]
    first = store.create_person(
        c.db, values=_values(tid, uuid4().hex + "@example.test")
    )
    second = store.create_person(
        c.db, values=_values(tid, uuid4().hex + "@example.test")
    )
    retained = c.db.get(Party, first.id)
    c.db.execute(
        update(Party)
        .where(Party.id == first.id)
        .values(display_name="Current")
        .execution_options(synchronize_session=False)
    )
    assert retained.display_name == " Caller display "
    facts = store.lock_parties(
        c.db, tenant_id=tid, party_ids=(second.id, first.id, second.id)
    )
    assert [f.id for f in facts] == sorted((first.id, second.id))
    assert next(f for f in facts if f.id == first.id).display_name == "Current"
    assert retained.display_name == "Current"


@pytest.mark.parametrize("step", [1, 2])
def test_each_real_flush_failure_rolls_back_in_caller_savepoint(
    storage_case, monkeypatch, step
):
    c = storage_case
    values = _values(c.tenants[0], uuid4().hex + "@example.test")
    original = c.db.flush
    count = 0

    def flush(*args, **kwargs):
        nonlocal count
        from_create = inspect.currentframe().f_back.f_code.co_name == "create_person"
        result = original(*args, **kwargs)
        if from_create:
            count += 1
            if count == step:
                raise RuntimeError("after real flush")
        return result

    monkeypatch.setattr(c.db, "flush", flush)
    with (
        pytest.raises(RuntimeError, match="after real flush"),
        conflict_savepoint(c.db),
    ):
        store.create_person(c.db, values=values)
    assert c.db.get_transaction() is c.root
    assert (
        c.db.get(Party, values.id) is None and c.db.get(PartyPerson, values.id) is None
    )


def test_collisions_propagate_and_delete_is_caller_rollback_owned(storage_case):
    c = storage_case
    tid = c.tenants[0]
    email = uuid4().hex + "@example.test"
    party = store.create_person(c.db, values=_values(tid, email))
    fact = store.find_party_exact_email(c.db, tenant_id=tid, email=email)
    assert fact.id == party.id
    with pytest.raises(IntegrityError), conflict_savepoint(c.db):
        store.create_person(c.db, values=_values(tid, email.upper()))
    assert store.find_party_exact_email(c.db, tenant_id=tid, email=email) == fact
    with pytest.raises(RuntimeError, match="caller rollback"), conflict_savepoint(c.db):
        store.delete_person(c.db, tenant_id=tid, party_id=fact.id)
        assert c.db.scalar(select(Party.id).where(Party.id == fact.id)) is None
        raise RuntimeError("caller rollback")
    assert store.find_party_exact_email(c.db, tenant_id=tid, email=email) == fact
    store.delete_person(c.db, tenant_id=tid, party_id=fact.id)
    assert (
        c.db.scalar(select(PartyPerson.party_id).where(PartyPerson.party_id == fact.id))
        is None
    )
    with pytest.raises(ValueError, match="deletion outcome"), conflict_savepoint(c.db):
        store.delete_person(c.db, tenant_id=tid, party_id=fact.id)
    assert c.db.get_transaction() is c.root


def _storage_principal(c, tid):
    values = _values(tid, uuid4().hex + "@example.test")
    person = store.create_person(c.db, values=values)
    store.lock_parties(c.db, tenant_id=tid, party_ids=(person.id,))
    return person.id


def test_exact_grant_removal_preserves_unselected_rows_and_refuses_drift(storage_case):
    c = storage_case
    tid = c.tenants[0]
    pid = _storage_principal(c, tid)
    roles = store.provision_missing_roles(
        c.db,
        tenant_id=tid,
        catalog=(),
        configured_names={"remove": "Remove", "preserve": "Preserve"},
        missing_role_ids={"remove": uuid4(), "preserve": uuid4()},
        created_at=NOW,
        updated_at=NOW,
    )
    grants = tuple(
        store.ensure_grant(
            c.db,
            tenant_id=tid,
            party_id=pid,
            role_id=role.id,
            new_grant_id=uuid4(),
            created_at=NOW,
            updated_at=NOW,
        )
        for role in roles
    )
    before = tuple(
        c.db.execute(
            select(PartyRoleGrant.__table__)
            .where(PartyRoleGrant.tenant_id == tid, PartyRoleGrant.party_id == pid)
            .order_by(PartyRoleGrant.id)
        )
    )
    bad = store.GrantFact(grants[0].id, tid, pid, uuid4())
    with pytest.raises(ValueError, match="cohort differs"), conflict_savepoint(c.db):
        c.db.connection()  # materialize the caller-owned physical savepoint
        store.remove_grants(c.db, tenant_id=tid, party_id=pid, expected=(bad,))
    assert (
        tuple(
            c.db.execute(
                select(PartyRoleGrant.__table__)
                .where(PartyRoleGrant.tenant_id == tid, PartyRoleGrant.party_id == pid)
                .order_by(PartyRoleGrant.id)
            )
        )
        == before
    )
    with conflict_savepoint(c.db):
        c.db.connection()
        assert store.remove_grants(c.db, tenant_id=tid, party_id=pid, expected=()) == ()
        assert store.remove_grants(
            c.db, tenant_id=tid, party_id=pid, expected=(grants[0],)
        ) == (grants[0],)
    assert (
        c.db.execute(
            select(PartyRoleGrant.id).where(PartyRoleGrant.id == grants[0].id)
        ).first()
        is None
    )
    assert (
        c.db.execute(
            select(PartyRoleGrant.id).where(PartyRoleGrant.id == grants[1].id)
        ).scalar_one()
        == grants[1].id
    )
    preserved = c.db.execute(
        select(PartyRoleGrant.__table__).where(PartyRoleGrant.id == grants[1].id)
    ).one()
    assert preserved == next(row for row in before if row.id == grants[1].id)
    assert c.db.get_transaction() is c.root


def test_session_revocation_exact_empty_all_idempotence_and_unchanged_material(
    storage_case,
):
    from datetime import timedelta

    from dotmac_kernel.models import AuthSession, ExternalIdentityBinding

    c = storage_case
    tid = c.tenants[0]
    pid = _storage_principal(c, tid)
    other = _storage_principal(c, c.tenants[1])
    ids = tuple(uuid4() for _ in range(4))
    old = NOW - timedelta(days=3)
    table = AuthSession.__table__
    binding_id = uuid4()
    c.db.execute(
        insert(ExternalIdentityBinding.__table__).values(
            id=binding_id,
            tenant_id=tid,
            party_id=pid,
            provider_binding="storage-" + uuid4().hex,
            issuer="https://storage.example.test",
            subject=uuid4().hex,
            is_active=True,
            bound_by="storage canary",
            bind_reason="provenance canary",
            bound_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    for index, sid in enumerate(ids):
        c.db.execute(
            insert(table).values(
                id=sid,
                tenant_id=tid if index < 3 else c.tenants[1],
                party_id=pid if index < 3 else other,
                token_hash=(str(index) * 64),
                expires_at=NOW - timedelta(days=1),
                revoked_at=old if index == 2 else None,
                created_at=old,
                external_identity_binding_id=binding_id if index == 0 else None,
            )
        )
    before = {
        row.id: row
        for row in c.db.execute(
            select(table).where(table.c.id.in_(ids)).order_by(table.c.id)
        )
    }
    with conflict_savepoint(c.db):
        c.db.connection()
        assert (
            store.revoke_party_sessions(
                c.db, tenant_id=tid, party_id=pid, session_ids=(), revoked_at=NOW
            )
            == ()
        )
        assert store.revoke_party_sessions(
            c.db, tenant_id=tid, party_id=pid, session_ids=(ids[1],), revoked_at=NOW
        ) == (ids[1],)
        assert (
            store.revoke_party_sessions(
                c.db,
                tenant_id=tid,
                party_id=pid,
                session_ids=(ids[1],),
                revoked_at=NOW + timedelta(hours=1),
            )
            == ()
        )
        assert store.revoke_party_sessions(
            c.db, tenant_id=tid, party_id=pid, session_ids=None, revoked_at=NOW
        ) == (ids[0],)
    after = {
        row.id: row
        for row in c.db.execute(
            select(table).where(table.c.id.in_(ids)).order_by(table.c.id)
        )
    }
    for sid in ids:
        assert after[sid].token_hash == before[sid].token_hash
        assert after[sid].expires_at == before[sid].expires_at
        assert (
            after[sid].external_identity_binding_id
            == before[sid].external_identity_binding_id
        )
        assert after[sid].created_at == before[sid].created_at
    assert after[ids[0]].revoked_at == NOW
    assert after[ids[1]].revoked_at == NOW
    assert after[ids[2]].revoked_at == old
    assert after[ids[3]].revoked_at is None

    assert after[ids[0]].updated_at == NOW
    assert after[ids[1]].updated_at == NOW
    assert after[ids[2]].updated_at == before[ids[2]].updated_at
    assert after[ids[3]].updated_at == before[ids[3]].updated_at


def test_foreign_session_cohort_refuses_before_any_transition(storage_case):
    from dotmac_kernel.models import AuthSession

    c = storage_case
    tid, foreign = c.tenants
    pid = _storage_principal(c, tid)
    other = _storage_principal(c, foreign)
    local_id, foreign_id = uuid4(), uuid4()
    for sid, tenant_id, party_id in (
        (local_id, tid, pid),
        (foreign_id, foreign, other),
    ):
        c.db.execute(
            insert(AuthSession.__table__).values(
                id=sid,
                tenant_id=tenant_id,
                party_id=party_id,
                token_hash=uuid4().hex * 2,
                expires_at=NOW,
                created_at=NOW,
            )
        )
    for scope in ((local_id, foreign_id), (uuid4(),)):
        with (
            pytest.raises(ValueError, match="cohort differs"),
            conflict_savepoint(c.db),
        ):
            c.db.connection()
            store.revoke_party_sessions(
                c.db, tenant_id=tid, party_id=pid, session_ids=scope, revoked_at=NOW
            )
        assert tuple(
            c.db.scalars(
                select(AuthSession.revoked_at).where(
                    AuthSession.id.in_((local_id, foreign_id))
                )
            )
        ) == (None, None)
    with pytest.raises(ValueError, match="cohort differs"), conflict_savepoint(c.db):
        c.db.connection()
        store.revoke_party_sessions(
            c.db,
            tenant_id=foreign,
            party_id=pid,
            session_ids=(local_id,),
            revoked_at=NOW,
        )
    assert c.db.get_transaction() is c.root


@pytest.mark.parametrize("pending_kind", ("new", "dirty", "deleted"))
def test_storage_pending_refusal_does_no_io_and_preserves_caller_state(
    storage_case, pending_kind
):
    from dotmac_kernel.transactions import ConflictSavepointProtocolError
    from sqlalchemy import event
    from sqlalchemy import inspect as sa_inspect

    c = storage_case
    tid = c.tenants[0]
    pid = _storage_principal(c, tid)
    row = c.db.get(Party, pid)
    if pending_kind == "new":
        pending = Tenant(id=uuid4(), slug=uuid4().hex, name="Caller pending")
        c.db.add(pending)
    elif pending_kind == "dirty":
        pending = row
        pending.display_name = "Caller pending"
    else:
        pending = row
        c.db.delete(pending)
    before = (tuple(c.db.new), tuple(c.db.dirty), tuple(c.db.deleted))
    history = (
        sa_inspect(pending).attrs.name.history
        if pending_kind == "new"
        else sa_inspect(pending).attrs.display_name.history
    )
    calls = []

    def observe(*args):
        calls.append(1)

    connection = c.db.connection()
    event.listen(connection, "before_cursor_execute", observe)
    try:
        for command in (
            lambda: store.remove_grants(c.db, tenant_id=tid, party_id=pid, expected=()),
            lambda: store.revoke_party_sessions(
                c.db, tenant_id=tid, party_id=pid, session_ids=(), revoked_at=NOW
            ),
        ):
            with pytest.raises(
                ConflictSavepointProtocolError, match="clean pending state"
            ):
                command()
        assert calls == []
        assert (tuple(c.db.new), tuple(c.db.dirty), tuple(c.db.deleted)) == before
        assert (
            sa_inspect(pending).attrs.name.history
            if pending_kind == "new"
            else sa_inspect(pending).attrs.display_name.history
        ) == history
        assert c.db.get_transaction() is c.root
    finally:
        event.remove(connection, "before_cursor_execute", observe)
    # The fixture root rolls back all caller-owned pending work itself.


def test_revocation_callback_pending_refuses_and_preserves_prior_flushed_root(
    storage_case,
):
    from dotmac_kernel.models import AuthSession
    from dotmac_kernel.transactions import ConflictSavepointProtocolError
    from sqlalchemy import event

    c = storage_case
    tid = c.tenants[0]
    pid = _storage_principal(c, tid)
    sid = uuid4()
    c.db.execute(
        insert(AuthSession.__table__).values(
            id=sid,
            tenant_id=tid,
            party_id=pid,
            token_hash=uuid4().hex * 2,
            expires_at=NOW,
            created_at=NOW,
        )
    )
    connection = c.db.connection()
    injected = Tenant(id=uuid4(), slug=uuid4().hex, name="Callback pending")
    calls = []

    def inject(conn, cursor, statement, parameters, context, executemany):
        # Real SQLAlchemy callback; do not record SQL or parameters. The next
        # statement after materialization is the primitive's detached inventory.
        calls.append(1)
        c.db.add(injected)

    with (
        pytest.raises(ConflictSavepointProtocolError, match="clean pending state"),
        conflict_savepoint(c.db),
    ):
        c.db.connection()
        event.listen(connection, "after_cursor_execute", inject, once=True)
        store.revoke_party_sessions(
            c.db, tenant_id=tid, party_id=pid, session_ids=(sid,), revoked_at=NOW
        )
    assert calls == [1]
    assert c.db.get_transaction() is c.root
    assert c.db.get(Party, pid) is not None
    assert (
        c.db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == sid)) is None
    )
    assert c.db.scalar(select(Tenant.id).where(Tenant.id == injected.id)) is None
    assert not c.db.new and not c.db.dirty and not c.db.deleted


def test_caught_post_dml_callback_cannot_release_partial_primitive_effect(storage_case):
    from dotmac_kernel.models import AuthSession
    from dotmac_kernel.transactions import ConflictSavepointProtocolError
    from sqlalchemy import event

    class InjectedFailure(Exception):
        pass

    c = storage_case
    tid = c.tenants[0]
    pid = _storage_principal(c, tid)
    sid = uuid4()
    c.db.execute(
        insert(AuthSession.__table__).values(
            id=sid,
            tenant_id=tid,
            party_id=pid,
            token_hash=uuid4().hex * 2,
            expires_at=NOW,
            created_at=NOW,
        )
    )
    connection = c.db.connection()
    invoked = []

    def fail_after_update(conn, cursor, statement, parameters, context, executemany):
        if context.isupdate and not invoked:
            invoked.append(1)
            raise InjectedFailure()

    with pytest.raises(
        ConflictSavepointProtocolError, match="requires savepoint rollback"
    ):
        with conflict_savepoint(c.db):
            c.db.connection()
            event.listen(connection, "after_cursor_execute", fail_after_update)
            try:
                try:
                    store.revoke_party_sessions(
                        c.db,
                        tenant_id=tid,
                        party_id=pid,
                        session_ids=(sid,),
                        revoked_at=NOW,
                    )
                except InjectedFailure:
                    pass  # caller cannot turn a failed issued phase into release
            finally:
                event.remove(connection, "after_cursor_execute", fail_after_update)
    assert invoked == [1]
    assert c.db.get_transaction() is c.root
    assert c.db.scalar(select(Party.id).where(Party.id == pid)) == pid
    assert (
        c.db.scalar(select(AuthSession.revoked_at).where(AuthSession.id == sid)) is None
    )
    assert not c.db.new and not c.db.dirty and not c.db.deleted
    with conflict_savepoint(c.db):
        c.db.connection()
    assert c.db.get_transaction() is c.root


def test_grant_cohort_duplicate_missing_foreign_scope_never_removes_subset(
    storage_case,
):
    c = storage_case
    tid, foreign = c.tenants
    pid = _storage_principal(c, tid)
    role = store.provision_missing_roles(
        c.db,
        tenant_id=tid,
        catalog=(),
        configured_names={"exact": "Exact"},
        missing_role_ids={"exact": uuid4()},
        created_at=NOW,
        updated_at=NOW,
    )[0]
    grant = store.ensure_grant(
        c.db,
        tenant_id=tid,
        party_id=pid,
        role_id=role.id,
        new_grant_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    cases = (
        (tid, pid, (grant, grant)),
        (tid, pid, (grant, store.GrantFact(uuid4(), tid, pid, role.id))),
        (foreign, pid, (store.GrantFact(grant.id, foreign, pid, role.id),)),
        (tid, uuid4(), (grant,)),
    )
    for tenant_id, party_id, expected in cases:
        with pytest.raises(ValueError), conflict_savepoint(c.db):
            c.db.connection()
            store.remove_grants(
                c.db, tenant_id=tenant_id, party_id=party_id, expected=expected
            )
        assert (
            c.db.scalar(select(PartyRoleGrant.id).where(PartyRoleGrant.id == grant.id))
            == grant.id
        )
    assert c.db.get_transaction() is c.root


def test_required_revocation_shapes_refuse_before_query_or_dml(storage_case):
    from dotmac_kernel.transactions import ConflictSavepointProtocolError
    from sqlalchemy import event

    c = storage_case
    tid = c.tenants[0]
    pid = _storage_principal(c, tid)
    connection = c.db.connection()
    calls = []

    def observe(*args):
        calls.append(1)

    with conflict_savepoint(c.db):
        c.db.connection()
        event.listen(connection, "before_cursor_execute", observe)
        try:
            for ids, at in (
                ((), NOW.replace(tzinfo=None)),
                ((uuid4(),) * 2, NOW),
                ([], NOW),
            ):
                with pytest.raises(ValueError):
                    store.revoke_party_sessions(
                        c.db,
                        tenant_id=tid,
                        party_id=pid,
                        session_ids=ids,
                        revoked_at=at,
                    )
            with pytest.raises(TypeError):
                store.revoke_party_sessions(
                    c.db, tenant_id=tid, party_id=pid, revoked_at=NOW
                )
            assert calls == []
        finally:
            event.remove(connection, "before_cursor_execute", observe)
    with pytest.raises(ConflictSavepointProtocolError, match="caller savepoint"):
        store.revoke_party_sessions(
            c.db, tenant_id=tid, party_id=pid, session_ids=(), revoked_at=NOW
        )
