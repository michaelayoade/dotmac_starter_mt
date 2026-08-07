"""Fast unit-test fixtures on in-memory SQLite.

RLS does not exist on SQLite — tenancy enforcement is covered by the Postgres
canaries in tests/test_cross_tenant_isolation.py. Unit tests exercise service
logic only and must scope queries explicitly where they care about tenancy.
"""

from __future__ import annotations

from collections.abc import Generator

# Installed MODULE models, for the same reason — and with one extra consequence:
# these are bound to `mod_tstudio`, and `create_test_engine` ATTACHes every
# schema it finds in `Base.metadata`, so the import must happen before the
# engine fixture runs or the qualified CREATE TABLE has nowhere to land.
import dotmac_template_studio as template_studio
import pytest

# DATABASE_URL is pinned to a hermetic placeholder in tests/conftest.py (the
# root conftest), which pytest imports before this module — see the comment
# there about import-time engine creation in dotmac_kernel.db.
from dotmac_kernel import (
    audit,  # noqa: F401
    models_platform,  # noqa: F401
    settings_models,  # noqa: F401
)
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.features import load_manifests
from dotmac_kernel.messaging import models as messaging_models  # noqa: F401
from dotmac_kernel.models import Party, PartyPerson, PartyType, Tenant
from dotmac_kernel.permissions import PermissionCatalogue, install_permissions
from dotmac_kernel.templating import install_surface_globals

# The in-memory engine + savepoint-isolated session are the kernel's supported
# test kit (`dotmac_kernel.testing`, kernel-boundary Task 5) — this assembly
# consumes it instead of hand-rolling the harness it used to define here.
from dotmac_kernel.testing import create_test_engine, isolated_session
from dotmac_template_studio import models as template_studio_models  # noqa: F401
from sqlalchemy.orm import Session

from app.features import FEATURE_MODULES

# Import feature model modules so Base.metadata is fully populated BEFORE
# create_test_engine() runs create_all (UserCredential moved to
# dotmac_kernel.models in control-plane security Task 2).
from app.features.custom_fields import models as custom_fields  # noqa: F401
from app.features.licensing import models as licensing_models  # noqa: F401


def _all_manifests():
    """Every manifest the reference assembly composes — its own features plus
    each installed module. The two declaration catalogues below are built from
    this, not from `FEATURE_MODULES` alone: a module's permissions and audit
    actions are as real as a feature's, and omitting them would make every
    `require_permission` guard 500 and every `write_audit_event` raise inside
    the unit lane while working perfectly in the real app.
    """
    return [*load_manifests(FEATURE_MODULES), template_studio.module]


@pytest.fixture(autouse=True)
def _default_declaration_catalogues():
    """Install the process-active permission catalogue + audit-action registry.

    Same reasoning as `_default_surface_globals` below, for the module
    control-plane step-3 declarations: `create_app` installs both, but a unit
    test that mounts a router on a bare `FastAPI()` never calls it — and both
    default to EMPTY (deny/reject everything), so without this every
    `require_permission` guard would 500 and every `write_audit_event` would
    raise. Re-installed before EVERY test so a previous test that built an app
    from a narrower module set (e.g. `tests/unit/test_create_app.py`) cannot
    leave a truncated catalogue behind.
    """
    manifests = _all_manifests()
    install_permissions(PermissionCatalogue.from_manifests(manifests))
    install_audit_actions(AuditActionRegistry.from_manifests(manifests))


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
    install_surface_globals(_all_manifests(), disabled=set(), web_enabled=True)


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
