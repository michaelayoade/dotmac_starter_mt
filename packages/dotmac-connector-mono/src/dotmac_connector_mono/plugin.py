"""Mono Financial Data v2 polling and neutral transaction translation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final
from urllib.parse import urlsplit

import httpx
from dotmac_integration.spi import (
    CapabilityDeclaration,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    EgressDeclaration,
    InboundEvent,
    PollHandler,
    SecretBindingDeclaration,
    SpiRange,
)

CONNECTOR_KEY: Final = "mono"
CAPABILITY_ID: Final = "banking.transaction.observation.v1"
VERSION: Final = "0.1.0a1"
API_HOST: Final = "api.withmono.com"
API_ORIGIN: Final = f"https://{API_HOST}"
API_SECRET_KEY: Final = "api_secret_key"

_ACCOUNT_ID_RE: Final = re.compile(r"[A-Za-z0-9_-]{1,160}")
_CURRENCY_RE: Final = re.compile(r"[A-Z]{3}")
_DATE_RE: Final = re.compile(r"\d{2}-\d{2}-\d{4}")

CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["account_id", "currency", "page_size", "start_date"],
    "properties": {
        "account_id": {
            "type": "string",
            "pattern": r"^[A-Za-z0-9_-]{1,160}$",
        },
        "currency": {"type": "string", "pattern": r"^[A-Z]{3}$"},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
        "start_date": {
            "type": "string",
            "pattern": r"^\d{2}-\d{2}-\d{4}$",
        },
    },
}

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SpiRange.parse(">=1.3,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            config_schema=CONFIG_SCHEMA,
        ),
    ),
    secret_bindings=(
        SecretBindingDeclaration(
            name=API_SECRET_KEY,
            description="Mono app secret sent in the mono-sec-key header.",
        ),
    ),
    egress=EgressDeclaration(hosts=(API_HOST,)),
)


class MonoError(RuntimeError):
    """A Mono operation could not produce trustworthy observations."""


class MonoProtocolError(MonoError):
    """The provider response or persisted cursor is outside the contract."""


class MonoRequestError(MonoError):
    """A request failed without retaining provider body or secret material."""

    def __init__(self, code: str) -> None:
        self.code = code if code.isidentifier() else "provider_request_failed"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class _Config:
    account_id: str
    currency: str
    page_size: int
    start_date: str


@dataclass(frozen=True, slots=True)
class _Cursor:
    account_id: str
    next: str | None = None
    watermark: str | None = None

    def encode(self) -> str:
        return json.dumps(
            {
                "account_id": self.account_id,
                "next": self.next,
                "watermark": self.watermark,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _config(value: Mapping[str, object]) -> _Config:
    account_id = _text(value.get("account_id"))
    currency = _text(value.get("currency"))
    page_size = value.get("page_size")
    start_date = _text(value.get("start_date"))
    if account_id is None or _ACCOUNT_ID_RE.fullmatch(account_id) is None:
        raise MonoProtocolError("account_id is invalid")
    if currency is None or _CURRENCY_RE.fullmatch(currency) is None:
        raise MonoProtocolError("currency is invalid")
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= 100
    ):
        raise MonoProtocolError("page_size is invalid")
    if start_date is None or _DATE_RE.fullmatch(start_date) is None:
        raise MonoProtocolError("start_date is invalid")
    try:
        datetime.strptime(start_date, "%d-%m-%Y")
    except ValueError:
        raise MonoProtocolError("start_date is invalid") from None
    return _Config(account_id, currency, page_size, start_date)


def _secret(value: Mapping[str, object]) -> str:
    material = value.get(API_SECRET_KEY)
    if not isinstance(material, str) or not material:
        raise MonoProtocolError("required material is unavailable")
    return material


def _timestamp(value: object) -> str:
    text = _text(value)
    if text is None:
        raise MonoProtocolError("transaction timestamp is missing")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise MonoProtocolError("transaction timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise MonoProtocolError("transaction timestamp has no timezone")
    return text


def _relative_next(value: object, account_id: str) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if text is None:
        raise MonoProtocolError("pagination cursor is invalid")
    parsed = urlsplit(text)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != API_HOST:
            raise MonoProtocolError("pagination origin is not the declared provider")
    if parsed.fragment:
        raise MonoProtocolError("pagination cursor contains a fragment")
    allowed_paths = {
        f"/v2/accounts/{account_id}/transactions",
        f"/v2/{account_id}/transactions",
    }
    if parsed.path not in allowed_paths:
        raise MonoProtocolError("pagination path is not the configured account")
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _cursor(value: str | None, account_id: str) -> _Cursor:
    if value is None:
        return _Cursor(account_id=account_id)
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        raise MonoProtocolError("poll cursor is not valid JSON") from None
    if not isinstance(decoded, dict) or set(decoded) != {
        "account_id",
        "next",
        "watermark",
    }:
        raise MonoProtocolError("poll cursor has an invalid shape")
    if decoded["account_id"] != account_id:
        raise MonoProtocolError("poll cursor belongs to another account")
    watermark = decoded["watermark"]
    if watermark is not None:
        watermark = _timestamp(watermark)
    return _Cursor(
        account_id=account_id,
        next=_relative_next(decoded["next"], account_id),
        watermark=watermark,
    )


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MonoProtocolError(f"{label} is not an object")
    return value


def _transaction(value: object, config: _Config) -> tuple[InboundEvent, str]:
    item = _mapping(value, label="transaction")
    transaction_id = _text(item.get("id"))
    amount = item.get("amount")
    direction = _text(item.get("type"))
    occurred_at = _timestamp(item.get("date"))
    if transaction_id is None:
        raise MonoProtocolError("transaction id is missing")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise MonoProtocolError("transaction amount is invalid")
    if direction not in {"credit", "debit"}:
        raise MonoProtocolError("transaction direction is invalid")
    payload: dict[str, object] = {
        "capability_id": CAPABILITY_ID,
        "provider_account_id": config.account_id,
        "provider_transaction_id": transaction_id,
        "amount_minor": str(amount),
        "currency": config.currency,
        "direction": direction,
        "occurred_at": occurred_at,
        "arrival_mode": "poll",
    }
    for source, target in (
        ("narration", "narration"),
        ("category", "category"),
    ):
        optional = _text(item.get(source))
        if optional is not None:
            payload[target] = optional
    balance = item.get("balance")
    if balance is not None:
        if not isinstance(balance, int) or isinstance(balance, bool):
            raise MonoProtocolError("transaction balance is invalid")
        payload["balance_minor"] = str(balance)
    return (
        InboundEvent(
            provider_event_id=transaction_id,
            event_type=CAPABILITY_ID,
            payload=payload,
        ),
        occurred_at,
    )


@dataclass(frozen=True, slots=True)
class MonoPollHandler:
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0

    def _request(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None,
        secret: str,
    ) -> Mapping[str, object]:
        try:
            with httpx.Client(
                base_url=API_ORIGIN,
                headers={"accept": "application/json", "mono-sec-key": secret},
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.get(path, params=params)
        except httpx.RequestError:
            raise MonoRequestError("provider_request_failed") from None
        if response.status_code >= 400:
            code = (
                "authentication_rejected"
                if response.status_code in {401, 403}
                else "rate_limited"
                if response.status_code == 429
                else "provider_request_failed"
            )
            raise MonoRequestError(code)
        try:
            body = response.json()
        except ValueError:
            raise MonoProtocolError("provider response is not JSON") from None
        return _mapping(body, label="provider response")

    def poll(
        self,
        cursor: str | None,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> tuple[tuple[InboundEvent, ...], str | None]:
        resolved = _config(config)
        state = _cursor(cursor, resolved.account_id)
        secret = _secret(secrets)
        path = state.next or f"/v2/accounts/{resolved.account_id}/transactions"
        params: dict[str, str | int] | None = None
        if state.next is None:
            start = resolved.start_date
            if state.watermark is not None:
                parsed = datetime.fromisoformat(state.watermark.replace("Z", "+00:00"))
                start = parsed.strftime("%d-%m-%Y")
            params = {
                "limit": resolved.page_size,
                "paginate": "true",
                "start": start,
            }
        body = self._request(path, params=params, secret=secret)
        data = body.get("data")
        if not isinstance(data, list):
            raise MonoProtocolError("provider response data is not a list")
        translated = tuple(_transaction(item, resolved) for item in data)
        events = tuple(event for event, _ in translated)
        watermark = max(
            (timestamp for _, timestamp in translated),
            default=state.watermark,
        )
        meta = _mapping(body.get("meta", {}), label="provider response meta")
        next_path = (
            _relative_next(meta.get("next"), resolved.account_id) if data else None
        )
        next_state = _Cursor(
            account_id=resolved.account_id,
            next=next_path,
            watermark=watermark,
        )
        return events, next_state.encode()


@dataclass(frozen=True, slots=True)
class MonoPlugin:
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    manifest: ConnectorManifest = MANIFEST
    historical_manifests: tuple[ConnectorManifest, ...] = ()
    modes: frozenset[ConnectorMode] = frozenset({ConnectorMode.POLL})

    def poll_handler_for(self, capability_id: str) -> PollHandler:
        self.manifest.require_declares(capability_id)
        return MonoPollHandler(self.transport, self.timeout_seconds)

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        try:
            resolved = _config(config)
            secret = _secret(secrets)
        except MonoProtocolError:
            return (Diagnostic(ok=False, code="configuration_invalid"),)
        handler = MonoPollHandler(self.transport, self.timeout_seconds)
        try:
            body = handler._request(
                f"/v2/accounts/{resolved.account_id}",
                params=None,
                secret=secret,
            )
            if not isinstance(body.get("data"), Mapping):
                return (Diagnostic(ok=False, code="provider_contract_invalid"),)
        except MonoRequestError as exc:
            return (Diagnostic(ok=False, code=exc.code),)
        except MonoProtocolError:
            return (Diagnostic(ok=False, code="provider_contract_invalid"),)
        return ()


PLUGIN: Final = MonoPlugin()
