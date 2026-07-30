"""Fast unit-test fixtures on in-memory SQLite.

RLS does not exist on SQLite — tenancy enforcement is covered by the Postgres
canaries in tests/test_cross_tenant_isolation.py. Unit tests exercise service
logic only and must scope queries explicitly where they care about tenancy.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

# DATABASE_URL is pinned to a hermetic placeholder in tests/conftest.py (the
# root conftest), which pytest imports before this module — see the comment
# there about import-time engine creation in dotmac_kernel.db.
from dotmac_kernel import (
    audit,  # noqa: F401
    models_platform,  # noqa: F401
    settings_models,  # noqa: F401
)
from dotmac_kernel.features import load_manifests
from dotmac_kernel.messaging import models as messaging_models  # noqa: F401
from dotmac_kernel.models import Party, PartyPerson, PartyType, Tenant
from dotmac_kernel.templating import install_surface_globals

# The in-memory engine + savepoint-isolated session are the kernel's supported
# test kit (`dotmac_kernel.testing`, kernel-boundary Task 5) — this assembly
# consumes it instead of hand-rolling the harness it used to define here.
from dotmac_kernel.testing import create_test_engine, isolated_session
from sqlalchemy.orm import Session

from app.features import FEATURE_MODULES

# Import feature model modules so Base.metadata is fully populated BEFORE
# create_test_engine() runs create_all (UserCredential moved to
# dotmac_kernel.models in control-plane security Task 2).
from app.features.custom_fields import models as custom_fields  # noqa: F401


@pytest.fixture(autouse=True)
def _default_surface_globals():
    """`install_surface_globals` sets the process-static `enabled_features`/
    `nav_items` Jinja globals (dotmac_kernel.templating) — normally a side effect
    of importing `app.main`. A unit test that builds its own throwaway app
    (bypassing app.main entirely) still renders real templates through the
    shared `dotmac_kernel.templating.templates` singleton, so those globals must
    default to "every feature enabled" deterministically here, regardless of
    import/test order or a previous test's disabled-feature override. Tests
    proving F1/F5 behavior call `install_surface_globals` again themselves,
    inside the test body, to set a different (disabled) state — this
    autouse fixture only establishes the baseline before each test runs.
    """
    install_surface_globals(
        load_manifests(FEATURE_MODULES), disabled=set(), web_enabled=True
    )


@pytest.fixture(scope="session")
def unit_engine():
    # The kit's create_test_engine() builds the in-memory SQLite engine and
    # runs create_all over the (now fully imported) Base.metadata — same engine
    # this conftest used to hand-build, including the check_same_thread=False
    # relaxation TestClient needs. See dotmac_kernel.testing.harness.
    engine = create_test_engine()
    yield engine
    engine.dispose()


@pytest.fixture()
def db(unit_engine) -> Generator[Session, None, None]:
    # Savepoint-isolated session from the kit — a test is fully rolled back even
    # if service code commits (dotmac_kernel.testing.harness.isolated_session).
    with isolated_session(unit_engine) as session:
        yield session


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
