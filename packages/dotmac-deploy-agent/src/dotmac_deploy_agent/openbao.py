"""D1 delivery identity client (ruling R4, P-SEC s3.3-3.5).

Secret zero is the delivery AppRole `role_id` + `secret_id`, both arriving as
systemd credentials. Per run the agent logs in at `<env>/auth/approle-delivery`,
holds the 5-minute token in memory only, and revokes it at the end.

For each role it mints a RESPONSE-WRAPPED `secret_id` in the PARENT namespace
`<env>` at `<product>/auth/approle-apps/role/<product>-<env>-<role>/secret-id`,
looks the wrapping token up and requires that `creation_path` BEFORE unwrapping
(the WireGuard leg is plain HTTP, so a swapped token would otherwise be
accepted), then unwraps it. The request body is limited to `metadata`, `ttl` and,
for a single-use role, `num_uses = 1` (the delivery policy's `allowed_parameters`).

Values are held in locals only: never logged, never in an observation, never in
an exception. The delivery identity has no `role-id` read and no `list`.
"""

from __future__ import annotations

import json
import logging
from typing import Final, cast

from .refusal import Refusal
from .transport import Transport

LOG = logging.getLogger("dotmac.deploy_agent")
APPROLE_DELIVERY: Final = "auth/approle-delivery"
APPROLE_APPS: Final = "auth/approle-apps"


def role_name(product: str, environment: str, role: str) -> str:
    return f"{product}-{environment}-{role}"


def mint_path(product: str, environment: str, role: str) -> str:
    full = role_name(product, environment, role)
    return f"{product}/{APPROLE_APPS}/role/{full}/secret-id"


def accessor_destroy_path(product: str, environment: str, role: str) -> str:
    return (
        f"{product}/{APPROLE_APPS}/role/{role_name(product, environment, role)}"
        "/secret-id-accessor/destroy"
    )


class OpenBaoSession:
    def __init__(self, transport: Transport, namespace: str) -> None:
        self._transport = transport
        self._namespace = namespace
        self._token: str | None = None

    def __repr__(self) -> str:
        return f"OpenBaoSession(namespace={self._namespace!r})"

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        token: str | None = None,
        wrap_ttl: int | None = None,
        failure: str = "secret_delivery_failed",
    ) -> dict[str, object]:
        headers: dict[str, str] = {
            "X-Vault-Namespace": self._namespace,
            "Content-Type": "application/json",
        }
        if token is not None:
            headers["X-Vault-Token"] = token
        if wrap_ttl is not None:
            headers["X-Vault-Wrap-TTL"] = f"{wrap_ttl}s"
        payload = json.dumps(body, sort_keys=True).encode() if body is not None else b""
        try:
            reply = self._transport.request(method, f"/v1/{path}", headers, payload)
        except Refusal:
            raise Refusal("secret_delivery_failed") from None
        if reply.status == 204:
            return {}
        if reply.status != 200:
            raise Refusal(failure)
        try:
            parsed = json.loads(reply.body)
        except ValueError:
            raise Refusal(failure) from None
        if not isinstance(parsed, dict):
            raise Refusal(failure)
        return cast("dict[str, object]", parsed)

    @staticmethod
    def _field(container: object, name: str) -> str:
        if not isinstance(container, dict):
            raise Refusal("secret_delivery_failed")
        value = cast("dict[str, object]", container).get(name)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise Refusal("secret_delivery_failed")
        return value

    # -- identity ----------------------------------------------------------
    def login(self, role_id: str, secret_id: str) -> None:
        document = self._call(
            "POST",
            f"{APPROLE_DELIVERY}/login",
            body={"role_id": role_id, "secret_id": secret_id},
        )
        self._token = self._field(document.get("auth"), "client_token")

    def revoke(self) -> None:
        """End of run: the token never outlives it. Best effort, never raises."""
        token, self._token = self._token, None
        if token is None:
            return
        try:
            self._call("POST", "auth/token/revoke-self", token=token)
        except Refusal:
            pass  # a 5 minute token expires on its own

    def _require_token(self) -> str:
        if self._token is None:
            raise Refusal("secret_delivery_failed")
        return self._token

    # -- minting -----------------------------------------------------------
    def mint_wrapped(
        self,
        *,
        product: str,
        environment: str,
        role: str,
        single_use: bool,
        ttl_seconds: int,
        wrap_ttl_seconds: int,
        dispatch_id: str,
    ) -> str:
        """The wrapping token for a freshly minted `secret_id` (not the id itself)."""
        body: dict[str, object] = {
            "metadata": json.dumps({"dispatch_id": dispatch_id}, sort_keys=True),
            "ttl": f"{ttl_seconds}s",
        }
        if single_use:
            body["num_uses"] = 1
        document = self._call(
            "POST",
            mint_path(product, environment, role),
            body=body,
            token=self._require_token(),
            wrap_ttl=wrap_ttl_seconds,
        )
        return self._field(document.get("wrap_info"), "token")

    def require_creation_path(self, wrapping_token: str, expected: str) -> None:
        """`sys/wrapping/lookup` BEFORE unwrap. A token that is gone was already
        unwrapped by somebody else: a distinct alarm, never retried."""
        document = self._call(
            "POST",
            "sys/wrapping/lookup",
            body={"token": wrapping_token},
            failure="secret_wrap_already_unwrapped",
        )
        path = self._field(document.get("data"), "creation_path")
        # The lookup may report the path with the namespace prefix (T1 proves which).
        if path == expected:
            LOG.info("wrapping creation_path form=bare")
        elif path == f"{self._namespace}/{expected}":
            LOG.info("wrapping creation_path form=namespaced")
        else:
            raise Refusal("secret_wrap_unexpected_path")

    def unwrap(self, wrapping_token: str) -> tuple[str, str | None]:
        """`(secret_id, secret_id_accessor)`. The accessor is not a secret."""
        document = self._call(
            "POST",
            "sys/wrapping/unwrap",
            token=wrapping_token,
            failure="secret_wrap_already_unwrapped",
        )
        data = document.get("data")
        secret_id = self._field(data, "secret_id")
        accessor = cast("dict[str, object]", data).get("secret_id_accessor")
        return secret_id, accessor if isinstance(accessor, str) and accessor else None

    def destroy_accessor(
        self, *, product: str, environment: str, role: str, accessor: str
    ) -> None:
        self._call(
            "POST",
            accessor_destroy_path(product, environment, role),
            body={"secret_id_accessor": accessor},
            token=self._require_token(),
        )
