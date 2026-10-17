"""Real factory/auth/log composition; hosted CI owns execution.

No auth dependency is replaced: only its database resource port is supplied.
ERP remains the first adopter; this proves owner composition, not CP admission.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel import ProductAssemblySpec, create_app
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.assembly import RequestEvidenceConfigurationV1
from dotmac_kernel.config import settings
from dotmac_kernel.deps import get_platform_db
from dotmac_kernel.features import FeatureManifest
from dotmac_kernel.logging import JsonLogFormatter, request_id_var
from dotmac_kernel.middleware import tenant as tenant_middleware
from dotmac_kernel.middleware.observability import ObservabilityMiddleware
from dotmac_kernel.models_platform import PlatformAdmin, PlatformSession
from dotmac_kernel.platform_auth import issue_platform_token, require_platform_admin
from dotmac_kernel.request_evidence import (
    UNSET_EVIDENCE,
    RequestEvidenceContextV1,
    TrustedProxyPolicy,
    current_evidence,
)
from dotmac_kernel.security import hash_password, hash_token
from fastapi import APIRouter, Depends


@pytest.fixture
def assembled(monkeypatch, db):
    monkeypatch.setattr(settings, "platform_root_domain", "localhost")
    monkeypatch.setattr(settings, "trusted_hosts", "")
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "csrf_enabled", False)

    @contextmanager
    def resolver_resource():
        yield db

    monkeypatch.setattr(tenant_middleware, "resolver_session", resolver_resource)
    router = APIRouter()

    @router.get("/platform/async-evidence")
    async def async_consumer(_admin=Depends(require_platform_admin)):
        evidence = current_evidence()
        return {"actor_id": evidence.actor.id, "request_id": evidence.request_id}

    app = create_app(
        ProductAssemblySpec(
            name="request-owner-proof",
            modules=(FeatureManifest(name="evidence_consumer", routers=(router,)),),
            api_documentation=api_documentation_policy("development"),
            request_evidence=RequestEvidenceConfigurationV1(
                trusted_proxies=TrustedProxyPolicy.from_declaration(
                    "203.0.113.0/24,::1"
                )
            ),
        )
    )

    def resource():
        yield db

    app.dependency_overrides[get_platform_db] = resource
    return app


@pytest.fixture
def structured_records():
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(json.loads(JsonLogFormatter().format(record)))

    handler = Capture()
    logger = logging.getLogger("app.requests")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def _credential(db):
    admin = PlatformAdmin(
        email="context@platform.example",
        password_hash=hash_password("fixture-password"),
        is_active=True,
    )
    db.add(admin)
    db.flush()
    token, expires = issue_platform_token(admin.id)
    session = PlatformSession(
        admin_id=admin.id, token_hash=hash_token(token), expires_at=expires
    )
    db.add(session)
    db.flush()
    return admin, token, session


async def _request(
    app, *, path="/health", method="GET", peer="203.0.113.2", headers=(), send_hook=None
):
    messages = []
    sent_body = False
    disconnected = asyncio.Event()

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if send_hook:
            await send_hook(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            disconnected.set()

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "server": ("localhost", 80),
            "client": (peer, 1234),
            "headers": (
                [(b"host", b"localhost")]
                if not any(name.lower() == b"host" for name, _ in headers)
                else []
            )
            + list(headers),
        },
        receive,
        send,
    )
    response = next(m for m in messages if m["type"] == "http.response.start")
    response["_observed_body"] = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return response


def test_factory_selects_exactly_one_writer(assembled):
    classes = [middleware.cls for middleware in assembled.user_middleware]
    assert classes.count(RequestEvidenceContextV1) == 1
    assert ObservabilityMiddleware not in classes
    legacy = create_app(
        ProductAssemblySpec(
            name="legacy",
            web_enabled=False,
            api_documentation=api_documentation_policy("development"),
        )
    )
    classes = [middleware.cls for middleware in legacy.user_middleware]
    assert classes.count(ObservabilityMiddleware) == 1
    assert RequestEvidenceContextV1 not in classes


def test_closed_configuration_refuses_wrong_shapes():
    with pytest.raises(TypeError):
        RequestEvidenceConfigurationV1(trusted_proxies="::1")
    with pytest.raises(TypeError):
        ProductAssemblySpec(name="bad", request_evidence={})


@pytest.mark.parametrize(
    "peer,trusted",
    [("203.0.113.2", True), ("::1", True), ("::2", False), ("198.51.100.2", False)],
)
def test_real_response_and_logger_consume_owner_evidence(
    assembled, caplog, structured_records, peer, trusted
):
    caplog.set_level(logging.INFO, logger="app.requests")

    async def drive():
        prior = request_id_var.set("outer")
        try:
            response = await _request(
                assembled,
                peer=peer,
                headers=[
                    (b"x-request-id", b"trusted-correlation"),
                    (b"x-forwarded-for", b"192.0.2.8"),
                    (b"user-agent", b"owner-proof"),
                ],
            )
            assert current_evidence() is UNSET_EVIDENCE
            assert request_id_var.get() == "outer"
            header = dict(response["headers"])[b"x-request-id"].decode()
            record = [r for r in caplog.records if r.name == "app.requests"][-1]
            assert record.request_id == header
            assert (header == "trusted-correlation") is trusted
            assert record.actor_kind == "anonymous"
            assert record.actor_id is None and record.actor_scopes == ()
            assert record.client_address == ("192.0.2.8" if trusted else peer)
            payload = structured_records[-1]
            assert payload["request_id"] == header
            assert payload["actor_kind"] == "anonymous"
            assert payload["actor_scopes"] == []
            assert payload["client_address"] == record.client_address
            assert record.status_code == 200 and record.duration_ms >= 0
        finally:
            request_id_var.reset(prior)

    asyncio.run(drive())


@pytest.mark.parametrize("browser", [False, True])
@pytest.mark.parametrize("invalid", [None, "revoked", "expired", "inactive", "garbage"])
def test_real_auth_binds_actor_and_next_anonymous_request_does_not_inherit(
    assembled, db, caplog, browser, invalid
):
    admin, token, session = _credential(db)
    if invalid == "revoked":
        session.revoked_at = datetime.now(UTC)
    elif invalid == "expired":
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif invalid == "inactive":
        admin.is_active = False
    elif invalid == "garbage":
        token = "invalid-token"
    db.flush()
    caplog.set_level(logging.INFO, logger="app.requests")

    async def drive():
        response = await _request(
            assembled,
            path="/platform" if browser else "/platform/auth/logout",
            method="GET" if browser else "POST",
            headers=[(b"cookie", f"platform_access_token={token}".encode())]
            if browser
            else [(b"authorization", f"Bearer {token}".encode())],
        )
        record = [r for r in caplog.records if r.name == "app.requests"][-1]
        assert record.actor_kind == ("anonymous" if invalid else "user")
        assert record.actor_id == (None if invalid else str(admin.id))
        assert record.actor_scopes == ()
        assert response["status"] == (
            (302 if browser else 401) if invalid else (200 if browser else 204)
        )
        assert current_evidence() is UNSET_EVIDENCE
        assert request_id_var.get() is None
        await _request(assembled)
        record = [r for r in caplog.records if r.name == "app.requests"][-1]
        assert record.actor_kind == "anonymous" and record.actor_id is None
        assert current_evidence() is UNSET_EVIDENCE

    asyncio.run(drive())


def test_exception_and_cancellation_restore_context_and_log(assembled, caplog):
    caplog.set_level(logging.INFO, logger="app.requests")

    async def drive(error):
        async def fail(message):
            if message["type"] == "http.response.start":
                raise error

        prior = request_id_var.set("outer")
        try:
            with pytest.raises(type(error)):
                await _request(assembled, send_hook=fail)
            assert current_evidence() is UNSET_EVIDENCE
            assert request_id_var.get() == "outer"
            assert any(r.name == "app.requests" for r in caplog.records)
        finally:
            request_id_var.reset(prior)

    asyncio.run(drive(RuntimeError("transport failed")))
    asyncio.run(drive(asyncio.CancelledError()))


def test_actual_stack_and_async_domain_consumer(assembled, db, caplog):
    admin, token, _ = _credential(db)
    caplog.set_level(logging.INFO, logger="app.requests")

    async def drive():
        response = await _request(
            assembled,
            path="/platform/async-evidence",
            headers=[(b"authorization", f"Bearer {token}".encode())],
        )
        assert response["status"] == 200
        body = json.loads(response["_observed_body"])
        record = [r for r in caplog.records if r.name == "app.requests"][-1]
        assert body["actor_id"] == record.actor_id == str(admin.id)
        assert body["request_id"] == record.request_id
        stack = assembled.middleware_stack
        classes = []
        while stack is not None:
            classes.append(type(stack))
            stack = getattr(stack, "app", None)
        assert classes.count(RequestEvidenceContextV1) == 1
        assert ObservabilityMiddleware not in classes
        assert current_evidence() is UNSET_EVIDENCE

    asyncio.run(drive())


def test_nested_same_task_request_restores_outer_context(assembled, caplog):
    caplog.set_level(logging.INFO, logger="app.requests")

    async def drive():
        entered = False

        async def nested(message):
            nonlocal entered
            if message["type"] != "http.response.start" or entered:
                return
            entered = True
            outer = current_evidence()
            outer_log_id = request_id_var.get()
            assert outer is not UNSET_EVIDENCE
            await _request(assembled, headers=[(b"x-request-id", b"inner")])
            assert current_evidence() is outer
            assert request_id_var.get() == outer_log_id

        await _request(
            assembled, headers=[(b"x-request-id", b"outer")], send_hook=nested
        )
        assert current_evidence() is UNSET_EVIDENCE
        assert request_id_var.get() is None
        ids = [r.request_id for r in caplog.records if r.name == "app.requests"]
        assert ids == ["inner", "outer"]

    asyncio.run(drive())


def test_host_short_circuit_keeps_response_and_anonymous_observation(assembled, caplog):
    # The database resource contains no matching tenant; real owner resolution
    # and host policy short-circuit before the downstream snapshot is reached.
    caplog.set_level(logging.INFO, logger="app.requests")

    async def drive():
        response = await _request(
            assembled, path="/missing", headers=[(b"host", b"unknown.invalid")]
        )
        assert response["status"] == 404
        record = [r for r in caplog.records if r.name == "app.requests"][-1]
        assert record.actor_kind == "anonymous" and record.actor_id is None
        assert record.status_code == 404
        assert dict(response["headers"])[b"x-request-id"].decode() == record.request_id
        assert current_evidence() is UNSET_EVIDENCE

    asyncio.run(drive())


def test_overlapping_requests_keep_distinct_actor_and_correlation(
    assembled, db, caplog
):
    admin, token, _ = _credential(db)
    caplog.set_level(logging.INFO, logger="app.requests")

    async def drive():
        authenticated, anonymous = await asyncio.gather(
            _request(
                assembled,
                path="/platform/async-evidence",
                headers=[
                    (b"authorization", f"Bearer {token}".encode()),
                    (b"x-request-id", b"authenticated-overlap"),
                ],
            ),
            _request(assembled, headers=[(b"x-request-id", b"anonymous-overlap")]),
        )
        assert authenticated["status"] == anonymous["status"] == 200
        records = {r.request_id: r for r in caplog.records if r.name == "app.requests"}
        assert records["authenticated-overlap"].actor_id == str(admin.id)
        assert records["anonymous-overlap"].actor_kind == "anonymous"
        assert records["anonymous-overlap"].actor_id is None
        assert current_evidence() is UNSET_EVIDENCE
        assert request_id_var.get() is None

    asyncio.run(drive())
