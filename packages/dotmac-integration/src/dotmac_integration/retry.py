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

`base · 2^(attempt-1)` seconds, capped — Sub's formula, with its production
numbers now living in :class:`dotmac_integration.policy.ExecutionPolicy` rather
than hardcoded here. A provider-supplied `retry_after_seconds` always wins: a
provider that tells you when to come back knows better than an exponential
curve, and ignoring it is how rate limits become outages.

## Rate limits are never terminal

A 429 means the request was REFUSED, not that it can never succeed — so a
connector that reports it as `TERMINAL` is asking the engine to dead-letter work
a provider explicitly invited us to resend. :func:`classify` corrects exactly
that, and only that: a TERMINAL outcome carrying a status in
`ExecutionPolicy.retryable_provider_status_codes` becomes RETRYABLE.

Three limits on the correction, each load-bearing:

* it reads `provider_status_code`, a TYPED field this module defines, and never
  `error_code`, which is the connector's own vocabulary. The boundary Sub's
  `crm_customer_name_rejected` check crossed stays uncrossed — the difference is
  that an HTTP status means the same thing at every provider, and a
  product-specific error string does not;
* it never touches `RECONCILIATION_REQUIRED`. That status says the effect may
  have half-landed, which is a stronger claim than any status code refutes;
  promoting it to a retry is how a provider gets charged twice;
* it never makes an outcome terminal. The correction only ever moves work back
  into the queue, so a mistaken status set costs retries, not lost deliveries.

Attempt exhaustion still applies afterwards: a rescued 429 dead-letters at
`max_attempts` like anything else, so a provider that throttles forever does not
become immortal.

:func:`parse_retry_after` turns an HTTP `Retry-After` header — delta-seconds or
HTTP-date — into the integer `Outcome.retry_after_seconds` this module already
honoured, so every connector does not reimplement RFC 7231 § 7.1.3 (and get the
date form wrong).
"""

from __future__ import annotations

import email.utils
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum

from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy

__all__ = [
    "Outcome",
    "OutcomeStatus",
    "classify",
    "next_state",
    "parse_retry_after",
    "retry_delay_seconds",
    "throttle_cooldown_seconds",
]

# The numbers live in `ExecutionPolicy`, not here. They are deployment
# decisions — a webhook fan-out and a nightly bulk poll do not want the same
# backoff — and this module is the classification, not the configuration.


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
    #: Bounded provider evidence used to correlate a later callback. This is a
    #: reference, not an arbitrary result object: response bodies are neither
    #: required for retry classification nor safe to retain in the outbox.
    provider_reference: str | None = None
    #: The HTTP status observed for this attempt, when the provider spoke HTTP.
    #: Kept typed so a connector cannot smuggle a response body through an
    #: unstructured evidence mapping.
    provider_status_code: int | None = None
    #: The NORMALIZED result body, in the owning domain's vocabulary — the thing
    #: ADR-0024 § 10.2 calls `result_schema` and § 12.2 says a provider customer
    #: id, recipient code or transfer reference must arrive as, rather than as a
    #: separate product-visible provider action.
    #:
    #: This is not a loophole in the rule two fields above. `provider_status_code`
    #: is typed precisely so a raw response body cannot travel as unstructured
    #: evidence, and this field does not reopen that: `dispatch.settle` validates
    #: it against the DOMAIN's published `result_schema` before it writes, so
    #: what may travel here is exactly what one owner published and every product
    #: can already read. An unvalidated mapping is what was refused; a
    #: contract-validated one is what makes provider normalization possible at
    #: all.
    #:
    #: ``None`` means the connector returned no body — legitimate for a capability
    #: whose contract publishes no `result_schema`, and refused for one that does.
    result: dict[str, object] | None = None

    def __post_init__(self) -> None:
        reference = self.provider_reference
        if reference is not None:
            normalized = reference.strip()
            if not normalized or len(normalized) > 500:
                raise ValueError("provider_reference must be 1..500 characters")
            object.__setattr__(self, "provider_reference", normalized)
        status = self.provider_status_code
        if status is not None and (
            not isinstance(status, int)
            or isinstance(status, bool)
            or not 100 <= status <= 599
        ):
            raise ValueError("provider_status_code must be an HTTP status 100..599")

    @property
    def is_final(self) -> bool:
        return self.status is not OutcomeStatus.RETRYABLE


def parse_retry_after(
    value: str | int | None, *, now: datetime | None = None
) -> int | None:
    """An HTTP `Retry-After` header as whole seconds, or `None` if unusable.

    RFC 7231 § 7.1.3 allows two forms and providers use both: delta-seconds
    (`Retry-After: 120`) and an HTTP-date (`Retry-After: Wed, 21 Oct 2015
    07:28:00 GMT`). A connector that handles only the first silently treats the
    second as absent and falls back to the exponential curve — which is not
    wrong so much as it is ignoring an instruction the provider bothered to
    send.

    Lives here rather than in each connector because every connector needs it
    and RFC date parsing is exactly the kind of thing that gets written once,
    correctly, or many times, nearly.

    A malformed value returns `None` (fall back to the curve) rather than
    raising: a provider's header is untrusted input on an error path, and
    failing the whole outcome over it would turn a recoverable rate limit into
    a lost attempt. A past date clamps to `0` — "come back now", not "come back
    before now".
    """
    if value is None:
        return None
    if isinstance(value, bool):  # `True` is an `int`; it is not a duration.
        return None
    if isinstance(value, int):
        return max(0, value)
    text = value.strip()
    if not text:
        return None
    try:
        return max(0, int(text))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:  # pragma: no cover - defensive, older parser contracts
        return None
    if when.tzinfo is None:
        # RFC 7231 dates are GMT. A naive result means the header omitted the
        # zone; assuming UTC matches the spec rather than the host's timezone,
        # which would make the same header mean different things per deployment.
        when = when.replace(tzinfo=UTC)
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0, int((when - moment).total_seconds()))


def classify(outcome: Outcome, *, policy: ExecutionPolicy = DEFAULT_POLICY) -> Outcome:
    """The outcome the engine acts on, after correcting a terminal rate limit.

    Returns `outcome` unchanged in every case but one: a `TERMINAL` outcome
    whose `provider_status_code` is a configured retryable status becomes
    `RETRYABLE`, keeping every other field — including the status code and the
    connector's `error_code` — so the evidence of what happened survives the
    reclassification.

    See this module's docstring for why this is not the boundary violation it
    superficially resembles, and for the three limits on it.
    """
    if outcome.status is not OutcomeStatus.TERMINAL:
        return outcome
    status = outcome.provider_status_code
    if status is None or status not in policy.retryable_provider_status_codes:
        return outcome
    return replace(outcome, status=OutcomeStatus.RETRYABLE)


def throttle_cooldown_seconds(
    outcome: Outcome, *, policy: ExecutionPolicy = DEFAULT_POLICY
) -> int | None:
    """How long the whole INSTALLATION should pause, or `None` if it need not.

    Distinct from :func:`retry_delay_seconds`, which schedules ONE delivery.
    A 429 is a statement about the provider account, not about the payload that
    happened to hit the limit — so the sibling deliveries queued behind it are
    going to be refused too, and sending them anyway is how a throttle becomes a
    ban. `dispatch.settle` turns this number into
    `admission.apply_provider_cooldown`.

    The provider's own timing wins when it gave one; otherwise the configured
    cooldown applies. Both are capped by `max_backoff_seconds`, so a provider
    cannot park an installation past the ceiling an operator agreed to.
    """
    status = outcome.provider_status_code
    if status is None or status not in policy.throttling_provider_status_codes:
        return None
    named = outcome.retry_after_seconds
    seconds = (
        int(named) if named is not None else policy.default_throttle_cooldown_seconds
    )
    return max(0, min(seconds, policy.max_backoff_seconds))


def retry_delay_seconds(
    attempt_count: int,
    outcome: Outcome | None = None,
    *,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> int:
    """Seconds until the next attempt.

    :param attempt_count: attempts made SO FAR, so the first retry waits the
        base delay rather than double it.
    """
    if outcome is not None and outcome.retry_after_seconds is not None:
        # Trusted, but not unboundedly: a provider sending `retry_after: 10y`
        # would otherwise park the delivery past any operator's attention.
        return max(0, min(int(outcome.retry_after_seconds), policy.max_backoff_seconds))
    exponent = max(attempt_count - 1, 0)
    # Guard the shift itself: 2 ** 10_000 is computed before min() sees it.
    if exponent > 32:
        delay = int(policy.max_backoff_seconds)
    else:
        delay = int(
            min(policy.max_backoff_seconds, policy.base_delay_seconds * (2**exponent))
        )
    if outcome is not None:
        cooldown = throttle_cooldown_seconds(outcome, policy=policy)
        if cooldown is not None:
            # A throttling provider that named no `Retry-After` still told us
            # something: coming back sooner than the configured cooldown is
            # asking for the same refusal. The curve is a FLOOR here, never a
            # reason to return early.
            delay = max(delay, cooldown)
    return delay


def next_state(
    outcome: Outcome,
    *,
    attempt_count: int,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> str:
    """The delivery state this outcome produces.

    Attempt exhaustion turns RETRYABLE into `dead_letter`, and a terminal rate
    limit turns into RETRYABLE — the two places the engine overrides a
    connector's classification, and the only two.

    The `classify` call is FIRST so every caller gets the correction without
    opting in. `execution.record_delivery_outcome` and `dispatch.settle` both
    route through here, which is what makes "a 429 is never terminal" a property
    of the engine rather than a rule each settle path remembers.
    """
    outcome = classify(outcome, policy=policy)
    if outcome.status is OutcomeStatus.SUCCEEDED:
        return "delivered"
    if outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED:
        return "reconciliation_required"
    if outcome.status is OutcomeStatus.TERMINAL:
        return "dead_letter"
    # The policy validated its own bounds at construction, so no clamping is
    # needed here — a nonsense cap was refused where it was configured.
    return "dead_letter" if attempt_count >= policy.max_attempts else "retryable"
