"""Typed interactive-browser composition contracts (ADR-0006).

This module is deliberately pure configuration.  It imports no assembly, ORM,
database session, templating engine, or design-system package.  A product passes
the exact UI contract integer it selected; the kernel never reaches forward
into ``dotmac-ui`` to discover one.

The runtime that consumes these records lives in the kernel's internal
``web_runtime`` module.  Keeping validation here makes a surface declaration
usable by packaging/inspection tools without constructing an application.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from fastapi import APIRouter
from fastapi.routing import APIRoute

from dotmac_kernel.route_metadata import CAPABILITY_CODE_ATTR, PERMISSION_CODE_ATTR

if TYPE_CHECKING:
    from dotmac_kernel.modules import ModuleManifest

_CODE = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")
_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class WebSurfaceError(ValueError):
    """Base for an incoherent interactive-browser composition."""


class DuplicateFacetError(WebSurfaceError):
    """Two facets claim the same code or URL prefix."""


class UnknownFacetError(WebSurfaceError):
    """A module contribution targets no assembly-declared facet."""


class DuplicateSurfaceError(WebSurfaceError):
    """A module declares the same stable surface identity twice."""


class DuplicateNavigationItemError(WebSurfaceError):
    """Two items claim the same stable id inside one facet."""


class UnknownAuthenticationProfileError(WebSurfaceError):
    """A facet references no assembly-bound authentication profile."""


class UIContractCompatibilityError(WebSurfaceError):
    """A surface cannot render against the assembly's selected UI contract."""


class UnknownBrowserCapabilityError(WebSurfaceError):
    """A required browser capability has no compatible assembly provider."""


class RouteCompositionError(WebSurfaceError):
    """A route or navigation declaration is missing, ambiguous, or colliding."""


class NamespaceCollisionError(WebSurfaceError):
    """Two packages claim the same template or static namespace."""


def _validate_code(value: str, *, field_name: str) -> str:
    if not _CODE.fullmatch(value):
        raise WebSurfaceError(
            f"{field_name} must be a stable lower-case code, got {value!r}"
        )
    return value


def _normalize_prefix(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        raise WebSurfaceError(
            f"facet URL prefix must start with one '/', got {value!r}"
        )
    if (
        "?" in value
        or "#" in value
        or "//" in value
        or any(ord(ch) < 0x20 or ch in "{};\\" for ch in value)
    ):
        raise WebSurfaceError(
            f"facet URL prefix must be one safe static path: {value!r}"
        )
    return value.rstrip("/") or "/"


def _path_scope_contains(scope: str, target: str) -> bool:
    normalized = scope.rstrip("/") or "/"
    return (
        normalized == "/" or target == normalized or target.startswith(f"{normalized}/")
    )


def _route_shape(path: str) -> str:
    """Erase parameter names/converters so order-dependent twins collide."""

    return re.sub(r"\{[^{}]+\}", "{}", path)


class BrowserCredentialTransport(StrEnum):
    """Closed security mechanics understood by the kernel.

    Audience/facet codes stay open.  Transport is closed because each member
    changes mandatory controls (notably CSRF and session-cookie handling) and a
    value the kernel does not understand cannot be secured by configuration.
    """

    NONE = "none"
    COOKIE_SESSION = "cookie_session"


class BrowserSecurityPlane(StrEnum):
    """The persistence/authorization plane an authentication profile enters."""

    NONE = "none"
    TENANT = "tenant"
    PLATFORM = "platform"


class BrowserSecurityRequirement(StrEnum):
    """Closed CSP relaxations a trusted browser-capability provider may need.

    A module names a versioned capability; the assembly's provider declares
    its browser-security consequences.  Keeping this vocabulary closed is
    intentional: installed packages cannot inject raw directives, origins,
    wildcards, ``unsafe-inline`` or ``unsafe-eval`` into the product policy.
    New mechanics require a kernel-contract change and security review.
    """

    WORKER_SELF = "worker_self"
    WORKER_BLOB = "worker_blob"
    MEDIA_SELF = "media_self"
    MEDIA_BLOB = "media_blob"
    FRAME_SELF = "frame_self"


class BrowserAuthenticationProvider(Protocol):
    """Typed assembly binding around an authentication dependency."""

    transport: BrowserCredentialTransport

    @property
    def dependency(self) -> Callable[..., object]:
        """FastAPI dependency that returns the authenticated principal."""


@dataclass(frozen=True, slots=True)
class BrowserSessionPolicy:
    cookie_name: str
    cookie_path: str = "/"
    same_site: Literal["lax", "strict", "none"] = "lax"
    secure_in_production: bool = True
    http_only: bool = True
    shared_session_group: str | None = None

    def __post_init__(self) -> None:
        if not _COOKIE_NAME.fullmatch(self.cookie_name):
            raise WebSurfaceError("session cookie name must be a valid HTTP token")
        if (
            not self.cookie_path.startswith("/")
            or "?" in self.cookie_path
            or "#" in self.cookie_path
            or any(ord(ch) < 0x20 or ch == ";" for ch in self.cookie_path)
        ):
            raise WebSurfaceError("session cookie path must be a safe absolute path")
        object.__setattr__(self, "cookie_path", self.cookie_path.rstrip("/") or "/")
        same_site = self.same_site.lower()
        if same_site not in {"lax", "strict", "none"}:
            raise WebSurfaceError("session SameSite must be lax, strict, or none")
        object.__setattr__(self, "same_site", same_site)
        if not self.http_only:
            raise WebSurfaceError("browser session cookies must be HttpOnly")
        if same_site == "none" and not self.secure_in_production:
            raise WebSurfaceError("SameSite=None session cookies must be Secure")
        if self.shared_session_group is not None:
            _validate_code(
                self.shared_session_group, field_name="shared session group code"
            )


@dataclass(frozen=True, slots=True)
class AuthenticationProfileBinding:
    code: str
    provider: BrowserAuthenticationProvider | None = None
    session: BrowserSessionPolicy | None = None
    security_plane: BrowserSecurityPlane = BrowserSecurityPlane.TENANT

    def __post_init__(self) -> None:
        _validate_code(self.code, field_name="authentication profile code")
        try:
            plane = BrowserSecurityPlane(self.security_plane)
        except (TypeError, ValueError) as exc:
            raise WebSurfaceError(
                f"authentication profile {self.code!r} declares an unknown "
                "security plane"
            ) from exc
        object.__setattr__(self, "security_plane", plane)
        if self.provider is None:
            if self.session is not None:
                raise WebSurfaceError(
                    f"public authentication profile {self.code!r} cannot declare "
                    "a session"
                )
            if self.security_plane is not BrowserSecurityPlane.NONE:
                object.__setattr__(self, "security_plane", BrowserSecurityPlane.NONE)
            return
        if not isinstance(self.provider.transport, BrowserCredentialTransport):
            raise WebSurfaceError(
                f"authentication provider for {self.code!r} declares an unknown "
                "credential transport"
            )
        if self.security_plane is BrowserSecurityPlane.NONE:
            raise WebSurfaceError(
                f"authenticated profile {self.code!r} requires a security plane"
            )
        if self.provider.transport is BrowserCredentialTransport.NONE:
            raise WebSurfaceError(
                f"authentication provider for {self.code!r} cannot use the public "
                "transport"
            )
        if self.provider.transport is BrowserCredentialTransport.COOKIE_SESSION:
            if self.session is None:
                raise WebSurfaceError(
                    f"cookie authentication profile {self.code!r} requires a "
                    "session policy"
                )
        elif self.session is not None:
            raise WebSurfaceError(
                f"non-cookie authentication profile {self.code!r} cannot declare "
                "a session"
            )

    @property
    def transport(self) -> BrowserCredentialTransport:
        if self.provider is None:
            return BrowserCredentialTransport.NONE
        return self.provider.transport


@dataclass(frozen=True, slots=True)
class BrowserCapabilityRequirement:
    code: str
    contract_version: int

    def __post_init__(self) -> None:
        _validate_code(self.code, field_name="browser capability code")
        if self.contract_version < 1:
            raise WebSurfaceError(
                "browser capability contract version must be positive"
            )


@dataclass(frozen=True, slots=True)
class BrowserCapabilityProvision:
    code: str
    contract_version: int
    security_requirements: frozenset[BrowserSecurityRequirement] = frozenset()

    def __post_init__(self) -> None:
        _validate_code(self.code, field_name="browser capability code")
        if self.contract_version < 1:
            raise WebSurfaceError(
                "browser capability contract version must be positive"
            )
        try:
            requirements = frozenset(
                BrowserSecurityRequirement(value)
                for value in self.security_requirements
            )
        except (TypeError, ValueError) as exc:
            raise WebSurfaceError(
                f"browser capability {self.code!r} declares an unknown browser "
                "security requirement"
            ) from exc
        object.__setattr__(self, "security_requirements", requirements)


@dataclass(frozen=True, slots=True)
class TemplateRef:
    name: str
    namespace: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.name
            or self.name.startswith("/")
            or ".." in Path(self.name).parts
            or "\\" in self.name
            or any(ord(ch) < 0x20 for ch in self.name)
        ):
            raise WebSurfaceError(
                f"template name must be relative and non-empty: {self.name!r}"
            )
        if self.namespace is not None:
            _validate_code(self.namespace, field_name="template namespace")

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}/{self.name}" if self.namespace else self.name


@dataclass(frozen=True, slots=True)
class TemplatePackage:
    namespace: str
    root: Path

    def __post_init__(self) -> None:
        _validate_code(self.namespace, field_name="template namespace")
        object.__setattr__(self, "root", Path(self.root))


@dataclass(frozen=True, slots=True)
class StaticPackage:
    namespace: str
    root: Path

    def __post_init__(self) -> None:
        _validate_code(self.namespace, field_name="static namespace")
        object.__setattr__(self, "root", Path(self.root))


@dataclass(frozen=True, slots=True)
class LocalizedText:
    message_id: str
    default: str

    def __post_init__(self) -> None:
        _validate_code(self.message_id, field_name="message id")
        if not self.default.strip():
            raise WebSurfaceError("localized text requires a non-empty default")


@dataclass(frozen=True, slots=True)
class NavigationRegion:
    code: str
    label: LocalizedText | None = None
    order: int = 0

    def __post_init__(self) -> None:
        _validate_code(self.code, field_name="navigation region code")


@dataclass(frozen=True, slots=True)
class WebRouteRef:
    module: str
    surface: str
    route_name: str

    def __post_init__(self) -> None:
        _validate_code(self.module, field_name="route module")
        _validate_code(self.surface, field_name="route surface")
        _validate_code(self.route_name, field_name="local route name")


@dataclass(frozen=True, slots=True)
class WebNavItem:
    code: str
    region: str
    label: LocalizedText
    route_name: str
    group: str | None = None
    order: int = 0

    def __post_init__(self) -> None:
        _validate_code(self.code, field_name="navigation item code")
        _validate_code(self.region, field_name="navigation region code")
        if self.group is not None:
            _validate_code(self.group, field_name="navigation group code")
        _validate_code(self.route_name, field_name="navigation route name")


@dataclass(frozen=True, slots=True)
class WebFacetMount:
    code: str
    url_prefix: str
    shell: TemplateRef
    authentication_profile: str | None = None
    admission_permission: str | None = None
    navigation_regions: Sequence[NavigationRegion] = field(default_factory=tuple)
    entry_routes: Sequence[WebRouteRef] = field(default_factory=tuple)
    login_route: WebRouteRef | None = None
    landing_route: WebRouteRef | None = None
    logout_route: WebRouteRef | None = None

    def __post_init__(self) -> None:
        _validate_code(self.code, field_name="facet code")
        object.__setattr__(self, "url_prefix", _normalize_prefix(self.url_prefix))
        if self.authentication_profile is not None:
            _validate_code(
                self.authentication_profile,
                field_name="authentication profile reference",
            )
        if self.admission_permission is not None:
            _validate_code(
                self.admission_permission, field_name="facet admission permission"
            )
            if self.authentication_profile is None:
                raise WebSurfaceError(
                    f"facet {self.code!r} cannot require admission without "
                    "authentication"
                )
        regions = tuple(self.navigation_regions)
        if len({region.code for region in regions}) != len(regions):
            raise WebSurfaceError(f"facet {self.code!r} repeats a navigation region")
        object.__setattr__(self, "navigation_regions", regions)
        entries = tuple(self.entry_routes)
        if len(set(entries)) != len(entries):
            raise WebSurfaceError(f"facet {self.code!r} repeats an entry route")
        object.__setattr__(self, "entry_routes", entries)
        if self.login_route is not None and self.login_route not in self.entry_routes:
            raise WebSurfaceError(
                f"facet {self.code!r} login route must also be an entry route"
            )


@dataclass(frozen=True, slots=True)
class WebSurfaceContribution:
    code: str
    facet: str
    routers: Sequence[APIRouter]
    navigation: Sequence[WebNavItem] = field(default_factory=tuple)
    templates: TemplatePackage | None = None
    static_assets: StaticPackage | None = None
    supported_ui_contract_versions: frozenset[int] = frozenset()
    browser_capabilities: Sequence[BrowserCapabilityRequirement] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        _validate_code(self.code, field_name="surface code")
        _validate_code(self.facet, field_name="target facet code")
        routers = tuple(self.routers)
        if not routers:
            raise WebSurfaceError(f"surface {self.code!r} declares no routers")
        object.__setattr__(self, "routers", routers)
        object.__setattr__(self, "navigation", tuple(self.navigation))
        versions = frozenset(self.supported_ui_contract_versions)
        if not versions or any(version < 1 for version in versions):
            raise WebSurfaceError(
                f"surface {self.code!r} requires positive supported UI contracts"
            )
        object.__setattr__(self, "supported_ui_contract_versions", versions)
        object.__setattr__(
            self, "browser_capabilities", tuple(self.browser_capabilities)
        )
        requirements = self.browser_capabilities
        if len({item.code for item in requirements}) != len(requirements):
            raise WebSurfaceError(
                f"surface {self.code!r} repeats a browser capability requirement"
            )


@dataclass(frozen=True, slots=True)
class RegisteredWebNavItem:
    code: str
    region: str
    label: LocalizedText
    route_name: str
    group: str | None
    order: int
    required_permissions: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    legacy_path: str | None = None


@dataclass(frozen=True, slots=True)
class RegisteredWebSurface:
    owner: str
    contribution: WebSurfaceContribution
    navigation: tuple[RegisteredWebNavItem, ...]
    legacy: bool = False

    @property
    def identity(self) -> tuple[str, str]:
        return self.owner, self.contribution.code


@dataclass(frozen=True, slots=True)
class ResolvedWebNavItem:
    code: str
    region: str
    label: str
    href: str
    group: str | None
    order: int
    feature: str

    @property
    def path(self) -> str:
        """Contract-v1 template compatibility during the migration window."""

        return self.href


@dataclass(frozen=True, slots=True)
class SurfaceContext:
    facet: str
    shell: str
    owner: str
    surface: str
    enabled_modules: frozenset[str]
    navigation: tuple[ResolvedWebNavItem, ...]
    stylesheets: tuple[str, ...]
    ui_contract_version: int | None
    login_path: str | None = None
    landing_path: str | None = None
    logout_path: str | None = None
    url_prefix: str = ""

    @classmethod
    def empty(cls) -> SurfaceContext:
        return cls("", "", "", "", frozenset(), (), (), None)


def qualified_route_name(facet: str, owner: str, surface: str, local: str) -> str:
    """Collision-free runtime name derived entirely from stable identities."""

    return f"web:{facet}:{owner}:{surface}:{local}"


def surface_route_name(context: SurfaceContext, local: str) -> str:
    """Resolve a local module route name inside the current request surface."""

    if not context.facet or not context.owner or not context.surface:
        raise RouteCompositionError("request has no current composed web surface")
    return qualified_route_name(context.facet, context.owner, context.surface, local)


def surface_path(request: Any, local: str, **path_params: object) -> str:
    """Build a path to a sibling route without knowing the facet prefix."""

    context = getattr(request.state, "surface_context", SurfaceContext.empty())
    route_name = (
        surface_route_name(context, local)
        if context.facet
        else local  # contract-v1 direct-router compatibility
    )
    return str(request.url_for(route_name, **path_params).path)


def current_session_policy(request: Any) -> BrowserSessionPolicy:
    """Return the assembly-owned session mechanics for the current facet."""

    context = getattr(request.state, "surface_context", SurfaceContext.empty())
    registry = getattr(request.app.state, "web_surface_registry", None)
    facet_code = context.facet
    if not facet_code:
        route_name = getattr(request.scope.get("route"), "name", "")
        if isinstance(route_name, str) and route_name.startswith("web:"):
            parts = route_name.split(":", 4)
            if len(parts) == 5:
                facet_code = parts[1]
    if registry is None or not facet_code:
        raise WebSurfaceError("request has no composed browser session policy")
    facet = registry.facet(facet_code)
    if facet.authentication_profile is None:
        raise WebSurfaceError(f"facet {facet.code!r} is public and has no session")
    profile = registry.authentication_profile(facet.authentication_profile)
    if profile.session is None:
        raise WebSurfaceError(
            f"authentication profile {profile.code!r} has no browser session"
        )
    return profile.session


def _dependency_codes(route: APIRoute, attribute: str) -> tuple[str, ...]:
    found: set[str] = set()

    def walk(dependant: object) -> None:
        call = getattr(dependant, "call", None)
        code = getattr(call, attribute, None)
        if isinstance(code, str):
            found.add(code)
        for child in getattr(dependant, "dependencies", ()):
            walk(child)

    walk(route.dependant)
    return tuple(sorted(found))


def _routes(contribution: WebSurfaceContribution) -> dict[str, APIRoute]:
    routes: dict[str, APIRoute] = {}
    for router in contribution.routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            if not route.name:
                raise RouteCompositionError(
                    f"surface {contribution.code!r} has an unnamed route"
                )
            _validate_code(route.name, field_name="surface local route name")
            if route.name in routes:
                raise RouteCompositionError(
                    f"surface {contribution.code!r} repeats route name {route.name!r}"
                )
            routes[route.name] = route
    return routes


def _new_navigation(
    owner: str,
    contribution: WebSurfaceContribution,
    facet: WebFacetMount,
) -> tuple[RegisteredWebNavItem, ...]:
    routes = _routes(contribution)
    region_codes = {region.code for region in facet.navigation_regions}
    result: list[RegisteredWebNavItem] = []
    for item in contribution.navigation:
        if item.region not in region_codes:
            raise RouteCompositionError(
                f"navigation item {item.code!r} references unknown region "
                f"{item.region!r} on facet {facet.code!r}"
            )
        route = routes.get(item.route_name)
        if route is None:
            raise RouteCompositionError(
                f"navigation item {item.code!r} references missing route "
                f"{item.route_name!r}"
            )
        if "GET" not in (route.methods or set()):
            raise RouteCompositionError(
                f"navigation route {item.route_name!r} is not a GET route"
            )
        if "{" in route.path:
            raise RouteCompositionError(
                f"navigation route {item.route_name!r} requires path parameters"
            )
        result.append(
            RegisteredWebNavItem(
                code=item.code,
                region=item.region,
                label=item.label,
                route_name=qualified_route_name(
                    contribution.facet, owner, contribution.code, item.route_name
                ),
                group=item.group,
                order=item.order,
                required_permissions=_dependency_codes(route, PERMISSION_CODE_ATTR),
                required_capabilities=_dependency_codes(route, CAPABILITY_CODE_ATTR),
            )
        )
    return tuple(result)


def _legacy_surface(
    manifest: ModuleManifest,
    facet: WebFacetMount,
    ui_contract_version: int | None,
) -> RegisteredWebSurface:
    # The v1 adapter is deliberately staff-admin-only.  It preserves absolute
    # routers and path navigation for one compatibility generation; new code
    # cannot opt into this shape.
    versions = (
        frozenset({ui_contract_version}) if ui_contract_version else frozenset({1})
    )
    contribution = WebSurfaceContribution(
        code="legacy",
        facet=facet.code,
        routers=manifest.web_routers,
        supported_ui_contract_versions=versions,
    )
    route_list = [
        route
        for router in contribution.routers
        for route in router.routes
        if isinstance(route, APIRoute)
    ]
    navigation: list[RegisteredWebNavItem] = []
    default_region = (
        facet.navigation_regions[0].code if facet.navigation_regions else "primary"
    )
    for index, item in enumerate(manifest.nav):
        matches = [
            route
            for route in route_list
            if route.path == item.path and "GET" in (route.methods or set())
        ]
        if len(matches) != 1:
            raise RouteCompositionError(
                f"legacy nav path {item.path!r} in module {manifest.code!r} "
                f"matches {len(matches)} GET routes; expected exactly one"
            )
        route = matches[0]
        navigation.append(
            RegisteredWebNavItem(
                code=f"{manifest.code}.{index}",
                region=default_region,
                label=LocalizedText(
                    message_id=f"{manifest.code}.nav.{index}", default=item.label
                ),
                route_name=qualified_route_name(
                    facet.code, manifest.code, "legacy", route.name
                ),
                group=None,
                order=index,
                required_permissions=_dependency_codes(route, PERMISSION_CODE_ATTR),
                required_capabilities=_dependency_codes(route, CAPABILITY_CODE_ATTR),
                legacy_path=item.path,
            )
        )
    return RegisteredWebSurface(
        owner=manifest.code,
        contribution=contribution,
        navigation=tuple(navigation),
        legacy=True,
    )


class WebSurfaceRegistry:
    """Validate and freeze one assembly's complete interactive-web graph."""

    def __init__(
        self,
        *,
        manifests: Sequence[ModuleManifest],
        facets: Sequence[WebFacetMount] = (),
        authentication_profiles: Sequence[AuthenticationProfileBinding] = (),
        browser_capabilities: Sequence[BrowserCapabilityProvision] = (),
        ui_contract_version: int | None = None,
        built_in_surfaces: Sequence[tuple[str, WebSurfaceContribution]] = (),
    ) -> None:
        facet_values = list(facets)
        has_legacy = any(manifest.web_routers for manifest in manifests)
        if has_legacy and not facet_values:
            raise UnknownFacetError(
                "legacy web modules require the assembly to explicitly declare "
                "a secured staff_admin facet"
            )
        self._facets = self._unique_facets(facet_values)
        self._profiles = self._unique_profiles(authentication_profiles)
        self._browser_capabilities = self._unique_browser_capabilities(
            browser_capabilities
        )
        if ui_contract_version is not None and ui_contract_version < 1:
            raise UIContractCompatibilityError(
                "selected UI contract version must be positive"
            )
        self.ui_contract_version = ui_contract_version

        for facet in self._facets.values():
            if (
                facet.authentication_profile is not None
                and facet.authentication_profile not in self._profiles
            ):
                raise UnknownAuthenticationProfileError(
                    f"facet {facet.code!r} references unknown authentication "
                    f"profile {facet.authentication_profile!r}"
                )
            if facet.authentication_profile is not None:
                profile = self._profiles[facet.authentication_profile]
                if (
                    facet.admission_permission is not None
                    and profile.security_plane is not BrowserSecurityPlane.TENANT
                ):
                    raise WebSurfaceError(
                        f"facet {facet.code!r} declares admission permission "
                        f"{facet.admission_permission!r} but authentication "
                        f"profile {profile.code!r} enters the "
                        f"{profile.security_plane.value} plane. Admission is "
                        "evaluated with authorize_party(db, tenant, party, "
                        "code), which needs a tenant-scoped Party: a platform "
                        "profile resolves a PlatformAdmin and a public profile "
                        "resolves no principal at all, so there is nothing the "
                        "permission could be checked against. Either bind a "
                        "tenant-plane profile or drop the admission permission "
                        "and authorize inside the facet's own routes."
                    )
                if profile.session is not None and not _path_scope_contains(
                    profile.session.cookie_path, facet.url_prefix
                ):
                    raise WebSurfaceError(
                        f"authentication profile {profile.code!r} cookie path "
                        f"{profile.session.cookie_path!r} does not cover facet "
                        f"prefix {facet.url_prefix!r}"
                    )

        surfaces: list[RegisteredWebSurface] = []
        staff = self._facets.get("staff_admin")
        if has_legacy:
            if staff is None:
                raise UnknownFacetError(
                    "legacy web modules require an explicit staff_admin facet"
                )
            if (
                staff.authentication_profile is None
                or staff.admission_permission is None
            ):
                raise WebSurfaceError(
                    "legacy staff_admin composition requires both an "
                    "authentication profile and an admission permission; the "
                    "compatibility adapter never infers an authorization policy"
                )
        for manifest in manifests:
            if manifest.web_surfaces:
                for contribution in manifest.web_surfaces:
                    surfaces.append(self._register_new(manifest.code, contribution))
            if manifest.web_routers:
                if staff is None:  # pragma: no cover - guarded once above
                    raise UnknownFacetError("legacy web module requires staff_admin")
                surfaces.append(_legacy_surface(manifest, staff, ui_contract_version))
        for owner, contribution in built_in_surfaces:
            _validate_code(owner, field_name="built-in surface owner")
            surfaces.append(self._register_new(owner, contribution))

        self._validate_surfaces(surfaces)
        self.surfaces = tuple(surfaces)
        self._surface_by_identity = {surface.identity: surface for surface in surfaces}
        active_capability_codes = {
            requirement.code
            for surface in surfaces
            for requirement in surface.contribution.browser_capabilities
        }
        self.browser_security_requirements = frozenset(
            requirement
            for code in active_capability_codes
            for requirement in self._browser_capabilities[code].security_requirements
        )
        self._validate_entry_routes()

    @staticmethod
    def _unique_facets(facets: Iterable[WebFacetMount]) -> dict[str, WebFacetMount]:
        by_code: dict[str, WebFacetMount] = {}
        by_prefix: dict[str, str] = {}
        for facet in facets:
            if facet.code in by_code:
                raise DuplicateFacetError(f"facet code {facet.code!r} is duplicated")
            for prefix, other in by_prefix.items():
                if _path_scope_contains(prefix, facet.url_prefix) or (
                    _path_scope_contains(facet.url_prefix, prefix)
                ):
                    raise DuplicateFacetError(
                        f"facets {other!r} and {facet.code!r} claim overlapping "
                        f"prefixes {prefix!r} and {facet.url_prefix!r}"
                    )
            by_code[facet.code] = facet
            by_prefix[facet.url_prefix] = facet.code
        return by_code

    @staticmethod
    def _unique_profiles(
        profiles: Iterable[AuthenticationProfileBinding],
    ) -> dict[str, AuthenticationProfileBinding]:
        result: dict[str, AuthenticationProfileBinding] = {}
        cookie_owners: dict[str, AuthenticationProfileBinding] = {}
        for profile in profiles:
            if profile.code in result:
                raise WebSurfaceError(
                    f"authentication profile {profile.code!r} is duplicated"
                )
            result[profile.code] = profile
            if profile.session is None:
                continue
            other = cookie_owners.get(profile.session.cookie_name)
            if other is not None:
                left = other.session
                right = profile.session
                if left is None:
                    raise WebSurfaceError(
                        f"authentication profile {other.code!r} has no session"
                    )
                if (
                    not left.shared_session_group
                    or left.shared_session_group != right.shared_session_group
                    or left != right
                ):
                    raise WebSurfaceError(
                        f"authentication profiles {other.code!r} and {profile.code!r} "
                        f"share cookie {right.cookie_name!r} without one identical, "
                        "named shared-session policy"
                    )
            cookie_owners[profile.session.cookie_name] = profile
        return result

    @staticmethod
    def _unique_browser_capabilities(
        provisions: Iterable[BrowserCapabilityProvision],
    ) -> dict[str, BrowserCapabilityProvision]:
        result: dict[str, BrowserCapabilityProvision] = {}
        for provision in provisions:
            if provision.code in result:
                raise WebSurfaceError(
                    f"browser capability {provision.code!r} has multiple providers"
                )
            result[provision.code] = provision
        return result

    def _register_new(
        self, owner: str, contribution: WebSurfaceContribution
    ) -> RegisteredWebSurface:
        facet = self._facets.get(contribution.facet)
        if facet is None:
            raise UnknownFacetError(
                f"surface {owner}.{contribution.code} targets unknown facet "
                f"{contribution.facet!r}"
            )
        selected = self.ui_contract_version
        if (
            selected is None
            or selected not in contribution.supported_ui_contract_versions
        ):
            raise UIContractCompatibilityError(
                f"surface {owner}.{contribution.code} does not support selected UI "
                f"contract {selected!r}; supports "
                f"{sorted(contribution.supported_ui_contract_versions)}"
            )
        for requirement in contribution.browser_capabilities:
            provision = self._browser_capabilities.get(requirement.code)
            if (
                provision is None
                or provision.contract_version != requirement.contract_version
            ):
                supplied = None if provision is None else provision.contract_version
                raise UnknownBrowserCapabilityError(
                    f"surface {owner}.{contribution.code} requires browser capability "
                    f"{requirement.code}@{requirement.contract_version}; assembly "
                    f"provides {supplied!r}"
                )
        facet_prefix = facet.url_prefix
        if facet_prefix != "/":
            for route in _routes(contribution).values():
                if route.path == facet_prefix or route.path.startswith(
                    f"{facet_prefix}/"
                ):
                    raise RouteCompositionError(
                        f"surface {owner}.{contribution.code} route {route.name!r} "
                        f"authors assembly prefix {facet_prefix!r}; v2 routes must "
                        "be facet-relative"
                    )
        for package, kind in (
            (contribution.templates, "template"),
            (contribution.static_assets, "static"),
        ):
            if package is not None and not package.root.is_dir():
                raise WebSurfaceError(
                    f"{kind} package {package.namespace!r} root {package.root} "
                    "does not exist or is not a directory"
                )
        return RegisteredWebSurface(
            owner=owner,
            contribution=contribution,
            navigation=_new_navigation(owner, contribution, facet),
        )

    def _validate_surfaces(self, surfaces: Sequence[RegisteredWebSurface]) -> None:
        identities: set[tuple[str, str]] = set()
        nav_codes: set[tuple[str, str]] = set()
        template_namespaces: dict[str, tuple[str, str]] = {}
        static_namespaces: dict[str, tuple[str, str]] = {}
        route_keys: dict[tuple[str, str], str] = {}
        for surface in surfaces:
            if surface.identity in identities:
                raise DuplicateSurfaceError(
                    f"surface {surface.owner}.{surface.contribution.code} is duplicated"
                )
            identities.add(surface.identity)
            for item in surface.navigation:
                key = (surface.contribution.facet, item.code)
                if key in nav_codes:
                    raise DuplicateNavigationItemError(
                        f"facet {key[0]!r} repeats navigation id {key[1]!r}"
                    )
                nav_codes.add(key)
            for package, seen, kind in (
                (surface.contribution.templates, template_namespaces, "template"),
                (surface.contribution.static_assets, static_namespaces, "static"),
            ):
                if package is None:
                    continue
                existing = seen.get(package.namespace)
                if existing is not None:
                    raise NamespaceCollisionError(
                        f"{kind} namespace {package.namespace!r} claimed by "
                        f"{existing[0]}.{existing[1]} and "
                        f"{surface.owner}.{surface.contribution.code}"
                    )
                seen[package.namespace] = surface.identity
            facet = self._facets[surface.contribution.facet]
            for router in surface.contribution.routers:
                for route in router.routes:
                    if not isinstance(route, APIRoute):
                        continue
                    path = (
                        route.path
                        if surface.legacy
                        else _join_path(facet.url_prefix, route.path)
                    )
                    for method in route.methods or set():
                        key = (method, _route_shape(path))
                        route_owner = route_keys.get(key)
                        identity = (
                            f"{surface.owner}.{surface.contribution.code}.{route.name}"
                        )
                        if route_owner is not None:
                            raise RouteCompositionError(
                                f"{method} {path} is claimed by {route_owner} and "
                                f"{identity}"
                            )
                        route_keys[key] = identity

    def _validate_entry_routes(self) -> None:
        for facet in self._facets.values():
            for reference in facet.entry_routes:
                self._referenced_route(facet, reference, purpose="entry")
            if facet.login_route is not None:
                route = self._referenced_route(
                    facet, facet.login_route, purpose="login"
                )
                if "GET" not in (route.methods or set()) or "{" in route.path:
                    raise RouteCompositionError(
                        f"facet {facet.code!r} login route must be a parameterless "
                        "GET route"
                    )
            if facet.landing_route is not None:
                route = self._referenced_route(
                    facet, facet.landing_route, purpose="landing"
                )
                if "GET" not in (route.methods or set()) or "{" in route.path:
                    raise RouteCompositionError(
                        f"facet {facet.code!r} landing route must be a parameterless "
                        "GET route"
                    )
            if facet.logout_route is not None:
                route = self._referenced_route(
                    facet, facet.logout_route, purpose="logout"
                )
                if "POST" not in (route.methods or set()) or "{" in route.path:
                    raise RouteCompositionError(
                        f"facet {facet.code!r} logout route must be a parameterless "
                        "POST route"
                    )

    def _referenced_route(
        self, facet: WebFacetMount, reference: WebRouteRef, *, purpose: str
    ) -> APIRoute:
        surface = self._surface_by_identity.get((reference.module, reference.surface))
        if surface is None or surface.contribution.facet != facet.code:
            raise RouteCompositionError(
                f"facet {facet.code!r} {purpose} route references missing surface "
                f"{reference.module}.{reference.surface}"
            )
        route = _routes(surface.contribution).get(reference.route_name)
        if route is None:
            raise RouteCompositionError(
                f"facet {facet.code!r} {purpose} route references missing route "
                f"{reference.module}.{reference.surface}.{reference.route_name}"
            )
        return route

    def route_path(
        self, request: Any, facet_code: str, reference: WebRouteRef | None
    ) -> str | None:
        if reference is None:
            return None
        facet = self.facet(facet_code)
        self._referenced_route(facet, reference, purpose="runtime")
        url = request.url_for(
            qualified_route_name(
                facet.code,
                reference.module,
                reference.surface,
                reference.route_name,
            )
        )
        return str(url.path)

    def login_path(self, request: Any, facet_code: str) -> str | None:
        facet = self.facet(facet_code)
        return self.route_path(request, facet_code, facet.login_route)

    @property
    def facets(self) -> tuple[WebFacetMount, ...]:
        return tuple(self._facets.values())

    @property
    def authentication_profiles(self) -> tuple[AuthenticationProfileBinding, ...]:
        return tuple(self._profiles.values())

    def facet(self, code: str) -> WebFacetMount:
        try:
            return self._facets[code]
        except KeyError:
            raise UnknownFacetError(f"unknown web facet {code!r}") from None

    def authentication_profile(self, code: str) -> AuthenticationProfileBinding:
        try:
            return self._profiles[code]
        except KeyError:
            raise UnknownAuthenticationProfileError(
                f"unknown authentication profile {code!r}"
            ) from None

    def browser_capability(self, code: str) -> BrowserCapabilityProvision:
        try:
            return self._browser_capabilities[code]
        except KeyError:
            raise UnknownBrowserCapabilityError(
                f"unknown browser capability {code!r}"
            ) from None

    def is_entry_route(
        self, *, facet: str, owner: str, surface: str, route_name: str
    ) -> bool:
        reference = WebRouteRef(owner, surface, route_name)
        return reference in self.facet(facet).entry_routes

    @property
    def template_packages(self) -> tuple[TemplatePackage, ...]:
        return tuple(
            surface.contribution.templates
            for surface in self.surfaces
            if surface.contribution.templates is not None
        )

    @property
    def static_packages(self) -> tuple[StaticPackage, ...]:
        return tuple(
            surface.contribution.static_assets
            for surface in self.surfaces
            if surface.contribution.static_assets is not None
        )

    @property
    def session_cookie_names(self) -> tuple[str, ...]:
        return tuple(
            profile.session.cookie_name
            for profile in self._profiles.values()
            if profile.session is not None
        )


def _join_path(prefix: str, route_path: str) -> str:
    if prefix == "/":
        return route_path or "/"
    if route_path in {"", "/"}:
        return prefix + ("/" if route_path == "/" else "")
    return f"{prefix}{route_path if route_path.startswith('/') else '/' + route_path}"


__all__ = [
    "AuthenticationProfileBinding",
    "BrowserAuthenticationProvider",
    "BrowserCapabilityProvision",
    "BrowserCapabilityRequirement",
    "BrowserCredentialTransport",
    "BrowserSecurityPlane",
    "BrowserSecurityRequirement",
    "BrowserSessionPolicy",
    "DuplicateFacetError",
    "DuplicateNavigationItemError",
    "DuplicateSurfaceError",
    "LocalizedText",
    "NamespaceCollisionError",
    "NavigationRegion",
    "RegisteredWebNavItem",
    "RegisteredWebSurface",
    "ResolvedWebNavItem",
    "RouteCompositionError",
    "StaticPackage",
    "SurfaceContext",
    "TemplatePackage",
    "TemplateRef",
    "UIContractCompatibilityError",
    "UnknownAuthenticationProfileError",
    "UnknownBrowserCapabilityError",
    "UnknownFacetError",
    "WebFacetMount",
    "WebNavItem",
    "WebRouteRef",
    "WebSurfaceContribution",
    "WebSurfaceError",
    "WebSurfaceRegistry",
    "qualified_route_name",
    "current_session_policy",
    "surface_path",
    "surface_route_name",
]
