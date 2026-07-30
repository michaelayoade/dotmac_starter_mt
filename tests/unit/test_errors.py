from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.exceptions import ConflictError, NotFoundError, UnauthorizedError
from dotmac_kernel.middleware.observability import ObservabilityMiddleware
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    # Populates `request_id_var` the same way app.main wires it in production
    # — the HTML negotiation tests below assert the branded page surfaces a
    # real request id, not just an empty envelope field.
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/missing")
    def missing():
        raise NotFoundError("Widget not found")

    @app.get("/http-forbidden")
    def http_forbidden():
        raise HTTPException(status_code=403, detail="Nope")

    @app.get("/http-teapot")
    def http_teapot():
        raise HTTPException(status_code=418, detail="I'm a teapot")

    @app.get("/http-auth")
    def http_auth():
        raise HTTPException(
            status_code=401,
            detail="Credentials required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.get("/conflict")
    def conflict():
        raise ConflictError("Duplicate slug")

    @app.get("/unauthorized")
    def unauthorized():
        raise UnauthorizedError("Invalid credentials")

    @app.get("/boom")
    def boom():
        raise RuntimeError("nope")

    return app


def test_not_found_envelope():
    client = TestClient(_make_app())
    resp = client.get("/missing")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "not_found"
    assert body["message"] == "Widget not found"
    assert "request_id" in body


def test_conflict_envelope():
    resp = TestClient(_make_app()).get("/conflict")
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


def test_unhandled_exception_is_opaque():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"
    assert "nope" not in resp.text


def test_unauthorized_envelope():
    resp = TestClient(_make_app()).get("/unauthorized")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "unauthorized"
    assert body["message"] == "Invalid credentials"


def test_bare_http_exception_envelope():
    """A bare HTTPException must return the same envelope shape as DomainErrors."""
    resp = TestClient(_make_app()).get("/http-forbidden")
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "forbidden"
    assert body["message"] == "Nope"
    assert "request_id" in body
    assert "detail" not in body


def test_http_exception_unknown_status_falls_back_to_http_error():
    resp = TestClient(_make_app()).get("/http-teapot")
    assert resp.status_code == 418
    assert resp.json()["code"] == "http_error"


def test_http_exception_preserves_headers():
    resp = TestClient(_make_app()).get("/http-auth")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    assert resp.json()["code"] == "unauthorized"


def test_validation_error_envelope():
    app = _make_app()

    @app.get("/typed/{n}")
    def typed(n: int):
        return {"n": n}

    resp = TestClient(app).get("/typed/abc")
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"
    assert isinstance(resp.json()["details"], list)


# ---------------------------------------------------------------------------
# Content negotiation: Accept: text/html gets the branded page (same
# envelope data as the JSON sibling); anything else keeps the JSON envelope
# byte-identical to the tests above (default TestClient sends "Accept: */*",
# which does not contain "text/html").
# ---------------------------------------------------------------------------


def test_json_client_unaffected_by_negotiation():
    """Explicit `Accept: application/json` still gets the plain envelope."""
    resp = TestClient(_make_app()).get(
        "/missing", headers={"Accept": "application/json"}
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["code"] == "not_found"
    assert body["message"] == "Widget not found"


def test_html_client_gets_branded_404_page():
    resp = TestClient(_make_app()).get("/missing", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "Page not found" in resp.text
    assert "Widget not found" in resp.text


def test_html_client_404_page_contains_request_id():
    resp = TestClient(_make_app()).get("/missing", headers={"Accept": "text/html"})
    assert "Reference ID:" in resp.text


def test_html_client_gets_branded_500_page():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom", headers={"Accept": "text/html"})
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("text/html")
    assert "Server error" in resp.text
    assert "nope" not in resp.text


def test_htmx_request_accepting_html_gets_html_not_json():
    """HTMX sends `Accept: text/html, */*` on every request — it must get the
    branded HTML page, same as a plain browser navigation."""
    resp = TestClient(_make_app()).get(
        "/missing",
        headers={"Accept": "text/html, */*", "HX-Request": "true"},
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")


def test_html_client_gets_branded_403_page_for_bare_http_exception():
    resp = TestClient(_make_app()).get(
        "/http-forbidden", headers={"Accept": "text/html"}
    )
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("text/html")
    assert "Access denied" in resp.text


def test_html_client_unmapped_status_falls_back_to_generic_template():
    """418 has no dedicated template — falls back to the 400-shaped generic
    page (status < 500) rather than a raw error or missing template."""
    resp = TestClient(_make_app()).get("/http-teapot", headers={"Accept": "text/html"})
    assert resp.status_code == 418
    assert resp.headers["content-type"].startswith("text/html")


def test_html_client_401_preserves_www_authenticate_header():
    resp = TestClient(_make_app()).get("/http-auth", headers={"Accept": "text/html"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    assert resp.headers["content-type"].startswith("text/html")


# ---------------------------------------------------------------------------
# Error-page render failure fallback: if template rendering throws
# (missing/corrupt template), the JSON envelope is returned instead, with
# the correct status code and a logged exception.
# ---------------------------------------------------------------------------


def test_html_client_render_failure_falls_back_to_json_envelope(monkeypatch, caplog):
    """When HTML rendering fails, a browser request gets the JSON envelope."""
    from dotmac_kernel import errors as errors_module

    # Monkeypatch render_error to throw an exception
    def broken_render_error(*args, **kwargs):
        raise RuntimeError("Template rendering failed")

    monkeypatch.setattr(errors_module, "render_error", broken_render_error)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/missing", headers={"Accept": "text/html"})

    # Should get JSON 404, not HTML or 500
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["code"] == "not_found"
    assert body["message"] == "Widget not found"
    assert "request_id" in body

    # The exception must be logged
    assert "Error-page render failed" in caplog.text


def test_csrf_middleware_render_failure_falls_back_to_json_envelope(
    monkeypatch, caplog
):
    """CSRF middleware (outside ExceptionMiddleware) also gets JSON fallback."""

    from dotmac_kernel import errors as errors_module
    from dotmac_kernel.middleware.csrf import CSRFMiddleware

    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(CSRFMiddleware, enabled=True)

    @app.post("/form")
    def form():
        return {"ok": True}

    # Monkeypatch render_error to throw
    def broken_render_error(*args, **kwargs):
        raise RuntimeError("Template rendering failed")

    monkeypatch.setattr(errors_module, "render_error", broken_render_error)

    client = TestClient(app, raise_server_exceptions=False)
    # CSRF check will fail (no csrf token in header), triggering _negotiate.
    # We need to set a cookie so the middleware runs the CSRF check.
    resp = client.post(
        "/form", headers={"Accept": "text/html"}, cookies={"other_cookie": "value"}
    )

    # Should get JSON 403 with csrf_failed code, not HTML or 500
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["code"] == "csrf_failed"
    assert "request_id" in body

    # The exception must be logged
    assert "Error-page render failed" in caplog.text


# NOTE: `request.state.branding` fallback/override behavior for HTML error
# pages is exercised directly against `dotmac_kernel.templating.render()` in
# `tests/unit/test_templating.py` (Task 4 / F4) -- none of this app's
# `errors/*.html` templates actually surface `brand.name` in their body (the
# `{% block title %}` that would show it is overridden by every status-code
# template), so a `TestClient` assertion against error-page HTML text
# cannot observe the enrichment; the render()-level test can.
