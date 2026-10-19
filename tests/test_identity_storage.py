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
