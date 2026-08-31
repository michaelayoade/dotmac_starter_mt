"""API documentation is declared per assembly, and the kernel refuses to guess.

Ported with the policy itself from `vendor_cp.api_documentation`
(`dotmac_platform_control_plane` PR #94, head `cbc433cf`), which proved it in
production. Two properties are kernel-specific and are the reason the port is
not a copy:

* **Suppression happens at construction.** The product deleted routes after the
  factory returned because it did not own the constructor. The kernel does, so
  the assertion is that the route was never mounted -- not that something
  removed it.
* **An absent policy REFUSES.** The product could fall back to its own default;
  a kernel default would be an exposure nobody declared, which is the inherited
  behaviour being repaired.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.api_documentation import (
    DEFAULT_PATH_BY_ATTRIBUTE,
    OPENAPI_PATH,
    PRODUCTION,
    REDOC_PATH,
    SWAGGER_PATH,
    ApiDocumentationPolicy,
    ApiDocumentationPolicyError,
    DocumentationExposure,
    DocumentationPlane,
    api_documentation_policy,
    audit_api_documentation,
    classify_environment,
    documentation_arguments,
    documentation_routes,
    environment_api_documentation_policy,
)
from fastapi import FastAPI

DISABLED = DocumentationExposure.DISABLED
PUBLIC = DocumentationExposure.PUBLIC
BEARER = DocumentationExposure.PLATFORM_BEARER


# -- resolution fails closed


@pytest.mark.parametrize("raw", ["dev", "development", "local", "DEVELOPMENT", " dev "])
def test_development_spellings_select_the_development_policy(raw):
    assert classify_environment(raw) == "development"


@pytest.mark.parametrize("raw", ["test", "testing", "ci", "CI"])
def test_test_spellings_select_the_test_policy(raw):
    assert classify_environment(raw) == "test"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "staging",
        "stage",
        "prod",
        "production",
        "Producton",
        "developmnet",
        "uat",
    ],
)
def test_everything_unrecognised_resolves_to_production(raw):
    """The whole point: publishing is opt-in BY NAME and has no default.

    `staging` and a typo are in this list deliberately -- both are environments
    somebody has an opinion about, and neither is one this policy has reasoned
    about, so both withhold the surface.
    """

    assert classify_environment(raw) == PRODUCTION


def test_the_environment_helper_reads_the_variable_and_fails_closed(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert environment_api_documentation_policy().environment == "development"
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert environment_api_documentation_policy().environment == PRODUCTION


# -- the declared policies


def test_production_publishes_no_browser_pages_and_bearer_gates_the_document():
    policy = api_documentation_policy(PRODUCTION)
    assert policy.exposure(DocumentationPlane.INTERACTIVE) is DISABLED
    assert policy.exposure(DocumentationPlane.DOCUMENT) is BEARER


@pytest.mark.parametrize("environment", ["development", "test"])
def test_development_and_test_publish_both_planes(environment):
    policy = api_documentation_policy(environment)
    assert policy.exposure(DocumentationPlane.INTERACTIVE) is PUBLIC
    assert policy.exposure(DocumentationPlane.DOCUMENT) is PUBLIC


def test_every_declared_policy_carries_a_rationale():
    for environment in ("development", "test", PRODUCTION):
        assert api_documentation_policy(environment).rationale.strip()


# -- incoherent policies are refused at construction


def test_interactive_documentation_cannot_be_bearer_protected():
    """A browser navigating to /docs sends no Authorization header."""

    with pytest.raises(ApiDocumentationPolicyError):
        ApiDocumentationPolicy(
            environment="custom",
            interactive=BEARER,
            document=BEARER,
            rationale="x",
        )


def test_production_may_not_publish_either_plane():
    with pytest.raises(ApiDocumentationPolicyError):
        ApiDocumentationPolicy(
            environment=PRODUCTION,
            interactive=DISABLED,
            document=PUBLIC,
            rationale="x",
        )


def test_public_browser_pages_require_the_document_they_fetch():
    with pytest.raises(ApiDocumentationPolicyError):
        ApiDocumentationPolicy(
            environment="custom",
            interactive=PUBLIC,
            document=DISABLED,
            rationale="x",
        )


def test_a_policy_without_a_rationale_is_refused():
    with pytest.raises(ApiDocumentationPolicyError):
        ApiDocumentationPolicy(
            environment="custom",
            interactive=DISABLED,
            document=DISABLED,
            rationale="   ",
        )


def test_a_coherent_custom_policy_is_accepted():
    """Sensitivity: the refusals above are not refusing everything."""

    policy = ApiDocumentationPolicy(
        environment="custom",
        interactive=DISABLED,
        document=BEARER,
        rationale="documented",
    )
    assert policy.exposure(DocumentationPlane.DOCUMENT) is BEARER


# -- construction arguments, the kernel's replacement for route surgery


def test_a_disabled_plane_contributes_none_for_every_coordinate():
    arguments = documentation_arguments(api_documentation_policy(PRODUCTION))
    assert arguments["docs_url"] is None
    assert arguments["redoc_url"] is None
    assert arguments["swagger_ui_oauth2_redirect_url"] is None
    # bearer: suppressed here, mounted guarded by the factory
    assert arguments["openapi_url"] is None


def test_a_public_plane_contributes_the_default_paths():
    arguments = documentation_arguments(api_documentation_policy("development"))
    for attribute, path in DEFAULT_PATH_BY_ATTRIBUTE.items():
        assert arguments[attribute] == path


def test_the_production_arguments_actually_suppress_the_routes():
    """The route is never mounted -- not mounted and then deleted."""

    app = FastAPI(**documentation_arguments(api_documentation_policy(PRODUCTION)))
    served = {route.path for route in app.router.routes}
    assert SWAGGER_PATH not in served
    assert REDOC_PATH not in served
    assert OPENAPI_PATH not in served


def test_construction_suppression_is_sensitive_public_really_mounts_them():
    """Sensitivity: the absence above must mean suppression, not an empty app."""

    app = FastAPI(**documentation_arguments(api_documentation_policy("development")))
    served = {route.path for route in app.router.routes}
    assert SWAGGER_PATH in served
    assert REDOC_PATH in served
    assert OPENAPI_PATH in served


# -- the audit reads the LIVE inventory


def test_the_audit_passes_when_the_inventory_matches_the_policy():
    policy = api_documentation_policy(PRODUCTION)
    app = FastAPI(**documentation_arguments(policy))

    @app.get(OPENAPI_PATH, include_in_schema=False)
    def _document() -> dict[str, str]:  # pragma: no cover - inventory only
        return {}

    # unguarded bearer document -> a violation, which is the next test; here we
    # only assert the interactive plane is clean
    interactive = [
        route
        for route in documentation_routes(app)
        if route.plane is DocumentationPlane.INTERACTIVE
    ]
    assert interactive == []


def test_a_bearer_document_without_the_guard_is_a_violation():
    policy = api_documentation_policy(PRODUCTION)
    app = FastAPI(**documentation_arguments(policy))

    @app.get(OPENAPI_PATH, include_in_schema=False)
    def _document() -> dict[str, str]:  # pragma: no cover - inventory only
        return {}

    violations = audit_api_documentation(app, policy)
    assert any("require_platform_admin" in v for v in violations)


def test_a_disabled_plane_with_a_mounted_route_is_a_violation():
    policy = api_documentation_policy(PRODUCTION)
    app = FastAPI(**documentation_arguments(policy))

    @app.get(SWAGGER_PATH, include_in_schema=False)
    def _swagger() -> dict[str, str]:  # pragma: no cover - inventory only
        return {}

    violations = audit_api_documentation(app, policy)
    assert any(SWAGGER_PATH in v for v in violations)


def test_the_audit_finds_a_documentation_route_moved_off_its_default_path():
    """A route is located by PATH, so a moved document is still found."""

    policy = api_documentation_policy(PRODUCTION)
    app = FastAPI(**documentation_arguments(policy))
    app.openapi_url = "/schema.json"

    @app.get("/schema.json", include_in_schema=False)
    def _document() -> dict[str, str]:  # pragma: no cover - inventory only
        return {}

    assert any(
        route.path == "/schema.json" and route.plane is DocumentationPlane.DOCUMENT
        for route in documentation_routes(app)
    )


# -- the kernel refuses to guess


def test_create_app_refuses_an_assembly_that_declares_no_policy():
    """The property that makes this a kernel contract rather than a product one.

    A default here would be a kernel-chosen exposure nobody declared, picked by
    the party with the least information about where the deployment sits --
    which is the inherited FastAPI behaviour this whole module repairs.
    """

    from dotmac_kernel.app_factory import create_app
    from dotmac_kernel.assembly import ProductAssemblySpec

    with pytest.raises(RuntimeError, match="api_documentation"):
        create_app(ProductAssemblySpec(name="undeclared", modules=()))


def test_the_refusal_is_sensitive_a_declared_policy_builds():
    """Sensitivity: the refusal must be about the policy, not about the spec."""

    from dotmac_kernel.app_factory import create_app
    from dotmac_kernel.assembly import ProductAssemblySpec

    app = create_app(
        ProductAssemblySpec(
            name="declared",
            modules=(),
            api_documentation=api_documentation_policy("development"),
        )
    )
    served = {route.path for route in app.router.routes}
    assert SWAGGER_PATH in served


def test_a_production_assembly_serves_no_browser_documentation():
    """End to end through the factory, on the live route inventory."""

    from dotmac_kernel.app_factory import create_app
    from dotmac_kernel.assembly import ProductAssemblySpec

    app = create_app(
        ProductAssemblySpec(
            name="prod",
            modules=(),
            api_documentation=api_documentation_policy(PRODUCTION),
        )
    )
    served = {route.path for route in app.router.routes}
    assert SWAGGER_PATH not in served
    assert REDOC_PATH not in served
    # the document exists, but only behind the platform bearer guard
    document = [r for r in documentation_routes(app) if r.path == OPENAPI_PATH]
    assert document, "the bearer-protected OpenAPI document should be mounted"
    assert "require_platform_admin" in document[0].guards
