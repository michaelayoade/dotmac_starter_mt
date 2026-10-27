"""The credential owner's single-use secret fetch, over HTTP.

ADR-0076 section 7 fixes the shape and this module only carries it: a
synchronous, authenticated POST keyed by `(credential_id, secret_version)`
plus the open projection-request reference. The owner answers at most once
per request; a refusal is final for that request (`SecretFetchRefused`),
anything transport-shaped is retryable (`SecretFetchUnavailable`).

The secret exists in this process only as this call's return value. It is
never logged (no logger in this module), never placed in evidence and never
retained on the instance — the object below holds an endpoint and a token,
not a cache.
"""

from __future__ import annotations

import httpx

from dotmac_connector_freeradius.ports import (
    SecretFetchRefused,
    SecretFetchUnavailable,
)

_REFUSAL_STATUSES = frozenset({400, 401, 403, 404, 409, 422})


class HttpSecretFetch:
    """One fetch per call; no retries (retry policy is the engine's)."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/projection-secret"
        self._token = token
        self._timeout = timeout_seconds
        self._transport = transport

    def fetch_secret(
        self,
        *,
        credential_id: str,
        secret_version: int,
        projection_request_ref: str,
        operation_ref: str,
    ) -> str:
        headers = {}
        if self._token:
            # The credential owner's API-key principal arrives via
            # X-Api-Key (the same header the Integrator uses against the
            # owner's descriptor and registry reads).
            headers["X-Api-Key"] = self._token
        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = client.post(
                    self._url,
                    json={
                        "credential_id": credential_id,
                        "secret_version": secret_version,
                        "projection_request_ref": projection_request_ref,
                        "operation_ref": operation_ref,
                    },
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise SecretFetchUnavailable("secret fetch transport failed") from exc
        if response.status_code in _REFUSAL_STATUSES:
            raise SecretFetchRefused(
                f"secret fetch refused with status {response.status_code}"
            )
        if response.status_code != 200:
            raise SecretFetchUnavailable(
                f"secret fetch answered status {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SecretFetchUnavailable("secret fetch answered non-JSON") from exc
        secret = payload.get("secret") if isinstance(payload, dict) else None
        if not isinstance(secret, str) or not secret:
            raise SecretFetchUnavailable("secret fetch answered without a secret")
        return secret


__all__ = ["HttpSecretFetch"]
