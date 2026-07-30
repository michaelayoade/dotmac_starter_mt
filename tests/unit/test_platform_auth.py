"""Unit tests for `dotmac_kernel.platform_auth` (control-plane security Task 1).

Postgres-level behavior (grants, deny-by-default through the full app) lives
in `tests/test_platform_auth_denies.py`; these SQLite tests pin the guard's
own decision logic — host exactness, `aud` separation, session liveness,
admin activity — and the CLI upsert's idempotency.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from dotmac_kernel import platform_auth
from dotmac_kernel.config import settings
from dotmac_kernel.middleware.tenant import _is_platform_path
from dotmac_kernel.models_platform import PlatformAdmin, PlatformSession
from dotmac_kernel.security import hash_password, hash_token, issue_access_token
from starlette.requests import Request


def _request(host: str) -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/platform/tenants",
            "headers": [(b"host", host.encode())],
        }
    )


@pytest.fixture(autouse=True)
def _root_domain(monkeypatch):
    monkeypatch.setattr(settings, "platform_root_domain", "localhost")


@pytest.fixture
def admin(db) -> PlatformAdmin:
    row = PlatformAdmin(
        email="unit-admin@platform.example.com",
        password_hash=hash_password("unit-password"),
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _issue_session(db, admin: PlatformAdmin) -> str:
    token, expires_at = platform_auth.issue_platform_token(admin.id)
    db.add(
        PlatformSession(
            admin_id=admin.id, token_hash=hash_token(token), expires_at=expires_at
        )
    )
    db.flush()
    return token


class TestIsPlatformPathHostExact:
    """The middleware predicate is host-exact: the pre-fix
    `startswith("/platform/")` branch passed ANY host."""

    def test_root_host_is_platform_valid_for_any_path(self):
        assert _is_platform_path("/platform/tenants", "localhost", "localhost")
        assert _is_platform_path("/anything", "localhost", "localhost")

    def test_platform_path_on_unknown_host_is_not_platform_valid(self):
        assert not _is_platform_path(
            "/platform/tenants", "nowhere.invalid", "localhost"
        )

    def test_platform_path_on_tenant_host_is_not_platform_valid(self):
        assert not _is_platform_path("/platform/tenants", "acme.localhost", "localhost")

    def test_health_paths_stay_host_agnostic(self):
        assert _is_platform_path("/health", "nowhere.invalid", "localhost")
        assert _is_platform_path("/health/ready", "acme.localhost", "localhost")


class TestAuthenticatePlatformRequest:
    def test_happy_path(self, db, admin):
        token = _issue_session(db, admin)
        result = platform_auth.authenticate_platform_request(
            _request("localhost"), db, token=token
        )
        assert result is not None and result.id == admin.id

    def test_host_mismatch_fails(self, db, admin):
        token = _issue_session(db, admin)
        assert (
            platform_auth.authenticate_platform_request(
                _request("acme.localhost"), db, token=token
            )
            is None
        )

    def test_tenant_token_fails_aud_check(self, db, admin):
        # A real tenant token — valid signature/expiry, but no aud claim.
        tenant_token, _ = issue_access_token(uuid4(), uuid4())
        assert (
            platform_auth.authenticate_platform_request(
                _request("localhost"), db, token=tenant_token
            )
            is None
        )

    def test_garbage_token_fails(self, db, admin):
        assert (
            platform_auth.authenticate_platform_request(
                _request("localhost"), db, token="not-a-jwt"
            )
            is None
        )

    def test_token_without_session_row_fails(self, db, admin):
        token, _ = platform_auth.issue_platform_token(admin.id)  # never stored
        assert (
            platform_auth.authenticate_platform_request(
                _request("localhost"), db, token=token
            )
            is None
        )

    def test_revoked_session_fails(self, db, admin):
        token = _issue_session(db, admin)
        session = db.query(PlatformSession).one()
        session.revoked_at = datetime.now(UTC)
        db.flush()
        assert (
            platform_auth.authenticate_platform_request(
                _request("localhost"), db, token=token
            )
            is None
        )

    def test_expired_session_fails(self, db, admin):
        token = _issue_session(db, admin)
        session = db.query(PlatformSession).one()
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.flush()
        assert (
            platform_auth.authenticate_platform_request(
                _request("localhost"), db, token=token
            )
            is None
        )

    def test_inactive_admin_fails(self, db, admin):
        token = _issue_session(db, admin)
        admin.is_active = False
        db.flush()
        assert (
            platform_auth.authenticate_platform_request(
                _request("localhost"), db, token=token
            )
            is None
        )


class TestLoginService:
    def test_login_issues_session_backed_token(self, db, admin):
        token, _expires = platform_auth.login(
            db, email="UNIT-ADMIN@platform.example.com", password="unit-password"
        )
        assert (
            platform_auth.authenticate_platform_request(
                _request("localhost"), db, token=token
            )
            is not None
        )

    def test_wrong_password_raises(self, db, admin):
        from dotmac_kernel.exceptions import UnauthorizedError

        with pytest.raises(UnauthorizedError):
            platform_auth.login(db, email=admin.email, password="wrong-password")

    def test_unknown_email_raises_after_dummy_verify(self, db, monkeypatch):
        """Constant-work miss path: the dummy verification actually runs."""
        from dotmac_kernel.exceptions import UnauthorizedError

        calls: list[str] = []
        real_verify = platform_auth.verify_password

        def counting_verify(password: str, password_hash: str) -> bool:
            calls.append(password_hash)
            return real_verify(password, password_hash)

        monkeypatch.setattr(platform_auth, "verify_password", counting_verify)
        with pytest.raises(UnauthorizedError):
            platform_auth.login(
                db, email="nobody@platform.example.com", password="whatever"
            )
        assert calls == [platform_auth._DUMMY_HASH]

    def test_logout_revokes(self, db, admin):
        token = _issue_session(db, admin)
        platform_auth.logout(db, token=token)
        assert (
            platform_auth.authenticate_platform_request(
                _request("localhost"), db, token=token
            )
            is None
        )

    def test_logout_of_unknown_token_is_a_noop(self, db):
        platform_auth.logout(db, token="never-issued")
        platform_auth.logout(db, token=None)


class TestCliUpsert:
    @staticmethod
    def _cli():
        path = (
            Path(__file__).resolve().parent.parent.parent
            / "scripts"
            / "create_platform_admin.py"
        )
        spec = importlib.util.spec_from_file_location("create_platform_admin", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_upsert_is_idempotent_by_email_and_rotates(self, db):
        cli = self._cli()
        first = cli.upsert_admin(
            db, email="Boot@Platform.example.com", password="first-password"
        )
        second = cli.upsert_admin(
            db,
            email="boot@platform.example.com",
            password="second-password",
            is_active=False,
        )
        assert first.id == second.id
        admins = db.query(PlatformAdmin).all()
        assert len(admins) == 1
        assert admins[0].email == "boot@platform.example.com"
        assert admins[0].is_active is False
        from dotmac_kernel.security import verify_password

        assert verify_password("second-password", admins[0].password_hash)
        assert not verify_password("first-password", admins[0].password_hash)
