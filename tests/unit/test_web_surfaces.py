"""Canaries for the interactive-browser composition contract (ADR-0006)."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.deps import get_db
from dotmac_kernel.models import Party
from dotmac_kernel.modules import ModuleManifest, ModuleRegistryError
from dotmac_kernel.permissions import PermissionSpec
from dotmac_kernel.web_deps import WebAuthRedirect
from dotmac_kernel.web_surfaces import (
    AuthenticationProfileBinding,
    BrowserCapabilityProvision,
    BrowserCapabilityRequirement,
    BrowserCredentialTransport,
    BrowserSecurityPlane,
    BrowserSecurityRequirement,
    BrowserSessionPolicy,
    DuplicateFacetError,
    LocalizedText,
    NavigationRegion,
    TemplatePackage,
    TemplateRef,
    UIContractCompatibilityError,
    UnknownBrowserCapabilityError,
    UnknownFacetError,
    WebFacetMount,
    WebNavItem,
    WebRouteRef,
    WebSurfaceContribution,
    WebSurfaceError,
    WebSurfaceRegistry,
    qualified_route_name,
)
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient


class _DenyingProvider:
    transport = BrowserCredentialTransport.COOKIE_SESSION

    @staticmethod
    def dependency(request: Request) -> None:
        raise WebAuthRedirect(next_url=request.url.path)


def _facet(
    *,
    entry_routes: tuple[WebRouteRef, ...] = (),
    landing_route: WebRouteRef | None = None,
    logout_route: WebRouteRef | None = None,
    admission_permission: str | None = None,
) -> WebFacetMount:
    return WebFacetMount(
        code="staff_admin",
        url_prefix="/staff",
        shell=TemplateRef("layouts/admin.html"),
        authentication_profile="staff_session",
        admission_permission=admission_permission,
        navigation_regions=(NavigationRegion("primary"),),
        entry_routes=entry_routes,
        login_route=entry_routes[0] if entry_routes else None,
        landing_route=landing_route,
        logout_route=logout_route,
    )


def _profile() -> AuthenticationProfileBinding:
    return AuthenticationProfileBinding(
        code="staff_session",
        provider=_DenyingProvider(),
        session=BrowserSessionPolicy(cookie_name="access_token"),
    )


def _surface(router: APIRouter, **overrides) -> WebSurfaceContribution:
    values = {
        "code": "console",
        "facet": "staff_admin",
        "routers": (router,),
        "supported_ui_contract_versions": frozenset({1}),
    }
    values.update(overrides)
    return WebSurfaceContribution(**values)


def _registry(
    manifest: ModuleManifest,
    *,
    facet: WebFacetMount | None = None,
    ui_contract_version: int = 1,
    browser_capabilities: tuple[BrowserCapabilityProvision, ...] = (),
) -> WebSurfaceRegistry:
    return WebSurfaceRegistry(
        manifests=(manifest,),
        facets=(facet or _facet(),),
        authentication_profiles=(_profile(),),
        browser_capabilities=browser_capabilities,
        ui_contract_version=ui_contract_version,
    )


def test_unnamed_legacy_web_shape_infers_contract_v1() -> None:
    router = APIRouter()

    manifest = ModuleManifest(
        code="legacy-without-version",
        version="1.0.0",
        web_routers=(router,),
    )

    assert manifest.contract_version == 1


def test_explicit_module_contract_v2_refuses_the_legacy_web_shape() -> None:
    router = APIRouter()

    with pytest.raises(ModuleRegistryError, match="contract 2.*web_surfaces"):
        ModuleManifest(
            code="legacy-on-v2",
            version="1.0.0",
            contract_version=2,
            web_routers=(router,),
        )


def test_v1_legacy_web_shape_remains_adaptable_during_the_window() -> None:
    router = APIRouter(prefix="/admin")

    @router.get("/legacy")
    def legacy() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="legacy",
        version="1.0.0",
        contract_version=1,
        web_routers=(router,),
    )
    registry = _registry(
        manifest,
        facet=_facet(admission_permission="legacy.portal.access"),
    )

    surface = registry.surfaces[0]
    assert surface.legacy is True
    assert surface.owner == "legacy"
    assert surface.contribution.facet == "staff_admin"


def test_legacy_web_shape_requires_an_explicit_staff_facet() -> None:
    router = APIRouter(prefix="/admin")

    @router.get("/legacy")
    def legacy() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="legacy",
        version="1.0.0",
        web_routers=(router,),
    )

    with pytest.raises(UnknownFacetError, match="explicit.*staff_admin"):
        WebSurfaceRegistry(manifests=(manifest,), ui_contract_version=1)


@pytest.mark.parametrize(
    ("authentication_profile", "admission_permission", "match"),
    (
        # Refused when the MOUNT is constructed: a facet that admits on a
        # permission but names no authentication profile has no established
        # party to evaluate that permission against.
        (None, "legacy.portal.access", "admission without authentication"),
        # Refused by the REGISTRY: authenticating is not admitting, and the v1
        # compatibility adapter never infers an authorization policy.
        ("staff_session", None, "authentication profile and an admission"),
    ),
)
def test_legacy_staff_facet_requires_authentication_and_admission(
    authentication_profile: str | None,
    admission_permission: str | None,
    match: str,
) -> None:
    router = APIRouter(prefix="/admin")

    @router.get("/legacy")
    def legacy() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="legacy",
        version="1.0.0",
        web_routers=(router,),
    )
    # The mount is built INSIDE the raises block on purpose: one of these two
    # under-secured shapes is refused by `WebFacetMount.__post_init__` and the
    # other by the registry, so hoisting the construction out would let the
    # first case raise before `pytest.raises` was watching.
    with pytest.raises(WebSurfaceError, match=match):
        WebSurfaceRegistry(
            manifests=(manifest,),
            facets=(
                WebFacetMount(
                    code="staff_admin",
                    url_prefix="/admin",
                    shell=TemplateRef("layouts/admin.html"),
                    authentication_profile=authentication_profile,
                    admission_permission=admission_permission,
                    navigation_regions=(NavigationRegion("primary"),),
                ),
            ),
            authentication_profiles=(_profile(),),
            ui_contract_version=1,
        )


def test_a_module_cannot_mix_legacy_and_v2_web_declarations() -> None:
    router = APIRouter()
    with pytest.raises(ModuleRegistryError, match="both legacy and v2"):
        ModuleManifest(
            code="mixed",
            version="1.0.0",
            contract_version=1,
            web_routers=(router,),
            web_surfaces=(_surface(router),),
        )


def test_contract_v1_cannot_claim_the_v2_web_shape() -> None:
    router = APIRouter()
    with pytest.raises(ModuleRegistryError, match="requires contract 2"):
        ModuleManifest(
            code="v2-on-v1",
            version="1.0.0",
            contract_version=1,
            web_surfaces=(_surface(router),),
        )


def test_unknown_facet_fails_before_mounting() -> None:
    router = APIRouter(prefix="/things")

    @router.get("", name="index")
    def index() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="things",
        version="1.0.0",
        web_surfaces=(_surface(router, facet="missing"),),
    )

    with pytest.raises(UnknownFacetError, match="missing"):
        _registry(manifest)


def test_selected_ui_contract_must_be_supported_by_every_surface() -> None:
    router = APIRouter(prefix="/things")

    @router.get("", name="index")
    def index() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="things",
        version="1.0.0",
        web_surfaces=(_surface(router),),
    )

    with pytest.raises(UIContractCompatibilityError, match="contract 2"):
        _registry(manifest, ui_contract_version=2)


def test_browser_capability_requirement_needs_one_compatible_provider() -> None:
    router = APIRouter(prefix="/things")

    @router.get("", name="index")
    def index() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="things",
        version="1.0.0",
        web_surfaces=(
            _surface(
                router,
                browser_capabilities=(BrowserCapabilityRequirement("htmx", 2),),
            ),
        ),
    )

    with pytest.raises(UnknownBrowserCapabilityError, match="htmx"):
        _registry(manifest)

    registry = _registry(
        manifest,
        browser_capabilities=(BrowserCapabilityProvision("htmx", 2),),
    )
    assert registry.browser_capability("htmx").contract_version == 2


def test_only_required_browser_capabilities_contribute_typed_security_needs() -> None:
    router = APIRouter(prefix="/things")

    @router.get("", name="index")
    def index() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="things",
        version="1.0.0",
        web_surfaces=(
            _surface(
                router,
                browser_capabilities=(BrowserCapabilityRequirement("worker", 1),),
            ),
        ),
    )
    registry = _registry(
        manifest,
        browser_capabilities=(
            BrowserCapabilityProvision(
                "worker",
                1,
                frozenset(
                    {
                        BrowserSecurityRequirement.WORKER_SELF,
                        BrowserSecurityRequirement.WORKER_BLOB,
                    }
                ),
            ),
            BrowserCapabilityProvision(
                "unused_media",
                1,
                frozenset({BrowserSecurityRequirement.MEDIA_BLOB}),
            ),
        ),
    )

    assert registry.browser_security_requirements == frozenset(
        {
            BrowserSecurityRequirement.WORKER_SELF,
            BrowserSecurityRequirement.WORKER_BLOB,
        }
    )


def test_browser_capability_rejects_unknown_security_mechanics() -> None:
    with pytest.raises(WebSurfaceError, match="unknown browser security"):
        BrowserCapabilityProvision("unsafe", 1, frozenset({"unsafe_eval"}))  # type: ignore[arg-type]


def test_overlapping_facet_prefixes_are_rejected_but_codes_remain_open() -> None:
    second = WebFacetMount(
        code="wholesale",
        url_prefix="/staff/wholesale",
        shell=TemplateRef("layouts/admin.html"),
        authentication_profile="staff_session",
    )

    with pytest.raises(DuplicateFacetError, match="overlapping"):
        WebSurfaceRegistry(
            manifests=(),
            facets=(_facet(), second),
            authentication_profiles=(_profile(),),
            ui_contract_version=1,
        )


def test_session_cookie_path_must_cover_the_facet_prefix() -> None:
    narrow = AuthenticationProfileBinding(
        code="narrow_session",
        provider=_DenyingProvider(),
        session=BrowserSessionPolicy(
            cookie_name="narrow_access_token", cookie_path="/other"
        ),
    )
    facet = WebFacetMount(
        code="staff_admin",
        url_prefix="/staff",
        shell=TemplateRef("layouts/admin.html"),
        authentication_profile="narrow_session",
    )

    with pytest.raises(ValueError, match="does not cover facet"):
        WebSurfaceRegistry(
            manifests=(),
            facets=(facet,),
            authentication_profiles=(narrow,),
            ui_contract_version=1,
        )


def test_v2_surface_cannot_author_the_assembly_facet_prefix() -> None:
    router = APIRouter(prefix="/staff/things")

    @router.get("", name="index")
    def index() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="things",
        version="1.0.0",
        web_surfaces=(_surface(router),),
    )

    with pytest.raises(ValueError, match="facet-relative"):
        _registry(manifest)


def test_parameter_renaming_cannot_hide_a_route_collision() -> None:
    first = APIRouter(prefix="/things")
    second = APIRouter(prefix="/things")

    @first.get("/{thing_id}", name="by_id")
    def by_id(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    @second.get("/{slug}", name="by_slug")
    def by_slug(slug: str) -> dict[str, str]:
        return {"slug": slug}

    manifest = ModuleManifest(
        code="things",
        version="1.0.0",
        web_surfaces=(
            _surface(first, code="first"),
            _surface(second, code="second"),
        ),
    )

    with pytest.raises(ValueError, match="is claimed"):
        _registry(manifest)


def test_two_profiles_cannot_accidentally_share_one_session_cookie() -> None:
    other = AuthenticationProfileBinding(
        code="other_session",
        provider=_DenyingProvider(),
        session=BrowserSessionPolicy(cookie_name="access_token"),
    )

    with pytest.raises(ValueError, match="share cookie"):
        WebSurfaceRegistry(
            manifests=(),
            authentication_profiles=(_profile(), other),
        )


def test_template_package_is_namespaced_and_must_exist(tmp_path: Path) -> None:
    router = APIRouter(prefix="/things")

    @router.get("", name="index")
    def index() -> dict[str, bool]:
        return {"ok": True}

    missing = tmp_path / "missing"
    manifest = ModuleManifest(
        code="things",
        version="1.0.0",
        web_surfaces=(
            _surface(
                router,
                templates=TemplatePackage(namespace="things", root=missing),
            ),
        ),
    )

    with pytest.raises(ValueError, match="does not exist"):
        _registry(manifest)


def test_navigation_references_one_named_parameterless_get_route() -> None:
    router = APIRouter(prefix="/things")

    @router.get("", name="index")
    def index() -> dict[str, bool]:
        return {"ok": True}

    nav = WebNavItem(
        code="things",
        region="primary",
        label=LocalizedText("things.nav", "Things"),
        route_name="index",
    )
    manifest = ModuleManifest(
        code="things",
        version="1.0.0",
        web_surfaces=(_surface(router, navigation=(nav,)),),
    )
    registry = _registry(manifest)

    item = registry.surfaces[0].navigation[0]
    assert item.route_name == qualified_route_name(
        "staff_admin", "things", "console", "index"
    )


def test_entry_route_is_assembly_approved_while_sibling_route_uses_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `TenantResolverMiddleware` short-circuits only `/health` and `/static`,
    # so every other path resolves a tenant against the DATABASE — and in the
    # unit tier `DATABASE_URL` is a hermetic placeholder with nothing behind it
    # (tests/conftest.py). This test is about the SURFACE CONTEXT a facet
    # builds, not about tenancy, so resolution is stubbed rather than backed by
    # a real engine; the isolation canaries under tests/ own the tenancy
    # question against a migrated Postgres.
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="facet-test")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, t: t)

    router = APIRouter(prefix="/session")

    @router.get("/login", name="login")
    def login(request: Request) -> dict[str, str | None]:
        context = request.state.surface_context
        return {
            "facet": context.facet,
            "login": context.login_path,
            "landing": context.landing_path,
        }

    @router.get("/home", name="home")
    def home() -> dict[str, bool]:
        return {"ok": True}

    surface = _surface(router)
    manifest = ModuleManifest(
        code="identity",
        version="1.0.0",
        web_surfaces=(surface,),
    )
    entry = WebRouteRef("identity", "console", "login")
    landing = WebRouteRef("identity", "console", "home")
    spec = ProductAssemblySpec(
        name="facet-test",
        modules=(manifest,),
        web_facets=(_facet(entry_routes=(entry,), landing_route=landing),),
        authentication_profiles=(_profile(),),
        ui_contract_version=1,
    )

    from dotmac_kernel import create_app

    app = create_app(spec)
    with TestClient(app) as client:
        assert client.get("/staff/session/login").json() == {
            "facet": "staff_admin",
            "login": "/staff/session/login",
            "landing": "/staff/session/home",
        }
        denied = client.get("/staff/session/home", follow_redirects=False)
        assert denied.status_code == 302
        assert denied.headers["location"] == (
            "/staff/session/login?next=%2Fstaff%2Fsession%2Fhome"
        )


def test_login_route_must_be_a_parameterless_get_entry() -> None:
    router = APIRouter(prefix="/session")

    @router.post("/login", name="login")
    def login() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="identity",
        version="1.0.0",
        web_surfaces=(_surface(router),),
    )
    login_ref = WebRouteRef("identity", "console", "login")

    with pytest.raises(ValueError, match="parameterless GET"):
        _registry(manifest, facet=_facet(entry_routes=(login_ref,)))


# ── admission is a TENANT-plane decision (ADR-0006, post-a97 repair) ─────────
#
# `admission_permission` is evaluated as `authorize_party(db, tenant, party,
# code)`. That call needs a tenant-scoped `Party`. A PLATFORM profile resolves
# a `PlatformAdmin`, and a public/NONE profile resolves no principal at all —
# so on either plane there is nothing the permission could be checked against,
# and the runtime's non-tenant context dependency simply never consults it.
#
# The declaration therefore READ as an access control while enforcing nothing.
# That is the worst shape a security control can take: a reviewer sees the
# permission on the facet, the permission catalogue confirms it is declared,
# and every request is admitted. The binding is invalid, so the registry now
# refuses it at startup rather than letting it look enforced.


class _PlatformProvider:
    transport = BrowserCredentialTransport.COOKIE_SESSION

    @staticmethod
    def dependency() -> object:
        return SimpleNamespace(id=uuid.uuid4(), email="ops@example.test")


def _platform_profile(
    code: str = "platform_session",
) -> AuthenticationProfileBinding:
    return AuthenticationProfileBinding(
        code=code,
        provider=_PlatformProvider(),
        session=BrowserSessionPolicy(cookie_name="platform_token"),
        security_plane=BrowserSecurityPlane.PLATFORM,
    )


def _facet_on(
    profile_code: str,
    *,
    admission_permission: str | None,
    prefix: str = "/ops",
    code: str = "ops_console",
) -> WebFacetMount:
    return WebFacetMount(
        code=code,
        url_prefix=prefix,
        shell=TemplateRef("layouts/admin.html"),
        authentication_profile=profile_code,
        admission_permission=admission_permission,
        navigation_regions=(NavigationRegion("primary"),),
    )


def _manifest() -> ModuleManifest:
    return ModuleManifest(code="plane-probe", version="1.0.0")


def test_a_platform_profile_cannot_carry_an_admission_permission() -> None:
    with pytest.raises(WebSurfaceError, match="plane"):
        WebSurfaceRegistry(
            manifests=(_manifest(),),
            facets=(_facet_on("platform_session", admission_permission="ops.access"),),
            authentication_profiles=(_platform_profile(),),
            ui_contract_version=1,
        )


def test_a_public_profile_cannot_carry_an_admission_permission() -> None:
    """A public binding has `provider=None`, which forces its plane to NONE.

    `WebFacetMount` already refuses admission with NO profile at all; this is
    the gap that left — a profile is named, so the facet-level check passes,
    and the named profile turns out to authenticate nobody.
    """
    with pytest.raises(WebSurfaceError, match="plane"):
        WebSurfaceRegistry(
            manifests=(_manifest(),),
            facets=(_facet_on("public", admission_permission="ops.access"),),
            authentication_profiles=(AuthenticationProfileBinding(code="public"),),
            ui_contract_version=1,
        )


def test_a_platform_profile_without_admission_remains_valid() -> None:
    """The repair must refuse an invalid BINDING, not the platform plane.

    A platform facet authorizes inside its own routes against a
    `PlatformAdmin`; that is the supported shape and this canary is what stops
    the check being widened into "platform facets cannot be secured".
    """
    registry = WebSurfaceRegistry(
        manifests=(_manifest(),),
        facets=(_facet_on("platform_session", admission_permission=None),),
        authentication_profiles=(_platform_profile(),),
        ui_contract_version=1,
    )
    facet = registry.facet("ops_console")
    assert facet.admission_permission is None
    assert facet.authentication_profile == "platform_session"


def test_a_tenant_profile_with_admission_is_accepted_and_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive half: accepted at startup AND actually consulted.

    Refusing the invalid planes is only half a repair — a check that also
    stopped the valid binding from enforcing would pass the three tests above
    while quietly disabling admission everywhere. So this drives a real
    request and asserts both verdicts, and that the code reaching
    `authorize_party` is the facet's own.
    """
    from dotmac_kernel import create_app
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="admission-test")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, t: t)

    party = Party(id=uuid.uuid4(), tenant_id=tenant.id, party_type="person")

    class _PartyProvider:
        transport = BrowserCredentialTransport.COOKIE_SESSION

        @staticmethod
        def dependency() -> Party:
            return party

    asked: list[str] = []
    verdict = {"allow": False}

    def _authorize(db, *, tenant, party, code):
        asked.append(code)
        return verdict["allow"]

    monkeypatch.setattr("dotmac_kernel.web_runtime.authorize_party", _authorize)

    router = APIRouter(prefix="/console")

    @router.get("/home", name="home")
    def home() -> dict[str, bool]:
        return {"ok": True}

    manifest = ModuleManifest(
        code="admission-probe",
        version="1.0.0",
        web_surfaces=(_surface(router, facet="ops_console"),),
        permissions=(PermissionSpec(code="ops.access", description="Ops access"),),
    )
    spec = ProductAssemblySpec(
        name="admission-test",
        modules=(manifest,),
        web_facets=(_facet_on("tenant_session", admission_permission="ops.access"),),
        authentication_profiles=(
            AuthenticationProfileBinding(
                code="tenant_session",
                provider=_PartyProvider(),
                session=BrowserSessionPolicy(cookie_name="access_token"),
            ),
        ),
        ui_contract_version=1,
    )

    app = create_app(spec)
    app.dependency_overrides[get_db] = lambda: None

    with TestClient(app) as client:
        assert client.get("/ops/console/home").status_code == 403
        verdict["allow"] = True
        assert client.get("/ops/console/home").json() == {"ok": True}

    assert asked == ["ops.access", "ops.access"]
