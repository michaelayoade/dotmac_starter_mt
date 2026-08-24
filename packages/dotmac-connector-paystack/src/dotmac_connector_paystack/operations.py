"""Outbound Paystack operations — provider I/O, and nothing that decides money.

Nine provider-neutral operations, each one command the connector is TOLD to
perform. It authenticates, translates the wire format, performs the call and
classifies what came back. It never reads a product database, never decides
whether a refund is warranted, which invoice a payment covers, whether a
customer may be charged, or when to try again — those are the product's, and
retry/checkpoint/dead-letter is the engine's.

## Five outcomes, because four of them are not enough

============= ==================================================================
outcome       what it means
============= ==================================================================
``SUCCEEDED``  the provider performed it and said so conclusively
``DECLINED``   the provider performed it and REFUSED — an answer, not a fault
``RETRYABLE``  the call demonstrably did not reach the provider's ledger
``TERMINAL``   the provider rejected the request before acting on it
``AMBIGUOUS``  it MAY have landed. Nobody may retry it; somebody must look
============= ==================================================================

A DECLINE IS NOT A RETRYABLE ERROR. It is the provider's answer, and repeating
the question does not change it — worse, `dotmac_sub`'s autopay records why
repeating it is actively harmful: *a decline burns the reference at Paystack*,
so the identical retry is refused while a fresh attempt (a product decision,
tracked by the mandate's failure count) is not. So `DECLINED` maps to the
engine's `TERMINAL` and never to `RETRYABLE`; the mapping is a total table with
an import-time totality guard, not a chain of ``if``\\ s a later member can slip
past.

`AMBIGUOUS` is the one people leave out, and leaving it out is the
duplicate-charge bug. A read timeout on a charge or a transfer means the bytes
were sent and the answer was lost — the money may already have moved. Retrying
captures the card twice; dead-lettering hides a live transaction. It maps to
`RECONCILIATION_REQUIRED`, which stops the engine attempting anything and hands
the question to a reconciler that resolves it against provider state
(`GET /transaction/verify/{reference}`, `GET /transfer/verify/{reference}`,
`GET /refund?transaction=...`). That is exactly what `dotmac_erp`'s
`payment_service._recover_transfer_initiation` does after a timeout or a
duplicate-reference refusal, and what `dotmac_sub`'s `autopay._recover_charge`
does after any charge exception.

## Paystack has no idempotency header. It has something better and narrower

Checked against the provider's own contract and against both production
integrations: **Paystack publishes no ``Idempotency-Key`` header**, and neither
ERP's 1,164-line client nor Sub's connector runtime ever sends one. What it
publishes instead is a client-supplied unique ``reference`` on
``/transaction/initialize``, ``/transaction/charge_authorization`` and
``/transfer``. Reusing one is REFUSED server-side — "Duplicate Transaction
Reference" on a transaction, ``duplicate_transfer_reference`` on a transfer —
so the reference is a real, provider-enforced, at-most-once key for the three
operations that move money into the merchant's balance or out of it.

This module derives that reference from the ENGINE's idempotency key, which is
stable across attempts of one delivery (`dotmac_integration.dispatch` takes it
from the delivery row, never from the attempt number). Two independent
protections therefore stack, and neither relies on the other:

1. the classification above never lets an ambiguous send be retried at all;
2. if something retries anyway, Paystack refuses the duplicate reference.

The reference is derived from the key and never read from the payload. A guard
whose value arrives in a mutable command payload is a guard a caller defeats by
accident, and the product's own identifiers travel in ``metadata`` where they
are evidence rather than a lock.

``/refund`` accepts no client reference — the one money-moving endpoint here
with no provider-side key. Sub solved that in production by writing its request
key into ``merchant_note`` and matching on it when listing refunds for a
transaction, and that is ported verbatim: every refund carries the derived
reference in ``merchant_note``, so an ambiguous refund is decidable by reading
the provider's own refund list rather than guessing.

## Money is exact, and it is never a `float`

An amount crosses this boundary as an exact decimal STRING in the currency's
major units with an explicit currency code — the same shape the ingress side
already emits, so a value can round-trip. It is parsed with `Decimal`, scaled by
Paystack's wire scale and required to land on a whole minor unit; a value finer
than the wire scale is REFUSED rather than rounded, because silently rounding
somebody's money is how a reconciliation becomes a dispute. `float` never
appears: it cannot represent 0.10, and a payment system that cannot represent a
customer's amount has already lost.

`int` is refused as an input too, deliberately. ``1000`` is unreadable — naira
or kobo? — and that ambiguity is worth an exception, not a convention.

Both directions of the conversion live here, so inbound normalization and
outbound translation cannot drift into disagreeing about what a kobo is.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Final
from urllib.parse import quote

import httpx
from dotmac_integration.retry import Outcome, OutcomeStatus

__all__ = [
    "API_HOST",
    "ENGINE_STATUS",
    "OPERATIONS",
    "PAYSTACK_WIRE_SCALE",
    "MoneyContractError",
    "OperationContractError",
    "OperationOutcome",
    "OperationResult",
    "PaystackOperations",
    "exact_amount",
    "minor_units",
    "provider_reference",
]

API_HOST: Final = "api.paystack.co"

# Paystack's wire contract multiplies the base-unit amount by 100 for every
# supported currency. This stays true for XOF, which has no ordinary fractional
# subunit — so the constant is PROVIDER PROTOCOL, never an ISO-4217 policy
# decision and never a product currency default. One definition, consumed by
# both the inbound and the outbound conversion below, so the two cannot drift.
PAYSTACK_WIRE_SCALE: Final = 2

#: The engine's own idempotency key, hashed, is what becomes a provider
#: reference. The prefix is a fixed marker so an operator reading a Paystack
#: dashboard can tell a Dotmac-issued reference from a hand-made one.
REFERENCE_PREFIX: Final = "dmi"

#: An exact amount in MAJOR units. Anchored, digits only, at most six fraction
#: digits — wide enough for any minor-unit scale, narrow enough that a
#: float's repr (``1e-05``, ``0.1000000000000000055``) cannot pass.
_EXACT_AMOUNT_RE: Final[re.Pattern[str]] = re.compile(
    r"(0|[1-9][0-9]{0,15})(\.[0-9]{1,6})?"
)
_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z]{3}")
#: What may appear in a URL path segment or a provider identifier we echo back.
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9@._+:=-]{1,120}")
_ACCOUNT_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{5,20}")
_BANK_CODE_RE: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-z-]{2,20}")
#: Paystack's own recipient types. A closed set: an unknown one is a contract
#: error here rather than a 400 discovered at the provider.
_RECIPIENT_TYPES: Final[frozenset[str]] = frozenset(
    {"nuban", "ghipss", "mobile_money", "basa"}
)

#: The longest provider identifier accepted as success EVIDENCE. Ported from
#: Sub's `payment_gateway_adapter`, which refuses an over-long external id
#: rather than storing it and discovering the truncation during a dispute.
_MAX_EVIDENCE_LENGTH: Final = 120


class OperationOutcome(str, Enum):
    """What the connector observed. Not what anyone should do about it."""

    SUCCEEDED = "succeeded"
    DECLINED = "declined"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    AMBIGUOUS = "ambiguous"


#: The TOTAL mapping onto the engine's vocabulary. A table rather than a
#: branch, because a branch is how a sixth outcome arrives with no engine
#: meaning and quietly falls through to whatever the last ``else`` said.
ENGINE_STATUS: Final[Mapping[OperationOutcome, OutcomeStatus]] = MappingProxyType(
    {
        OperationOutcome.SUCCEEDED: OutcomeStatus.SUCCEEDED,
        # A decline is an ANSWER. Repeating the question cannot change it, and
        # at Paystack the reference is already burnt, so the retry would be
        # refused even if it were attempted.
        OperationOutcome.DECLINED: OutcomeStatus.TERMINAL,
        OperationOutcome.RETRYABLE: OutcomeStatus.RETRYABLE,
        OperationOutcome.TERMINAL: OutcomeStatus.TERMINAL,
        # Never RETRYABLE. This is the duplicate-charge line.
        OperationOutcome.AMBIGUOUS: OutcomeStatus.RECONCILIATION_REQUIRED,
    }
)


def _unmapped_outcomes(
    outcomes: Iterable[object], mapped: Iterable[object]
) -> frozenset[object]:
    """Which of `outcomes` has no engine status.

    A named function so the import-time guard below and its sensitivity proof
    run the SAME expression; a guard whose test re-implements the predicate
    proves only that two authors agreed. Both parameters are `Iterable[object]`
    because the proof has to hand it an outcome set that has GROWN a member,
    which by definition is not an :class:`OperationOutcome` — and a signature
    that could not express the broken case could not be tested against it.
    """
    return frozenset(outcomes) - frozenset(mapped)


# Checked where it cannot be skipped. A test can be deleted; an import-time
# refusal means an outcome added with no engine meaning cannot be imported.
_UNMAPPED: Final[frozenset[object]] = _unmapped_outcomes(
    OperationOutcome, ENGINE_STATUS
)
if _UNMAPPED:  # pragma: no cover - an import-time guard, proved by test
    raise RuntimeError(
        f"OperationOutcome has {sorted(str(o) for o in _UNMAPPED)} with no engine "
        "status; an unmapped money outcome has no defined consequence"
    )


class MoneyContractError(ValueError):
    """An amount cannot be represented exactly on the provider's wire."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OperationContractError(ValueError):
    """A command cannot be translated into the provider's contract.

    Raised BEFORE any request leaves, which is why it becomes `TERMINAL`: a
    request that was never sent cannot have landed, so there is nothing to
    reconcile and nothing a retry would fix.
    """

    def __init__(self, code: str) -> None:
        self.code = code if code.isidentifier() else "operation_contract_invalid"
        super().__init__(self.code)


# ── money, both directions, one owner ────────────────────────────────────────


def minor_units(amount: object, *, currency: str) -> int:
    """Exact provider minor units for an exact decimal STRING in major units.

    `currency` is present because the caller must have decided one before it can
    talk about money at all; Paystack's scale is currency-independent, so it
    does not change the arithmetic — it makes an amount with no currency
    impossible to express.
    """
    del currency
    if not isinstance(amount, str) or _EXACT_AMOUNT_RE.fullmatch(amount) is None:
        # An `int`, a `float` and a `Decimal` all land here on purpose. `1000`
        # is unreadable (naira or kobo?) and a float cannot hold 0.10.
        raise MoneyContractError("amount_not_exact")
    try:
        value = Decimal(amount)
    except InvalidOperation:  # pragma: no cover - the regex already refused it
        raise MoneyContractError("amount_not_exact") from None
    # ERP's form, which rounds half-up on an already-integral value and is
    # therefore exact; Sub's `int(Decimal(str(x)) * 100)` truncates and is the
    # half of the two implementations that was not ported.
    scaled = (value * Decimal(10) ** PAYSTACK_WIRE_SCALE).to_integral_value(
        rounding=ROUND_HALF_UP
    )
    if scaled != value * Decimal(10) ** PAYSTACK_WIRE_SCALE:
        # Finer than the provider can carry. Refused, never rounded: rounding
        # somebody's money without telling them is how a payment becomes a
        # dispute.
        raise MoneyContractError("amount_finer_than_wire_scale")
    units = int(scaled)
    if units <= 0:
        raise MoneyContractError("amount_not_positive")
    return units


def exact_amount(units: object, *, allow_zero: bool) -> str | None:
    """A provider minor-unit integer as an exact major-unit decimal string.

    The inverse of :func:`minor_units`, and the inbound side's only conversion.
    Returns `None` rather than raising: on the inbound path a malformed amount
    is transport evidence to be recorded, not an exception to be thrown at a
    provider that is waiting for an acknowledgement.
    """
    if not isinstance(units, int) or isinstance(units, bool):
        return None
    if units < 0 or (units == 0 and not allow_zero):
        return None
    return format(
        Decimal(units).scaleb(-PAYSTACK_WIRE_SCALE), f".{PAYSTACK_WIRE_SCALE}f"
    )


def _currency(value: object) -> str:
    if (
        not isinstance(value, str)
        or _CURRENCY_RE.fullmatch(value.strip().upper()) is None
    ):
        raise OperationContractError("currency_invalid")
    return value.strip().upper()


# ── the provider reference, derived from the engine's key ────────────────────


def provider_reference(idempotency_key: object) -> str:
    """The provider-side at-most-once key for one command.

    Lower-case hexadecimal with a fixed prefix, so ONE derivation satisfies both
    of Paystack's charset rules at once — transactions allow ``- . =`` and
    alphanumerics, transfers allow ``- _`` and alphanumerics and want lower
    case. Deterministic in the engine's key, so every attempt of one delivery
    presents the identical reference and the provider refuses the second.
    """
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        # Refused rather than substituted. A generated key would be unique per
        # attempt, which is precisely the duplicate-charge machine this exists
        # to prevent — an absent key must stop the send, not weaken it.
        raise OperationContractError("idempotency_key_required")
    digest = hashlib.sha256(idempotency_key.strip().encode()).hexdigest()
    return f"{REFERENCE_PREFIX}{digest}"


# ── the operation table ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Operation:
    """One provider-neutral command and how its silence must be read."""

    name: str
    method: str
    #: What an INCONCLUSIVE send means for this operation: the answer never
    #: arrived, so the question is whether the effect could have landed.
    #: `AMBIGUOUS` wherever a duplicate would cost real money or the provider's
    #: duplicate semantics are undocumented; `RETRYABLE` only where the
    #: operation is a pure read or the provider documents that repeating it
    #: returns the existing record.
    indeterminate: OperationOutcome
    #: Whether the command carries the derived reference in the provider's own
    #: ``reference`` field, which is what makes a duplicate refusable.
    carries_reference: bool = False
    #: Whether the command needs the engine's key at all (a refund does, to
    #: stamp ``merchant_note``, even though it has no ``reference`` field).
    needs_key: bool = False


OPERATIONS: Final[Mapping[str, _Operation]] = MappingProxyType(
    {
        operation.name: operation
        for operation in (
            # Money in. A duplicate is a second charge on a customer's card.
            _Operation(
                "initialize_payment",
                "POST",
                OperationOutcome.AMBIGUOUS,
                carries_reference=True,
                needs_key=True,
            ),
            _Operation(
                "charge_authorization",
                "POST",
                OperationOutcome.AMBIGUOUS,
                carries_reference=True,
                needs_key=True,
            ),
            # Money back out. No provider-side reference field exists, so the
            # derived key is stamped into `merchant_note` instead and an
            # ambiguous refund is resolved by reading the provider's refund
            # list for that transaction (Sub's production mechanism).
            _Operation("refund", "POST", OperationOutcome.AMBIGUOUS, needs_key=True),
            # A pure read. Repeating it changes nothing at the provider.
            _Operation("resolve_bank_account", "GET", OperationOutcome.RETRYABLE),
            # Paystack documents that a duplicate account number RETRIEVES the
            # existing recipient rather than creating a second one, so this is
            # safe to repeat. That is a documented provider guarantee, not an
            # assumption about POST.
            _Operation("create_transfer_recipient", "POST", OperationOutcome.RETRYABLE),
            # Money out. The one operation where a duplicate pays a stranger
            # twice, and the reason `verify_transfer(reference)` exists.
            _Operation(
                "initiate_transfer",
                "POST",
                OperationOutcome.AMBIGUOUS,
                carries_reference=True,
                needs_key=True,
            ),
            # Paystack does NOT document what a repeated create does with an
            # email it already holds. An undocumented duplicate semantic is
            # ambiguous by the rule this module is built on, even though no
            # money moves — a reconciler reads `GET /customer/{email}` and the
            # question is answered in one call.
            _Operation("create_customer", "POST", OperationOutcome.AMBIGUOUS),
            # Idempotent by construction: the same fields written to the same
            # customer code produce the same customer.
            _Operation("update_customer", "PUT", OperationOutcome.RETRYABLE),
            _Operation("read_customer", "GET", OperationOutcome.RETRYABLE),
        )
    }
)


# ── the provider's own status vocabularies, closed on purpose ────────────────
#
# Ported from Sub's `PaymentGatewayProviderStatus` allowlist. Closed, and
# checked in this order: SUCCEEDED, then DECLINED, then in-flight, then
# anything else. A token outside every set is AMBIGUOUS rather than a guess,
# because "we did not recognise the provider's answer" and "the provider said
# it worked" must never be the same branch. Sub proves the point in the
# opposite direction: a Paystack transaction reporting ``successful`` — a word
# a SIBLING provider uses for success — is deliberately not a success there
# either. This connector names no sibling provider at all; the sets below are
# Paystack's own vocabulary and nobody else's.

_CHARGE_SUCCEEDED: Final[frozenset[str]] = frozenset({"success"})
_CHARGE_DECLINED: Final[frozenset[str]] = frozenset({"failed", "reversed", "abandoned"})
_CHARGE_IN_FLIGHT: Final[frozenset[str]] = frozenset(
    {
        "ongoing",
        "open_url",
        "otp",
        "pay_offline",
        "pending",
        "processing",
        "queued",
        "send_birthday",
        "send_otp",
        "send_phone",
        "send_pin",
    }
)

_TRANSFER_SUCCEEDED: Final[frozenset[str]] = frozenset({"success"})
_TRANSFER_DECLINED: Final[frozenset[str]] = frozenset(
    {"abandoned", "failed", "reversed"}
)
_TRANSFER_IN_FLIGHT: Final[frozenset[str]] = frozenset(
    {"otp", "pending", "processing", "queued", "received"}
)

#: A refund COMMAND has landed the moment the provider holds a refund record;
#: `pending`/`processing`/`processed` are that record's own lifecycle and
#: travel back as evidence rather than as a verdict.
_REFUND_DECLINED: Final[frozenset[str]] = frozenset({"failed"})

#: Provider messages that mean "this exact reference was already used". A used
#: reference is PROOF the earlier attempt reached the provider, so it is
#: ambiguous — never a fresh failure, and never grounds to send again.
#: The two Paystack phrasings are the ones ERP's
#: `_recover_transfer_initiation` matches in production.
_REFERENCE_USED_MARKERS: Final[tuple[str, ...]] = (
    "duplicate transaction reference",
    "duplicate_transfer_reference",
    "reference already exists",
    "transaction reference has been used",
)


# ── results ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OperationResult:
    """What one attempt observed, plus the evidence needed to resolve it.

    `provider_reference` is present on EVERY outcome that had one, including
    the ones where no answer arrived — that is the whole point. A reference the
    connector definitely sent is exactly the handle a reconciler needs to ask
    the provider what happened, and losing it at the moment things go wrong is
    losing it precisely when it matters.
    """

    operation: str
    outcome: OperationOutcome
    error_code: str | None = None
    http_status: int | None = None
    retry_after_seconds: int | None = None
    provider_reference: str | None = None
    provider_transaction_id: str | None = None
    #: The provider's own status token, verbatim. A connector reports what the
    #: provider said; it does not translate it into a billing lifecycle.
    provider_status: str | None = None
    #: Provider-neutral reply fields an operation exists to obtain — a resolved
    #: account name, a recipient code, a checkout URL. Bounded strings only.
    reply: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reply", MappingProxyType(dict(self.reply)))

    @property
    def is_success(self) -> bool:
        return self.outcome is OperationOutcome.SUCCEEDED

    def as_outcome(self) -> Outcome:
        """Hand the engine its own vocabulary, and nothing it must not store.

        `error_detail` is deliberately never set. The engine PERSISTS it, and
        the only text available here is a provider response body — which may
        carry a customer's details and was produced by a call made with
        materialized credentials. The machine code is enough to act on.
        """
        return Outcome(
            status=ENGINE_STATUS[self.outcome],
            error_code=self.error_code,
            retry_after_seconds=self.retry_after_seconds,
            provider_reference=self.provider_reference,
            provider_status_code=self.http_status,
        )


# ── request translation ──────────────────────────────────────────────────────


def _params(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OperationContractError("params_invalid")
    return value


def _text(
    params: Mapping[str, object], key: str, code: str, *, required: bool = True
) -> str | None:
    value = params.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise OperationContractError(code)
        return None
    if not isinstance(value, str) or len(value) > 255:
        raise OperationContractError(code)
    return value.strip()


def _identifier(params: Mapping[str, object], key: str, code: str) -> str:
    value = _text(params, key, code)
    if value is None or _IDENTIFIER_RE.fullmatch(value) is None:
        raise OperationContractError(code)
    return value


def _matching(
    params: Mapping[str, object], key: str, pattern: re.Pattern[str], code: str
) -> str:
    value = _text(params, key, code)
    if value is None or pattern.fullmatch(value) is None:
        raise OperationContractError(code)
    return value


def _optional_mapping(
    params: Mapping[str, object], key: str, code: str
) -> object | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise OperationContractError(code)
    try:
        # Fail at translation time rather than half-way through an httpx
        # serialization, where the exception would escape classification
        # entirely and the engine would have to guess whether it sent.
        json.dumps(value)
    except (TypeError, ValueError):
        raise OperationContractError(code) from None
    return value


def _money_body(params: Mapping[str, object]) -> tuple[int, str]:
    currency = _currency(params.get("currency"))
    return minor_units(params.get("amount"), currency=currency), currency


def _put(body: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        body[key] = value


def _build(
    operation: _Operation, params: Mapping[str, object], reference: str | None
) -> tuple[str, dict[str, object] | None, dict[str, str] | None]:
    """`(path, json_body, query)` for one command, or raise a contract error."""
    name = operation.name
    if name == "initialize_payment":
        amount, currency = _money_body(params)
        body: dict[str, object] = {
            "email": _text(params, "email", "email_required"),
            "amount": amount,
            "currency": currency,
            "reference": reference,
        }
        _put(
            body,
            "callback_url",
            _text(params, "callback_url", "callback_url_invalid", required=False),
        )
        _put(
            body, "metadata", _optional_mapping(params, "metadata", "metadata_invalid")
        )
        return "/transaction/initialize", body, None

    if name == "charge_authorization":
        amount, currency = _money_body(params)
        body = {
            "email": _text(params, "email", "email_required"),
            "amount": amount,
            "currency": currency,
            "authorization_code": _identifier(
                params, "authorization_code", "authorization_code_invalid"
            ),
            "reference": reference,
        }
        _put(
            body, "metadata", _optional_mapping(params, "metadata", "metadata_invalid")
        )
        return "/transaction/charge_authorization", body, None

    if name == "refund":
        body = {
            "transaction": _identifier(params, "transaction", "transaction_required")
        }
        if params.get("amount") is not None:
            amount, currency = _money_body(params)
            body["amount"] = amount
            body["currency"] = currency
        _put(
            body,
            "customer_note",
            _text(params, "customer_note", "customer_note_invalid", required=False),
        )
        # Paystack accepts no client reference on a refund, so the derived key
        # is stamped here — the durable handle Sub matches on when it has to
        # decide whether an ambiguous refund already exists.
        body["merchant_note"] = reference
        return "/refund", body, None

    if name == "resolve_bank_account":
        return (
            "/bank/resolve",
            None,
            {
                "account_number": _matching(
                    params,
                    "account_number",
                    _ACCOUNT_NUMBER_RE,
                    "account_number_invalid",
                ),
                "bank_code": _matching(
                    params, "bank_code", _BANK_CODE_RE, "bank_code_invalid"
                ),
            },
        )

    if name == "create_transfer_recipient":
        recipient_type = _text(params, "type", "recipient_type_invalid")
        if recipient_type not in _RECIPIENT_TYPES:
            raise OperationContractError("recipient_type_invalid")
        body = {
            "type": recipient_type,
            "name": _text(params, "name", "name_required"),
            "account_number": _matching(
                params, "account_number", _ACCOUNT_NUMBER_RE, "account_number_invalid"
            ),
            "bank_code": _matching(
                params, "bank_code", _BANK_CODE_RE, "bank_code_invalid"
            ),
            "currency": _currency(params.get("currency")),
        }
        _put(
            body,
            "description",
            _text(params, "description", "description_invalid", required=False),
        )
        _put(
            body, "metadata", _optional_mapping(params, "metadata", "metadata_invalid")
        )
        return "/transferrecipient", body, None

    if name == "initiate_transfer":
        amount, currency = _money_body(params)
        body = {
            # Paystack's only supported funding source today. Declared rather
            # than accepted from the payload: which pot a payout leaves from is
            # not something a command may choose per request.
            "source": "balance",
            "amount": amount,
            "currency": currency,
            "recipient": _identifier(params, "recipient", "recipient_required"),
            "reference": reference,
        }
        _put(body, "reason", _text(params, "reason", "reason_invalid", required=False))
        return "/transfer", body, None

    if name == "create_customer":
        body = {"email": _text(params, "email", "email_required")}
        for key in ("first_name", "last_name", "phone"):
            _put(body, key, _text(params, key, f"{key}_invalid", required=False))
        _put(
            body, "metadata", _optional_mapping(params, "metadata", "metadata_invalid")
        )
        return "/customer", body, None

    if name == "update_customer":
        code = _identifier(params, "customer", "customer_required")
        body = {}
        for key in ("first_name", "last_name", "phone"):
            _put(body, key, _text(params, key, f"{key}_invalid", required=False))
        _put(
            body, "metadata", _optional_mapping(params, "metadata", "metadata_invalid")
        )
        if not body:
            raise OperationContractError("update_has_no_fields")
        return f"/customer/{quote(code, safe='')}", body, None

    if name == "read_customer":
        code = _identifier(params, "customer", "customer_required")
        return f"/customer/{quote(code, safe='')}", None, None

    raise OperationContractError("operation_unsupported")  # pragma: no cover


# ── response classification ──────────────────────────────────────────────────


def _evidence(value: object) -> str | None:
    """A provider identifier, if it is one we may store as evidence."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > _MAX_EVIDENCE_LENGTH:
        return None
    return text


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _status_token(data: Mapping[str, object]) -> str | None:
    value = data.get("status")
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _references_a_used_reference(body: Mapping[str, object]) -> bool:
    haystack = " ".join(
        str(body.get(key) or "") for key in ("message", "code")
    ).casefold()
    return any(marker in haystack for marker in _REFERENCE_USED_MARKERS)


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    return int(value) if value is not None and value.isdigit() else None


@dataclass(frozen=True, slots=True)
class _Sent:
    """What the attempt committed to, before anything came back."""

    operation: _Operation
    reference: str | None
    correlation: str | None
    amount_minor: int | None
    currency: str | None

    @property
    def evidence_reference(self) -> str | None:
        return self.reference or self.correlation


def _result(
    sent: _Sent,
    outcome: OperationOutcome,
    *,
    error_code: str | None = None,
    http_status: int | None = None,
    retry_after_seconds: int | None = None,
    provider_transaction_id: str | None = None,
    provider_status: str | None = None,
    reply: Mapping[str, str] | None = None,
) -> OperationResult:
    return OperationResult(
        operation=sent.operation.name,
        outcome=outcome,
        error_code=error_code,
        http_status=http_status,
        retry_after_seconds=retry_after_seconds,
        provider_reference=sent.evidence_reference,
        provider_transaction_id=provider_transaction_id,
        provider_status=provider_status,
        reply=reply or {},
    )


def _money_evidence_holds(sent: _Sent, data: Mapping[str, object]) -> bool:
    """Does the provider's echo agree, exactly, with what we asked for?

    No tolerance. ERP allows a kobo (and its webhook path allows five outbound,
    for fees on money that has already left) — but a TOLERANCE IS A POLICY,
    and policy is the product's. What a connector may say is narrower and
    sharper: the amount that came back is the amount that went out, or this is
    not a conclusive success and somebody has to look at it.
    """
    if sent.amount_minor is None:
        return True
    echoed = data.get("amount")
    if not isinstance(echoed, int) or isinstance(echoed, bool):
        return False
    if echoed != sent.amount_minor:
        return False
    currency = data.get("currency")
    return isinstance(currency, str) and currency.strip().upper() == sent.currency


def _charge_result(
    sent: _Sent, data: Mapping[str, object], http_status: int
) -> OperationResult:
    token = _status_token(data)
    transaction_id = _evidence(data.get("id"))
    reply = {
        key: value
        for key, value in (
            ("authorization_url", _evidence(data.get("authorization_url"))),
            ("access_code", _evidence(data.get("access_code"))),
            ("gateway_response", _evidence(data.get("gateway_response"))),
        )
        if value is not None
    }

    def answer(
        outcome: OperationOutcome, error_code: str | None = None
    ) -> OperationResult:
        return _result(
            sent,
            outcome,
            error_code=error_code,
            http_status=http_status,
            provider_transaction_id=transaction_id,
            provider_status=token,
            reply=reply,
        )

    if sent.operation.name == "initialize_payment":
        # An initialization has no lifecycle token; it has a checkout handle.
        # Without one the command achieved nothing usable, and whether it
        # created a transaction at the provider is exactly unknown.
        if "authorization_url" not in reply or "access_code" not in reply:
            return answer(sent.operation.indeterminate, "provider_receipt_missing")
        return answer(OperationOutcome.SUCCEEDED)

    if token in _CHARGE_DECLINED:
        return answer(OperationOutcome.DECLINED, "provider_declined")
    if token in _CHARGE_IN_FLIGHT:
        return answer(OperationOutcome.AMBIGUOUS, "provider_status_not_conclusive")
    if token not in _CHARGE_SUCCEEDED:
        return answer(OperationOutcome.AMBIGUOUS, "provider_status_unknown")
    if transaction_id is None or not _money_evidence_holds(sent, data):
        # A success whose money does not add up is not a success. Ported from
        # Sub's evidence gate, tightened from "within a kobo" to "exactly".
        return answer(OperationOutcome.AMBIGUOUS, "provider_evidence_incomplete")
    return answer(OperationOutcome.SUCCEEDED)


def _transfer_result(
    sent: _Sent, data: Mapping[str, object], http_status: int
) -> OperationResult:
    token = _status_token(data)
    transfer_code = _evidence(data.get("transfer_code"))
    reply = {"transfer_code": transfer_code} if transfer_code is not None else {}

    def answer(
        outcome: OperationOutcome, error_code: str | None = None
    ) -> OperationResult:
        return _result(
            sent,
            outcome,
            error_code=error_code,
            http_status=http_status,
            provider_transaction_id=_evidence(data.get("id")),
            provider_status=token,
            reply=reply,
        )

    if token in _TRANSFER_DECLINED:
        return answer(OperationOutcome.DECLINED, "provider_declined")
    if token in _TRANSFER_IN_FLIGHT:
        # The money is moving and nobody may send it again. `verify_transfer`
        # on the SAME reference is how this resolves — ERP looks it up by the
        # merchant reference, never by the `TRF_` code.
        return answer(OperationOutcome.AMBIGUOUS, "provider_status_not_conclusive")
    if token not in _TRANSFER_SUCCEEDED:
        return answer(OperationOutcome.AMBIGUOUS, "provider_status_unknown")
    if transfer_code is None or not _money_evidence_holds(sent, data):
        return answer(OperationOutcome.AMBIGUOUS, "provider_evidence_incomplete")
    return answer(OperationOutcome.SUCCEEDED)


def _refund_result(
    sent: _Sent, data: Mapping[str, object], http_status: int
) -> OperationResult:
    token = _status_token(data)
    refund_id = _evidence(data.get("id"))
    reply = {"refund_id": refund_id} if refund_id is not None else {}

    def answer(
        outcome: OperationOutcome, error_code: str | None = None
    ) -> OperationResult:
        return _result(
            sent,
            outcome,
            error_code=error_code,
            http_status=http_status,
            provider_transaction_id=refund_id,
            provider_status=token,
            reply=reply,
        )

    if token in _REFUND_DECLINED:
        return answer(OperationOutcome.DECLINED, "provider_declined")
    if refund_id is None:
        return answer(sent.operation.indeterminate, "provider_receipt_missing")
    if not _money_evidence_holds(sent, data):
        # Only bites when an amount was REQUESTED: a refund sent without one
        # takes the transaction's own amount, and there is nothing to compare.
        return answer(OperationOutcome.AMBIGUOUS, "provider_evidence_incomplete")
    # The refund record exists; `pending`/`processing`/`processed` is its own
    # lifecycle and travels back as `provider_status` for the product to read.
    return answer(OperationOutcome.SUCCEEDED)


#: Which reply field proves a non-money operation actually answered.
_REPLY_EVIDENCE: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "resolve_bank_account": ("account_name", "account_number"),
        "create_transfer_recipient": ("recipient_code",),
        "create_customer": ("customer_code",),
        "update_customer": ("customer_code",),
        "read_customer": ("customer_code",),
    }
)


def _record_result(
    sent: _Sent, data: Mapping[str, object], http_status: int
) -> OperationResult:
    fields = _REPLY_EVIDENCE[sent.operation.name]
    reply = {
        key: value for key in fields if (value := _evidence(data.get(key))) is not None
    }
    identity = reply.get(fields[0])
    outcome = (
        sent.operation.indeterminate if identity is None else OperationOutcome.SUCCEEDED
    )
    return _result(
        sent,
        outcome,
        error_code=None if identity is not None else "provider_receipt_missing",
        http_status=http_status,
        provider_transaction_id=identity,
        provider_status=_status_token(data),
        reply=reply,
    )


def _succeeded(
    sent: _Sent, data: Mapping[str, object], http_status: int
) -> OperationResult:
    name = sent.operation.name
    if name in ("initialize_payment", "charge_authorization"):
        return _charge_result(sent, data, http_status)
    if name == "initiate_transfer":
        return _transfer_result(sent, data, http_status)
    if name == "refund":
        return _refund_result(sent, data, http_status)
    return _record_result(sent, data, http_status)


def _reused_reference(
    sent: _Sent, envelope: Mapping[str, object], status: int
) -> OperationResult | None:
    """PROOF that an earlier attempt reached the provider, if that is what it is.

    Not a failure and not grounds to send again: verify the reference and find
    out what it did. Checked on BOTH refusal paths — an HTTP 4xx and a 200
    carrying ``status: false`` — because Paystack uses each for it depending on
    the endpoint (400 on a transaction, 404 on a transfer).
    """
    if not _references_a_used_reference(envelope):
        return None
    return _result(
        sent,
        OperationOutcome.AMBIGUOUS,
        error_code="provider_reference_already_used",
        http_status=status,
    )


def _classify(sent: _Sent, response: httpx.Response) -> OperationResult:
    status = response.status_code
    if status == 429:
        # A rate limit is a refusal to act. Nothing landed, so it is safe to
        # come back — and the provider's own Retry-After beats any curve.
        return _result(
            sent,
            OperationOutcome.RETRYABLE,
            error_code="provider_rate_limited",
            http_status=status,
            retry_after_seconds=_retry_after(response),
        )
    if status in (401, 403):
        return _result(
            sent,
            OperationOutcome.TERMINAL,
            error_code="authentication_rejected",
            http_status=status,
        )
    try:
        body = response.json()
    except ValueError:
        body = None
    envelope = _mapping(body)

    if status >= 500:
        # The provider received it and then failed to answer. For a read that
        # is safe to repeat; for anything that moves money it is precisely the
        # ambiguity that must not be retried.
        return _result(
            sent,
            sent.operation.indeterminate,
            error_code="provider_unavailable",
            http_status=status,
        )
    if status >= 400:
        reused = _reused_reference(sent, envelope, status)
        if reused is not None:
            return reused
        if status == 404 and sent.operation.name == "read_customer":
            # A definite answer — the record is absent. ERP returns `None`
            # here rather than raising, for the same reason.
            return _result(
                sent,
                OperationOutcome.TERMINAL,
                error_code="provider_record_not_found",
                http_status=status,
            )
        # An HTTP 4xx is the provider rejecting the request before acting on
        # it: nothing to reconcile, and nothing a retry would fix.
        return _result(
            sent,
            OperationOutcome.TERMINAL,
            error_code="provider_rejected_request",
            http_status=status,
        )
    if body is None:
        # A 2xx whose body cannot be read tells us the call was accepted and
        # nothing about what it did. Checked BEFORE the envelope, because an
        # unreadable body has no `status` field and reading its absence as a
        # refusal would turn "we could not tell" into "it definitely did not
        # happen" — on a money command, the exact inversion that matters.
        return _result(
            sent,
            sent.operation.indeterminate,
            error_code="provider_response_unreadable",
            http_status=status,
        )
    if envelope.get("status") is not True:
        reused = _reused_reference(sent, envelope, status)
        if reused is not None:
            return reused
        # The provider answered 200 and said it did not do it. An explicit
        # refusal is terminal; it never acted, so there is nothing to reconcile.
        return _result(
            sent,
            OperationOutcome.TERMINAL,
            error_code="provider_refused_request",
            http_status=status,
        )
    return _succeeded(sent, _mapping(envelope.get("data")), status)


def _request_failure(sent: _Sent, exc: httpx.RequestError) -> OperationResult:
    """The classification this whole module exists for.

    A connect failure means the bytes never left: nothing can have landed, so
    it is safe to come back. ANYTHING ELSE — a read timeout, a write timeout, a
    connection dropped mid-response — means the request may have been received
    and applied while the answer was lost. For a charge or a transfer that is
    the duplicate-charge case, and the answer is never "try again".
    """
    if isinstance(exc, httpx.ConnectTimeout | httpx.ConnectError | httpx.PoolTimeout):
        return _result(
            sent, OperationOutcome.RETRYABLE, error_code="provider_connect_failed"
        )
    return _result(
        sent, sent.operation.indeterminate, error_code="provider_outcome_ambiguous"
    )


# ── the client ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PaystackOperations:
    """Authenticated provider I/O for the nine outbound commands.

    Holds no state between calls, owns no ledger, and keeps no retry budget:
    each `run` is one attempt, classified and handed back. Whether there is
    another attempt is the engine's decision, and whether there should be one
    at all is what the outcome says.
    """

    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0

    def run(
        self,
        operation: str,
        params: object,
        *,
        idempotency_key: object,
        api_secret: object,
    ) -> OperationResult:
        spec = OPERATIONS.get(operation) if isinstance(operation, str) else None
        if spec is None:
            return OperationResult(
                operation=operation if isinstance(operation, str) else "unknown",
                outcome=OperationOutcome.TERMINAL,
                error_code="operation_unsupported",
            )
        try:
            if not isinstance(api_secret, str) or not api_secret:
                raise OperationContractError("api_secret_key_unavailable")
            fields = _params(params)
            reference = provider_reference(idempotency_key) if spec.needs_key else None
            path, body, query = _build(spec, fields, reference)
            amount = body.get("amount") if body is not None else None
            currency = body.get("currency") if body is not None else None
            sent = _Sent(
                operation=spec,
                reference=reference,
                correlation=self._correlation(fields),
                amount_minor=amount if isinstance(amount, int) else None,
                currency=currency if isinstance(currency, str) else None,
            )
        except (MoneyContractError, OperationContractError) as exc:
            # Nothing was sent, so nothing can have landed. Terminal, and the
            # code names the field a human has to fix.
            return OperationResult(
                operation=spec.name,
                outcome=OperationOutcome.TERMINAL,
                error_code=exc.code,
            )

        try:
            with httpx.Client(
                base_url=f"https://{API_HOST}",
                transport=self.transport,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = client.request(
                    spec.method,
                    path,
                    json=body,
                    params=query,
                    headers={
                        "authorization": f"Bearer {api_secret}",
                        "content-type": "application/json",
                    },
                )
        except httpx.RequestError as exc:
            return _request_failure(sent, exc)
        return _classify(sent, response)

    @staticmethod
    def _correlation(params: Mapping[str, object]) -> str | None:
        """The provider identifier this command was aimed at.

        Evidence for the operations that carry no reference of their own: a
        refund's target transaction, a customer's code. Without it an
        ambiguous refund has no handle at all.
        """
        for key in ("transaction", "customer", "recipient"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:_MAX_EVIDENCE_LENGTH]
        return None
