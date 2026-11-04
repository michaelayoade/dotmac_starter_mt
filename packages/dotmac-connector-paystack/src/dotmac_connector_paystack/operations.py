"""Outbound Paystack operations — provider I/O, and nothing that decides money.

Two provider-neutral operations, both on the refund path: ``refund``, the one
command the connector is TOLD to perform, and ``find_refund``, the read that
decides an ambiguous one. (Payment intent, saved-authorization charge, payout
and customer operations are NOT part of this release; they were removed rather
than disabled, because the capability allocation guard in ``delivery`` forbids
an operation no capability can reach.) The connector authenticates, translates
the wire format, performs the call and classifies what came back. It never
reads a product database, never decides whether a refund is warranted, and
never decides when to try again — those are the product's, and
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
the question does not change it. So `DECLINED` maps to the engine's `TERMINAL`
and never to `RETRYABLE`; the mapping is a total table with an import-time
totality guard, not a chain of ``if``\\ s a later member can slip past.

`AMBIGUOUS` is the one people leave out, and leaving it out is the
duplicate-refund bug. A read timeout on a refund means the bytes were sent and
the answer was lost — the money may already be on its way back. Retrying
refunds twice. It maps to `RECONCILIATION_REQUIRED`, which stops the engine
attempting anything and hands the question to a reconciler that resolves it
against provider state with ``find_refund`` (``GET /refund?transaction=...``)
and NEVER by sending the refund again.

## Paystack's `/refund` has no idempotency key

Checked against the provider's own contract: **Paystack publishes no
``Idempotency-Key`` header**, and ``/refund`` accepts no client-supplied
reference either — the one money-moving endpoint here with no provider-side
at-most-once key. Nothing at the provider refuses a second refund, so the
protection is ours: the classification above never lets an ambiguous send be
retried, and every refund carries a reference derived from the ENGINE's
idempotency key (stable across attempts of one delivery) in ``merchant_note``,
where ``find_refund`` matches it exactly. Sub solved it the same way in
production.

``find_refund`` reads the list of refunds for a transaction and compares the
derived reference with each record's ``merchant_note``: zero matches means the
refund did not land, one means it did (its id and status travel as evidence),
more than one is an anomaly for a human. A list that is unreadable or reports
another page decides nothing and is AMBIGUOUS.

The reference is derived from the key and never read from the payload. A guard
whose value arrives in a mutable command payload is a guard a caller defeats by
accident.

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
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Final

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

#: An exact amount in MAJOR units. Anchored, digits plus an explicit decimal
#: point and at most six fraction digits — wide enough for any minor-unit scale,
#: while making ``"5000"`` (major units or minor units?) unrepresentable and
#: narrow enough that a
#: float's repr (``1e-05``, ``0.1000000000000000055``) cannot pass.
_EXACT_AMOUNT_RE: Final[re.Pattern[str]] = re.compile(
    r"(0|[1-9][0-9]{0,15})\.[0-9]{1,6}"
)
_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z]{3}")
#: What may appear in a URL path segment or a provider identifier we echo back.
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9@._+:=-]{1,120}")
#: Paystack's own recipient types. A closed set: an unknown one is a contract
#: error here rather than a 400 discovered at the provider.
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
    #: `AMBIGUOUS` wherever a duplicate would cost real money; `RETRYABLE` only
    #: where the operation is a pure read.
    indeterminate: OperationOutcome
    #: Whether the command needs the engine's key at all (a refund does, to
    #: stamp ``merchant_note``; a refund lookup does, to match on it).
    needs_key: bool = False


OPERATIONS: Final[Mapping[str, _Operation]] = MappingProxyType(
    {
        operation.name: operation
        for operation in (
            # Money back out. No provider-side reference field exists, so the
            # derived key is stamped into `merchant_note` instead and an
            # ambiguous refund is resolved by `find_refund`, which reads the
            # provider's refund list for that transaction (Sub's production
            # mechanism).
            _Operation("refund", "POST", OperationOutcome.AMBIGUOUS, needs_key=True),
            # A pure read. Repeating it changes nothing at the provider. It is
            # how an AMBIGUOUS refund is decided without ever re-sending it.
            _Operation(
                "find_refund", "GET", OperationOutcome.RETRYABLE, needs_key=True
            ),
        )
    }
)


# ── the provider's own refund status vocabulary ─────────────────────────────

#: A refund COMMAND has landed the moment the provider holds a refund record;
#: `pending`/`processing`/`processed` are that record's own lifecycle and
#: travel back as evidence rather than as a verdict.
_REFUND_DECLINED: Final[frozenset[str]] = frozenset({"failed"})

#: Paystack lists refunds in pages. One transaction has a handful; a page this
#: wide that still reports another page is treated as unresolved, never as a
#: complete list.
_REFUND_PAGE: Final = 100


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
    if name == "refund":
        body: dict[str, object] = {
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

    if name == "find_refund":
        # The list is filtered by transaction only; matching on the derived
        # reference in `merchant_note` is done on the answer, exactly.
        return (
            "/refund",
            None,
            {
                "transaction": _identifier(
                    params, "transaction", "transaction_required"
                ),
                "perPage": str(_REFUND_PAGE),
            },
        )

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


def _find_refund_result(
    sent: _Sent, envelope: Mapping[str, object], http_status: int
) -> OperationResult:
    """Decide, from the provider's own refund list, whether a refund landed.

    Zero exact matches on the derived ``merchant_note`` means it did not land;
    exactly one means it did, and its id and status travel as evidence; more
    than one is an anomaly nobody may resolve by sending again. A list that
    cannot be read, or that says there is another page, decides nothing.
    """
    data = envelope.get("data")

    def answer(
        outcome: OperationOutcome,
        error_code: str | None = None,
        *,
        refund_id: str | None = None,
        token: str | None = None,
        matches: str | None = None,
    ) -> OperationResult:
        reply = {
            key: value
            for key, value in (
                ("matches", matches),
                ("refund_id", refund_id),
                ("refund_status", token),
            )
            if value is not None
        }
        return _result(
            sent,
            outcome,
            error_code=error_code,
            http_status=http_status,
            provider_transaction_id=refund_id,
            provider_status=token,
            reply=reply,
        )

    if not isinstance(data, list):
        return answer(sent.operation.indeterminate, "provider_receipt_missing")
    page_count = _mapping(envelope.get("meta")).get("pageCount")
    if isinstance(page_count, int) and not isinstance(page_count, bool):
        if page_count > 1:
            return answer(OperationOutcome.AMBIGUOUS, "refund_list_truncated")
    elif len(data) >= _REFUND_PAGE:
        return answer(OperationOutcome.AMBIGUOUS, "refund_list_truncated")
    found = [
        record
        for record in data
        if isinstance(record, Mapping) and record.get("merchant_note") == sent.reference
    ]
    if not found:
        return answer(OperationOutcome.SUCCEEDED, matches="0")
    if len(found) > 1:
        return answer(
            OperationOutcome.AMBIGUOUS, "refund_match_not_unique", matches="many"
        )
    record = found[0]
    refund_id = _evidence(record.get("id"))
    if refund_id is None:
        return answer(
            OperationOutcome.AMBIGUOUS, "provider_receipt_missing", matches="1"
        )
    return answer(
        OperationOutcome.SUCCEEDED,
        refund_id=refund_id,
        token=_status_token(record),
        matches="1",
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
        # The provider answered 200 and said it did not do it. An explicit
        # refusal is terminal; it never acted, so there is nothing to reconcile.
        return _result(
            sent,
            OperationOutcome.TERMINAL,
            error_code="provider_refused_request",
            http_status=status,
        )
    if sent.operation.name == "find_refund":
        return _find_refund_result(sent, envelope, status)
    return _refund_result(sent, _mapping(envelope.get("data")), status)


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
