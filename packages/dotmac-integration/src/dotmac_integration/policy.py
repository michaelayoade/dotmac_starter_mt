"""Execution policy — the operational numbers, typed and injected.

Retry caps, lease durations and staleness thresholds are DEPLOYMENT decisions,
not properties of the engine. Hardcoding them means an operator who needs a
90-second lease edits library code, and a module that ships one set of numbers
for every provider is wrong for all of them: a webhook fan-out and a nightly
bulk poll do not want the same backoff.

So they arrive through one frozen, validated object rather than as loose
keyword defaults scattered across call sites. Validation is at construction —
`max_attempts=0` refused when the policy is built beats it discovered when a
delivery dead-letters on its first attempt.

The DEFAULTS below are Sub's production values, kept so the port behaves as the
source did unless a deployment says otherwise.

## The safety knobs live here too, and deliberately not on a second object

`dispatch_enabled` (the kill switch), the per-installation concurrency ceiling
and the provider-status classification sets are the same KIND of decision as
`max_attempts`: numbers and switches a deployment owns and the engine merely
obeys. A second policy object would give a deployment two places to look and
this module two admission authorities — the parallel-control-path failure
ADR-0024 records. Each is validated here, at construction, for the same reason
`max_attempts=0` is.

None of the added defaults is prod-unsafe, and that is a deliberate property:
each reproduces the behaviour in force before the knob existed (dispatch on,
concurrency unbounded, backpressure applied, the same backoff curve), so an
assembly that upgrades without touching its policy gets exactly what it had.
The one value with a genuinely new effect — `default_throttle_cooldown_seconds`
— only ever applies when a provider has already said it is throttling and
declined to say for how long.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["DEFAULT_POLICY", "ExecutionPolicy"]

_MIN_ATTEMPTS: Final[int] = 1
_MAX_ATTEMPTS: Final[int] = 20
_MIN_HTTP_STATUS: Final[int] = 100
_MAX_HTTP_STATUS: Final[int] = 599


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Operational knobs for one deployment of the control plane."""

    #: Attempts before a retryable delivery is dead-lettered.
    max_attempts: int = 10
    #: First retry delay; doubles per attempt.
    base_delay_seconds: int = 60
    #: Ceiling. Beyond this a queue is not retrying, it is hoarding.
    max_backoff_seconds: int = 8 * 60 * 60
    #: How long a worker holds a delivery. Too long strands work when a worker
    #: dies; too short lets a slow provider call be double-sent.
    lease_seconds: int = 300
    #: A checkpoint that has not advanced in this long is reported stale.
    stale_checkpoint_after_seconds: int = 24 * 60 * 60

    # ── Safety ──────────────────────────────────────────────────────────────

    #: THE KILL SWITCH. `False` refuses every dispatch this policy governs.
    #:
    #: It halts, it does not delete: nothing is dequeued, no lease is broken,
    #: no state is rewritten, and `next_attempt_at` is left exactly where the
    #: retry curve put it. Flipping it back resumes the same queue with the
    #: same durable rows — which is the whole difference between a kill switch
    #: and a purge, and the reason this is a refusal at ADMISSION rather than
    #: anything that touches a row.
    dispatch_enabled: bool = True
    #: The most attempts one installation may have in flight at once, or `None`
    #: for unbounded — the behaviour before this knob existed.
    #:
    #: This is the backpressure that acts BEFORE a provider complains. A queue
    #: that has just been unpaused, or a burst enqueued by a bulk job, will
    #: otherwise open as many concurrent provider calls as there are workers,
    #: which is how an integration earns the rate limit it then has to back off
    #: from.
    max_in_flight_per_installation: int | None = None

    # ── Provider rate limits ────────────────────────────────────────────────

    #: HTTP statuses whose observation makes an outcome retryable even when the
    #: connector classified it TERMINAL.
    #:
    #: The engine branches on a TYPED HTTP status here, never on a connector's
    #: `error_code` — that boundary (see `retry`) is intact. A status code is
    #: the one piece of provider vocabulary that is standardised across every
    #: provider, so acting on it is generic in a way that acting on
    #: `crm_customer_name_rejected` never was.
    retryable_provider_status_codes: tuple[int, ...] = (
        408,  # Request Timeout — the request was not processed
        425,  # Too Early — replay protection asked us to come back
        429,  # Too Many Requests — the rate limit, the case this exists for
        500,  # Internal Server Error
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    )
    #: Statuses that mean the PROVIDER is throttling this account, so every
    #: other delivery queued for the same installation should also wait.
    #: A subset of `retryable_provider_status_codes`, checked at construction.
    throttling_provider_status_codes: tuple[int, ...] = (429, 503)
    #: How long the installation waits when a provider throttles and names no
    #: `Retry-After`. Never applied when the provider DID name one.
    default_throttle_cooldown_seconds: int = 60
    #: Whether an observed throttle delays the throttled installation's other
    #: queued deliveries. Off makes each delivery discover the limit alone.
    apply_provider_backpressure: bool = True

    # ── Metrics ─────────────────────────────────────────────────────────────

    #: The window latency and throughput metrics are computed over. A lifetime
    #: average tells an operator nothing about the last ten minutes.
    metrics_window_seconds: int = 60 * 60
    #: The most delivered rows one latency computation reads. Latency is
    #: derived in Python from two timestamp columns rather than in SQL, so the
    #: read stays dialect-neutral — and therefore has to stay bounded.
    metrics_sample_limit: int = 5_000

    def __post_init__(self) -> None:
        if not _MIN_ATTEMPTS <= self.max_attempts <= _MAX_ATTEMPTS:
            raise ValueError(
                f"max_attempts={self.max_attempts} is outside "
                f"[{_MIN_ATTEMPTS}, {_MAX_ATTEMPTS}]. Zero would dead-letter "
                "every delivery on its first attempt; an unbounded value makes "
                "a permanently failing delivery immortal"
            )
        if self.base_delay_seconds < 1:
            raise ValueError("base_delay_seconds must be at least 1")
        if self.max_backoff_seconds < self.base_delay_seconds:
            raise ValueError(
                "max_backoff_seconds must be at least base_delay_seconds, or "
                "the ceiling silently cancels the curve"
            )
        if self.lease_seconds < 1:
            raise ValueError(
                "lease_seconds must be at least 1 — a zero lease is no claim "
                "at all, and two dispatchers would both call the provider"
            )
        if self.stale_checkpoint_after_seconds < 0:
            raise ValueError("stale_checkpoint_after_seconds must not be negative")
        limit = self.max_in_flight_per_installation
        if limit is not None and limit < 1:
            raise ValueError(
                f"max_in_flight_per_installation={limit} is not a limit. Zero "
                "would refuse every dispatch while reading as a tuning value; "
                "the way to stop dispatching is dispatch_enabled=False, which "
                "says so"
            )
        for field_name in (
            "retryable_provider_status_codes",
            "throttling_provider_status_codes",
        ):
            statuses = getattr(self, field_name)
            for status in statuses:
                if (
                    not isinstance(status, int)
                    or isinstance(status, bool)
                    or not _MIN_HTTP_STATUS <= status <= _MAX_HTTP_STATUS
                ):
                    raise ValueError(
                        f"{field_name} contains {status!r}, which is not an "
                        f"HTTP status in [{_MIN_HTTP_STATUS}, {_MAX_HTTP_STATUS}]"
                    )
            if len(set(statuses)) != len(statuses):
                raise ValueError(f"{field_name} lists a status twice")
        not_retryable = set(self.throttling_provider_status_codes) - set(
            self.retryable_provider_status_codes
        )
        if not_retryable:
            # The invariant that keeps a rate limit from dead-lettering. A
            # status may only pause the whole installation if it can also
            # rescue the one delivery that observed it; otherwise the queue
            # backs off politely while the request that discovered the limit
            # is thrown away.
            raise ValueError(
                f"throttling_provider_status_codes {sorted(not_retryable)} are "
                "not in retryable_provider_status_codes. A throttle that is "
                "not retryable would delay the queue and dead-letter the "
                "delivery that observed it"
            )
        if not 1 <= self.default_throttle_cooldown_seconds <= self.max_backoff_seconds:
            raise ValueError(
                "default_throttle_cooldown_seconds must be between 1 and "
                f"max_backoff_seconds ({self.max_backoff_seconds}); a cooldown "
                "past the backoff ceiling parks a queue longer than the retry "
                "curve ever would"
            )
        if self.metrics_window_seconds < 1:
            raise ValueError(
                "metrics_window_seconds must be at least 1 — a zero window "
                "reports every rate as zero, which reads as a healthy idle "
                "queue rather than as an unset knob"
            )
        if self.metrics_sample_limit < 1:
            raise ValueError("metrics_sample_limit must be at least 1")


#: Sub's production values. Used when a caller supplies no policy, so the port
#: behaves as the source did by default.
DEFAULT_POLICY: Final[ExecutionPolicy] = ExecutionPolicy()
