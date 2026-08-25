"""The DELIVERY-mode adapter: one dispatched command, one classified outcome.

Thin by design. Everything that decides anything lives in
:mod:`dotmac_connector_paystack.operations`; this file resolves the delivery
configuration, materializes nothing, checks that the binding is allowed to ask
for this command, and translates the result into the engine's `Outcome`.

## One binding is one blast radius

Four capabilities rather than one, and the split is a security property, not
taxonomy. `ACTIONS_BY_CAPABILITY` is ported from Sub's
`PaymentGatewayRunner`, which refuses an action outside the bound capability
with `operation_not_allowed` — so an installation bound only for payment
intents cannot be made to issue a refund or a payout by a crafted payload. The
table is checked for TOTALITY at import: an operation that belongs to no
capability would be unreachable, and one that belongs to two would widen every
binding that names either.

## What this handler never does

It does not decide whether the command should have been issued, does not
consult a product database, does not retry, and does not reschedule itself.
`dotmac_integration.dispatch.invoke` hands it a materialized secret and an
idempotency key and takes back a classification; the retry curve, the
dead-letter and the reconciliation queue are the engine's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

import httpx
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import DispatchRequest

from dotmac_connector_paystack.operations import (
    OPERATIONS,
    OperationContractError,
    PaystackOperations,
)

__all__ = [
    "ACTIONS_BY_CAPABILITY",
    "API_SECRET_KEY",
    "OUTBOUND_CAPABILITY_IDS",
    "OUTBOUND_CONFIG_SCHEMA",
    "PaystackDeliveryHandler",
]

API_SECRET_KEY: Final = "api_secret_key"

PAYMENT_INTENT_CAPABILITY: Final = "payments.intent.v1"
PAYMENT_REFUND_CAPABILITY: Final = "payments.refund.v1"
PAYMENT_PAYOUT_CAPABILITY: Final = "payments.payout.v1"
PAYMENT_CUSTOMER_CAPABILITY: Final = "payments.customer.v1"

#: Which commands each bound capability may issue. Sub's allow-list, kept as
#: data so widening one is a reviewable diff rather than a new ``or`` clause.
ACTIONS_BY_CAPABILITY: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        PAYMENT_INTENT_CAPABILITY: frozenset(
            {"initialize_payment", "charge_authorization"}
        ),
        PAYMENT_REFUND_CAPABILITY: frozenset({"refund"}),
        PAYMENT_PAYOUT_CAPABILITY: frozenset(
            {"resolve_bank_account", "create_transfer_recipient", "initiate_transfer"}
        ),
        PAYMENT_CUSTOMER_CAPABILITY: frozenset(
            {"create_customer", "update_customer", "read_customer"}
        ),
    }
)

OUTBOUND_CAPABILITY_IDS: Final[tuple[str, ...]] = tuple(ACTIONS_BY_CAPABILITY)


def _misallocated_operations(
    operations: Mapping[str, object], allocation: Mapping[str, frozenset[str]]
) -> frozenset[str]:
    """Operations reachable through no capability, or through more than one.

    A named predicate so the import-time refusal and its sensitivity proof run
    the same expression. Unreachable is a dead command; reachable twice is a
    binding that grants more than its name says.
    """
    counted = [action for actions in allocation.values() for action in actions]
    once = {action for action in counted if counted.count(action) == 1}
    return (frozenset(operations) - once) | (frozenset(counted) - frozenset(operations))


_MISALLOCATED: Final[frozenset[str]] = _misallocated_operations(
    OPERATIONS, ACTIONS_BY_CAPABILITY
)
if _MISALLOCATED:  # pragma: no cover - an import-time guard, proved by test
    raise RuntimeError(
        f"paystack operations {sorted(_MISALLOCATED)} are unreachable or reachable "
        "through more than one capability binding"
    )

#: Every outbound capability takes the same configuration, and `timeout_seconds`
#: is REQUIRED rather than defaulted. A hidden default on a money path is a
#: deployment decision nobody made; a stated one is reviewable.
OUTBOUND_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["timeout_seconds"],
    "properties": {
        "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 60},
    },
}

_ACTION_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _timeout_seconds(config: Mapping[str, object]) -> int | float:
    value = config.get("timeout_seconds")
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 1 <= value <= 60
    ):
        raise OperationContractError("timeout_seconds_invalid")
    return value


def _api_secret(secrets: Mapping[str, object]) -> str:
    value = secrets.get(API_SECRET_KEY)
    if not isinstance(value, str) or not value:
        raise OperationContractError("api_secret_key_unavailable")
    return value


def _action(capability_id: str, payload: Mapping[str, object]) -> str:
    value = payload.get("action")
    if not isinstance(value, str) or _ACTION_RE.fullmatch(value) is None:
        raise OperationContractError("action_required")
    if value not in ACTIONS_BY_CAPABILITY.get(capability_id, frozenset()):
        # Sub's refusal, kept: a binding that was granted payment intents may
        # not be talked into issuing a refund by the shape of a payload.
        raise OperationContractError("operation_not_allowed")
    return value


@dataclass(frozen=True, slots=True)
class PaystackDeliveryHandler:
    """Provider I/O for one bound outbound capability."""

    capability_id: str
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __call__(self, request: DispatchRequest) -> Outcome:
        if self.capability_id not in ACTIONS_BY_CAPABILITY:
            return Outcome(
                status=OutcomeStatus.TERMINAL, error_code="capability_unsupported"
            )
        if request.capability_id != self.capability_id:
            # The handler was built for one capability and handed another's
            # work. Refused rather than served under whichever id happens to
            # be nearer to hand — the allow-list is only a boundary if the
            # capability it is checked against is the bound one.
            return Outcome(
                status=OutcomeStatus.TERMINAL, error_code="capability_mismatch"
            )
        try:
            timeout = _timeout_seconds(request.config)
            secret = _api_secret(request.secrets)
            action = _action(self.capability_id, request.payload)
        except OperationContractError as exc:
            # Refused before a byte left, so nothing can have landed.
            return Outcome(status=OutcomeStatus.TERMINAL, error_code=exc.code)

        result = PaystackOperations(
            transport=self.transport, timeout_seconds=timeout
        ).run(
            action,
            request.payload.get("params"),
            # The engine's key, unchanged. `dispatch` takes it from the delivery
            # row and never from the attempt number, which is the property that
            # makes the derived provider reference identical on every attempt.
            idempotency_key=request.idempotency_key,
            api_secret=secret,
        )
        return result.as_outcome()
