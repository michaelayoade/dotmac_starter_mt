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

# Same reason, for `mod_appdir`. The reference assembly does NOT compose
# `dotmac-application-directory` — its consumer is the Tenant Workspace, and the
# starter is a target application rather than a workspace (ADR-0021) — but the
# module's service tests run in this lane, so its models must be in
# `Base.metadata` before `create_test_engine` ATTACHes the schemas it finds.
# Deliberately NOT added to `_all_manifests()` below: that list is what the
# reference assembly composes, and this module is not part of it.
from dotmac_application_directory import (
    models as application_directory_models,  # noqa: F401
)

# DATABASE_URL is pinned to a hermetic placeholder in tests/conftest.py (the
# root conftest), which pytest imports before this module — see the comment
# there about import-time engine creation in dotmac_kernel.db.
from dotmac_kernel import (
    audit,  # noqa: F401
    flag_models,  # noqa: F401
    models_platform,  # noqa: F401
    settings_models,  # noqa: F401
)
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.cache import MemoryCache
from dotmac_kernel.capabilities import CapabilityCatalogue, install_capabilities
from dotmac_kernel.entitlements import grant_entitlement
from dotmac_kernel.features import load_manifests
from dotmac_kernel.flag_models import install_flag_cache
from dotmac_kernel.flags import FlagCatalogue, install_flags
from dotmac_kernel.messaging import models as messaging_models  # noqa: F401
from dotmac_kernel.models import Party, PartyPerson, PartyType, Tenant
from dotmac_kernel.permissions import PermissionCatalogue, install_permissions
from dotmac_kernel.setting_domains import (
    SettingDomainRegistry,
    install_setting_domains,
)
from dotmac_kernel.setting_scopes import ScopeKindRegistry, install_scope_kinds
from dotmac_kernel.setting_value_types import (
    SettingValueTypeRegistry,
    install_setting_value_types,
)
from dotmac_kernel.templating import compose_templates, install_surface_globals

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
    # Capabilities too (step 4). Re-installed per test for the same reason:
    # a test that installs a narrower probe catalogue must not leave it
    # behind for the next one.
    install_capabilities(CapabilityCatalogue.from_manifests(manifests))
    install_flags(FlagCatalogue.from_manifests(manifests))
    install_setting_domains(SettingDomainRegistry.from_manifests(manifests))
    install_setting_value_types(SettingValueTypeRegistry.from_manifests(manifests))
    install_scope_kinds(ScopeKindRegistry.from_manifests(manifests))
    # A fresh evaluation cache per test: the store is process-global, and a
    # leaked entry would make one test's overrides decide another's answer.
    install_flag_cache(MemoryCache())


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


@pytest.fixture(autouse=True)
def _reference_template_composition():
    """Compose the template loader the way the reference assembly does.

    Third instance of the same hazard as `_default_surface_globals` above, and
    it bit for real: `compose_templates` mutates the process-static loader
    behind `dotmac_kernel.templating.templates`, and `create_app` is the only
    thing that calls it. A unit test that mounts routers on a bare `FastAPI()`
    -- which most of the web tests do -- therefore rendered against
    KERNEL-ONLY templates while production rendered against the assembly's
    full composition.

    That gap was invisible until a kernel template imported a macro from an
    installed package (`dotmac_ui/components/empty_state.html`), at which point
    every `/admin` page carrying an empty state 500'd with `TemplateNotFound`
    in CI while passing every component-level test. The composition is part of
    the app under test, so the fixtures have to establish it.

    Uses `app.assembly`'s real tuple rather than a hand-listed one, so a
    package added to the assembly is automatically visible here and this cannot
    drift from what production loads.
    """
    from app.assembly import assembly

    compose_templates(
        assembly_dir=assembly.assembly_template_dir,
        packaged_dirs=assembly.packaged_template_dirs,
    )


@pytest.fixture(scope="session")
def unit_engine():
    # Importing an optional package only populates global metadata; it does not
    # compose that package. Name the two module schemas this shared fixture
    # actually exercises so unrelated architecture-test imports cannot consume
    # SQLite's ten-attachment limit or silently alter the unit assembly.
    engine = create_test_engine(
        module_schemas=("mod_appdir", "mod_tstudio"),
    )
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
    """The shared unit-test tenant — entitled to every declared capability.

    `require_capability` is deny-by-default, so a bare tenant would 403 on every
    gated route and each feature's tests would have to re-grant the same codes.
    Granting them here mirrors what migration `a004` does for every tenant that
    existed when enforcement landed: the DEFAULT test subject is an ordinary,
    fully-entitled tenant, and a test that cares about the un-entitled case
    builds its own (see `tests/unit/test_require_capability.py`).
    """
    row = Tenant(slug="acme", name="Acme")
    db.add(row)
    db.flush()
    catalogue = CapabilityCatalogue.from_manifests(_all_manifests())
    for code in sorted(catalogue.codes()):
        grant_entitlement(
            db,
            tenant_id=row.id,
            capability_code=code,
            catalogue=catalogue,
            source="test-fixture",
        )
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
