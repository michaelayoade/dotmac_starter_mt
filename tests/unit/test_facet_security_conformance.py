"""The facet security contract, proved per DECLARED facet rather than per path.

`tests/unit/test_admin_route_sweep.py` is the `staff_admin` actor journey and
says so; `tests/architecture/test_web_facet_contract.py` proves CSRF is attached
to composed unsafe routes.  Between them sit the claims the browser programme
actually rests on, several of which nothing checked:

1. **Authentication and admission are separate decisions.**  A facet's
   admission permission is enforced in `_tenant_context_dependency` only.  The
   non-tenant path (`BrowserSecurityPlane.PLATFORM`, `NONE`) never reads
   `facet.admission_permission`, so a facet that declares one there boots green
   and enforces nothing.  The reference assembly does not do this — its
   `platform_admin` facet passes `None` — which is precisely why it needs a
   gate: the hole is latent, and the next facet is the one that falls in.
2. **Every facet's mutations are guarded, not just `/admin`'s.**  The composed
   sweep below is derived from `assembly.web_facets`, so a second facet joins it
   by being declared rather than by someone remembering to widen a prefix.
3. **CSRF fails closed when its middleware or state is absent.**  A dependency
   that silently no-ops without its middleware is worse than no dependency: the
   route still LOOKS protected.
4. **A cookie-less pre-auth POST is still protected.**  This was a live defect —
   CSRF used to mean "the request carried some cookie", which a cross-site POST
   to `/admin/login` simply did not.
5. **A raw CSP override may only tighten.**
6. **Disabling the web surface leaves the JSON API intact — and the enabled
   case is non-vacuous**, so "no web routes" cannot pass for both answers.
7. **Named-route navigation cannot reference an absent route**, checked against
   the routes this exact assembly mounts.
8. **Request-scoped surface state does not leak** between facets or between
   requests.

Every check that can pass over an empty set asserts the set first.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

import pytest
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.app_factory import create_app
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.middleware.csrf import (
    CSRF_PROTECTED_ATTR,
    CSRFMiddleware,
    require_csrf,
)
from dotmac_kernel.middleware.security_headers import _STRICT_CSP
from dotmac_kernel.middleware.security_headers import (
    _validate_content_security_policy_override as validate_csp_override,
)
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.web_runtime import mount_web_surfaces
from dotmac_kernel.web_surfaces import (
    BrowserSecurityPlane,
    BrowserSecurityRequirement,
    NavigationRegion,
    TemplateRef,
    WebFacetMount,
    WebSurfaceContribution,
    WebSurfaceRegistry,
    qualified_route_name,
)
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.assembly import assembly
from app.main import app as production_app
from tests.architecture.test_route_guards import AUTH_GUARD_NAMES, MUTATING_ALLOWLIST

#: Test assemblies declare the development policy explicitly: the kernel
#: refuses to build without one, and a fallback would be the inherited
#: exposure `api_documentation` exists to end.
_DOCS_POLICY = api_documentation_policy("development")

_MUTATING: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The composed baseline the raw-override validator compares against.  Imported
#: rather than restated: `_STRICT_CSP` is COMPUTED from the asset inventory, so
#: a local copy would drift into asserting a policy the product does not ship.
#: Every case below therefore MUTATES the live baseline instead of describing it.
_BASELINE_CSP: Final[str] = _STRICT_CSP


def _composed_routes() -> list[APIRoute]:
    return [route for route in production_app.routes if isinstance(route, APIRoute)]


def _browser_routes() -> list[APIRoute]:
    return [route for route in _composed_routes() if route.name.startswith("web:")]


def _facet_of(route: APIRoute) -> str:
    return route.name.split(":")[1]


def _guard_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        if dependency.call is not None:
            names.add(getattr(dependency.call, "__name__", ""))
        stack.extend(dependency.dependencies)
    return names


def _has_attribute(dependant: object, attribute: str) -> bool:
    if getattr(getattr(dependant, "call", None), attribute, False):
        return True
    return any(
        _has_attribute(child, attribute)
        for child in getattr(dependant, "dependencies", ())
    )


# ---------------------------------------------------------------------------
# 1. Authentication and admission are enforced independently.
# ---------------------------------------------------------------------------


def test_admission_is_only_declared_where_the_runtime_can_enforce_it() -> None:
    """A declared admission permission must reach an enforcement path.

    `dotmac_kernel.web_runtime` evaluates `facet.admission_permission` inside
    `_tenant_context_dependency` and nowhere else. A facet bound to a PLATFORM
    or public profile that declares an admission permission therefore gets a
    green boot and zero enforcement — the declaration reads as a control and is
    not one, which is worse than declaring nothing.

    The reference assembly is clean today. This gate exists for the next facet,
    and for the downstream products now declaring their own.
    """
    profiles = {profile.code: profile for profile in assembly.authentication_profiles}
    facets = tuple(assembly.web_facets)
    assert facets, "no declared facets; this check is vacuous"

    unenforceable: list[str] = []
    for facet in facets:
        if facet.admission_permission is None:
            continue
        profile = profiles.get(facet.authentication_profile or "")
        if profile is None or profile.security_plane is not BrowserSecurityPlane.TENANT:
            plane = getattr(profile, "security_plane", "unbound")
            unenforceable.append(
                f"{facet.code} declares admission "
                f"{facet.admission_permission!r} on plane {plane}"
            )
    assert not unenforceable, (
        "facet(s) declare an admission permission the runtime never evaluates "
        "— only the TENANT security plane checks it:\n" + "\n".join(unenforceable)
    )


def test_at_least_one_facet_actually_declares_admission() -> None:
    """The check above passes trivially if nothing declares admission at all."""
    declaring = [
        facet.code
        for facet in assembly.web_facets
        if facet.admission_permission is not None
    ]
    assert declaring, (
        "no facet declares an admission permission, so the enforcement check "
        "above is passing over an empty set"
    )


def test_admission_and_authentication_are_distinct_declarations() -> None:
    """A facet cannot acquire admission by being authenticated.

    `WebFacetMount.__post_init__` refuses admission without authentication —
    the one-way implication. This pins the other direction: an authentication
    profile alone must never be read as admission, which is what the
    `platform_admin` facet demonstrates by declaring a profile and NO
    admission permission.
    """
    authenticated = [
        facet for facet in assembly.web_facets if facet.authentication_profile
    ]
    assert authenticated, "no authenticated facets declared; comparison is vacuous"
    assert any(facet.admission_permission is None for facet in authenticated), (
        "every authenticated facet also declares admission, so nothing here "
        "proves the two are independent declarations"
    )


# ---------------------------------------------------------------------------
# 2. The mutating-route sweep is derived from declarations, not from `/admin`.
# ---------------------------------------------------------------------------


def _facet_mutations() -> dict[str, list[APIRoute]]:
    grouped: dict[str, list[APIRoute]] = {
        facet.code: [] for facet in assembly.web_facets
    }
    for route in _browser_routes():
        if _MUTATING.intersection(route.methods or set()):
            grouped.setdefault(_facet_of(route), []).append(route)
    return grouped


def test_every_facet_mutation_carries_csrf_and_an_auth_tier_guard() -> None:
    """The `/admin` sweep, generalised to every declared facet.

    Enumeration comes from `assembly.web_facets`, so a product that adds a
    `customer_portal` facet is swept the moment it declares it — the failure
    mode ADR-0018 describes (a rule whose coverage silently narrows to one
    directory) cannot happen by omission here.

    Entry routes are excluded from the auth-tier requirement, not skipped: the
    assembly declared them reachable before admission, and
    `tests/architecture/test_facet_guard_exemptions.py` is what holds that
    declaration honest. They are NOT excluded from CSRF.
    """
    entry_names = {
        qualified_route_name(facet.code, ref.module, ref.surface, ref.route_name)
        for facet in assembly.web_facets
        for ref in facet.entry_routes
    }
    grouped = _facet_mutations()
    assert grouped, "no declared facets to sweep"

    swept = 0
    no_csrf: list[str] = []
    no_guard: list[str] = []
    for routes in grouped.values():
        for route in routes:
            swept += 1
            if not _has_attribute(route.dependant, CSRF_PROTECTED_ATTR):
                no_csrf.append(f"{sorted(route.methods or set())} {route.path}")
            if route.name in entry_names:
                continue
            if not (_guard_names(route) & AUTH_GUARD_NAMES):
                no_guard.append(f"{sorted(route.methods or set())} {route.path}")

    assert swept, "the facet-derived mutation sweep is vacuous"
    assert not no_csrf, "composed mutation(s) without CSRF: " + ", ".join(no_csrf)
    assert not no_guard, (
        "composed mutation(s) with no authentication-tier guard and no "
        "assembly entry-route declaration: " + ", ".join(no_guard)
    )


def test_the_facet_sweep_reaches_more_than_one_facet() -> None:
    """A sweep that only ever saw `/admin` would pass every assertion above."""
    covered = {code for code, routes in _facet_mutations().items() if routes}
    assert len(covered) >= 2, (
        "the mutation sweep covers fewer than two facets, so nothing here "
        f"distinguishes it from the old `/admin`-only sweep: {sorted(covered)}"
    )


def test_the_behavioural_admission_journey_covers_every_admitting_facet() -> None:
    """State the premise the composed 403 sweep depends on.

    `test_admin_route_sweep.py` drives real requests with a non-admitted cookie
    for `staff_admin` only, and its docstring says other facets need their own
    journey. That is a correct scope statement while `staff_admin` is the only
    facet declaring admission — and an unmonitored region the moment it is not.
    """
    admitting = {
        facet.code
        for facet in assembly.web_facets
        if facet.admission_permission is not None
    }
    covered = {"staff_admin"}
    uncovered = admitting - covered
    assert not uncovered, (
        "facet(s) declare an admission permission with no behavioural "
        "non-admitted actor journey. Add one (see "
        "tests/unit/test_admin_route_sweep.py) and record it here: "
        + ", ".join(sorted(uncovered))
    )


# ---------------------------------------------------------------------------
# 3. CSRF fails closed when its middleware or state is missing.
# ---------------------------------------------------------------------------


def _probe_app(*, csrf: bool | None) -> FastAPI:
    """A minimal app carrying one CSRF-protected mutation.

    `csrf=None` installs NO middleware at all — the "someone assembled an app
    without the security stack" case.
    """
    app = FastAPI()
    if csrf is not None:
        app.add_middleware(
            CSRFMiddleware,
            enabled=csrf,
            secret="conformance-probe-csrf-not-a-deployment-secret",
        )

    router = APIRouter()

    @router.post("/mutate", name="mutate", dependencies=[Depends(require_csrf)])
    def mutate() -> dict[str, bool]:
        return {"reached": True}

    app.include_router(router)
    return app


def test_csrf_fails_closed_when_its_middleware_is_absent() -> None:
    """A protected route with no CSRF middleware must refuse, never proceed.

    If `require_csrf` returned quietly when its state was missing, every route
    would still LOOK protected — the dependency is present, the test that
    counts dependencies passes — while enforcing nothing. Failing loudly is
    what makes a mis-assembled app impossible to ship rather than merely
    unlikely.
    """
    client = TestClient(_probe_app(csrf=None), raise_server_exceptions=False)
    response = client.post("/mutate")
    assert response.status_code >= 500, (
        "a CSRF-protected route reached its body with no CSRFMiddleware "
        f"installed (status {response.status_code})"
    )
    assert "reached" not in response.text


def test_csrf_fails_closed_when_middleware_state_is_incomplete() -> None:
    """Half-installed state is the same defect as no state at all."""

    # Drive the dependency directly against a request whose state carries the
    # enabled flag but not the signer — the shape a partial/monkeypatched
    # middleware would leave behind.
    class _State:
        csrf_enabled = True

    class _Request:
        state = _State()
        method = "POST"

    with pytest.raises(RuntimeError, match="validation state"):
        import asyncio

        asyncio.run(require_csrf(_Request()))  # type: ignore[arg-type]


def test_a_cookie_less_pre_auth_post_is_still_protected() -> None:
    """The live defect: CSRF that meant "carried some cookie".

    A cross-site POST to a pre-auth route arrives with no cookies at all. If
    absence of a cookie skipped validation, the most exposed routes in the
    product — login and logout — were the only unprotected ones.
    """
    client = TestClient(_probe_app(csrf=True), raise_server_exceptions=False)
    response = client.post("/mutate")
    assert response.status_code == 403, (
        f"a cookie-less unsafe request was not refused (status {response.status_code})"
    )
    assert "reached" not in response.text


def test_the_pre_auth_exemptions_are_still_csrf_protected() -> None:
    """Being exempt from AUTHENTICATION is not exemption from CSRF.

    `MUTATING_ALLOWLIST` waives the auth-tier guard for login/logout. Those are
    exactly the routes a cross-site POST targets, so each composed browser
    route among them must still carry the CSRF dependency.
    """
    by_key = {
        (method, route.path): route
        for route in _composed_routes()
        for method in route.methods or set()
    }
    checked = 0
    missing: list[str] = []
    for method, path in sorted(MUTATING_ALLOWLIST):
        route = by_key.get((method, path))
        if route is None or not route.name.startswith("web:"):
            continue
        checked += 1
        if not _has_attribute(route.dependant, CSRF_PROTECTED_ATTR):
            missing.append(f"{method} {path}")
    assert checked, "no composed browser routes in MUTATING_ALLOWLIST to check"
    assert not missing, (
        "authentication-exempt browser route(s) without CSRF protection: "
        + ", ".join(missing)
    )


# ---------------------------------------------------------------------------
# 5. A raw CSP override may only tighten.
# ---------------------------------------------------------------------------


def _directives() -> dict[str, list[str]]:
    """The live baseline, parsed — so every case below mutates what ships."""
    parsed: dict[str, list[str]] = {}
    for chunk in _BASELINE_CSP.split(";"):
        parts = chunk.split()
        if parts:
            parsed[parts[0]] = parts[1:]
    return parsed


def _render(directives: dict[str, list[str]]) -> str:
    return "; ".join(
        f"{name} {' '.join(sources)}" for name, sources in directives.items()
    )


def test_the_baseline_is_parseable_and_non_trivial() -> None:
    """Every override case below mutates this; an empty parse would pass all."""
    directives = _directives()
    assert len(directives) >= 5, f"baseline looks degenerate: {directives}"
    assert _render(directives) == _BASELINE_CSP, (
        "the parse/render round-trip does not reproduce the shipped baseline, "
        "so the mutations below are not mutations OF it"
    )


def test_a_raw_csp_override_cannot_replace_active_typed_requirements() -> None:
    """A capability's typed needs are not negotiable through raw policy.

    The closed `BrowserSecurityRequirement` vocabulary exists so an installed
    package cannot inject directives into the product policy. A raw override
    that could sit alongside an active requirement would be exactly that
    injection path, reopened from the other side.
    """
    with pytest.raises(ValueError, match="active typed browser-security"):
        validate_csp_override(_BASELINE_CSP, (BrowserSecurityRequirement.WORKER_SELF,))


def test_a_raw_csp_override_may_tighten_a_directive_to_none() -> None:
    """Tightening is the entire point of the seam, so prove it is permitted."""
    directives = _directives()
    widest = max(directives, key=lambda name: len(directives[name]))
    directives[widest] = ["'none'"]
    validate_csp_override(_render(directives), ())


def test_a_raw_csp_override_cannot_drop_a_baseline_directive() -> None:
    directives = _directives()
    directives.pop("object-src", None) or directives.pop(next(iter(directives)))
    with pytest.raises(ValueError, match="missing required directives"):
        validate_csp_override(_render(directives), ())


def test_a_raw_csp_override_cannot_widen_a_directive() -> None:
    directives = _directives()
    directives["script-src"] = [*directives["script-src"], "https://cdn.example"]
    with pytest.raises(ValueError, match="weaken the computed baseline"):
        validate_csp_override(_render(directives), ())


def test_a_raw_csp_override_cannot_add_an_untyped_directive() -> None:
    directives = _directives()
    assert "worker-src" not in directives, "pick a directive the baseline lacks"
    directives["worker-src"] = ["'self'"]
    with pytest.raises(ValueError, match="adds untyped directives"):
        validate_csp_override(_render(directives), ())


def test_the_unmodified_baseline_is_accepted() -> None:
    """The sensitivity floor: if the validator rejected everything, every
    `pytest.raises` above would pass for the wrong reason."""
    validate_csp_override(_BASELINE_CSP, ())


# ---------------------------------------------------------------------------
# 6. Disabling web leaves the API intact, and the enabled case is non-vacuous.
# ---------------------------------------------------------------------------


def test_the_enabled_web_surface_is_non_vacuous() -> None:
    """`WEB_ENABLED=false` dropping web routes proves nothing on its own.

    If the reference assembly mounted zero browser routes, the disabled
    assertion and the enabled assertion would both hold, and the switch would
    be untested in both positions.
    """
    browser = _browser_routes()
    assert browser, "the reference assembly composes no browser routes at all"
    assert len(browser) >= len(tuple(assembly.web_facets)), (
        "fewer composed browser routes than declared facets; at least one "
        "facet contributes nothing"
    )


def test_a_facet_with_no_surfaces_still_boots() -> None:
    """A declared facet nothing contributes to must not take the boot down.

    This is the `DISABLED_FEATURES=<owner>` shape: the facet stays declared
    while its contributing module is gone. Boot must survive and simply mount
    nothing under that prefix — the alternative is a feature switch that turns
    into an outage.
    """
    spec = ProductAssemblySpec(
        api_documentation=_DOCS_POLICY,
        name="empty-facet",
        modules=(ModuleManifest(code="nothing", version="1.0.0"),),
        platform_surface_enabled=False,
        ui_contract_version=1,
        web_facets=(
            WebFacetMount(
                code="customer",
                url_prefix="/portal",
                shell=TemplateRef("base.html"),
            ),
        ),
    )
    app = create_app(spec)
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/health" in paths
    assert not [path for path in paths if path.startswith("/portal")]


# ---------------------------------------------------------------------------
# 7. Named-route navigation cannot reference an absent route.
# ---------------------------------------------------------------------------


def test_every_declared_route_reference_resolves_in_this_assembly() -> None:
    """Shell, entry, login, landing, logout and nav, against mounted routes.

    The registry validates this at `create_app`, so a failure here is a boot
    failure. It is asserted separately because the registry's check and the
    mounted app can only agree by construction — and "by construction" is what
    stops being true when someone adds a second mounting path.
    """
    mounted = {route.name for route in _composed_routes()}
    references: list[tuple[str, str]] = []

    for facet in assembly.web_facets:
        refs = [
            *facet.entry_routes,
            *(
                ref
                for ref in (facet.login_route, facet.landing_route, facet.logout_route)
                if ref is not None
            ),
        ]
        for ref in refs:
            references.append(
                (
                    f"facet {facet.code}",
                    qualified_route_name(
                        facet.code, ref.module, ref.surface, ref.route_name
                    ),
                )
            )

    for manifest in assembly.modules:
        for surface in getattr(manifest, "web_surfaces", ()):
            for item in surface.navigation:
                references.append(
                    (
                        f"nav {item.code}",
                        qualified_route_name(
                            surface.facet,
                            manifest.code,
                            surface.code,
                            item.route_name,
                        ),
                    )
                )

    assert references, "no declared route references; this check is vacuous"
    assert any(label.startswith("nav ") for label, _ in references), (
        "no navigation items declared; the nav half of this check is vacuous"
    )

    dangling = [
        f"{label} -> {name}" for label, name in references if name not in mounted
    ]
    assert not dangling, (
        "declared route reference(s) name no mounted route:\n" + "\n".join(dangling)
    )


# ---------------------------------------------------------------------------
# 8. Request-scoped surface state does not leak.
# ---------------------------------------------------------------------------


def _two_facet_client() -> Iterator[TestClient]:
    """Two public facets in one process, each reporting the context it got.

    Built with `mount_web_surfaces` on a bare app rather than through
    `create_app`, for the same reason `tests/unit/test_admin_route_sweep.py`
    does: `create_app` installs `TenantResolverMiddleware`, which opens its own
    database connection outside dependency injection. Surface-state scoping is
    a request-lifecycle property and needs no tenant, so binding this to a
    database would only make the check harder to run, not stronger.

    Both facets are PUBLIC (no authentication profile) on purpose. Leakage is a
    property of the request scope itself; introducing authentication here would
    let a passing test be explained by the auth layer instead.
    """

    def _reporter(name: str) -> APIRouter:
        router = APIRouter()

        @router.get("/where", name="where")
        def where(request: Request) -> dict[str, str]:
            context = request.state.surface_context
            return {
                "surface": name,
                "facet": context.facet,
                "prefix": context.url_prefix,
                "shell": context.shell,
            }

        return router

    manifests = tuple(
        ModuleManifest(
            code=f"mod_{code}",
            version="1.0.0",
            web_surfaces=(
                WebSurfaceContribution(
                    code="pages",
                    facet=code,
                    routers=(_reporter(code),),
                    supported_ui_contract_versions=frozenset({1}),
                ),
            ),
        )
        for code in ("alpha", "beta")
    )
    registry = WebSurfaceRegistry(
        manifests=manifests,
        facets=(
            WebFacetMount(
                code="alpha",
                url_prefix="/alpha",
                shell=TemplateRef("base.html"),
                navigation_regions=(NavigationRegion("primary"),),
            ),
            WebFacetMount(
                code="beta",
                url_prefix="/beta",
                shell=TemplateRef("layouts/admin.html"),
                navigation_regions=(NavigationRegion("primary"),),
            ),
        ),
        authentication_profiles=(),
        browser_capabilities=(),
        ui_contract_version=1,
    )

    app = FastAPI()
    app.add_middleware(
        CSRFMiddleware,
        enabled=True,
        secret="surface-scope-probe-csrf-not-a-deployment-secret",
    )

    @app.get("/health", name="health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    mount_web_surfaces(
        app,
        registry=registry,
        enabled_modules=frozenset(manifest.code for manifest in manifests),
    )
    yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def two_facet_client() -> Iterator[TestClient]:
    yield from _two_facet_client()


def test_surface_state_is_per_request_and_per_facet(
    two_facet_client: TestClient,
) -> None:
    """Two facets in one process must never see each other's surface state.

    Surface state used to be installed as process-global Jinja state, which
    meant the LAST composed module decided what every request rendered. The
    contract is now request-scoped; this drives both facets and then returns to
    the first, so a value that persisted across requests is visible as the
    first facet reporting the second's.
    """
    first = two_facet_client.get("/alpha/where").json()
    second = two_facet_client.get("/beta/where").json()
    again = two_facet_client.get("/alpha/where").json()

    assert first["facet"] == "alpha" and first["prefix"] == "/alpha"
    assert second["facet"] == "beta" and second["prefix"] == "/beta"
    assert first["shell"] != second["shell"], (
        "both facets reported the same shell; the contexts are not distinct"
    )
    assert again == first, (
        "the third request saw state left over from the second — surface "
        f"context is leaking across requests: {again} != {first}"
    )


def test_a_non_surface_route_never_inherits_a_facet_context(
    two_facet_client: TestClient,
) -> None:
    """`/health` is not composed into any facet and must carry no facet state."""
    two_facet_client.get("/alpha/where")
    response = two_facet_client.get("/health")
    assert response.status_code == 200
    assert "alpha" not in response.text
