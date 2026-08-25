"""The browser CSRF contract: explicit routes, signed tokens, two transports."""

from __future__ import annotations

import pytest
from dotmac_kernel.config import Settings, validate_settings
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.middleware.csrf import (
    CSRF_COOKIE,
    CSRF_HOST_COOKIE,
    CSRFMiddleware,
    require_csrf,
)
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient


def _app(*, production: bool = False) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/form", dependencies=(Depends(require_csrf),))
    def form(request: Request) -> dict[str, str]:
        return {"token": request.state.csrf_token}

    @app.post("/form", dependencies=(Depends(require_csrf),))
    async def submit(request: Request) -> dict[str, str]:
        form = await request.form()
        return {"value": str(form.get("value", ""))}

    @app.post("/api")
    def bearer_only_api() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        CSRFMiddleware,
        secret="a-dedicated-csrf-signing-secret",
        production=production,
        session_cookie_names=("access_token",),
    )
    return app


def test_protected_route_fails_loudly_without_csrf_middleware() -> None:
    app = FastAPI()

    @app.get("/form", dependencies=(Depends(require_csrf),))
    def form() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        with pytest.raises(RuntimeError, match="CSRFMiddleware"):
            client.get("/form")


def test_pre_auth_unsafe_request_without_any_cookie_is_rejected() -> None:
    with TestClient(_app()) as client:
        response = client.post(
            "/form",
            data={"value": "attacker"},
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_signed_token_is_accepted_through_header_transport() -> None:
    with TestClient(_app()) as client:
        token = client.get("/form").json()["token"]
        response = client.post(
            "/form",
            data={"value": "ok"},
            headers={"X-CSRF-Token": token},
        )

    assert response.status_code == 200
    assert response.json() == {"value": "ok"}


def test_signed_token_is_accepted_through_hidden_form_transport() -> None:
    with TestClient(_app()) as client:
        token = client.get("/form").json()["token"]
        response = client.post(
            "/form",
            data={"value": "native", "csrf_token": token},
        )

    assert response.status_code == 200
    assert response.json() == {"value": "native"}


def test_tampered_token_is_rejected_even_when_cookie_and_header_match() -> None:
    with TestClient(_app()) as client:
        token = client.get("/form").json()["token"]
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        client.cookies.set(CSRF_COOKIE, tampered)
        response = client.post("/form", headers={"X-CSRF-Token": tampered})

    assert response.status_code == 403


def test_explicit_cross_site_provenance_is_rejected_even_with_a_valid_token() -> None:
    with TestClient(_app()) as client:
        token = client.get("/form").json()["token"]
        response = client.post(
            "/form",
            headers={
                "X-CSRF-Token": token,
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )

    assert response.status_code == 403


def test_same_origin_provenance_is_accepted_with_a_valid_token() -> None:
    with TestClient(_app(), base_url="https://example.test") as client:
        token = client.get("/form").json()["token"]
        response = client.post(
            "/form",
            headers={
                "X-CSRF-Token": token,
                "Origin": "https://example.test",
                "Sec-Fetch-Site": "same-origin",
            },
        )

    assert response.status_code == 200


def test_token_is_bound_to_the_current_browser_session_cookie() -> None:
    with TestClient(_app()) as client:
        anonymous = client.get("/form").json()["token"]
        client.cookies.set("access_token", "new-session")
        rejected = client.post("/form", headers={"X-CSRF-Token": anonymous})
        rotated = client.get("/form").json()["token"]
        accepted = client.post("/form", headers={"X-CSRF-Token": rotated})

    assert rejected.status_code == 403
    assert rotated != anonymous
    assert accepted.status_code == 200


def test_bearer_only_api_is_outside_csrf_by_explicit_dependency_shape() -> None:
    with TestClient(_app()) as client:
        response = client.post("/api")
    assert response.status_code == 200


def test_production_cookie_uses_host_prefix_secure_path_and_no_domain() -> None:
    with TestClient(_app(production=True), base_url="https://example.test") as client:
        response = client.get("/form")

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{CSRF_HOST_COOKIE}=")
    assert "Secure" in set_cookie
    assert "Path=/" in set_cookie
    assert "Max-Age=7200" in set_cookie
    assert "Domain=" not in set_cookie


def test_production_requires_a_strong_dedicated_csrf_secret() -> None:
    shared = "x" * 32
    configured = Settings(
        environment="production",
        database_url="postgresql://database",
        platform_database_url="postgresql://platform",
        trusted_hosts="example.test",
        platform_root_domain="platform.example.test",
        jwt_secret=shared,
        session_hash_secret="y" * 32,
        csrf_secret=shared,
    )

    errors = validate_settings(configured)

    assert any("distinct" in error for error in errors)


def test_production_rejects_a_short_csrf_secret() -> None:
    configured = Settings(
        environment="production",
        database_url="postgresql://database",
        platform_database_url="postgresql://platform",
        trusted_hosts="example.test",
        platform_root_domain="platform.example.test",
        jwt_secret="x" * 32,
        session_hash_secret="y" * 32,
        csrf_secret="too-short",
    )

    assert any("at least 32 bytes" in error for error in validate_settings(configured))
