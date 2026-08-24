"""Flutterwave API v4 outbound commands — carried, never owned.

Two DELIVERY capabilities and nothing else:

============================ ===============================================
capability                   what it carries
============================ ===============================================
``payments.intent.v1``       initialize one payment at the provider
``payments.refund.v1``       request one refund against one provider charge
============================ ===============================================

## What is deliberately absent

**Transfers / payouts.** ERP carries a payouts surface
(``app/services/finance/payments/batch_transfer_service.py``) and Flutterwave
v4 has one too. It is NOT built here: no product consumer exists, and an
outbound money-movement command with no owner asking for it is the one kind of
code whose first execution is also its first review.

**API v3.** The connector accepts v4 authentication, v4 hosts and v4 payloads on
both legs. There is no fallback — a "try v4, fall back to v3" client is how a v3
credential ends up trusted after v4 was chosen.

**Verification and refund STATUS.** Both stay reconciliation concerns served by
``polling.py`` and the settlement-observation capability. An acknowledgement
that a charge was submitted is not evidence that money moved
(``docs/superpowers/specs/2026-08-14-payment-connector-and-settlement-contracts.md``
§ 3.2), and turning a status READ into an outbound COMMAND would put a fact into
a queue that retries.

## Outcome classification — this is money

``dotmac_integration.retry.OutcomeStatus`` has four members, and the five
outcome kinds a payment command produces map onto them exactly once:

=================== =========================== ==============================
outcome kind        OutcomeStatus               why
=================== =========================== ==============================
success             ``SUCCEEDED``               provider accepted; reference held
decline             ``TERMINAL``                a refusal is a decision, and
                                                repeating the same request
                                                cannot change it — a decline is
                                                NEVER retryable
retryable           ``RETRYABLE``               the request provably did not
                                                reach the provider, or the
                                                provider asked for a retry
terminal            ``TERMINAL``                malformed command, or the
                                                credential was refused
ambiguous           ``RECONCILIATION_REQUIRED`` the effect may have landed
=================== =========================== ==============================

A timeout AFTER the bytes went out is ambiguous, not retryable: the charge may
be live at the provider, so a retry risks charging a customer twice and a
dead-letter hides a charge that exists. Only a connect-phase failure — where
nothing was ever sent — is retryable.

## Idempotency

The engine's ``DispatchRequest.idempotency_key`` is carried to Flutterwave v4's
own ``X-Idempotency-Key`` header, verbatim. The connector mints nothing, stores
nothing and compares nothing: the at-most-once ledger is
``dotmac_kernel.idempotency`` reached through
``dotmac_integration.idempotency`` (ADR-0014, hard rule 21), and the header is
the PROVIDER's second, independent guard over the one window that ledger cannot
see — between the request leaving and the response arriving.

``reference`` (the product's opaque ``intent_reference``) is a second natural key
at the provider, which refuses a re-used one. That refusal is evidence the first
attempt LANDED, so it is classified ambiguous rather than terminal.

## Money

Exact ``Decimal`` from the first line to the last byte, including JSON
serialization — :func:`exact_json` exists because ``json.dumps`` has no way to
write a ``Decimal`` as a JSON number without routing it through binary floating
point.

The currency and its minor-unit exponent are REQUIRED on the wire and carry no
default. ``dotmac_sub:app/services/integrations/connectors/payment_gateway.py``
``:333`` reads ``config.get("default_currency") or "NGN"`` — a hardcoded
currency three layers into a default chain — and ``:331``/``:371`` coerce the
amount through a binary floating-point value on the way to a payment wire. Both
are the port deltas.

The exponent is supplied by the PRODUCT, from
``dotmac_kernel.money.Currency.minor_units``, because a connector may import
nothing but the SPI among Dotmac packages
(``tests/architecture/test_flutterwave_connector_boundary.py``
``::test_connector_imports_only_the_spi_among_dotmac_packages``) and a private
ISO-4217 table inside a transport would become a second, drifting authority on
what a currency IS. The connector's job is to REFUSE a value that does not fit
the exponent it was told, never to round one into fitting: a transport that
rounds money has made a financial decision.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final
from urllib.parse import quote

import httpx
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import DispatchRequest

from dotmac_connector_flutterwave.polling import (
    IDENTITY_HOST,
    LIVE_HOST,
    SANDBOX_HOST,
    TOKEN_PATH,
)

INTENT_CAPABILITY_ID: Final = "payments.intent.v1"
REFUND_CAPABILITY_ID: Final = "payments.refund.v1"

#: Flutterwave v4's own idempotency mechanism. Sent on every outbound command.
IDEMPOTENCY_HEADER: Final = "x-idempotency-key"

CHARGES_PATH: Final = "/charges"
REFUNDS_PATH: Final = "/charges/{charge_id}/refunds"

#: Bounds on opaque product strings. The connector does not interpret them; it
#: refuses ones it cannot put on a wire, so a malformed command fails HERE
#: rather than as a provider 400 that reads like a provider fault.
_MAX_REFERENCE: Final = 100
_MAX_OPAQUE: Final = 500
#: No ISO-4217 currency has more than four fraction digits. A wider bound would
#: let a caller smuggle arbitrary precision past the exponent check below.
_MAX_MINOR_UNITS: Final = 4
_MIN_TIMEOUT: Final = 1
_MAX_TIMEOUT: Final = 120

#: `timeout_seconds` is REQUIRED and appears in no other capability's schema, so
#: it doubles as the discriminator `validate_connection` uses to recognise an
#: outbound binding. Requiring it is also right on its own terms: how long to
#: wait before a payment command's outcome becomes ambiguous is a deployment
#: decision, not a constant hiding in a dataclass default.
OUTBOUND_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["environment", "timeout_seconds"],
    "properties": {
        "environment": {"type": "string", "enum": ["sandbox", "live"]},
        "timeout_seconds": {
            "type": "integer",
            "minimum": _MIN_TIMEOUT,
            "maximum": _MAX_TIMEOUT,
        },
    },
}


class CommandContractError(ValueError):
    """The command this connector was handed is not one it can put on a wire.

    Carries a machine ``code`` only. The command holds money values and opaque
    product identifiers, and ``Outcome.error_detail`` is PERSISTED — so nothing
    read out of the payload may travel in the message.
    """

    def __init__(self, code: str) -> None:
        self.code = code if code.isidentifier() else "command_invalid"
        super().__init__(self.code)


# ── exact JSON, because `json.dumps` cannot write a Decimal ──────────────────


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def exact_json(value: object) -> bytes:
    """Serialize to JSON bytes with EXACT numbers and no binary floating point.

    ``json.dumps`` refuses a ``Decimal`` outright, and every published
    workaround — ``default=float``, a ``float`` subclass with a patched
    ``__repr__``, ``parse_float`` — routes the amount through binary floating
    point at least once. This walks the value instead and writes a ``Decimal``
    as the digits it actually holds.

    An unsupported type RAISES rather than being coerced, and that refusal is
    the point: a silent coercion is exactly how a binary floating-point amount
    reached a payment wire in the source implementation.
    """
    return _encode(value).encode()


def _encode(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CommandContractError("amount_not_finite")
        # `format(..., "f")` writes the digits held: never exponent notation,
        # never a binary approximation.
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _quoted(value)
    if isinstance(value, bytes | bytearray):
        raise CommandContractError("value_not_exactly_encodable")
    if isinstance(value, Mapping):
        items = ",".join(
            f"{_quoted(str(key))}:{_encode(item)}" for key, item in value.items()
        )
        return "{" + items + "}"
    if isinstance(value, Sequence):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    # Reached by a binary floating-point value, and by anything else nobody has
    # decided how to write exactly.
    raise CommandContractError("value_not_exactly_encodable")


# ── the command payload ──────────────────────────────────────────────────────


def _opaque(value: object, *, code: str, limit: int = _MAX_OPAQUE) -> str:
    if not isinstance(value, str):
        raise CommandContractError(code)
    text = value.strip()
    if not text or len(text) > limit:
        raise CommandContractError(code)
    return text


def _optional_opaque(value: object, *, code: str) -> str | None:
    return None if value is None else _opaque(value, code=code)


def _currency(value: object) -> str:
    code = _opaque(value, code="currency_required", limit=3)
    if len(code) != 3 or not code.isalpha() or not code.isascii():
        raise CommandContractError("currency_required")
    return code.upper()


def _minor_units(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CommandContractError("currency_minor_units_required")
    if not 0 <= value <= _MAX_MINOR_UNITS:
        raise CommandContractError("currency_minor_units_required")
    return value


def _exact_amount(value: object, minor_units: int) -> Decimal:
    """An exact positive amount that ALREADY fits the currency's exponent.

    A binary floating-point value is refused by TYPE, before any parsing:
    accepting one and rounding it would mean the transport decided what the
    amount was.
    """
    if isinstance(value, bool) or not isinstance(value, int | str | Decimal):
        raise CommandContractError("amount_not_exact")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        raise CommandContractError("amount_not_exact") from None
    if not amount.is_finite() or amount <= 0:
        raise CommandContractError("amount_not_positive")
    quantum = Decimal(1).scaleb(-minor_units)
    if amount != amount.quantize(quantum):
        # NOT rounded. A transport that rounds money has made a decision the
        # product owns; the product already quantized through `Money`, so a
        # value that does not fit is a product bug, and a loud one.
        raise CommandContractError("amount_precision_invalid")
    return amount.quantize(quantum)


@dataclass(frozen=True, slots=True)
class _Money:
    amount: Decimal
    currency: str


def _money(payload: Mapping[str, object]) -> _Money:
    minor_units = _minor_units(payload.get("currency_minor_units"))
    return _Money(
        amount=_exact_amount(payload.get("amount"), minor_units),
        currency=_currency(payload.get("currency")),
    )


def _intent_body(payload: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    reference = _opaque(
        payload.get("intent_reference"),
        code="intent_reference_required",
        limit=_MAX_REFERENCE,
    )
    money = _money(payload)
    body: dict[str, object] = {
        "reference": reference,
        "amount": money.amount,
        "currency": money.currency,
    }
    return_url = _optional_opaque(payload.get("return_url"), code="return_url_invalid")
    if return_url is not None:
        body["redirect_url"] = return_url
    payer_contact = _optional_opaque(
        payload.get("payer_contact"), code="payer_contact_invalid"
    )
    if payer_contact is not None:
        # Opaque. The product supplies whatever reaches the payer; this code
        # never parses or validates it, because "what identifies a payer" is a
        # product decision the transport must not second-guess.
        body["customer"] = {"email": payer_contact}
    mandate_ref = _optional_opaque(payload.get("mandate_ref"), code="mandate_invalid")
    if mandate_ref is not None:
        body["payment_method_id"] = mandate_ref
    merchant_reference = _optional_opaque(
        payload.get("merchant_reference"), code="merchant_reference_invalid"
    )
    if merchant_reference is not None:
        body["meta"] = {"merchant_reference": merchant_reference}
    return CHARGES_PATH, body


def _refund_body(payload: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    charge_id = _opaque(
        payload.get("provider_transaction_id"),
        code="provider_transaction_id_required",
        limit=_MAX_REFERENCE,
    )
    body: dict[str, object] = {}
    # A refund with no amount is a FULL refund at the provider. Absence is a
    # MEANING, so a partial amount is accepted only as a complete money triple —
    # never an amount with an assumed currency.
    if payload.get("amount") is not None:
        money = _money(payload)
        body["amount"] = money.amount
        body["currency"] = money.currency
    elif payload.get("currency") is not None:
        raise CommandContractError("amount_not_exact")
    note = _optional_opaque(payload.get("note"), code="note_invalid")
    if note is not None:
        body["comments"] = note
    return REFUNDS_PATH.format(charge_id=quote(charge_id, safe="")), body


# ── outcome classification ───────────────────────────────────────────────────

#: Provider status tokens meaning the provider REFUSED the command. Compared
#: case-folded, and carried no further: the token itself stays evidence for the
#: product, and the connector maps it to one engine outcome and nothing else.
_DECLINED_STATUSES: Final = frozenset({"failed", "declined", "cancelled", "canceled"})
#: Tokens meaning the provider ACCEPTED the command and may still be working.
#: That is a successful submission, not a settlement — settlement arrives later
#: as a `payments.settlement.observation.v1` fact through ingress or polling.
_ACCEPTED_STATUSES: Final = frozenset(
    {"succeeded", "successful", "pending", "processing", "new", "created"}
)


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    return int(value) if value is not None and value.isdigit() else None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _provider_reference(data: Mapping[str, object]) -> str | None:
    """The provider's own identity for what just happened.

    Provider-minted first: the charge/refund ``id`` is what a later status read
    and a later webhook both key on. ``reference``/``tx_ref`` is the product's
    own string echoed back and is only a fallback, because a value we sent is
    weaker evidence than one the provider assigned.
    """
    for key in ("id", "charge_id", "refund_id", "reference", "tx_ref"):
        candidate = _text(data.get(key))
        if candidate is not None and len(candidate) <= 500:
            return candidate
    return None


def _payload_data(response: httpx.Response) -> Mapping[str, object] | None:
    try:
        body = json.loads(response.content, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(body, Mapping):
        return None
    data = body.get("data")
    return data if isinstance(data, Mapping) else body


def _reference_or_none(response: httpx.Response) -> str | None:
    data = _payload_data(response)
    return None if data is None else _provider_reference(data)


def classify(response: httpx.Response) -> Outcome:
    """One HTTP response to one engine outcome. Nothing else reads the status."""
    status = response.status_code
    if status == 429:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_rate_limited",
            retry_after_seconds=_retry_after(response),
            provider_status_code=status,
        )
    if status >= 500:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_retryable_response",
            provider_status_code=status,
        )
    if status == 409:
        # The reference already exists at the provider, which means a previous
        # attempt LANDED. Neither retryable (it can never succeed) nor terminal
        # (a charge exists, and the product has to be told about it).
        return Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="provider_duplicate_reference",
            provider_reference=_reference_or_none(response),
            provider_status_code=status,
        )
    if status in {401, 403}:
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="authentication_rejected",
            provider_status_code=status,
        )
    if status == 404:
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_resource_unknown",
            provider_status_code=status,
        )
    if status < 200 or status >= 300:
        # A 4xx refusal is a DECISION. Repeating it byte for byte cannot change
        # the answer, so it must never come back as retryable.
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_declined",
            provider_reference=_reference_or_none(response),
            provider_status_code=status,
        )
    data = _payload_data(response)
    if data is None:
        return Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="provider_response_unreadable",
            provider_status_code=status,
        )
    reference = _provider_reference(data)
    provider_status = _text(data.get("status"))
    folded = provider_status.casefold() if provider_status is not None else None
    if folded in _DECLINED_STATUSES:
        # A 2xx carrying a declined status is still a decline, and still not
        # retryable. The reference is retained: a declined charge is a real
        # object at the provider that reconciliation must be able to find.
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_declined",
            provider_reference=reference,
            provider_status_code=status,
        )
    if reference is None:
        # Accepted, with nothing to correlate it by. The effect may have landed
        # and there is no way to ask about it — exactly the ambiguity that must
        # not be filed as either a success or a failure.
        return Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="provider_receipt_missing",
            provider_status_code=status,
        )
    if folded is not None and folded not in _ACCEPTED_STATUSES:
        # An unknown status token on an otherwise good response. The connector
        # refuses to guess which side of the line a token it has never seen
        # falls on — guessing "success" here is how a pending charge gets booked.
        return Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="provider_status_unrecognized",
            provider_reference=reference,
            provider_status_code=status,
        )
    return Outcome(
        status=OutcomeStatus.SUCCEEDED,
        provider_reference=reference,
        provider_status_code=status,
    )


def classify_request_error(exc: httpx.RequestError) -> Outcome:
    """A transport failure to an engine outcome — the split that matters most.

    ``ConnectTimeout``/``ConnectError`` happen BEFORE any request byte is
    written, so the command provably did not reach the provider and retrying is
    safe. Everything else — a read timeout above all — happened with bytes
    already on the wire: the charge may exist. That is ambiguous, never
    retryable.
    """
    if isinstance(exc, httpx.ConnectTimeout | httpx.ConnectError):
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_connect_failed",
        )
    return Outcome(
        status=OutcomeStatus.RECONCILIATION_REQUIRED,
        error_code="provider_outcome_ambiguous",
    )


# ── the handler ──────────────────────────────────────────────────────────────


def _host(config: Mapping[str, object]) -> str:
    environment = config.get("environment")
    if environment == "live":
        return LIVE_HOST
    if environment == "sandbox":
        return SANDBOX_HOST
    raise CommandContractError("environment_required")


def _timeout(config: Mapping[str, object], default: float) -> float:
    """Whole seconds only, so no configuration value reaches a binary float."""
    value = config.get("timeout_seconds")
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandContractError("timeout_invalid")
    if not _MIN_TIMEOUT <= value <= _MAX_TIMEOUT:
        raise CommandContractError("timeout_invalid")
    return value


def _credentials(secrets: Mapping[str, object]) -> tuple[str, str]:
    client_id = secrets.get("api_client_id")
    client_secret = secrets.get("api_client_secret")
    if not isinstance(client_id, str) or not client_id:
        raise CommandContractError("required_material_unavailable")
    if not isinstance(client_secret, str) or not client_secret:
        raise CommandContractError("required_material_unavailable")
    return client_id, client_secret


def outbound_connection_fault(
    config: Mapping[str, object], secrets: Mapping[str, object]
) -> str | None:
    """The machine code for why this binding could not deliver, or ``None``.

    Static only: it reads the declared configuration and the material that was
    materialized for the check. It performs NO provider call, so validating a
    connection cannot itself initialize a payment.
    """
    try:
        _host(config)
        _timeout(config, 0)
        _credentials(secrets)
    except CommandContractError as exc:
        return exc.code
    return None


class _TokenRejected(Exception):
    """The identity provider refused the client credentials."""


class _TokenUnreadable(Exception):
    """The identity provider answered something that is not a bearer token."""


def _token(client: httpx.Client, client_id: str, client_secret: str) -> str:
    response = client.post(
        f"https://{IDENTITY_HOST}{TOKEN_PATH}",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    if response.status_code >= 400:
        raise _TokenRejected
    try:
        body = response.json()
    except ValueError:
        raise _TokenUnreadable from None
    token = body.get("access_token") if isinstance(body, Mapping) else None
    if not isinstance(token, str) or not token:
        raise _TokenUnreadable
    return token


@dataclass(frozen=True, slots=True)
class FlutterwaveDeliveryHandler:
    """Provider I/O only. The engine owns claims, retries and persistence.

    Frozen, and holding no ledger, cursor, counter or session — a connector that
    could remember an attempt would be a second delivery ledger, which ADR-0024
    § 7 forbids and hard rule 21 assigns to the kernel.
    """

    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0

    def __call__(self, request: DispatchRequest) -> Outcome:
        try:
            if request.capability_id == INTENT_CAPABILITY_ID:
                path, body = _intent_body(request.payload)
            elif request.capability_id == REFUND_CAPABILITY_ID:
                path, body = _refund_body(request.payload)
            else:
                raise CommandContractError("capability_unsupported")
            host = _host(request.config)
            timeout = _timeout(request.config, self.timeout_seconds)
            client_id, client_secret = _credentials(request.secrets)
            content = exact_json(body)
        except CommandContractError as exc:
            # Nothing was sent. A command the connector cannot put on a wire is
            # terminal: retrying an unencodable payload produces the same bytes.
            return Outcome(status=OutcomeStatus.TERMINAL, error_code=exc.code)

        try:
            with httpx.Client(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                try:
                    token = _token(client, client_id, client_secret)
                except _TokenRejected:
                    return Outcome(
                        status=OutcomeStatus.TERMINAL,
                        error_code="authentication_rejected",
                    )
                except _TokenUnreadable:
                    # Nothing was charged: the command never left. Retryable
                    # because the identity provider misbehaved, not the payment.
                    return Outcome(
                        status=OutcomeStatus.RETRYABLE,
                        error_code="authentication_unreadable",
                    )
                response = client.post(
                    f"https://{host}{path}",
                    content=content,
                    headers={
                        "authorization": f"Bearer {token}",
                        "content-type": "application/json",
                        "accept": "application/json",
                        # v4's own idempotency guard, carrying the engine's key
                        # verbatim. The connector mints nothing here.
                        IDEMPOTENCY_HEADER: request.idempotency_key,
                    },
                )
        except httpx.RequestError as exc:
            return classify_request_error(exc)
        return classify(response)
