"""Authenticated Remita RRR status polling and neutral fact translation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

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

from dotmac_connector_remita.outbound import (
    ISSUANCE_CAPABILITY_ID,
    ISSUANCE_CONFIG_SCHEMA,
    CommandContractError,
    RemitaIssuanceHandler,
    issuance_config,
)

CONNECTOR_KEY: Final = "remita"
CAPABILITY_ID: Final = "payments.reference.status.observation.v1"
VERSION: Final = "0.1.0a1"
API_KEY: Final = "api_key"
DEMO_HOST: Final = "demo.remita.net"
LIVE_HOST: Final = "login.remita.net"
STATUS_PATH: Final = (
    "/remita/exapp/api/v1/send/api/echannelsvc/"
    "{merchant_id}/{rrr}/{api_hash}/status.reg"
)

_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9_-]{1,160}")
_RRR: Final = re.compile(r"[A-Za-z0-9-]{1,64}")

CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["merchant_id", "environment", "rrrs"],
    "properties": {
        "merchant_id": {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,160}$"},
        "environment": {"type": "string", "enum": ["demo", "live"]},
        "rrrs": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "uniqueItems": True,
            "items": {"type": "string", "pattern": r"^[A-Za-z0-9-]{1,64}$"},
        },
    },
}

# The exact published SPI-1.3 poll-only contract. It stays discoverable so an
# installed status-only revision is adopted deliberately rather than becoming an
# unknown digest.
POLL_ONLY_MANIFEST: Final = ConnectorManifest(
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
            name=API_KEY,
            description="Remita API key used only to derive request authentication.",
        ),
    ),
    egress=EgressDeclaration(hosts=(DEMO_HOST, LIVE_HOST)),
)

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    # SPI 1.4 for the per-capability mode mapping below. Without it a
    # conformance run would call the poll factory for a delivery-only
    # capability, which is exactly what `CapabilityDeclaration.modes` removes.
    spi_range=SpiRange.parse(">=1.4,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            config_schema=CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.POLL}),
        ),
        CapabilityDeclaration(
            capability_id=ISSUANCE_CAPABILITY_ID,
            config_schema=ISSUANCE_CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.DELIVERY}),
        ),
    ),
    secret_bindings=(
        SecretBindingDeclaration(
            name=API_KEY,
            description="Remita API key used only to derive request authentication.",
        ),
    ),
    # Unchanged: issuance reaches the SAME two fixed provider hosts the status
    # leg already reaches. An outbound capability that widened egress would be a
    # new reachability decision hiding inside a feature.
    egress=EgressDeclaration(hosts=(DEMO_HOST, LIVE_HOST)),
)


class RemitaError(RuntimeError):
    """A Remita operation could not produce a trustworthy observation."""


class RemitaProtocolError(RemitaError):
    """Configuration or a provider response is outside the connector contract."""


class RemitaRequestError(RemitaError):
    """Provider I/O failed without retaining material or response content."""

    def __init__(self, code: str) -> None:
        self.code = code if code.isidentifier() else "provider_request_failed"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class _Config:
    merchant_id: str
    host: str
    rrrs: tuple[str, ...]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _config(value: Mapping[str, object]) -> _Config:
    merchant_id = _text(value.get("merchant_id"))
    environment = value.get("environment")
    rrrs = value.get("rrrs")
    if merchant_id is None or _IDENTIFIER.fullmatch(merchant_id) is None:
        raise RemitaProtocolError("merchant_id is invalid")
    if environment not in {"demo", "live"}:
        raise RemitaProtocolError("environment is invalid")
    if not isinstance(rrrs, list) or not 1 <= len(rrrs) <= 100:
        raise RemitaProtocolError("rrrs must contain between one and 100 entries")
    normalized: list[str] = []
    for rrr in rrrs:
        item = _text(rrr)
        if item is None or _RRR.fullmatch(item) is None:
            raise RemitaProtocolError("rrr is invalid")
        normalized.append(item)
    if len(set(normalized)) != len(normalized):
        raise RemitaProtocolError("rrrs contains a duplicate")
    return _Config(
        merchant_id=merchant_id,
        host=LIVE_HOST if environment == "live" else DEMO_HOST,
        rrrs=tuple(normalized),
    )


def _secret(value: Mapping[str, object]) -> str:
    material = value.get(API_KEY)
    if not isinstance(material, str) or not material:
        raise RemitaProtocolError("required material is unavailable")
    return material


def _parse_response(text: str) -> Mapping[str, object]:
    stripped = text.strip()
    match = re.fullmatch(r"jsonp\s*\((.*)\)", stripped, re.DOTALL)
    encoded = match.group(1) if match else stripped
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError:
        raise RemitaProtocolError("provider response is not JSON") from None
    if not isinstance(value, Mapping):
        raise RemitaProtocolError("provider response is not an object")
    return value


def _optional_text(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if text is None:
        raise RemitaProtocolError(f"provider {label} is invalid")
    return text


def _amount(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise RemitaProtocolError("provider amount is invalid")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        raise RemitaProtocolError("provider amount is invalid") from None
    if not parsed.is_finite() or parsed < 0:
        raise RemitaProtocolError("provider amount is invalid")
    return format(parsed, "f")


def _event(rrr: str, body: Mapping[str, object]) -> InboundEvent:
    provider_rrr = _optional_text(body.get("RRR"), label="RRR") or rrr
    if provider_rrr != rrr:
        raise RemitaProtocolError("provider RRR differs from the requested RRR")
    status = _optional_text(body.get("status"), label="status")
    if status is None:
        raise RemitaProtocolError("provider status is missing")
    transaction_id = _optional_text(body.get("transactionId"), label="transaction id")
    payment_date = _optional_text(body.get("paymentDate"), label="payment date")
    amount = _amount(body.get("amount"))
    identity_material = "|".join(
        (provider_rrr, status, transaction_id or "", payment_date or "", amount or "")
    )
    identity = hashlib.sha256(identity_material.encode()).hexdigest()[:32]
    payload: dict[str, object] = {
        "capability_id": CAPABILITY_ID,
        "provider_reference": provider_rrr,
        "provider_status": status,
        "currency": "NGN",
        "arrival_mode": "poll",
    }
    for key, value in (
        ("provider_message", _optional_text(body.get("message"), label="message")),
        ("provider_transaction_id", transaction_id),
        ("provider_payment_date", payment_date),
        (
            "provider_debited_account",
            _optional_text(body.get("debittedAccount"), label="debited account"),
        ),
        ("amount", amount),
    ):
        if value is not None:
            payload[key] = value
    return InboundEvent(
        provider_event_id=f"{provider_rrr}:{identity}",
        event_type=CAPABILITY_ID,
        payload=payload,
    )


@dataclass(frozen=True, slots=True)
class RemitaPollHandler:
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0

    def _status(self, config: _Config, rrr: str, api_key: str) -> InboundEvent:
        api_hash = hashlib.sha512(
            f"{rrr}{api_key}{config.merchant_id}".encode()
        ).hexdigest()
        path = STATUS_PATH.format(
            merchant_id=config.merchant_id,
            rrr=rrr,
            api_hash=api_hash,
        )
        try:
            with httpx.Client(
                base_url=f"https://{config.host}",
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.get(
                    path,
                    headers={
                        "accept": "application/json",
                        "authorization": (
                            f"remitaConsumerKey={config.merchant_id},"
                            f"remitaConsumerToken={api_hash}"
                        ),
                    },
                )
        except httpx.RequestError:
            raise RemitaRequestError("provider_request_failed") from None
        if response.status_code >= 400:
            code = (
                "authentication_rejected"
                if response.status_code in {401, 403}
                else "rate_limited"
                if response.status_code == 429
                else "provider_request_failed"
            )
            raise RemitaRequestError(code)
        return _event(rrr, _parse_response(response.text))

    def poll(
        self,
        cursor: str | None,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> tuple[tuple[InboundEvent, ...], str | None]:
        del cursor
        resolved = _config(config)
        api_key = _secret(secrets)
        events = tuple(self._status(resolved, rrr, api_key) for rrr in resolved.rrrs)
        return events, None


@dataclass(frozen=True, slots=True)
class RemitaPlugin:
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    manifest: ConnectorManifest = MANIFEST
    historical_manifests: tuple[ConnectorManifest, ...] = (POLL_ONLY_MANIFEST,)
    modes: frozenset[ConnectorMode] = frozenset(
        {ConnectorMode.POLL, ConnectorMode.DELIVERY}
    )

    def poll_handler_for(self, capability_id: str) -> PollHandler:
        self.manifest.require_declares(capability_id)
        if capability_id != CAPABILITY_ID:
            raise ValueError(f"{capability_id!r} is not a poll capability")
        return RemitaPollHandler(self.transport, self.timeout_seconds)

    def handler_for(self, capability_id: str) -> RemitaIssuanceHandler:
        self.manifest.require_declares(capability_id)
        if capability_id != ISSUANCE_CAPABILITY_ID:
            raise ValueError(f"{capability_id!r} is not a delivery capability")
        return RemitaIssuanceHandler(DEMO_HOST, LIVE_HOST, self.transport)

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        if "settlement_currency" in config:
            # An issuance binding: `settlement_currency` is required by
            # ISSUANCE_CONFIG_SCHEMA and forbidden by the status schema's
            # `additionalProperties: false`, so its presence is unambiguous.
            # Validated STATICALLY, and that is deliberate:
            # the only Remita issuance call there is MINTS a reference, so a
            # live probe here would create a payment obligation just to answer
            # whether the credentials look usable. The status leg below can
            # probe because reading a reference changes nothing.
            try:
                issuance_config(config, DEMO_HOST, LIVE_HOST)
                _secret(secrets)
            except (CommandContractError, RemitaProtocolError):
                return (Diagnostic(ok=False, code="configuration_invalid"),)
            return ()
        try:
            resolved = _config(config)
            api_key = _secret(secrets)
        except RemitaProtocolError:
            return (Diagnostic(ok=False, code="configuration_invalid"),)
        handler = RemitaPollHandler(self.transport, self.timeout_seconds)
        try:
            handler._status(resolved, resolved.rrrs[0], api_key)
        except RemitaRequestError as exc:
            return (Diagnostic(ok=False, code=exc.code),)
        except RemitaProtocolError:
            return (Diagnostic(ok=False, code="provider_contract_invalid"),)
        return ()


PLUGIN: Final = RemitaPlugin()
