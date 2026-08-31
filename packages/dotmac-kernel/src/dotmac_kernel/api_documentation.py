"""Who may read an assembly's API documentation — declared, never inherited.

FastAPI mounts `/docs`, `/docs/oauth2-redirect`, `/redoc` and `/openapi.json` by
DEFAULT. `create_app` built `FastAPI(title=..., lifespan=...)` and passed none of
the four suppression arguments, so EVERY assembly over this kernel inherited a
complete, unauthenticated description of its own API — including deployments
whose ingress proxies `/` wholesale, where that inventory was readable by anyone
who could reach the host.

This module is the kernel half of that contract. It was extracted product-first
from `vendor_cp.api_documentation` (`dotmac_platform_control_plane` PR #94, head
`cbc433cf`), which is the consumer that proved the policy in production and
which this replaces: without a kernel owner, every assembly writes that file
again, and the sixth copy is the one that gets it wrong.

## Declared at construction, not repaired afterwards

The product implementation ran AFTER `create_app` returned: it deleted routes
from `app.router.routes` and cleared the `FastAPI` attributes. It had no choice —
it did not own the constructor. The kernel does, so the port drops the route
surgery entirely and passes the coordinates FastAPI needs at construction. A
route that is never mounted cannot be missed by a later filter, cannot be
re-added by a subsequent `include_router`, and does not depend on the order two
post-hoc steps happen to run in.

The audit half is kept exactly as the product proved it, because it answers a
different question: the constructor arguments are what the assembly MEANT, and
`audit_api_documentation` reads the routes the application actually serves. A
policy nobody applied fails there just as loudly as a policy applied wrongly.

## Why the application, and not an ingress rule

The obvious repair is three `location` blocks returning 404. That fixes the
symptom on ONE ingress. It is silently removed by a different ingress, by a
directly published container port, by an operator opening a debug tunnel, or by
a later `location` block that matches first. A deployment artifact cannot be the
authority for what an application serves, so the route inventory itself is the
control.

## Two planes, never mixed

`/docs` and `/redoc` are BROWSER pages. `/openapi.json` is a machine-readable
document for bearer-authenticated API clients. They authenticate differently and
always will, so this module refuses to blur them:

* `DocumentationExposure.PLATFORM_BEARER` is expressible only for the DOCUMENT
  plane. Declaring it for the interactive plane is rejected at construction,
  because a Swagger UI page cannot attach an `Authorization` header to its own
  navigation — the only way to make a bearer-gated `/docs` "work" in a browser
  is to accept a session cookie instead, which is exactly the cookie/bearer
  confusion this exists to prevent.
* No documentation route may depend on a cookie-transport guard under ANY
  exposure; `audit_api_documentation` fails on one.

## Resolution fails closed

Only the explicitly enumerated development and test spellings select a
publishing policy. An UNSET, blank, mistyped, `staging` or otherwise
unrecognised environment resolves to `production`, which publishes no browser
pages at all and serves the OpenAPI document only behind
`require_platform_admin`.

That is deliberately stricter than `Settings.environment`, which defaults to
development, and than `Settings.is_production`, which calls `staging` a
non-production host. The asymmetry is the point: a developer who must type
`ENVIRONMENT=development` has lost a minute; a host that forgot the line has
published its whole API.

There is no kernel default policy. `ProductAssemblySpec.api_documentation` is
required and `create_app` REFUSES to build without it — a fallback would be a
kernel-chosen exposure nobody declared, which is the inheritance this module
exists to end.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from dotmac_kernel.platform_auth import require_platform_admin

#: The one knob, and one an operator already sets.
ENVIRONMENT_ENV_VAR: Final[str] = "ENVIRONMENT"

DEVELOPMENT: Final[str] = "development"
TEST: Final[str] = "test"
PRODUCTION: Final[str] = "production"

#: Raw `ENVIRONMENT` values that select a publishing policy. Everything else —
#: including UNSET, blank, a typo and `staging` — resolves to `production`.
#: Publishing is opt-in by name; there is no default that does it.
_DEVELOPMENT_VALUES: Final[frozenset[str]] = frozenset({"dev", "development", "local"})
_TEST_VALUES: Final[frozenset[str]] = frozenset({"test", "testing", "ci"})


class DocumentationPlane(StrEnum):
    """The two audiences, which authenticate differently and always will."""

    #: Swagger UI and ReDoc: HTML pages a human navigates to in a browser.
    INTERACTIVE = "interactive"
    #: The OpenAPI document itself: fetched by bearer-authenticated API clients.
    DOCUMENT = "document"


class DocumentationExposure(StrEnum):
    """How much of a plane a deployment publishes."""

    #: The route does not exist. Not 403, not a redirect — absent from the
    #: inventory, so nothing downstream has to be trusted to refuse it.
    DISABLED = "disabled"
    #: Served to anyone who can reach the application.
    PUBLIC = "public"
    #: Served only behind `require_platform_admin`: host-exact, bearer-only,
    #: never a cookie. Expressible for the DOCUMENT plane alone.
    PLATFORM_BEARER = "platform-bearer"


#: FastAPI's default coordinates, kept as literals because the point of this
#: module is that they exist whether or not anybody declared them.
OPENAPI_PATH: Final[str] = "/openapi.json"
SWAGGER_PATH: Final[str] = "/docs"
SWAGGER_OAUTH2_REDIRECT_PATH: Final[str] = "/docs/oauth2-redirect"
REDOC_PATH: Final[str] = "/redoc"

#: `FastAPI` attribute -> the plane that attribute's route belongs to. Every
#: documentation path FastAPI can mount is named here; a route is located by
#: PATH so that clearing an attribute cannot hide a route that is still mounted.
PLANE_BY_ATTRIBUTE: Final[dict[str, DocumentationPlane]] = {
    "openapi_url": DocumentationPlane.DOCUMENT,
    "docs_url": DocumentationPlane.INTERACTIVE,
    "swagger_ui_oauth2_redirect_url": DocumentationPlane.INTERACTIVE,
    "redoc_url": DocumentationPlane.INTERACTIVE,
}

DEFAULT_PATH_BY_ATTRIBUTE: Final[dict[str, str]] = {
    "openapi_url": OPENAPI_PATH,
    "docs_url": SWAGGER_PATH,
    "swagger_ui_oauth2_redirect_url": SWAGGER_OAUTH2_REDIRECT_PATH,
    "redoc_url": REDOC_PATH,
}

#: Guards whose credential travels in a browser COOKIE. A documentation path may
#: never depend on one: that is the plane confusion this module refuses.
COOKIE_PLANE_GUARDS: Final[frozenset[str]] = frozenset(
    {"require_platform_web_auth", "require_web_auth"}
)

#: The one guard permitted on a documentation route.
BEARER_PLANE_GUARD: Final[str] = "require_platform_admin"


class ApiDocumentationPolicyError(ValueError):
    """A declared policy is internally incoherent."""


class ApiDocumentationPolicyViolation(RuntimeError):
    """The application's live route inventory contradicts its declared policy."""


@dataclass(frozen=True, slots=True)
class ApiDocumentationPolicy:
    """One environment's declared documentation exposure, per plane."""

    environment: str
    interactive: DocumentationExposure
    document: DocumentationExposure
    rationale: str

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ApiDocumentationPolicyError(
                f"policy for {self.environment!r} needs a rationale: a published "
                "documentation surface is a decision someone must be able to review"
            )
        if self.interactive is DocumentationExposure.PLATFORM_BEARER:
            raise ApiDocumentationPolicyError(
                "interactive documentation cannot be bearer-protected: a browser "
                "navigating to /docs sends no Authorization header, so the only "
                "way to make this 'work' is a session cookie — which is the "
                "cookie/bearer confusion this policy exists to prevent. Disable "
                "the browser pages and bearer-protect the OpenAPI document instead"
            )
        publishing = DocumentationExposure.PUBLIC in {self.interactive, self.document}
        if self.environment == PRODUCTION and publishing:
            raise ApiDocumentationPolicyError(
                "production may not publish API documentation unauthenticated; "
                f"got interactive={self.interactive}, document={self.document}"
            )
        if (
            self.interactive is DocumentationExposure.PUBLIC
            and self.document is not DocumentationExposure.PUBLIC
        ):
            raise ApiDocumentationPolicyError(
                "public interactive documentation needs the public OpenAPI "
                "document it fetches; a Swagger page that cannot load its own "
                "schema is a broken surface pretending to be a protected one"
            )

    def exposure(self, plane: DocumentationPlane) -> DocumentationExposure:
        if plane is DocumentationPlane.DOCUMENT:
            return self.document
        return self.interactive


POLICIES: Final[tuple[ApiDocumentationPolicy, ...]] = (
    ApiDocumentationPolicy(
        environment=DEVELOPMENT,
        interactive=DocumentationExposure.PUBLIC,
        document=DocumentationExposure.PUBLIC,
        rationale=(
            "A developer running the assembly locally sees the whole API. The "
            "host is a workstation and the data is fixtures."
        ),
    ),
    ApiDocumentationPolicy(
        environment=TEST,
        interactive=DocumentationExposure.PUBLIC,
        document=DocumentationExposure.PUBLIC,
        rationale=(
            "CI composes the same surface as development so a contract test can "
            "read the generated schema without a credential."
        ),
    ),
    ApiDocumentationPolicy(
        environment=PRODUCTION,
        interactive=DocumentationExposure.DISABLED,
        document=DocumentationExposure.PLATFORM_BEARER,
        rationale=(
            "The browser pages do not exist: they describe every control-plane "
            "endpoint to an unauthenticated reader and there is no operator task "
            "that needs them on the production host. The OpenAPI document stays "
            "reachable for API clients that already hold a platform-admin bearer "
            "token, which is the plane it belongs to."
        ),
    ),
)

_BY_ENVIRONMENT: Final[dict[str, ApiDocumentationPolicy]] = {
    policy.environment: policy for policy in POLICIES
}


def classify_environment(raw: str | None) -> str:
    """Map a raw `ENVIRONMENT` value onto a declared policy environment.

    Fails CLOSED. Only the enumerated development and test spellings select a
    publishing policy; anything else — `staging`, `prod`, `Production`, a typo,
    an UNSET or blank variable — resolves to `production`. An unrecognised
    environment that quietly published the API is exactly the failure being
    repaired, so publishing is opt-in by name and has no default.
    """
    value = (raw or "").strip().lower()
    if value in _DEVELOPMENT_VALUES:
        return DEVELOPMENT
    if value in _TEST_VALUES:
        return TEST
    return PRODUCTION


def api_documentation_policy(environment: str) -> ApiDocumentationPolicy:
    """The declared policy for an already-classified environment."""
    try:
        return _BY_ENVIRONMENT[environment]
    except KeyError:  # pragma: no cover - unreachable via classify_environment
        raise ApiDocumentationPolicyError(
            f"{environment!r} is not a declared policy environment; "
            f"expected one of {sorted(_BY_ENVIRONMENT)}"
        ) from None


@dataclass(frozen=True, slots=True)
class DocumentationRoute:
    """One documentation path the application actually serves, and its guards."""

    path: str
    plane: DocumentationPlane
    guards: tuple[str, ...]


def _plane_paths(app: FastAPI, plane: DocumentationPlane) -> frozenset[str]:
    """Every path that plane could be served on — declared OR default.

    Both halves matter. The default is what an assembly inherits without saying
    anything, and a route sitting at `/openapi.json` is public whether or not
    `app.openapi_url` still names it. The declared value catches an assembly
    that moved its documentation somewhere else.
    """
    paths: set[str] = set()
    for attribute, attribute_plane in PLANE_BY_ATTRIBUTE.items():
        if attribute_plane is not plane:
            continue
        paths.add(DEFAULT_PATH_BY_ATTRIBUTE[attribute])
        declared = getattr(app, attribute, None)
        if isinstance(declared, str) and declared:
            paths.add(declared)
    return frozenset(paths)


def _route_guards(route: object) -> tuple[str, ...]:
    """Names of the dependency callables FastAPI resolves for a route."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return ()
    names: list[str] = []
    pending = list(getattr(dependant, "dependencies", ()))
    while pending:
        dependency = pending.pop()
        call = getattr(dependency, "call", None)
        if call is not None:
            names.append(getattr(call, "__name__", repr(call)))
        pending.extend(getattr(dependency, "dependencies", ()))
    return tuple(sorted(names))


def documentation_routes(app: FastAPI) -> tuple[DocumentationRoute, ...]:
    """The application's LIVE documentation route inventory.

    Derived from the mounted routes, never from the `FastAPI` attributes: the
    attributes are what an assembly meant, the routes are what it serves.
    """
    plane_by_path = {
        path: plane for plane in DocumentationPlane for path in _plane_paths(app, plane)
    }
    found: list[DocumentationRoute] = []
    for route in app.router.routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        plane = plane_by_path.get(path)
        if plane is None:
            continue
        found.append(
            DocumentationRoute(path=path, plane=plane, guards=_route_guards(route))
        )
    return tuple(sorted(found, key=lambda r: r.path))


def audit_api_documentation(
    app: FastAPI, policy: ApiDocumentationPolicy
) -> tuple[str, ...]:
    """Every way the live inventory contradicts the declared policy.

    Empty means the application refuses exactly what the policy says it refuses.
    This is the gate, and it reads the running app rather than the source — a
    policy nobody applied fails here just as loudly as a policy applied wrongly.
    """
    routes = documentation_routes(app)
    violations: list[str] = []

    for route in routes:
        cookie_guards = sorted(set(route.guards) & COOKIE_PLANE_GUARDS)
        if cookie_guards:
            violations.append(
                f"{route.path} is guarded by the cookie plane {cookie_guards}; "
                "API documentation authenticates by bearer token or not at all"
            )

    for plane in DocumentationPlane:
        exposure = policy.exposure(plane)
        served = [route for route in routes if route.plane is plane]
        if exposure is DocumentationExposure.DISABLED:
            if served:
                violations.append(
                    f"{plane} documentation is DISABLED for {policy.environment} "
                    f"but these routes are mounted: {[r.path for r in served]}"
                )
            continue
        if not served:
            violations.append(
                f"{plane} documentation is {exposure} for {policy.environment} "
                "but no route is mounted"
            )
            continue
        for route in served:
            if exposure is DocumentationExposure.PUBLIC and route.guards:
                violations.append(
                    f"{route.path} is declared {exposure} but carries guards "
                    f"{list(route.guards)}"
                )
            if (
                exposure is DocumentationExposure.PLATFORM_BEARER
                and BEARER_PLANE_GUARD not in route.guards
            ):
                violations.append(
                    f"{route.path} is declared {exposure} but does not depend on "
                    f"{BEARER_PLANE_GUARD}; guards={list(route.guards)}"
                )

    return tuple(violations)


def documentation_arguments(policy: ApiDocumentationPolicy) -> dict[str, Any]:
    """The `FastAPI(...)` keyword arguments that realise `policy`.

    This is the half that replaces the product's post-hoc route deletion. A
    plane that is not PUBLIC contributes `None` for every one of its
    coordinates, so FastAPI never mounts the route in the first place.

    `PLATFORM_BEARER` also yields `None` here: FastAPI can only mount the
    document unguarded, so the kernel suppresses its built-in route and mounts a
    guarded one itself (`mount_bearer_protected_document`). Returning `None` and
    then re-mounting is not the surgery this port removed — nothing is deleted,
    and the guarded route is the only one that ever exists.
    """

    arguments: dict[str, Any] = {}
    for attribute, plane in PLANE_BY_ATTRIBUTE.items():
        exposure = policy.exposure(plane)
        if exposure is DocumentationExposure.PUBLIC:
            arguments[attribute] = DEFAULT_PATH_BY_ATTRIBUTE[attribute]
        else:
            arguments[attribute] = None
    return arguments


def mount_bearer_protected_document(app: FastAPI) -> None:
    """Serve `/openapi.json` behind the platform bearer guard.

    `require_platform_admin` is host-exact before it is authenticated, so this
    route 404s off the platform root and 401s without a live platform-admin
    token. `include_in_schema=False` for the same reason FastAPI's own route is:
    the document does not describe itself.
    """

    app.openapi_url = OPENAPI_PATH

    @app.get(
        OPENAPI_PATH,
        include_in_schema=False,
        dependencies=[Depends(require_platform_admin)],
    )
    def platform_openapi_document() -> JSONResponse:
        return JSONResponse(app.openapi())


def environment_api_documentation_policy(
    raw: str | None = None,
) -> ApiDocumentationPolicy:
    """Resolve this process's policy from `ENVIRONMENT`, failing closed.

    The composition-time convenience for an assembly that takes its exposure
    from the deployment environment rather than declaring one literal. Read once,
    while the assembly is being composed — never on a request path.
    """

    value = raw if raw is not None else os.getenv(ENVIRONMENT_ENV_VAR)
    return api_documentation_policy(classify_environment(value))


__all__ = [
    "BEARER_PLANE_GUARD",
    "COOKIE_PLANE_GUARDS",
    "DEFAULT_PATH_BY_ATTRIBUTE",
    "DEVELOPMENT",
    "ENVIRONMENT_ENV_VAR",
    "OPENAPI_PATH",
    "PLANE_BY_ATTRIBUTE",
    "POLICIES",
    "PRODUCTION",
    "REDOC_PATH",
    "SWAGGER_OAUTH2_REDIRECT_PATH",
    "SWAGGER_PATH",
    "TEST",
    "ApiDocumentationPolicy",
    "ApiDocumentationPolicyError",
    "ApiDocumentationPolicyViolation",
    "DocumentationExposure",
    "DocumentationPlane",
    "DocumentationRoute",
    "api_documentation_policy",
    "audit_api_documentation",
    "classify_environment",
    "documentation_arguments",
    "documentation_routes",
    "environment_api_documentation_policy",
    "mount_bearer_protected_document",
]
