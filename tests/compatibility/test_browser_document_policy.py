"""Canaries for reviewed inline script hashes on one authenticated document."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from dotmac_kernel import create_app
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.assembly import ProductAssemblySpec, ProductSecurityPolicy
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.web_surfaces import (
    AuthenticationProfileBinding,
    BrowserCapabilityProvision,
    BrowserCapabilityRequirement,
    BrowserCredentialTransport,
    BrowserDocumentPolicyV1,
    BrowserSecurityPlane,
    BrowserSecurityRequirement,
    BrowserSessionPolicy,
    InlineScriptHashV1,
    TemplateRef,
    WebFacetMount,
    WebRouteRef,
    WebSurfaceContribution,
    qualified_route_name,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.testclient import TestClient

SCRIPT = "document.body.dataset.console = 'ready';"
HASH = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
# Literal ACADEMY_CONTENT_SECURITY_POLICY from app/config.py at Academy
# 124155959c755cbb142099dd5517acc259f56fa8; independent of future Kernel defaults.
BASELINE = (
    "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
    "form-action 'self'; object-src 'none'; img-src 'self' data:; "
    "font-src 'self'; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'"
)
POLICY = BrowserDocumentPolicyV1(
    facet="learner",
    route=WebRouteRef("labs", "console", "document"),
    inline_scripts=(InlineScriptHashV1(HASH),),
)


class Provider:
    transport = BrowserCredentialTransport.COOKIE_SESSION
    calls = 0

    @staticmethod
    def dependency(request: Request) -> object:
        Provider.calls += 1
        if request.headers.get("x-deny"):
            raise HTTPException(401)
        return SimpleNamespace(id=uuid.uuid4())


def spec(
    *,
    policy=POLICY,
    method="GET",
    path="/document",
    public=False,
    router_dependencies=(),
    contexts=None,
    provider=None,
):
    contexts = [] if contexts is None else contexts
    router = APIRouter(dependencies=list(router_dependencies))

    @router.api_route(path, methods=[method], name="document")
    async def document(request: Request, count: int = 1):
        kind = request.query_params.get("kind", "html")
        if kind == "prestart":

            class BeforeStartFailure(HTMLResponse):
                async def __call__(self, scope, receive, send):
                    raise HTTPException(403)

            return BeforeStartFailure("never sent")
        if kind == "error":
            raise HTTPException(403)
        if kind == "redirect":
            return RedirectResponse("/learner/ordinary")
        if kind == "json":
            return {"ok": True}
        if kind == "capture":
            contexts.append(request.scope.get("_dotmac_browser_document_context"))
        if kind == "replay":
            request.scope["_dotmac_browser_document_context"] = contexts[0]
        if kind == "spoof":
            request.scope["_dotmac_browser_document_context"] = (POLICY, request.scope)
        if kind == "missing":
            request.scope.pop("_dotmac_browser_document_context", None)
        return HTMLResponse(
            f"<script>{SCRIPT}</script>",
            status_code=201 if kind == "created" else 200,
            headers={"Content-Security-Policy": request.query_params["csp"]}
            if "csp" in request.query_params
            else {},
        )

    @router.get("/ordinary", name="ordinary")
    def ordinary(request: Request):
        return HTMLResponse(
            "ordinary",
            headers={"Content-Security-Policy": request.query_params["csp"]}
            if "csp" in request.query_params
            else {},
        )

    @router.get("/login", name="login")
    def login():
        return HTMLResponse("login")

    @router.post("/logout", name="logout")
    def logout():
        return HTMLResponse("logout")

    login_ref = WebRouteRef("labs", "console", "login")
    return ProductAssemblySpec(
        name="document-policy-test",
        api_documentation=api_documentation_policy("development"),
        modules=(
            ModuleManifest(
                code="labs",
                version="1.0.0",
                web_surfaces=(
                    WebSurfaceContribution(
                        code="console",
                        facet="learner",
                        routers=(router,),
                        supported_ui_contract_versions=frozenset({1}),
                    ),
                ),
            ),
        ),
        web_facets=(
            WebFacetMount(
                code="learner",
                url_prefix="/learner",
                shell=TemplateRef("layouts/admin.html"),
                authentication_profile="session",
                entry_routes=(login_ref,),
                login_route=login_ref,
                logout_route=WebRouteRef("labs", "console", "logout"),
            ),
        ),
        authentication_profiles=(
            AuthenticationProfileBinding(
                code="session",
                provider=None if public else provider or Provider(),
                session=None
                if public
                else BrowserSessionPolicy(cookie_name="console_session"),
                security_plane=BrowserSecurityPlane.NONE
                if public
                else BrowserSecurityPlane.PLATFORM,
            ),
        ),
        ui_contract_version=1,
        browser_document_policies=(policy,),
        security_policy=ProductSecurityPolicy(content_security_policy=BASELINE),
    )


@pytest.fixture(autouse=True)
def isolated_browser_runtime(monkeypatch):
    import dotmac_kernel.app_factory as factory
    import fastapi
    import starlette

    assert (fastapi.__version__, starlette.__version__) == ("0.140.13", "1.10.0")
    monkeypatch.setattr(factory.settings, "environment", "dev")
    monkeypatch.setattr(factory.settings, "seed_on_startup", False)
    monkeypatch.setattr(factory.settings, "security_headers_enabled", True)
    monkeypatch.setattr(factory.settings, "content_security_policy", "")
    monkeypatch.setattr(factory.settings, "rate_limit_enabled", False)
    # Browser-only assemblies do not need live startup database diagnostics.
    monkeypatch.setattr(factory, "_required_setting_errors", lambda: [])
    monkeypatch.setattr(factory, "_tenancy_errors", lambda: [])


@pytest.fixture
def client(monkeypatch):
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="document-policy")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, value: value)
    with TestClient(create_app(spec())) as value:
        yield value


def test_only_reviewed_script_sources_change_on_authenticated_document(client):
    before = Provider.calls
    response = client.get("/learner/document")
    assert response.status_code == 200
    assert Provider.calls > before
    expected = BASELINE.replace("script-src 'self'", f"script-src 'sha256-{HASH}'")
    assert response.headers.get_list("content-security-policy") == [expected]
    assert "https:" not in expected
    for path in ("/learner/ordinary", "/learner/login"):
        assert client.get(path).headers["content-security-policy"] == BASELINE
    denied = client.get("/learner/document", headers={"x-deny": "1"})
    assert denied.status_code == 401
    assert denied.headers["content-security-policy"] == BASELINE


@pytest.mark.parametrize(
    "kind", ["error", "redirect", "json", "created", "spoof", "missing"]
)
def test_non_document_and_untrusted_context_keep_global_policy(client, kind):
    response = client.get(f"/learner/document?kind={kind}", follow_redirects=False)
    assert response.headers.get_list("content-security-policy") == [BASELINE]


@pytest.mark.parametrize(
    "digest",
    [
        "",
        "sha256-" + HASH,
        "'sha256-" + HASH + "'",
        HASH.rstrip("="),
        "A" * 44,
        " " + HASH,
        "AA==",
        HASH[:-2] + "B=",
    ],
)
def test_noncanonical_hash_is_refused(digest):
    with pytest.raises(ValueError):
        InlineScriptHashV1(digest)


def test_empty_and_duplicate_hash_sets_are_refused():
    for scripts in ((), (InlineScriptHashV1(HASH),) * 2):
        with pytest.raises(ValueError):
            replace(POLICY, inline_scripts=scripts)


@pytest.mark.parametrize("route", ["missing", "login", "logout"])
def test_unknown_and_entry_bindings_are_refused(route):
    with pytest.raises(ValueError):
        create_app(
            spec(policy=replace(POLICY, route=WebRouteRef("labs", "console", route)))
        )


@pytest.mark.parametrize(
    "options", [{"public": True}, {"method": "POST"}, {"path": "/{rest:path}"}]
)
def test_public_non_get_and_catchall_bindings_are_refused(options):
    with pytest.raises(ValueError):
        create_app(spec(**options))


def test_duplicate_binding_is_refused():
    with pytest.raises(ValueError):
        create_app(replace(spec(), browser_document_policies=(POLICY, POLICY)))


def test_script_none_cannot_be_reopened():
    with pytest.raises(ValueError):
        create_app(
            replace(
                spec(),
                security_policy=ProductSecurityPolicy(
                    content_security_policy=BASELINE.replace(
                        "script-src 'self'", "script-src 'none'"
                    )
                ),
            )
        )


def test_concurrent_documents_do_not_leak_to_unbound_routes(client):
    paths = ["/learner/document", "/learner/ordinary", "/learner/login"] * 8
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(client.get, paths))
    for path, response in zip(paths, responses, strict=True):
        expected = (
            BASELINE.replace("script-src 'self'", f"script-src 'sha256-{HASH}'")
            if path == "/learner/document"
            else BASELINE
        )
        assert response.headers.get_list("content-security-policy") == [expected]


def test_context_cannot_be_replayed_across_requests_or_app_instances(monkeypatch):
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="document-policy")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, value: value)
    contexts = []
    first = create_app(spec(contexts=contexts))
    second = create_app(spec(contexts=contexts))
    with TestClient(first) as left, TestClient(second) as right:
        assert (
            HASH
            in left.get("/learner/document?kind=capture").headers[
                "content-security-policy"
            ]
        )
        for client in (left, right):
            assert (
                client.get("/learner/document?kind=replay").headers[
                    "content-security-policy"
                ]
                == BASELINE
            )


@pytest.mark.parametrize(
    "raw",
    [
        "script-src 'self'",
        BASELINE + "; script-src-elem 'self'",
        BASELINE.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'"),
        BASELINE.replace("script-src 'self'", "script-src 'self' 'unsafe-eval'"),
        BASELINE.replace("connect-src 'self'", "connect-src 'self' https:"),
    ],
)
def test_existing_raw_policy_refusals_are_preserved(raw):
    with pytest.raises(ValueError):
        create_app(
            replace(
                spec(),
                security_policy=ProductSecurityPolicy(content_security_policy=raw),
            )
        )


@pytest.mark.parametrize("requirement", list(BrowserSecurityRequirement))
def test_raw_policy_with_typed_requirements_still_refuses(requirement):
    value = spec()
    manifest = value.modules[0]
    surface = replace(
        manifest.web_surfaces[0],
        browser_capabilities=(BrowserCapabilityRequirement("console.mechanics", 1),),
    )
    value = replace(
        value,
        modules=(replace(manifest, web_surfaces=(surface,)),),
        browser_capabilities=(
            BrowserCapabilityProvision(
                "console.mechanics", 1, frozenset({requirement})
            ),
        ),
    )
    with pytest.raises(ValueError, match="raw CSP override cannot replace"):
        create_app(value)


def test_custom_slash_consuming_converter_is_refused(monkeypatch):
    from starlette.convertors import CONVERTOR_TYPES, Convertor

    class Unbounded(Convertor):
        regex = ".+"

        def convert(self, value):
            return value

    monkeypatch.setitem(CONVERTOR_TYPES, "unbounded", Unbounded())
    with pytest.raises(ValueError, match="bounded GET"):
        create_app(spec(path="/{rest:unbounded}"))


def test_bounded_route_parameter_is_supported():
    # Construction must admit the positive parameter case as well as rejecting
    # catchalls; the policy binds a named route, not an arbitrary path prefix.
    create_app(spec(path="/labs/{lab_id:uuid}/document"))


def test_handled_error_rendered_as_html_200_keeps_global_policy(monkeypatch):
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware
    from starlette.exceptions import HTTPException as StarletteHTTPException

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="document-policy")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, value: value)
    app = create_app(spec())

    @app.exception_handler(StarletteHTTPException)
    async def handled(request, exc):
        return HTMLResponse("handled error", status_code=200)

    with TestClient(app) as client:
        response = client.get("/learner/document?kind=error")
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == BASELINE


def test_web_disabled_ignores_browser_document_declarations():
    create_app(replace(spec(), web_enabled=False))


@pytest.mark.parametrize("path", ["/learner/ordinary", "/learner/document"])
@pytest.mark.parametrize(
    "downstream", ["script-src 'none'", "script-src 'unsafe-inline'", BASELINE]
)
def test_downstream_policies_are_never_removed_to_enable_grant(
    client, path, downstream
):
    response = client.get(path, params={"csp": downstream})
    assert response.headers.get_list("content-security-policy") == [
        downstream,
        BASELINE,
    ]


def test_identical_selected_downstream_policy_is_collapsed_once(client):
    selected = BASELINE.replace("script-src 'self'", f"script-src 'sha256-{HASH}'")
    response = client.get("/learner/document", params={"csp": selected})
    assert response.headers.get_list("content-security-policy") == [selected]


def _capture_terminal_request(app):
    messages = []

    async def request():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/learner/document",
            "raw_path": b"/learner/document",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }

        request_received = False
        connected = asyncio.Event()

        async def receive():
            nonlocal request_received
            if not request_received:
                request_received = True
                return {"type": "http.request", "body": b"", "more_body": False}
            # Keep the client connected after the complete request body. Response
            # completion cancels any disconnect listener; no second request event
            # may confound the terminal dependency-cleanup exception under test.
            await connected.wait()
            raise AssertionError("connected-client event must remain unset")

        async def send(message):
            messages.append(dict(message))

        with pytest.raises(RuntimeError, match="response already started"):
            await asyncio.wait_for(app(scope, receive, send), timeout=10)

    asyncio.run(request())
    starts = [
        message for message in messages if message["type"] == "http.response.start"
    ]
    assert len(starts) == 1
    assert starts[0]["status"] == 200
    policies = [
        value.decode()
        for name, value in starts[0]["headers"]
        if name.lower() == b"content-security-policy"
    ]
    assert policies == [
        BASELINE.replace("script-src 'self'", f"script-src 'sha256-{HASH}'")
    ]
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert body == f"<script>{SCRIPT}</script>".encode()


@pytest.mark.parametrize("failure_owner", ["authentication", "source_router"])
def test_request_cleanup_after_start_never_replaces_response(
    monkeypatch, failure_owner
):
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    events = []

    def failing_cleanup():
        events.append("entered")
        yield SimpleNamespace(id=uuid.uuid4())
        events.append("cleanup")
        raise HTTPException(403)

    class GeneratorProvider:
        transport = BrowserCredentialTransport.COOKIE_SESSION
        dependency = staticmethod(failing_cleanup)

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="document-policy")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, value: value)
    app = create_app(
        spec(provider=GeneratorProvider())
        if failure_owner == "authentication"
        else spec(router_dependencies=(Depends(failing_cleanup),))
    )
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def handled(request, exc):
        events.append("handled")
        return HTMLResponse("replacement error body", status_code=200)

    _capture_terminal_request(app)
    assert events == ["entered", "cleanup"]


def test_function_scoped_router_cleanup_before_start_uses_baseline(monkeypatch):
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    class CleanupError(Exception):
        pass

    events = []

    def source_dependency():
        events.append("source_enter")
        yield
        events.append("source_cleanup")
        raise CleanupError()

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="document-policy")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, value: value)
    app = create_app(
        spec(router_dependencies=(Depends(source_dependency, scope="function"),))
    )

    @app.exception_handler(CleanupError)
    async def handled(request, exc):
        events.append("handled")
        return HTMLResponse("router cleanup failed", status_code=200)

    with TestClient(app) as client:
        response = client.get("/learner/document")
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == BASELINE
    assert events == ["source_enter", "source_cleanup", "handled"]


@pytest.mark.parametrize("mutation", ["missing", "earlier_parent"])
def test_construction_refuses_missing_or_enclosed_lifecycle_guard(
    monkeypatch, mutation
):
    import dotmac_kernel.app_factory as factory

    original = factory._effective_route_contexts

    def altered(app):
        for route in original(app):
            if getattr(route, "name", None) == qualified_route_name(
                "learner", "labs", "console", "document"
            ):
                dependencies = list(route.dependant.dependencies)
                if mutation == "missing":
                    dependencies.pop(0)
                else:
                    dependencies.insert(
                        0, SimpleNamespace(call=lambda: None, dependencies=())
                    )
                yield SimpleNamespace(
                    name=route.name,
                    dependant=SimpleNamespace(dependencies=dependencies),
                )
            else:
                yield route

    monkeypatch.setattr(factory, "_effective_route_contexts", altered)
    with pytest.raises(ValueError, match="first effective dependency"):
        create_app(spec())


def test_successful_authentication_generator_cleanup_retains_document_grant(
    monkeypatch,
):
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    events = []

    class GeneratorProvider:
        transport = BrowserCredentialTransport.COOKIE_SESSION

        @staticmethod
        def dependency():
            events.append("authenticated")
            yield SimpleNamespace(id=uuid.uuid4())
            events.append("cleanup")

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="document-policy")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, value: value)
    app = create_app(spec(provider=GeneratorProvider()))
    with TestClient(app) as client:
        response = client.get("/learner/document")
        assert response.headers.get_list("content-security-policy") == [
            BASELINE.replace("script-src 'self'", f"script-src 'sha256-{HASH}'")
        ]
    assert events == ["authenticated", "cleanup"]


@pytest.mark.parametrize(
    "query,deny",
    [
        ("kind=html", True),
        ("kind=error", False),
        ("count=invalid", False),
        ("kind=prestart", False),
    ],
)
def test_prestart_auth_endpoint_validation_and_response_failures_use_baseline(
    monkeypatch, query, deny
):
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware
    from starlette.exceptions import HTTPException as StarletteHTTPException

    tenant = SimpleNamespace(id=uuid.uuid4(), slug="document-policy")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, value: value)
    app = create_app(spec())

    async def handled(request, exc):
        return HTMLResponse("prestart handled error", status_code=200)

    app.add_exception_handler(StarletteHTTPException, handled)
    app.add_exception_handler(RequestValidationError, handled)
    with TestClient(app) as client:
        response = client.get(
            "/learner/document?" + query, headers={"x-deny": "1"} if deny else {}
        )
    assert response.status_code == 200
    assert response.headers.get_list("content-security-policy") == [BASELINE]


def test_active_document_policy_refuses_disabled_kernel_header_writer(monkeypatch):
    from dotmac_kernel.config import settings

    monkeypatch.setattr(settings, "security_headers_enabled", False)
    with pytest.raises(
        ValueError,
        match="browser document policies require the Kernel security-header writer",
    ):
        create_app(spec())


@pytest.mark.parametrize(
    "failure", ["none", "missing_id", "wrong_facet", "wrong_plane"]
)
def test_document_requires_existing_matching_facet_principal(monkeypatch, failure):
    from dotmac_kernel import web_runtime
    from dotmac_kernel.facet_principal import FacetPrincipalError
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    authentication_calls = []

    class ProjectionProvider:
        transport = BrowserCredentialTransport.COOKIE_SESSION

        @staticmethod
        def dependency():
            authentication_calls.append("authenticated")
            if failure == "none":
                return None
            if failure == "missing_id":
                return SimpleNamespace()
            return SimpleNamespace(id=uuid.uuid4())

    original_record = web_runtime.record_facet_principal

    def record_wrong_projection(request, principal):
        if failure == "wrong_facet":
            principal = replace(principal, facet="other_facet")
        elif failure == "wrong_plane":
            principal = replace(
                principal,
                security_plane=BrowserSecurityPlane.TENANT,
                tenant_id=uuid.uuid4(),
            )
        original_record(request, principal)

    monkeypatch.setattr(web_runtime, "record_facet_principal", record_wrong_projection)
    tenant = SimpleNamespace(id=uuid.uuid4(), slug="document-policy")
    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", lambda self, host: tenant)
    monkeypatch.setattr(TenantResolverMiddleware, "_allow", lambda self, value: value)
    app = create_app(spec(provider=ProjectionProvider()))

    @app.exception_handler(FacetPrincipalError)
    async def handled(request, exc):
        return HTMLResponse("principal projection refused", status_code=200)

    with TestClient(app) as client:
        response = client.get("/learner/document")
    assert response.status_code == 200
    assert response.text == "principal projection refused"
    assert response.headers.get_list("content-security-policy") == [BASELINE]
    assert authentication_calls == ["authenticated"]
