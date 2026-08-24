"""May this worker dispatch right now? — the one admission authority.

`dispatch.prepare` already refuses a dispatch it cannot serve. This module owns
the other half: the dispatches it CAN serve and deliberately will not, right
now — because the deployment is halted, because the installation is
quarantined, or because the installation already has as much work in flight as
the deployment allows.

Keeping those in one place is the point. Scattering them would give the outbound
runtime several ways to stop, each with its own state, its own operator gesture
and its own way of being forgotten — the parallel-control-path failure ADR-0024
records. There is one decision function, one closed set of reasons, and
`prepare` consults it before it claims anything.

## Not-admitted is not unavailable, and both are not "no claim"

Three different answers a caller must be able to tell apart:

=========================== ===============================================
`prepare` returns `None`     the database did not grant a claim. Contention.
                             Move to the next delivery.
`DispatchNotAdmitted`        the runtime WILL not dispatch this right now.
                             Expected, reversible, already recorded. Stop
                             consuming this installation; do not alert.
`DispatchUnavailable`        the CONFIGURATION cannot serve it. A disabled
                             installation, a missing binding or plugin, a
                             manifest pin no longer honoured. Alert.
=========================== ===============================================

Collapsing the middle one into the third pages someone every time an operator
quarantines a connector; collapsing it into the first makes a halted deployment
look like a busy one.

## Quarantine is PER INSTALLATION

The unit is `connector_installations.state = 'quarantined'` — an existing state
in an existing table, not a new flag — and the choice between the three
candidate scopes is not arbitrary:

* **per installation (chosen).** An installation is the configured instance: it
  owns the credentials, the current configuration revision and the manifest pin.
  Everything that makes a connector misbehave in a way worth quarantining —
  wrong credentials, a provider account being abused, a runaway retry loop, a
  connector version that corrupts payloads — is a property of that instance.
  Quarantining it stops every capability it serves at once, which is what an
  operator who has just lost trust in a connector actually wants. It is also the
  only one of the three the platform can act on without a human deciding which
  half of a connector is safe.
* **per binding (rejected).** A binding is a ROUTE into an installation, and it
  already has an operator on/off switch (`lifecycle.set_binding_enabled`).
  Quarantining one binding leaves the same credentials and the same connector
  code running on every other binding of the same installation, so a compromised
  credential keeps being used. A narrower blast radius is worse when the thing
  you distrust is shared.
* **per capability (rejected).** A capability is a CONTRACT — `ticket.
  observation.v1` — implemented by many installations across many connectors.
  Quarantining it would stop unrelated, well-behaved installations because one
  of them misbehaved, which is a fleet outage dressed as a containment measure.

Quarantine is a HALT, never a delete: no queued delivery is removed, no lease is
broken, no `next_attempt_at` is rewritten. Releasing it (`lifecycle.
release_quarantine`) returns the installation to `disabled` — deliberately not
straight to `enabled`, so leaving quarantine and being trusted again are two
decisions and the second one runs `lifecycle.enable`'s live connection check.

## Backpressure is a schedule, never a sleep

Nothing in this module waits. A throttled provider is handled by moving
`next_attempt_at` forward on the installation's queued rows — one short UPDATE,
in the caller's transaction — so the pause lives in the database and survives a
worker restart. Sleeping instead would hold a session across wall-clock time,
which is the exact failure `dispatch`'s three-phase shape exists to prevent, and
would evaporate the moment the process died.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

from dotmac_integration.models import ConnectorInstallation, DeliveryAttempt
from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy

__all__ = [
    "ADMISSION_REASONS",
    "AdmissionDecision",
    "admit",
    "admit_installation",
    "admit_runtime",
    "apply_provider_cooldown",
]

#: The CLOSED set of reasons a dispatch may be refused admission. Closed for the
#: same reason `retention.REFUSAL_REASONS` is: a reason invented at a call site
#: cannot be counted, alerted on or reviewed, and an operator asking "why is
#: nothing dispatching?" needs an answer from a vocabulary, not a sentence.
#:
#: These strings are an OPERATOR-FACING contract — they reach metrics labels and
#: dashboards — so they are language-neutral and stable, and renaming one is a
#: breaking change.
ADMITTED: Final = "admitted"
DISPATCH_HALTED: Final = "dispatch_halted"
INSTALLATION_QUARANTINED: Final = "installation_quarantined"
INSTALLATION_AT_CONCURRENCY_LIMIT: Final = "installation_at_concurrency_limit"

ADMISSION_REASONS: Final[tuple[str, ...]] = (
    ADMITTED,
    DISPATCH_HALTED,
    INSTALLATION_QUARANTINED,
    INSTALLATION_AT_CONCURRENCY_LIMIT,
)

#: The state an installation is quarantined INTO. One name, referenced rather
#: than spelled at each site, so the state machine in `models
#: .INSTALLATION_STATES` and the admission check cannot drift apart silently.
QUARANTINED_STATE: Final = "quarantined"


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Whether to dispatch, and — when not — which rule said so."""

    admitted: bool
    #: One of `ADMISSION_REASONS`. Always set, including on the admitted path,
    #: so a caller logging the reason does not need a null branch.
    reason: str
    #: A hint for when the caller might come back, when the refusing rule knows.
    #: `None` means "nothing here schedules your return" — a halted deployment
    #: and a quarantined installation both wait on a person, not a clock.
    retry_after_seconds: int | None = None
    #: Human-readable, for logs and operator surfaces. Never parsed.
    detail: str = ""

    def __post_init__(self) -> None:
        if self.reason not in ADMISSION_REASONS:
            raise ValueError(
                f"{self.reason!r} is not one of the declared admission reasons "
                f"{list(ADMISSION_REASONS)}. A refusal with an ad-hoc reason "
                "cannot be counted or alerted on"
            )
        if self.admitted and self.reason != ADMITTED:
            raise ValueError("an admitted decision must carry the ADMITTED reason")
        if not self.admitted and self.reason == ADMITTED:
            raise ValueError("a refused decision must name the rule that refused")


_ADMIT: Final = AdmissionDecision(admitted=True, reason=ADMITTED)


def admit_runtime(policy: ExecutionPolicy = DEFAULT_POLICY) -> AdmissionDecision:
    """The kill switch, and nothing else. NO database access, by signature.

    Deliberately separable from `admit_installation` so the halted case costs a
    caller nothing: a deployment that has been switched off should not be
    issuing a query per queued row to discover that it is still switched off.
    """
    if policy.dispatch_enabled:
        return _ADMIT
    return AdmissionDecision(
        admitted=False,
        reason=DISPATCH_HALTED,
        detail=(
            "outbound dispatch is halted by ExecutionPolicy.dispatch_enabled. "
            "Queued deliveries, leases and retry schedules are untouched and "
            "resume when it is set back to True"
        ),
    )


def admit_installation(
    db: Any,
    installation: ConnectorInstallation,
    *,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> AdmissionDecision:
    """Quarantine, then the concurrency ceiling.

    Order matters. Quarantine is checked first because it is the answer an
    operator is owed: an installation that is both quarantined and at its
    concurrency limit is quarantined, and reporting the ceiling instead would
    send someone tuning a number when the real state is "the platform stopped
    trusting this".

    The ceiling is evaluated only for an ENABLED installation, which keeps every
    other state on the path it already had — `dispatch.prepare` still raises
    `DispatchUnavailable` for a disabled or draft installation, and this
    function does not quietly take that case over.
    """
    if installation.state == QUARANTINED_STATE:
        return AdmissionDecision(
            admitted=False,
            reason=INSTALLATION_QUARANTINED,
            detail=(
                f"installation {installation.name!r} is quarantined"
                + (
                    f": {installation.state_reason}"
                    if installation.state_reason
                    else ""
                )
                + ". Nothing queued for it is lost; release the quarantine and "
                "re-enable it to resume"
            ),
        )

    limit = policy.max_in_flight_per_installation
    if limit is None or installation.state != "enabled":
        return _ADMIT

    from sqlalchemy import func, select

    moment = now or datetime.now(UTC)
    in_flight = int(
        db.execute(
            select(func.count())
            .select_from(DeliveryAttempt)
            .where(
                DeliveryAttempt.installation_id == installation.id,
                DeliveryAttempt.state == "in_flight",
                # A LIVE lease only. An expired one is stranded work waiting for
                # `operations.release_expired_leases`, and counting it as
                # concurrency would let one dead worker throttle an installation
                # until someone noticed.
                DeliveryAttempt.leased_until.is_not(None),
                DeliveryAttempt.leased_until >= moment,
            )
        ).scalar_one()
        or 0
    )
    if in_flight < limit:
        return _ADMIT
    return AdmissionDecision(
        admitted=False,
        reason=INSTALLATION_AT_CONCURRENCY_LIMIT,
        # The lease is what bounds the wait: every counted attempt either
        # settles or expires within it, so that is the honest "come back in".
        retry_after_seconds=policy.lease_seconds,
        detail=(
            f"installation {installation.name!r} has {in_flight} deliveries in "
            f"flight, at its ceiling of {limit}"
        ),
    )


def admit(
    db: Any,
    installation: ConnectorInstallation,
    *,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> AdmissionDecision:
    """Both checks, in the order a dispatcher wants them."""
    runtime = admit_runtime(policy)
    if not runtime.admitted:
        return runtime
    return admit_installation(db, installation, policy=policy, now=now)


def apply_provider_cooldown(
    db: Any,
    *,
    installation_id: UUID,
    cooldown_seconds: int,
    now: datetime | None = None,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> int:
    """Delay one installation's other queued deliveries. Returns rows delayed.

    The backpressure half of rate-limit handling. One delivery observing a 429
    has learned something about the whole provider ACCOUNT, and the deliveries
    queued behind it will meet the same limit — so they wait, in the database,
    by moving `next_attempt_at` forward.

    Three properties this deliberately has:

    * **it only ever delays.** A row already scheduled further out keeps its
      later time, so a cooldown can never pull a backed-off delivery forward and
      undo the retry curve;
    * **it touches no in-flight, delivered or terminal row.** A cooldown is a
      statement about work not yet started; rewriting the schedule of a delivery
      another worker holds would fight that worker's `settle`;
    * **it is one UPDATE, and it waits for nothing.** No session is held across
      any provider I/O — this runs in the short settle transaction, after the
      provider call has already returned.
    """
    from sqlalchemy import or_, update

    moment = now or datetime.now(UTC)
    seconds = max(0, min(int(cooldown_seconds), policy.max_backoff_seconds))
    until = moment + timedelta(seconds=seconds)
    result = db.execute(
        update(DeliveryAttempt)
        .where(
            DeliveryAttempt.installation_id == installation_id,
            DeliveryAttempt.state.in_(("pending", "retryable")),
            or_(
                DeliveryAttempt.next_attempt_at.is_(None),
                DeliveryAttempt.next_attempt_at < until,
            ),
        )
        .values(next_attempt_at=until)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
