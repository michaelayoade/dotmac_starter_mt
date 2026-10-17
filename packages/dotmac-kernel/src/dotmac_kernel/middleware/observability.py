"""Request observation with a legacy writer and a non-writing evidence reader."""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dotmac_kernel.logging import request_id_var
from dotmac_kernel.request_evidence import (
    UNSET_EVIDENCE,
    RequestEvidence,
    current_evidence,
)

logger = logging.getLogger("app.requests")
_EVIDENCE_SNAPSHOT_KEY = "_dotmac_observed_request_evidence"


class _RequestEvidenceSnapshotMiddleware:
    """Read the actual downstream context across the tenant task boundary.

    BaseHTTPMiddleware copies ContextVars into its child task, but actor binds
    do not flow back. This transports a frozen observation through the shared
    ASGI state; it never creates, updates or resets the evidence context.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            scope.setdefault("state", {})[_EVIDENCE_SNAPSHOT_KEY] = current_evidence()


async def _observe(
    app: ASGIApp,
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    request_id: str,
    evidence: RequestEvidence | None = None,
) -> None:
    request = Request(scope, receive)
    started = time.perf_counter()
    status_code = 500
    if evidence is not None:
        # Never accept a preexisting snapshot; only the actual downstream reader
        # may supply one for this invocation. Short circuits retain initial
        # anonymous evidence and still receive full observation.
        scope.setdefault("state", {}).pop(_EVIDENCE_SNAPSHOT_KEY, None)

    async def send_with_request_id(message: Message) -> None:
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
            headers = list(message.get("headers", []))
            headers.append((b"x-request-id", request_id.encode()))
            message["headers"] = headers
        await send(message)

    try:
        await app(scope, receive, send_with_request_id)
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        tenant = getattr(request.state, "tenant", None)
        fields: dict[str, object] = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "tenant_id": str(tenant.id) if tenant is not None else None,
        }
        if evidence is not None:
            observed = scope.get("state", {}).pop(_EVIDENCE_SNAPSHOT_KEY, evidence)
            if (
                not isinstance(observed, RequestEvidence)
                or observed.request_id != request_id
            ):
                raise RuntimeError(
                    "downstream evidence snapshot does not match this request"
                )
            fields.update(
                actor_kind=observed.actor.kind,
                actor_id=observed.actor.id,
                actor_scopes=observed.actor.scopes,
                client_address=observed.client_address,
            )
        logger.info("request", extra=fields)


class RequestEvidenceObserverMiddleware:
    """Preserve response/log observation without writing a second context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        evidence = current_evidence()
        if evidence is UNSET_EVIDENCE:
            raise RuntimeError(
                "RequestEvidenceObserverMiddleware requires an active evidence context"
            )
        await _observe(
            self.app,
            scope,
            receive,
            send,
            request_id=evidence.request_id,
            evidence=evidence,
        )


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, *, trust_inbound_request_id: bool = False) -> None:
        self.app = app
        self.trust_inbound_request_id = trust_inbound_request_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        inbound_request_id = request.headers.get("x-request-id")
        request_id = (
            inbound_request_id
            if self.trust_inbound_request_id and inbound_request_id
            else str(uuid4())
        )
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        try:
            await _observe(self.app, scope, receive, send, request_id=request_id)
        finally:
            request_id_var.reset(token)


__all__ = ["ObservabilityMiddleware", "RequestEvidenceObserverMiddleware"]
