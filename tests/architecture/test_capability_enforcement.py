"""Capability declarations are owned, referenced only when declared, and used.

The capability counterpart of `test_manifest_declarations.py`, closing module
control-plane directive step 4. Three rules, each with a demonstrated failure
mode — a contract with no proven failure is an assertion, not a guarantee:

1. Every capability a mounted route REFERENCES is declared by an installed
   module (`create_app` fails the boot otherwise).
2. Every DECLARED capability has a real consumer. A code nobody enforces is
   either a forgotten gate or dead vocabulary, and both rot quietly.
3. A capability is enforced on BOTH surfaces of the module that owns it — the
   JSON API and the admin portal. Gating one and not the other is the shape the
   directive names as a defect: "a tenant module can be accessed without
   require_capability".
"""

from __future__ import annotations

import pytest
from dotmac_kernel.app_factory import _referenced_capabilities
from dotmac_kernel.capabilities import (
    CapabilityCatalogue,
    UndeclaredCapabilityError,
    active_capabilities,
)
from dotmac_kernel.deps import require_capability
from fastapi import Depends, FastAPI

from app.assembly import assembly
from app.main import app as production_app


def _declared() -> CapabilityCatalogue:
    return CapabilityCatalogue.from_manifests(assembly.modules)


def test_the_assembly_declares_capabilities_at_all() -> None:
    """Assert on the set walked: every rule below is vacuous without one."""
    assert _declared().codes(), (
        "no installed module declares a capability — the checks below would "
        "pass over nothing"
    )


def test_every_referenced_capability_is_declared() -> None:
    catalogue = _declared()
    undeclared = [
        (label, code)
        for label, code in _referenced_capabilities(production_app)
        if not catalogue.is_declared(code)
    ]
    assert not undeclared, f"routes reference undeclared capabilities: {undeclared}"


def test_every_declared_capability_is_enforced_somewhere() -> None:
    """No orphan declarations.

    The mirror of the no-orphan-settings and declared-permission-has-a-consumer
    rules. A capability that nothing enforces is indistinguishable from one
    someone forgot to wire, and the entitlement grants referencing it would
    imply a gate that does not exist.
    """
    declared = _declared().codes()
    referenced = {code for _, code in _referenced_capabilities(production_app)}
    orphans = sorted(declared - referenced)
    assert not orphans, (
        f"capability code(s) declared but enforced by no mounted route: {orphans} "
        "— wire a `require_capability(...)` guard, or drop the declaration until "
        "there is something to gate"
    )


def test_each_capability_gates_both_of_its_module_surfaces() -> None:
    """A module's JSON API and its admin portal answer to the same entitlement.

    Checked per owning module rather than globally: a module with only one
    surface is fine, but a module with two must not gate only one of them.
    """
    catalogue = _declared()
    by_path = _referenced_capabilities(production_app)
    for code in sorted(catalogue.codes()):
        owner = catalogue.owner(code)
        paths = [label for label, referenced in by_path if referenced == code]
        admin = [p for p in paths if " /admin" in p]
        api = [p for p in paths if " /admin" not in p]
        manifest = next(m for m in assembly.modules if m.name == owner)
        if manifest.web_routers:
            assert admin, (
                f"{owner!r} ships web routers but no /admin route is gated by "
                f"{code!r} — the portal would reach the feature an un-entitled "
                "tenant does not have"
            )
        if manifest.routers:
            assert api, (
                f"{owner!r} ships JSON routers but no API route is gated by "
                f"{code!r}"
            )


# ── Sensitivity proofs ──────────────────────────────────────────────────────


def test_an_undeclared_capability_fails_the_boot() -> None:
    """The guard's undeclared-code failure mode, shown RED.

    Without this, the "referenced ⊆ declared" rule above could be satisfied by a
    validator that never actually rejects anything.
    """
    from dotmac_kernel.app_factory import _validate_referenced_capabilities

    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(require_capability("ghost.use"))])
    def _probe() -> dict[str, str]:
        return {}

    with pytest.raises(UndeclaredCapabilityError, match="ghost.use"):
        _validate_referenced_capabilities(app, _declared())


def test_the_reference_walker_finds_a_stamped_code() -> None:
    """`_referenced_capabilities` must actually read the stamp back.

    A walker that silently found nothing would make every rule above vacuous —
    the same failure class as a glob that matches no files.
    """
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(require_capability("probe.use"))])
    def _probe() -> dict[str, str]:
        return {}

    assert ("GET /probe", "probe.use") in _referenced_capabilities(app)


def test_the_installed_catalogue_is_not_empty_in_the_real_app() -> None:
    """Importing `app.main` runs `create_app`, which installs the catalogue.

    The default is empty-means-deny, so an app that never installed one would
    403 every gated route for every tenant — fail-closed, but a wiring bug that
    must not reach a deployment.
    """
    assert active_capabilities().codes() == _declared().codes()


# ── Provisioning defaults ───────────────────────────────────────────────────


def test_every_declared_capability_states_its_provisioning_default() -> None:
    """A newly provisioned tenant's entitlements are a DECLARATION, not a guess.

    Every declared code must resolve to a spec, so `provision_tenant` has an
    unambiguous answer for it. A code that only ever existed as a bare string
    still resolves — to `default_granted=True`, the behaviour it had before
    enforcement — but the resolution must never fail.
    """
    catalogue = _declared()
    for code in sorted(catalogue.codes()):
        assert catalogue.spec(code).code == code


def test_the_reference_assembly_provisions_its_bundled_capabilities() -> None:
    """The reference assembly bundles its modules rather than selling them, so a
    new tenant gets every declared capability. A product that sells one flips
    `default_granted` on that spec — this test then documents the change rather
    than failing it, because it asserts the DEFAULTS match the DECLARATIONS.
    """
    catalogue = _declared()
    expected = tuple(
        sorted(c for c in catalogue.codes() if catalogue.spec(c).default_granted)
    )
    assert catalogue.default_granted_codes() == expected
