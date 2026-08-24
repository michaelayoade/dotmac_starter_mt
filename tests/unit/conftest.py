"""Fast unit-test fixtures on in-memory SQLite.

RLS does not exist on SQLite — tenancy enforcement is covered by the Postgres
canaries in tests/test_cross_tenant_isolation.py. Unit tests exercise service
logic only and must scope queries explicitly where they care about tenancy.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import date

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

# For the capability vocabulary the ADR-0024 § 10.4 payload gates resolve
# against — see `_installed_capability_registry` at the foot of this file.
# Unlike the two module-model imports above, this one adds no schema to the
# shared engine: `dotmac_integration`'s tables live in `mod_intg`, which is not
# in `_SHARED_TEST_SCHEMAS`, so `_unit_tables()` filters them out and the
# ten-attachment budget is untouched.
from dotmac_integration import (
    CapabilityContract,
    CapabilityOwner,
    CapabilityRegistry,
    SchemaGrace,
    install_capability_registry,
)
from dotmac_integration.capability_registry import _reset_capability_registry

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
from dotmac_kernel.models import Base, Party, PartyPerson, PartyType, Tenant
from dotmac_kernel.outbox_event_types import (
    OutboxEventTypeRegistry,
    install_outbox_event_types,
)
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

# Test collection imports models from every package whose independent contract
# is inspected in this repository. That does not compose those packages into
# the reference assembly. The shared fixture needs every host/public table
# collection discovers, plus the two module schemas whose service tests
# deliberately consume this fixture. Independent packages build their own
# narrow engines. This semantic selection neither invents composition nor
# crosses SQLite's ten-attachment cap as the package inventory grows.
_SHARED_TEST_SCHEMAS = {"mod_appdir", "mod_tstudio"}


def _unit_tables():
    return tuple(
        table
        for table in Base.metadata.tables.values()
        if table.schema is None or table.schema in _SHARED_TEST_SCHEMAS
    )


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
    install_outbox_event_types(OutboxEventTypeRegistry.from_manifests(manifests))
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
    # The kit's create_test_engine() builds the in-memory SQLite engine over the
    # explicit shared-test surface above — same engine this conftest used to
    # hand-build, including the check_same_thread=False relaxation TestClient
    # needs. See dotmac_kernel.testing.harness.
    engine = create_test_engine(tables=_unit_tables())
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


# ── The declared capability vocabulary these tests dispatch against ────────
#
# ADR-0024 § 10.4 puts a payload gate on `enqueue_delivery`, `dispatch.settle`
# and `ingress.record_batch`. Each resolves the capability of the work in front
# of it and asks the INSTALLED registry what that capability's contract says —
# and `capability_registry()` fails closed when an assembly installed none,
# which is deliberate: a runtime that dispatches without declaring what its
# payloads mean is misconfigured, not permissive.
#
# So the unit lane installs one, exactly as a composing assembly would. Every id
# here is SYNTHETIC — none names a fleet capability, which is the same property
# `test_capability_ownership.py` asserts of the module itself — and every
# contract is in an explicit `SchemaGrace`, because these fixtures exercise
# ledger mechanics rather than payload shape. The payload gate's own fixtures
# publish real schemas; they build their registries locally and pass them
# explicitly, so nothing here can make one of them pass by accident.
#
# A test that binds an id NOT listed here fails with `UnknownCapabilityError`
# naming the declared set, which is the correct answer and points at this list.
_TEST_CAPABILITY_IDS: tuple[str, ...] = (
    "alpha_domain.emit.v1",
    "alpha_domain.receive.v1",
    "alpha_domain.receive.v2",
    "alpha_domain.receive.v3",
    "alpha_domain.receive.v12",
    "beta_domain.receive.v1",
    "conformance.echo.v1",
    "conformance.other.v1",
    "conformance.second.v1",
    "conformance.two.v1",
    "example.observe.v1",
    "message.observation.v1",
    "messaging.receive.v1",
    "messaging.receive.v2",
    "messaging.send.v1",
    "messaging.templates.read.v1",
    "payments.settlement.observation.v1",
    "ticket.observation.v1",
)


def _test_capability_registry() -> CapabilityRegistry:
    grace = SchemaGrace(
        reason=(
            "a synthetic test capability with no owning domain to publish a "
            "payload contract"
        ),
        retire_after=date(2099, 12, 31),
        tracked_by="tests/unit/conftest.py",
    )
    return CapabilityRegistry.from_declarations(
        CapabilityContract(
            capability_id=capability_id,
            owner=CapabilityOwner(application="testlab", module="fixtures"),
            summary="a synthetic contract installed by the unit-test lane",
            schema_grace=grace,
        )
        for capability_id in _TEST_CAPABILITY_IDS
    )


@pytest.fixture(autouse=True)
def _installed_capability_registry() -> Iterator[None]:
    """Install the vocabulary, and REMOVE it again.

    The teardown is load-bearing. `install_capability_registry` holds a module
    global, so a fixture that only installed would leave every later test — in
    any file, in any lane — running against a vocabulary it never asked for, and
    `test_capability_ownership.py::test_importing_the_package_declares_nothing`
    would stop being able to observe the uninstalled state it exists to check.
    """
    install_capability_registry(_test_capability_registry())
    yield
    _reset_capability_registry()
