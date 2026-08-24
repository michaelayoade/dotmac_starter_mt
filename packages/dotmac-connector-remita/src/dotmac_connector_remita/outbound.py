"""Remita RRR issuance — the provider command, carried and never owned.

One DELIVERY capability, ``payments.reference.issuance.v1``: ask Remita to mint
one Remita Retrieval Reference for one product-owned order, and report exactly
what Remita said.

Ported from ``dotmac_erp:app/services/remita/client.py`` (the only file in the
fleet that speaks Remita's wire), whose ``generate_rrr`` and ``_generate_hash``
are the behaviour this module reproduces. ``rrr_service.py`` is deliberately NOT
ported: it is DB-bound orchestration — biller/service selection, RRR lifecycle,
source-entity linkage and accounting consequence — every bit of which stays in
ERP under ADR-0024.

## What is deliberately absent

**Payment status.** It stays a POLL concern in ``plugin.py``. A status READ is a
fact arriving; turning it into an outbound COMMAND would file it in a queue that
retries, and Remita has no push channel at all — there is nothing to convert.

**Everything ``source_handler.py`` does.** What a paid RRR MEANS to a supplier
payment, a wage run or an expense claim is a product decision this transport
must never see.

## Authentication — SHA-512, and the ordering is load-bearing

``client.py:106-118``. The hash is a hex SHA-512 over UTF-8 bytes of the fields
concatenated with NO separator, and issuance and status use DIFFERENT orders::

    issuance : sha512(merchant_id + service_type_id + order_id + amount + api_key)
    status   : sha512(rrr + api_key + merchant_id)

They are not unified here, and :func:`issuance_hash` is deliberately a separate
function from the status hash in ``plugin.py`` for that reason — a single
"remita hash" helper is how the two orders get quietly merged into one wrong
one. ``tests/unit/test_remita_connector.py`` pins both orders against fixed
fixture values.

The amount that enters the hash is the SAME STRING that enters the payload, and
that is the subtle part: ``client.py:222`` formats it once
(``amount_str = f"{amount:.2f}"``) and feeds that one string to both. A
connector that hashed ``str(Decimal("20000"))`` and sent ``"20000.00"`` would
produce a hash Remita rejects, for a payload that looks correct.

The header is ``Authorization: remitaConsumerKey=<merchant>,remitaConsumerToken=
<hash>`` — comma-separated, no space, no scheme prefix (``client.py:119-122``).

## Idempotency — Remita offers no header, only ``orderId``

Confirmed by inventory across both product repositories: Remita accepts no
``Idempotency-Key`` or ``X-Idempotency-Key`` of any kind. Its only natural key is
``orderId``, and a repeat of one is answered with status code ``021``.

So the connector carries the PRODUCT's ``order_id`` verbatim and mints nothing.
That is the port delta: ``rrr_service.py:136`` builds
a fresh ``<org>-<uuid4>`` per call, so every attempt is a new order and
a retry issues a SECOND reference for the same obligation. A stable,
product-owned ``order_id`` makes the provider itself reject the duplicate, and
that rejection is classified ambiguous — a reference for this order already
exists and the status capability can find it — never terminal, never retried.

The engine's ``DispatchRequest.idempotency_key`` remains the at-most-once
record in ``dotmac_kernel.idempotency`` through
``dotmac_integration.idempotency`` (ADR-0014, hard rule 21). It is not sent,
because there is nowhere to send it.

## Outcome classification — this is money

============================= =========================== ====================
provider situation            OutcomeStatus               error_code
============================= =========================== ====================
``statuscode`` ``025`` + RRR  ``SUCCEEDED``               —
``025`` with no RRR           ``RECONCILIATION_REQUIRED`` receipt missing
``021`` duplicate order       ``RECONCILIATION_REQUIRED`` duplicate order
any other ``statuscode``      ``TERMINAL``                declined
unreadable body               ``RECONCILIATION_REQUIRED`` unreadable
HTTP 401/403                  ``TERMINAL``                auth rejected
other HTTP 4xx                ``TERMINAL``                rejected request
HTTP 429 / 5xx                ``RETRYABLE``               rate limited / retryable
connect failure               ``RETRYABLE``               connect failed
any later transport failure   ``RECONCILIATION_REQUIRED`` ambiguous
============================= =========================== ====================

A decline — ``027`` invalid service type, say — is a DECISION, and repeating the
identical request cannot change it, so it is never retryable. A timeout after
the bytes went out is ambiguous: Remita may have minted a reference the product
has never seen, and both retrying (a second reference) and dead-lettering
(a reference nobody knows about) are wrong.

ERP classifies ``025`` and raises a generic error for everything else
(``client.py:269-290``), losing the difference between a duplicate order, an
invalid service type and an unreachable host. Restoring that difference is the
second port delta.

## Money

Exact ``Decimal``, quantized to the exponent the PRODUCT declares, formatted
once, and used for both the payload and the hash. There is no binary
floating-point value anywhere on this path.

Remita's ``paymentinit`` payload carries NO currency field — the reference is
minted in the merchant's settlement currency. Rather than hardcode naira (which
``client.py`` effectively does by omission), the installation DECLARES its
``settlement_currency`` with no default, the command must state the same
currency explicitly, and a mismatch is refused. A silent currency assumption on
a payment wire is how an amount in one currency is charged in another.

The minor-unit exponent is supplied by the product, from
``dotmac_kernel.money.Currency.minor_units``: a connector may import nothing but
the SPI among Dotmac packages
(``tests/architecture/test_remita_connector_boundary.py``
``::test_connector_imports_only_the_spi_among_dotmac_packages``), and a private
ISO-4217 table inside a transport would be a second, drifting authority on what
a currency IS. With NGN's exponent of 2 the wire string is ``"20000.00"``, byte
for byte what ``client.py:222`` produces.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

import httpx
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import DispatchRequest

ISSUANCE_CAPABILITY_ID: Final = "payments.reference.issuance.v1"

#: `dotmac_erp:app/services/remita/client.py:73`.
ISSUANCE_PATH: Final = (
    "/remita/exapp/api/v1/send/api/echannelsvc/merchant/api/paymentinit"
)

#: `client.py:269-278`. The ONLY code that means a reference was minted.
STATUS_REFERENCE_GENERATED: Final = "025"
#: `client.py:271`. The order was already used, so an earlier attempt LANDED.
STATUS_DUPLICATE_ORDER: Final = "021"

_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9_-]{1,160}")
_ORDER_ID: Final = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
_CURRENCY: Final = re.compile(r"[A-Z]{3}")
_JSONP: Final = re.compile(r"jsonp\s*\((.*)\)", re.DOTALL)

#: `client.py:81` — a single scalar, 30 seconds, no connect/read split.
DEFAULT_TIMEOUT_SECONDS: Final = 30.0
_MIN_TIMEOUT: Final = 1
_MAX_TIMEOUT: Final = 120
#: No ISO-4217 currency has more than four fraction digits.
_MAX_MINOR_UNITS: Final = 4
_MAX_OPAQUE: Final = 500

ISSUANCE_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["merchant_id", "environment", "settlement_currency"],
    "properties": {
        "merchant_id": {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,160}$"},
        "environment": {"type": "string", "enum": ["demo", "live"]},
        # No default, deliberately. Remita's payload carries no currency field,
        # so the only protection against minting a reference in the wrong
        # currency is an installation that states which one it settles in.
        "settlement_currency": {"type": "string", "pattern": r"^[A-Z]{3}$"},
        "timeout_seconds": {
            "type": "integer",
            "minimum": _MIN_TIMEOUT,
            "maximum": _MAX_TIMEOUT,
        },
    },
}


class CommandContractError(ValueError):
    """The command handed over is not one this connector can put on a wire.

    Carries a machine ``code`` only. The command holds an amount, a payer name
    and a payer contact, and ``Outcome.error_detail`` is PERSISTED — so nothing
    read out of the payload may travel inside the message.
    """

    def __init__(self, code: str) -> None:
        self.code = code if code.isidentifier() else "command_invalid"
        super().__init__(self.code)


# ── authentication ───────────────────────────────────────────────────────────


def issuance_hash(
    *,
    merchant_id: str,
    service_type_id: str,
    order_id: str,
    amount: str,
    api_key: str,
) -> str:
    """``sha512(merchant_id + service_type_id + order_id + amount + api_key)``.

    Keyword-only, and every parameter named, because this function's ENTIRE
    contract is the order of five concatenated strings: a positional signature
    would let two of them swap places in a call and still typecheck, and the
    provider's only feedback for that mistake is a rejected request.

    ``amount`` is a string rather than a ``Decimal`` on purpose. The value that
    is hashed must be byte-identical to the value that is sent, so the caller
    formats it once and passes the same object to both.
    """
    concatenated = f"{merchant_id}{service_type_id}{order_id}{amount}{api_key}"
    return hashlib.sha512(concatenated.encode()).hexdigest()


def authorization_header(merchant_id: str, api_hash: str) -> str:
    """``client.py:119-122``. Comma-separated, no space, no scheme prefix."""
    return f"remitaConsumerKey={merchant_id},remitaConsumerToken={api_hash}"


# ── configuration and command ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Issuance:
    merchant_id: str
    host: str
    settlement_currency: str
    timeout_seconds: float


def _opaque(value: object, *, code: str, limit: int = _MAX_OPAQUE) -> str:
    if not isinstance(value, str):
        raise CommandContractError(code)
    text = value.strip()
    if not text or len(text) > limit:
        raise CommandContractError(code)
    return text


def _optional_opaque(value: object, *, code: str) -> str | None:
    return None if value is None else _opaque(value, code=code)


def _matching(value: str, pattern: re.Pattern[str], *, code: str) -> str:
    if pattern.fullmatch(value) is None:
        raise CommandContractError(code)
    return value


def _timeout(value: object) -> float:
    """Whole seconds only, so no configuration value reaches a binary float."""
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandContractError("timeout_invalid")
    if not _MIN_TIMEOUT <= value <= _MAX_TIMEOUT:
        raise CommandContractError("timeout_invalid")
    return value


def issuance_config(value: Mapping[str, object], demo_host: str, live_host: str):
    merchant_id = _matching(
        _opaque(value.get("merchant_id"), code="merchant_id_required", limit=160),
        _IDENTIFIER,
        code="merchant_id_required",
    )
    environment = value.get("environment")
    if environment not in {"demo", "live"}:
        raise CommandContractError("environment_required")
    settlement_currency = _matching(
        _opaque(
            value.get("settlement_currency"),
            code="settlement_currency_required",
            limit=3,
        ).upper(),
        _CURRENCY,
        code="settlement_currency_required",
    )
    return _Issuance(
        merchant_id=merchant_id,
        host=live_host if environment == "live" else demo_host,
        settlement_currency=settlement_currency,
        timeout_seconds=_timeout(value.get("timeout_seconds")),
    )


def _minor_units(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CommandContractError("currency_minor_units_required")
    if not 0 <= value <= _MAX_MINOR_UNITS:
        raise CommandContractError("currency_minor_units_required")
    return value


def exact_amount(value: object, minor_units: int) -> str:
    """The wire string for an amount: exact, positive, already fitting.

    A binary floating-point value is refused by TYPE before any parsing —
    accepting one and rounding it would mean this transport decided what the
    amount was. A value carrying more precision than the currency has is refused
    for the same reason rather than quietly rounded.
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
        raise CommandContractError("amount_precision_invalid")
    # ONE string, used for both the payload and the hash. `format(..., "f")`
    # writes the digits held: never exponent notation, never an approximation.
    return format(amount.quantize(quantum), "f")


def issuance_payload(
    payload: Mapping[str, object], config: _Issuance
) -> tuple[dict[str, str], str]:
    """The seven-field ``paymentinit`` body and the amount string it carries.

    Field names, types and the empty-string degradation of ``payerPhone`` are
    ``client.py:234-242`` verbatim: all seven are strings and all seven are
    always present, because Remita's contract has no notion of an absent field.
    """
    currency = _matching(
        _opaque(payload.get("currency"), code="currency_required", limit=3).upper(),
        _CURRENCY,
        code="currency_required",
    )
    if currency != config.settlement_currency:
        # Remita's payload has no currency field. Sending this amount would mint
        # a reference denominated in the merchant's settlement currency while
        # the product believes it asked for another one.
        raise CommandContractError("currency_unsupported")
    amount = exact_amount(
        payload.get("amount"), _minor_units(payload.get("currency_minor_units"))
    )
    body = {
        "serviceTypeId": _matching(
            _opaque(
                payload.get("service_type_id"),
                code="service_type_id_required",
                limit=160,
            ),
            _IDENTIFIER,
            code="service_type_id_required",
        ),
        "amount": amount,
        "orderId": _matching(
            _opaque(payload.get("order_id"), code="order_id_required", limit=100),
            _ORDER_ID,
            code="order_id_required",
        ),
        "payerName": _opaque(payload.get("payer_name"), code="payer_name_required"),
        "payerEmail": _opaque(payload.get("payer_email"), code="payer_email_required"),
        # `client.py:240` — degrades to "", never omitted.
        "payerPhone": _optional_opaque(
            payload.get("payer_phone"), code="payer_phone_invalid"
        )
        or "",
        "description": _optional_opaque(
            payload.get("description"), code="description_invalid"
        )
        or "",
    }
    return body, amount


# ── response classification ──────────────────────────────────────────────────


def parse_provider_body(text: str) -> Mapping[str, object] | None:
    """``client.py:170-192``: a JSONP wrapper, or bare JSON. ``None`` if neither.

    Returns rather than raises, because an unreadable answer to a command that
    was already SENT is an ambiguity to classify, not an error to propagate.
    """
    stripped = text.strip()
    match = _JSONP.fullmatch(stripped)
    encoded = match.group(1) if match else stripped
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def classify(response: httpx.Response) -> Outcome:
    """One HTTP response to one engine outcome. Nothing else reads the status."""
    status = response.status_code
    if status == 429:
        retry_after = response.headers.get("retry-after")
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_rate_limited",
            retry_after_seconds=(
                int(retry_after)
                if retry_after is not None and retry_after.isdigit()
                else None
            ),
            provider_status_code=status,
        )
    if status >= 500:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_retryable_response",
            provider_status_code=status,
        )
    if status in {401, 403}:
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="authentication_rejected",
            provider_status_code=status,
        )
    if status < 200 or status >= 300:
        # A 4xx refusal is a DECISION; repeating the identical request cannot
        # change the answer, so it must never come back as retryable.
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_rejected_request",
            provider_status_code=status,
        )
    body = parse_provider_body(response.text)
    if body is None:
        return Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="provider_response_unreadable",
            provider_status_code=status,
        )
    return classify_body(body, status)


def classify_body(body: Mapping[str, object], status: int | None = None) -> Outcome:
    """The provider's own ``statuscode``, mapped once.

    Split out from :func:`classify` so the provider-code table can be driven
    directly, without a fabricated HTTP response standing between the test and
    the branch it is checking.
    """
    code = _text(body.get("statuscode"))
    reference = _text(body.get("RRR"))
    if code == STATUS_REFERENCE_GENERATED:
        if reference is None or len(reference) > 500:
            # `client.py:275` reads `data["RRR"]` unguarded and raises KeyError
            # here. A reference may well have been minted; the connector says so
            # rather than crashing on the far side of a provider effect.
            return Outcome(
                status=OutcomeStatus.RECONCILIATION_REQUIRED,
                error_code="provider_receipt_missing",
                provider_status_code=status,
            )
        return Outcome(
            status=OutcomeStatus.SUCCEEDED,
            provider_reference=reference,
            provider_status_code=status,
        )
    if code == STATUS_DUPLICATE_ORDER:
        # The order already has a reference, which means an earlier attempt
        # LANDED. Neither retryable (it can never succeed) nor terminal (a
        # reference exists, and the product has to be told about it).
        return Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="provider_duplicate_order",
            provider_reference=reference,
            provider_status_code=status,
        )
    if code is None:
        return Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="provider_status_missing",
            provider_status_code=status,
        )
    # Every other provider code — `027` invalid service type among them — is a
    # refusal of THIS request. ERP collapses all of these into one generic
    # error; keeping them distinct from a duplicate order and from a transport
    # failure is the point of this branch.
    return Outcome(
        status=OutcomeStatus.TERMINAL,
        error_code="provider_declined",
        provider_reference=reference,
        provider_status_code=status,
    )


def classify_request_error(exc: httpx.RequestError) -> Outcome:
    """A transport failure to an engine outcome — the split that matters most.

    ``ConnectTimeout``/``ConnectError`` happen BEFORE any request byte is
    written, so the command provably did not reach Remita and retrying is safe.
    Everything else — a read timeout above all — happened with bytes already on
    the wire: a reference may exist that the product has never seen. That is
    ambiguous, never retryable.
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


def _api_key(secrets: Mapping[str, object]) -> str:
    material = secrets.get("api_key")
    if not isinstance(material, str) or not material:
        raise CommandContractError("required_material_unavailable")
    return material


@dataclass(frozen=True, slots=True)
class RemitaIssuanceHandler:
    """Provider I/O only. The engine owns claims, retries and persistence.

    Frozen, and holding no cursor, counter, record or session — a connector that
    could remember an attempt would be a second delivery record, which ADR-0024
    § 7 forbids and hard rule 21 assigns to the kernel.
    """

    demo_host: str
    live_host: str
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __call__(self, request: DispatchRequest) -> Outcome:
        try:
            if request.capability_id != ISSUANCE_CAPABILITY_ID:
                raise CommandContractError("capability_unsupported")
            config = issuance_config(request.config, self.demo_host, self.live_host)
            body, amount = issuance_payload(request.payload, config)
            api_key = _api_key(request.secrets)
            api_hash = issuance_hash(
                merchant_id=config.merchant_id,
                service_type_id=body["serviceTypeId"],
                order_id=body["orderId"],
                # The SAME string that goes on the wire. Formatting it twice is
                # how a correct-looking payload gets a hash Remita rejects.
                amount=amount,
                api_key=api_key,
            )
        except CommandContractError as exc:
            # Nothing was sent. A command that cannot be put on a wire is
            # terminal: retrying it produces the identical refusal.
            return Outcome(status=OutcomeStatus.TERMINAL, error_code=exc.code)

        try:
            with httpx.Client(
                base_url=f"https://{config.host}",
                timeout=config.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    ISSUANCE_PATH,
                    json=body,
                    headers={
                        "accept": "application/json",
                        "content-type": "application/json",
                        "authorization": authorization_header(
                            config.merchant_id, api_hash
                        ),
                    },
                )
        except httpx.RequestError as exc:
            return classify_request_error(exc)
        return classify(response)
