"""The agent's side of the ONE all-HTTPS consumption + lease API (DESIGN-rev3 s2.6).

The agent calls `materials` and `observations` (and, in lane B, `lease/*`). It
never calls `resolve`, `consume` or `committed`: those belong to the Foundation
bindings. The agent signs each request with the target PoP key and accepts a
response only if it verifies under the pinned CP response keys, is bound to this
request's nonce, and carries a CP time within 60 s of the host clock.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, cast

from . import observation_v2, wire
from .canon import canonical_json
from .config import AgentConfig
from .materials import check_shape
from .refusal import Refusal
from .transport import Transport

_ERROR_CODE: Final = re.compile(r"[a-z0-9_.-]{1,64}")


@dataclass(frozen=True)
class Reply:
    """A verified CP response. `store_now` is the signed CP DB time."""

    status: int
    document: dict[str, object]
    store_now: datetime


class TargetClient:
    def __init__(
        self,
        config: AgentConfig,
        signer: wire.PopSigner,
        transport: Transport,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        nonce: Callable[[], str] = wire.new_nonce,
    ) -> None:
        self._config = config
        self._signer = signer
        self._transport = transport
        self._clock = clock
        self._nonce = nonce

    def _path(self, endpoint: str) -> str:
        """Three segments of the target_ref grammar; never percent-encoded (R1)."""
        return wire.target_path(self._config.target_ref, endpoint)

    def call(
        self, method: str, endpoint: str, payload: dict[str, object] | None
    ) -> Reply:
        wire.check_route(method, endpoint)
        path = self._path(endpoint)
        body = canonical_json(payload) if payload is not None else b""
        issued_at = wire.utc_stamp(self._clock())
        nonce = self._nonce()
        message = wire.request_message(
            method=method,
            path=path,
            body=body,
            credential_id=self._signer.credential_id,
            target_ref=self._config.target_ref,
            issued_at=issued_at,
            nonce=nonce,
        )
        headers = {
            **wire.request_headers(
                credential_id=self._signer.credential_id,
                target_ref=self._config.target_ref,
                issued_at=issued_at,
                nonce=nonce,
                signature_b64url=self._signer.sign(message),
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = self._transport.request(method, path, headers, body)
        checked = wire.check_response(
            method=method,
            path=path,
            request_nonce=nonce,
            status=response.status,
            body=response.body,
            headers=response.headers,
        )
        wire.verify_signature(
            self._config.cp_response_public_keys_b64url,
            checked.message,
            checked.signature,
        )
        if response.status >= 500:
            raise Refusal("cp_unreachable")
        document: dict[str, object] = {}
        if response.body:
            parsed = wire.parse_json_body(response.body)
            if not isinstance(parsed, dict):
                raise Refusal("response_malformed")
            document = cast("dict[str, object]", parsed)
        raw_now = document.get("store_now")
        if not isinstance(raw_now, str):
            raise Refusal("response_malformed")
        store_now = wire.parse_stamp(raw_now)
        wire.check_skew(issued_at, raw_now)
        if response.status >= 400:
            error = document.get("error")
            detail = error if isinstance(error, str) else None
            if detail is not None and not _ERROR_CODE.fullmatch(detail):
                detail = None
            raise Refusal("cp_refused", detail)
        return Reply(response.status, document, store_now)

    def materials(self) -> Reply | None:
        """The pending signed dispatch materials, or None when nothing is pending."""
        reply = self.call("GET", "materials", None)
        if reply.document.get("pending") is False:
            check_shape(reply.document)  # idle: exactly the four always-keys
            if reply.document["target_ref"] != self._config.target_ref:
                raise Refusal("materials_target_mismatch")
            return None
        # A pending document is shape-checked and verified by `parse_materials`,
        # inside the launcher's refusal reporting.
        return reply

    def post_observation(self, observation: dict[str, object]) -> Reply:
        if self._config.target_api_version == 2:
            # Control's PoP-signed envelope (target_api_v2.json). A record that
            # cannot be expressed is a ValueError: the outbox dead-letters it.
            statement = observation_v2.statement_from(
                observation, key_id=self._signer.credential_id
            )
            return self.call(
                "POST", "observations", observation_v2.seal(statement, self._signer)
            )
        return self.call("POST", "observations", observation)
