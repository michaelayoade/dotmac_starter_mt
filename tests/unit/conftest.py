"""Fast unit-test fixtures on in-memory SQLite.

RLS does not exist on SQLite — tenancy enforcement is covered by the Postgres
canaries in tests/test_cross_tenant_isolation.py. Unit tests exercise service
logic only and must scope queries explicitly where they care about tenancy.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# DATABASE_URL is pinned to a hermetic placeholder in tests/conftest.py (the
# root conftest), which pytest imports before this module — see the comment
# there about import-time engine creation in app.core.db.
from app.core import (
    audit,  # noqa: F401
    settings_models,  # noqa: F401
)
from app.core.models import Base, Party, PartyPerson, PartyType, Tenant

# Import feature model modules so Base.metadata is fully populated.
from app.features.auth import models as auth  # noqa: F401
from app.features.custom_fields import models as custom_fields  # noqa: F401


@pytest.fixture(scope="session")
def unit_engine():
    # `check_same_thread=False`: FastAPI's TestClient (used by tests/unit/
    # test_settings_api.py to exercise the real guarded router) runs sync
    # route dependencies in a worker thread via `run_in_threadpool`, but the
    # `db` fixture below hands every test the SAME underlying connection
    # object, opened once on the pytest thread. Plain pysqlite refuses to let
    # a second thread touch a connection it didn't create; since our usage is
    # always sequential (one caller at a time, never concurrent), this is
    # safe to relax — same fix FastAPI's own testing docs recommend for
    # sqlite `:memory:` engines exercised via TestClient.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(unit_engine) -> Generator[Session, None, None]:
    connection = unit_engine.connect()
    outer = connection.begin()
    factory = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = factory()
    # Restart a savepoint whenever service code commits, so the outer
    # rollback still isolates the test.
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture()
def tenant_row(db: Session) -> Tenant:
    row = Tenant(slug="acme", name="Acme")
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def party_row(db: Session, tenant_row: Tenant) -> Party:
    """A person-type `Party` with its `PartyPerson` subtype row — the Task 6
    replacement for the old bare-`Person` fixture pattern.
    """
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Ada Lovelace",
        email="ada@example.com",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Ada", last_name="Lovelace"))
    db.flush()
    return party
