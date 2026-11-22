"""D1 delivery identity client: wrapped `secret_id` mint and unwrap (L5/L6).

Per run, for each role, the delivery identity mints a RESPONSE-WRAPPED
`secret_id`; the agent unwraps it on the host and writes it straight to
`/run/dotmac/<product>/<role>/secret_id`. Values are held in locals only: never
logged, never put in an observation, never in an exception.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final, cast

from .refusal import Refusal
from .transport import Transport

WRAP_TTL: Final = "60s"


class OpenBaoClient:
    def __init__(self, transport: Transport, token: str, namespace: str) -> None:
        self._transport = transport
        self._token = token
        self._namespace = namespace

    def __repr__(self) -> str:
        return f"OpenBaoClient(namespace={self._namespace!r})"

    def _call(
        self, method: str, path: str, *, token: str, wrap: bool, body: bytes = b""
    ) -> dict[str, object]:
        headers: dict[str, str] = {
            "X-Vault-Token": token,
            "X-Vault-Namespace": self._namespace,
            "Content-Type": "application/json",
        }
        if wrap:
            headers["X-Vault-Wrap-TTL"] = WRAP_TTL
        try:
            reply = self._transport.request(method, path, headers, body)
        except Refusal:
            raise Refusal("secret_delivery_failed") from None
        if reply.status != 200:
            raise Refusal("secret_delivery_failed")
        try:
            parsed = json.loads(reply.body)
        except ValueError:
            raise Refusal("secret_delivery_failed") from None
        if not isinstance(parsed, dict):
            raise Refusal("secret_delivery_failed")
        return cast("dict[str, object]", parsed)

    def role_id(self, role: str) -> str:
        document = self._call(
            "GET",
            f"/v1/auth/approle/role/{role}/role-id",
            token=self._token,
            wrap=False,
        )
        return self._field(document.get("data"), "role_id")

    def wrapped_secret_id(self, role: str) -> str:
        """The wrapping token for a freshly minted `secret_id` (not the id itself)."""
        document = self._call(
            "POST",
            f"/v1/auth/approle/role/{role}/secret-id",
            token=self._token,
            wrap=True,
            body=b"{}",
        )
        return self._field(document.get("wrap_info"), "token")

    def unwrap(self, wrapping_token: str) -> str:
        document = self._call(
            "POST",
            "/v1/sys/wrapping/unwrap",
            token=wrapping_token,
            wrap=False,
            body=b"{}",
        )
        return self._field(document.get("data"), "secret_id")

    @staticmethod
    def _field(container: object, name: str) -> str:
        if not isinstance(container, dict):
            raise Refusal("secret_delivery_failed")
        value = cast("Mapping[str, object]", container).get(name)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise Refusal("secret_delivery_failed")
        return value
