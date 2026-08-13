"""Retry classification and backoff — pure, and the engine's only opinion.

Ported from `dotmac_sub`'s delivery outcome handling, with one thing removed on
purpose (see "What did not come across").

## Four outcomes, because three is not enough

============================ ==============================================
outcome                      what the engine does
============================ ==============================================
``SUCCEEDED``                done; no further attempt
``RETRYABLE``                schedule another attempt, until the cap
``RECONCILIATION_REQUIRED``  stop attempting; a human or a repair command
                             must decide. NOT a failure and NOT a retry
``TERMINAL``                 dead-letter it; retrying cannot help
============================ ==============================================

`RECONCILIATION_REQUIRED` is the one people leave out, and leaving it out is
what makes a delivery queue lie: the effect may have half-landed at the
provider, so retrying risks duplicating it and dead-lettering it hides it. It
needs a third answer.

## What did not come across

Sub's generic claim path contains ``error_code == "crm_customer_name_rejected"``
— one product's business rule embedded in the shared engine. It is deliberately
NOT ported. A connector or product that needs an error to be terminal says so
through :class:`Outcome`, which is the generic way to express it; naming a
product's error string in the engine is how the engine stops being shared.

## Backoff

`60 · 2^(attempt-1)` seconds, capped at 8 hours — Sub's formula, kept because it
is in production. A provider-supplied `retry_after_seconds` always wins: a
provider that tells you when to come back knows better than an exponential
curve, and ignoring it is how rate limits become outages.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "MAX_BACKOFF_SECONDS",
    "Outcome",
    "OutcomeStatus",
    "next_state",
    "retry_delay_seconds",
]

#: Cap. Beyond this a queue is not retrying, it is hoarding.
MAX_BACKOFF_SECONDS: Final[int] = 8 * 60 * 60
_BASE_DELAY_SECONDS: Final[int] = 60

#: Sub's default, kept. Clamped to [1, 20] at the call site so a mis-typed
#: configuration cannot create either an un-retried delivery or an immortal one.
DEFAULT_MAX_ATTEMPTS: Final[int] = 10
_MIN_ATTEMPTS: Final[int] = 1
_MAX_ATTEMPTS: Final[int] = 20


class OutcomeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a connector reports about one attempt.

    `error_code` is a CONNECTOR's vocabulary, not the engine's. The engine
    stores it and never branches on it — that is the boundary Sub's
    `crm_customer_name_rejected` check crossed.
    """

    status: OutcomeStatus
    error_code: str | None = None
    error_detail: str | None = None
    #: A provider's own instruction, in seconds. Always wins over the curve.
    retry_after_seconds: int | None = None

    @property
    def is_final(self) -> bool:
        return self.status is not OutcomeStatus.RETRYABLE


def retry_delay_seconds(attempt_count: int, outcome: Outcome | None = None) -> int:
    """Seconds until the next attempt.

    :param attempt_count: attempts made SO FAR, so the first retry waits the
        base delay rather than double it.
    """
    if outcome is not None and outcome.retry_after_seconds is not None:
        # Trusted, but not unboundedly: a provider sending `retry_after: 10y`
        # would otherwise park the delivery past any operator's attention.
        return max(0, min(int(outcome.retry_after_seconds), MAX_BACKOFF_SECONDS))
    exponent = max(attempt_count - 1, 0)
    # Guard the shift itself: 2 ** 10_000 is computed before min() sees it.
    if exponent > 32:
        return MAX_BACKOFF_SECONDS
    return min(MAX_BACKOFF_SECONDS, _BASE_DELAY_SECONDS * (2**exponent))


def next_state(
    outcome: Outcome, *, attempt_count: int, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> str:
    """The delivery state this outcome produces.

    Attempt exhaustion turns RETRYABLE into `dead_letter`. That is the only
    place the engine overrides a connector's classification, and it is why the
    cap is clamped rather than trusted: `max_attempts: 0` from a bad config
    would otherwise dead-letter everything on its first attempt.
    """
    if outcome.status is OutcomeStatus.SUCCEEDED:
        return "delivered"
    if outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED:
        return "reconciliation_required"
    if outcome.status is OutcomeStatus.TERMINAL:
        return "dead_letter"
    cap = max(_MIN_ATTEMPTS, min(int(max_attempts), _MAX_ATTEMPTS))
    return "dead_letter" if attempt_count >= cap else "retryable"
