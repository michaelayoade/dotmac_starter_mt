"""`Party.is_active` is honoured at authentication, authorization and login.

Hosted PostgreSQL (RLS, real row locks). Review R3 kernel question 1: a
deactivated leaver must not be able to sign in, keep using an unrevoked session,
or pass an `authorize_party` check, and the refusal must reveal nothing.

Six tests: (1) unrevoked session, bearer 401 and cookie redirect; (2) correct
password for an inactive party looks exactly like a wrong one, with exactly one
password verification on each path; (3) reactivation restores login; (4) the
two-connection race; (5) `authorize_party` is false for an inactive party that
still holds a grant row; (6) external identity keeps refusing an inactive party.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from dotmac_kernel import deps
from dotmac_kernel.exceptions import UnauthorizedError
from dotmac_kernel.external_identity import (
    finalize_external_login,
    resolve_external_identity,
)
from dotmac_kernel.models import Party, Tenant
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.features.auth import service as auth_service
from app.features.auth.schemas import LoginRequest
from tests.conftest import DEFAULT_TEST_PASSWORD, client_for, provision_owner
from tests.test_admin_portal_e2e import web_login

EMAIL = "leaver@tenant-a.example.com"


@contextmanager
def _app_user_session(tenant_id: uuid.UUID) -> Iterator[Session]:
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(url, future=True)
    db = Session(engine)
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, false)"),
            {"t": str(tenant_id)},
        )
        yield db
    finally:
        db.rollback()
        db.close()
        engine.dispose()


def _set_active(admin_session: Session, tenant, active: bool) -> None:
    admin_session.execute(
        text("UPDATE parties SET is_active = :a WHERE tenant_id = :t AND email = :e"),
        {"a": active, "t": str(tenant.id), "e": EMAIL},
    )
    admin_session.commit()


def _party_id(admin_session: Session, tenant) -> uuid.UUID:
    return admin_session.scalars(
        select(Party.id).where(Party.tenant_id == tenant.id, Party.email == EMAIL)
    ).one()


def _session_count(admin_session: Session, tenant) -> int:
    return admin_session.execute(
        text("SELECT count(*) FROM auth_sessions WHERE tenant_id = :t"),
        {"t": str(tenant.id)},
    ).scalar_one()


def test_inactive_party_with_unrevoked_session_is_refused_on_bearer_and_cookie(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    provision_owner(admin_session, tenant_a, EMAIL)
    client = client_for(app_client, tenant_a.slug)
    token = client.post(
        "/auth/login", json={"email": EMAIL, "password": DEFAULT_TEST_PASSWORD}
    ).json()["access_token"]
    web_login(client, EMAIL)  # also leaves a live cookie session on this client
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.get("/admin", follow_redirects=False).status_code == 200

    _set_active(admin_session, tenant_a, False)

    # No session was revoked: only the party flag changed.
    assert client.get("/auth/me", headers=headers).status_code == 401
    cookie = client.get("/admin", follow_redirects=False)
    assert cookie.status_code == 302
    assert cookie.headers["location"].startswith("/admin/login")


def test_correct_password_for_an_inactive_party_is_the_same_refusal_as_a_wrong_one(
    admin_session: Session, tenant_a, monkeypatch
) -> None:
    provision_owner(admin_session, tenant_a, EMAIL)
    _set_active(admin_session, tenant_a, False)
    calls: list[str] = []
    real = auth_service.verify_password

    def counting(password: str, stored: str) -> bool:
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr(auth_service, "verify_password", counting)

    def attempt(password: str) -> tuple[str, int]:
        calls.clear()
        with _app_user_session(tenant_a.id) as db:
            with pytest.raises(UnauthorizedError) as refused:
                auth_service.login(
                    db, tenant_a, LoginRequest(email=EMAIL, password=password)
                )
        return str(refused.value), len(calls)

    inactive_correct = attempt(DEFAULT_TEST_PASSWORD)
    inactive_wrong = attempt("not-the-password")
    unknown_email = attempt_unknown(tenant_a, attempt_calls=calls)

    assert inactive_correct == inactive_wrong == ("Invalid credentials", 1)
    assert unknown_email == ("Invalid credentials", 1)
    assert _session_count(admin_session, tenant_a) == 0


def attempt_unknown(tenant, *, attempt_calls: list[str]) -> tuple[str, int]:
    attempt_calls.clear()
    with _app_user_session(tenant.id) as db:
        with pytest.raises(UnauthorizedError) as refused:
            auth_service.login(
                db,
                tenant,
                LoginRequest(email="nobody@tenant-a.example.com", password="x" * 12),
            )
    return str(refused.value), len(attempt_calls)


def test_reactivating_the_party_restores_login(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    provision_owner(admin_session, tenant_a, EMAIL)
    client = client_for(app_client, tenant_a.slug)
    body = {"email": EMAIL, "password": DEFAULT_TEST_PASSWORD}
    _set_active(admin_session, tenant_a, False)
    inactive = client.post("/auth/login", json=body)
    wrong = client.post("/auth/login", json={**body, "password": "not-the-password"})
    assert inactive.status_code == wrong.status_code == 401
    assert inactive.json() == wrong.json()
    _set_active(admin_session, tenant_a, True)
    assert client.post("/auth/login", json=body).status_code == 200


def test_login_racing_a_deactivation_issues_no_session(
    admin_session: Session, tenant_a
) -> None:
    """T1 locks the party and deactivates; T2's login blocks on that lock; T1
    commits; T2 re-reads `is_active = False` (populate_existing) and is refused,
    so no `auth_sessions` row exists afterwards."""
    provision_owner(admin_session, tenant_a, EMAIL)
    party_id = _party_id(admin_session, tenant_a)
    result: dict[str, object] = {}
    ready = threading.Event()

    def login_thread() -> None:
        try:
            with _app_user_session(tenant_a.id) as db:
                result["pid"] = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
                db.execute(text("SET LOCAL statement_timeout = '20s'"))
                ready.set()
                try:
                    auth_service.login(
                        db,
                        tenant_a,
                        LoginRequest(email=EMAIL, password=DEFAULT_TEST_PASSWORD),
                    )
                    result["outcome"] = "issued"
                    db.commit()
                except UnauthorizedError:
                    result["outcome"] = "refused"
                    db.rollback()
        except BaseException as exc:  # reported to the main thread
            result["error"] = exc
            ready.set()

    with _app_user_session(tenant_a.id) as holder:
        locked = holder.scalars(
            select(Party)
            .where(Party.tenant_id == tenant_a.id, Party.id == party_id)
            .with_for_update()
        ).one()
        locked.is_active = False
        holder.flush()
        thread = threading.Thread(target=login_thread)
        thread.start()
        try:
            assert ready.wait(10)
            assert "error" not in result, result
            deadline = time.monotonic() + 10
            blocked = False
            while time.monotonic() < deadline:
                blocked = holder.execute(
                    text("SELECT cardinality(pg_blocking_pids(:p)) > 0"),
                    {"p": result["pid"]},
                ).scalar_one()
                if blocked:
                    break
                time.sleep(0.02)
            assert blocked, "the login did not queue on the party lock"
            assert "outcome" not in result
            holder.commit()
        finally:
            holder.rollback()
            thread.join(30)
    assert not thread.is_alive()
    assert "error" not in result, result
    assert result["outcome"] == "refused"
    assert _session_count(admin_session, tenant_a) == 0


def test_authorize_party_is_false_for_an_inactive_party_holding_a_grant(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    provision_owner(admin_session, tenant_a, EMAIL)  # holds the `admin` grant
    party_id = _party_id(admin_session, tenant_a)

    def decision() -> bool:
        with _app_user_session(tenant_a.id) as db:
            party = db.get(Party, party_id)
            tenant = db.get(Tenant, tenant_a.id)
            assert party is not None and tenant is not None
            return deps.authorize_party(
                db, tenant=tenant, party=party, code="rbac.roles.read"
            )

    # The app is imported by `app_client`, which installs the declarations.
    assert decision() is True
    _set_active(admin_session, tenant_a, False)
    grants = admin_session.execute(
        text("SELECT count(*) FROM party_role_grants WHERE party_id = :p"),
        {"p": str(party_id)},
    ).scalar_one()
    assert grants == 1  # the grant row is still there
    assert decision() is False


def test_external_identity_still_refuses_an_inactive_party(
    admin_session: Session, tenant_a
) -> None:
    provision_owner(admin_session, tenant_a, EMAIL)
    party_id = _party_id(admin_session, tenant_a)
    admin_session.execute(
        text(
            "INSERT INTO external_identity_bindings (id, tenant_id, party_id, "
            "provider_binding, issuer, subject, is_active, bound_by, bind_reason) "
            "VALUES (:id, :t, :p, 'canary-idp', 'https://idp.example.com', "
            "'subject-1', true, 'canary@example.com', 'is_active canary')"
        ),
        {"id": str(uuid.uuid4()), "t": str(tenant_a.id), "p": str(party_id)},
    )
    admin_session.commit()
    lookup = {
        "provider_binding": "canary-idp",
        "issuer": "https://idp.example.com",
        "subject": "subject-1",
    }
    with _app_user_session(tenant_a.id) as db:
        tenant = db.get(Tenant, tenant_a.id)
        assert resolve_external_identity(db, tenant=tenant, **lookup) is not None

    _set_active(admin_session, tenant_a, False)
    with _app_user_session(tenant_a.id) as db:
        tenant = db.get(Tenant, tenant_a.id)
        assert resolve_external_identity(db, tenant=tenant, **lookup) is None
        assert finalize_external_login(db, tenant=tenant, **lookup) is None
