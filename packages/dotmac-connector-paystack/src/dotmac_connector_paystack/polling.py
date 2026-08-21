"""Paystack transaction-list reconciliation through the engine-owned cursor."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

import httpx
from dotmac_integration.spi import InboundEvent

API_HOST: Final = "api.paystack.co"


class PaystackPollError(RuntimeError):
    """Reconciliation could not produce a trustworthy complete page."""


def _timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaystackPollError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PaystackPollError(f"{label} is invalid") from None
    if parsed.tzinfo is None:
        raise PaystackPollError(f"{label} has no timezone")
    return value


def _cursor(value: str | None, start: str) -> tuple[int, str]:
    if value is None:
        return 1, _timestamp(start, label="reconcile_from")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        raise PaystackPollError("cursor is not valid JSON") from None
    if not isinstance(decoded, dict) or set(decoded) != {"page", "from"}:
        raise PaystackPollError("cursor has an invalid shape")
    page = decoded["page"]
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise PaystackPollError("cursor page is invalid")
    return page, _timestamp(decoded["from"], label="cursor from")


@dataclass(frozen=True, slots=True)
class PaystackPollHandler:
    translate: object = field(repr=False)
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0

    def poll(
        self,
        cursor: str | None,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> tuple[tuple[InboundEvent, ...], str | None]:
        start = config.get("reconcile_from")
        page_size = config.get("page_size", 50)
        if not isinstance(start, str):
            raise PaystackPollError("reconcile_from is required for polling")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or not 1 <= page_size <= 100
        ):
            raise PaystackPollError("page_size is invalid")
        api_secret = secrets.get("api_secret_key")
        if not isinstance(api_secret, str) or not api_secret:
            raise PaystackPollError("required material is unavailable")
        page, from_timestamp = _cursor(cursor, start)
        try:
            with httpx.Client(
                base_url=f"https://{API_HOST}",
                transport=self.transport,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = client.get(
                    "/transaction",
                    params={
                        "perPage": page_size,
                        "page": page,
                        "from": from_timestamp,
                    },
                    headers={"authorization": f"Bearer {api_secret}"},
                )
        except httpx.RequestError:
            raise PaystackPollError("provider_request_failed") from None
        if response.status_code >= 400:
            raise PaystackPollError(
                "authentication_rejected"
                if response.status_code in {401, 403}
                else "rate_limited"
                if response.status_code == 429
                else "provider_request_failed"
            )
        try:
            body = response.json()
        except ValueError:
            raise PaystackPollError("provider response is not JSON") from None
        if not isinstance(body, Mapping) or body.get("status") is not True:
            raise PaystackPollError("provider response status is invalid")
        data = body.get("data")
        meta = body.get("meta", {})
        if not isinstance(data, list) or not isinstance(meta, Mapping):
            raise PaystackPollError("provider response page is invalid")
        translate = self.translate
        events: list[InboundEvent] = []
        watermark = from_timestamp
        for item in data:
            if not isinstance(item, Mapping):
                raise PaystackPollError("provider transaction is not an object")
            event = translate(item)  # type: ignore[operator]
            event.payload["arrival_mode"] = "poll"
            event.payload["confirmation_evidence"] = "provider_api"
            events.append(event)
            candidate = item.get("paid_at") or item.get("created_at")
            if isinstance(candidate, str):
                _timestamp(candidate, label="provider transaction timestamp")
                watermark = max(watermark, candidate)
        page_count = meta.get("pageCount", page)
        if not isinstance(page_count, int) or isinstance(page_count, bool):
            raise PaystackPollError("provider page count is invalid")
        next_page = page + 1 if page < page_count else 1
        next_from = from_timestamp if next_page > 1 else watermark
        next_cursor = json.dumps(
            {"page": next_page, "from": next_from},
            sort_keys=True,
            separators=(",", ":"),
        )
        return tuple(events), next_cursor
