"""Flutterwave API v4 charge-list reconciliation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Final

import httpx
from dotmac_integration.spi import InboundEvent

SANDBOX_HOST: Final = "developersandbox-api.flutterwave.com"
LIVE_HOST: Final = "f4bexperience.flutterwave.com"
IDENTITY_HOST: Final = "idp.flutterwave.com"
TOKEN_PATH: Final = "/realms/flutterwave/protocol/openid-connect/token"


class FlutterwavePollError(RuntimeError):
    """API v4 reconciliation could not produce a trustworthy complete page."""


def _timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FlutterwavePollError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise FlutterwavePollError(f"{label} is invalid") from None
    if parsed.tzinfo is None:
        raise FlutterwavePollError(f"{label} has no timezone")
    return value


def _cursor(value: str | None, start: str) -> tuple[int, str]:
    if value is None:
        return 1, _timestamp(start, label="reconcile_from")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        raise FlutterwavePollError("cursor is not valid JSON") from None
    if not isinstance(decoded, dict) or set(decoded) != {"page", "from"}:
        raise FlutterwavePollError("cursor has an invalid shape")
    page = decoded["page"]
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise FlutterwavePollError("cursor page is invalid")
    return page, _timestamp(decoded["from"], label="cursor from")


@dataclass(frozen=True, slots=True)
class FlutterwavePollHandler:
    translate: object = field(repr=False)
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0

    def _token(
        self,
        client: httpx.Client,
        client_id: str,
        client_secret: str,
    ) -> str:
        try:
            response = client.post(
                f"https://{IDENTITY_HOST}{TOKEN_PATH}",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                },
            )
        except httpx.RequestError:
            raise FlutterwavePollError("provider_request_failed") from None
        if response.status_code >= 400:
            raise FlutterwavePollError("authentication_rejected")
        try:
            body = response.json()
        except ValueError:
            raise FlutterwavePollError("token response is not JSON") from None
        token = body.get("access_token") if isinstance(body, Mapping) else None
        if not isinstance(token, str) or not token:
            raise FlutterwavePollError("token response is invalid")
        return token

    def poll(
        self,
        cursor: str | None,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> tuple[tuple[InboundEvent, ...], str | None]:
        environment = config.get("environment")
        start = config.get("reconcile_from")
        page_size = config.get("page_size", 20)
        if environment not in {"sandbox", "live"}:
            raise FlutterwavePollError("environment is required for polling")
        if not isinstance(start, str):
            raise FlutterwavePollError("reconcile_from is required for polling")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or not 10 <= page_size <= 50
        ):
            raise FlutterwavePollError("page_size is invalid")
        client_id = secrets.get("api_client_id")
        client_secret = secrets.get("api_client_secret")
        if not client_id or not client_secret:
            raise FlutterwavePollError("required material is unavailable")
        page, from_timestamp = _cursor(cursor, start)
        host = LIVE_HOST if environment == "live" else SANDBOX_HOST
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                token = self._token(client, client_id, client_secret)
                response = client.get(
                    f"https://{host}/charges",
                    params={"page": page, "size": page_size, "from": from_timestamp},
                    headers={"authorization": f"Bearer {token}"},
                )
        except httpx.RequestError:
            raise FlutterwavePollError("provider_request_failed") from None
        if response.status_code >= 400:
            raise FlutterwavePollError(
                "authentication_rejected"
                if response.status_code in {401, 403}
                else "rate_limited"
                if response.status_code == 429
                else "provider_request_failed"
            )
        try:
            body = json.loads(response.content, parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FlutterwavePollError("provider response is not JSON") from None
        if not isinstance(body, Mapping):
            raise FlutterwavePollError("provider response is not an object")
        data = body.get("data")
        meta = body.get("meta", {})
        if not isinstance(data, list) or not isinstance(meta, Mapping):
            raise FlutterwavePollError("provider response page is invalid")
        translate = self.translate
        events: list[InboundEvent] = []
        watermark = from_timestamp
        for item in data:
            if not isinstance(item, Mapping):
                raise FlutterwavePollError("provider charge is not an object")
            event = translate(item)  # type: ignore[operator]
            event.payload["arrival_mode"] = "poll"
            event.payload["confirmation_evidence"] = "provider_api"
            events.append(event)
            candidate = item.get("created_datetime")
            if isinstance(candidate, str):
                _timestamp(candidate, label="provider charge timestamp")
                watermark = max(watermark, candidate)
        total_pages = meta.get("total_pages", page)
        if not isinstance(total_pages, int) or isinstance(total_pages, bool):
            raise FlutterwavePollError("provider page count is invalid")
        next_page = page + 1 if page < total_pages else 1
        next_from = from_timestamp if next_page > 1 else watermark
        return tuple(events), json.dumps(
            {"page": next_page, "from": next_from},
            sort_keys=True,
            separators=(",", ":"),
        )
