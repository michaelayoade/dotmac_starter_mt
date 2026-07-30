"""Security-baseline unit tests (control-plane security Task 5).

Covers: Argon2id storage with legacy-PBKDF2 verify + upgrade-on-login,
constant-work login miss paths, security headers on every response class,
and the bounded rate-limit store contract.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import security
from app.core.middleware.rate_limit import MemoryStore, RateLimitMiddleware
from app.core.middleware.security_headers import (
    _STRICT_CSP,
    SecurityHeadersMiddleware,
)
from app.core.models import Party, PartyPerson, PartyType, Tenant, UserCredential
from app.features.auth import service as auth_service
from app.features.auth.schemas import LoginRequest


def _legacy_pbkdf2_hash(password: str) -> str:
    """The exact pre-Task-5 stdlib format, reproduced for upgrade tests."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, security.PBKDF2_ITERATIONS
    )
    return (
        f"pbkdf2_sha256${security.PBKDF2_ITERATIONS}"
        f"${base64.urlsafe_b64encode(salt).decode()}"
        f"${base64.urlsafe_b64encode(digest).decode()}"
    )


class TestPasswordStorage:
    def test_new_hashes_are_argon2id(self):
        h = security.hash_password("hunter2hunter2")
        assert h.startswith("$argon2id$")
        assert security.verify_password("hunter2hunter2", h)
        assert not security.verify_password("wrong", h)

    def test_legacy_pbkdf2_still_verifies_and_wants_rehash(self):
        legacy = _legacy_pbkdf2_hash("old-password-123")
        assert security.verify_password("old-password-123", legacy)
        assert not security.verify_password("wrong", legacy)
        assert security.password_needs_rehash(legacy)
        assert not security.password_needs_rehash(
            security.hash_password("old-password-123")
        )

    def test_unknown_scheme_verifies_false(self):
        assert not security.verify_password("x", "bcrypt$whatever")
        assert not security.verify_password("x", "")


@pytest.fixture
def tenant_with_legacy_credential(db):
    tenant = Tenant(slug="argon", name="Argon Tenant")
    db.add(tenant)
    db.flush()
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name="Legacy User",
        email="legacy@argon.example.com",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Legacy", last_name="User"))
    credential = UserCredential(
        tenant_id=tenant.id,
        party_id=party.id,
        password_hash=_legacy_pbkdf2_hash("legacy-password-123"),
    )
    db.add(credential)
    db.flush()
    return tenant, credential


class TestLoginHardening:
    def test_legacy_hash_upgrades_on_successful_login(
        self, db, tenant_with_legacy_credential
    ):
        tenant, credential = tenant_with_legacy_credential
        result = auth_service.login(
            db,
            tenant,
            LoginRequest(
                email="legacy@argon.example.com", password="legacy-password-123"
            ),
        )
        assert result.access_token
        assert credential.password_hash.startswith("$argon2id$")
        # ... and the upgraded hash still verifies the same password.
        assert security.verify_password("legacy-password-123", credential.password_hash)

    def test_wrong_password_does_not_upgrade(self, db, tenant_with_legacy_credential):
        from app.core.exceptions import UnauthorizedError

        tenant, credential = tenant_with_legacy_credential
        with pytest.raises(UnauthorizedError):
            auth_service.login(
                db,
                tenant,
                LoginRequest(email="legacy@argon.example.com", password="wrong"),
            )
        assert credential.password_hash.startswith("pbkdf2_sha256$")

    def test_unknown_email_burns_a_dummy_verification(self, db, monkeypatch):
        """Constant-work: the miss path must run exactly one verify, against
        the module dummy hash (asserted via counter, not wall-clock)."""
        from app.core.exceptions import UnauthorizedError

        tenant = Tenant(slug="cw", name="Constant Work")
        db.add(tenant)
        db.flush()

        calls: list[str] = []
        real_verify = auth_service.verify_password

        def counting_verify(password: str, password_hash: str) -> bool:
            calls.append(password_hash)
            return real_verify(password, password_hash)

        monkeypatch.setattr(auth_service, "verify_password", counting_verify)
        with pytest.raises(UnauthorizedError):
            auth_service.login(
                db,
                tenant,
                LoginRequest(email="nobody@cw.example.com", password="whatever"),
            )
        assert calls == [auth_service._DUMMY_HASH]


def _headers_app(**mw_kwargs) -> TestClient:
    from fastapi import HTTPException

    app = FastAPI()

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/handled-500")
    def handled_500() -> None:
        # A RAISED-BUT-HANDLED server error (ExceptionMiddleware runs INSIDE
        # user middleware, so the response passes back through it). An
        # UNHANDLED exception is different: Starlette's ServerErrorMiddleware
        # sits OUTSIDE all user middleware, so that last-resort 500 cannot
        # carry these headers — documented in the middleware's docstring.
        raise HTTPException(status_code=500, detail="handled")

    app.add_middleware(SecurityHeadersMiddleware, **mw_kwargs)
    return TestClient(app, raise_server_exceptions=False)


class TestSecurityHeaders:
    def test_headers_on_success_and_404_and_handled_500(self):
        client = _headers_app()
        for path, status in (("/ok", 200), ("/missing", 404), ("/handled-500", 500)):
            resp = client.get(path)
            assert resp.status_code == status
            assert resp.headers["x-content-type-options"] == "nosniff"
            assert resp.headers["x-frame-options"] == "DENY"
            assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
            assert "camera=()" in resp.headers["permissions-policy"]
            assert resp.headers["content-security-policy"] == _STRICT_CSP

    def test_hsts_only_on_secure_requests(self):
        client = _headers_app()
        plain = client.get("/ok")
        assert "strict-transport-security" not in plain.headers
        forwarded = client.get("/ok", headers={"x-forwarded-proto": "https"})
        assert "strict-transport-security" in forwarded.headers

    def test_csp_override_and_disable(self):
        client = _headers_app(content_security_policy="default-src 'none'")
        assert (
            client.get("/ok").headers["content-security-policy"] == "default-src 'none'"
        )
        off = _headers_app(enabled=False)
        assert "content-security-policy" not in off.get("/ok").headers

    def test_strict_csp_has_no_external_origins(self):
        """Fonts are vendored (no-CDN standard) — the computed CSP must not
        reference any external host."""
        assert "googleapis" not in _STRICT_CSP
        assert "gstatic" not in _STRICT_CSP
        assert "http://" not in _STRICT_CSP
        # https: appears ONLY as the img-src scheme source for tenant logos.
        assert _STRICT_CSP.count("https:") == 1
        assert "img-src 'self' data: https:" in _STRICT_CSP


class TestBoundedRateLimitStore:
    def test_window_counting_and_retry_after(self):
        store = MemoryStore(max_keys=10)
        for i in range(3):
            count, retry = store.hit("k", float(i), window_seconds=60, limit=3)
            assert retry == 0 and count == i + 1
        count, retry = store.hit("k", 3.0, window_seconds=60, limit=3)
        assert retry >= 1  # rejected
        # Outside the window the key resets.
        count, retry = store.hit("k", 100.0, window_seconds=60, limit=3)
        assert retry == 0 and count == 1

    def test_lru_cap_bounds_memory(self):
        store = MemoryStore(max_keys=5)
        for i in range(50):
            store.hit(f"key-{i}", float(i), window_seconds=60, limit=10)
        assert len(store._hits) == 5
        # Oldest keys were evicted, newest survive.
        assert "key-49" in store._hits and "key-0" not in store._hits

    def test_key_uses_route_template_not_raw_path(self):
        app = FastAPI()
        captured: list[str] = []

        class CapturingStore(MemoryStore):
            def hit(self, key, now, *, window_seconds, limit):
                captured.append(key)
                return super().hit(key, now, window_seconds=window_seconds, limit=limit)

        @app.get("/things/{thing_id}")
        def get_thing(thing_id: str) -> dict[str, str]:
            return {"id": thing_id}

        app.add_middleware(RateLimitMiddleware, store=CapturingStore())
        client = TestClient(app)
        client.get("/things/abc")
        client.get("/things/def")
        templates = {k.rsplit(":", 1)[-1] for k in captured}
        assert templates == {"/things/{thing_id}"}

        captured.clear()
        client.get("/no-such-route/1")
        client.get("/no-such-route/2" + "x" * 200)
        for key in captured:
            assert "unmatched:" in key  # bounded hash bucket, never raw path
